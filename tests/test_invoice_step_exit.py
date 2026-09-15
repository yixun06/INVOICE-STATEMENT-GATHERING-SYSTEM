from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
)
from src.invoice_app.services.validation_recovery import (
    execute_current_invoice_staging_exit,
    plan_current_invoice_staging_exit,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _invoice_state() -> dict:
    return {
        "authenticated": True,
        "authenticated_username": "Admin",
        "navigation": "Data Import",
        "import_source_type": "Platform Orders",
        "data_import_step": 3,
        "batch_id": "invoice-exit-batch",
        "pdf_count": 3,
        "uploader_version": 7,
        "pdf_uploader_7": ["selected.zip"],
        "orders": [
            {
                "platform": "Shopee",
                "order_id": "ORDER-1",
                "source_pdf": "invoice.pdf",
                "status": "Accepted",
            }
        ],
        "products": [
            {
                "platform": "Shopee",
                "order_id": "ORDER-1",
                "source_pdf": "invoice.pdf",
                "product_name": "Product",
                "quantity": 1,
                "status": "Accepted",
            }
        ],
        "reviews": [
            {
                "platform": "Shopee",
                "order_id": "REVIEW-1",
                "source_pdf": "review.pdf",
                "status": "Manual Review",
                "reason": "Source review required",
            }
        ],
        "duplicate_skipped": [
            {"source_pdf": "duplicate.pdf", "order_id": "DUPLICATE-1"}
        ],
        "unsupported_files": [{"filename": "unsupported.txt"}],
        "processing_errors": [{"filename": "broken.pdf"}],
        "upload_result_summary": {"pdfs_processed": 3},
        "uat2_historical_commit_entries": ("uncommitted-preview",),
        "uat2_historical_commit_refresh_required": True,
        "uat2_historical_commit_signature": "stale-preview",
        "manual_review_correction_drafts": {"review-key": {"products": []}},
        "manual_resolution_notice": "Draft updated.",
        "validation_recovery_detail": "detail",
        "view_customize_open": True,
        "data_import_current_batch_order_filter": "ORDER-1",
        "mr_income_review-key": "12.00",
        # Unrelated/completed state must survive the narrow Invoice exit.
        "weekly_statement_stage": object(),
        "weekly_statement_review": object(),
        "weekly_statement_commit_completed": True,
        "Shopee_order_filter": "keep-global-preference",
        "remote_invoice_rows": [{"order_id": "REMOTE-1"}],
    }


def _seed_app(app: AppTest, state: dict) -> None:
    for key, value in state.items():
        app.session_state[key] = value


def test_authoritative_invoice_exit_clears_only_uncommitted_staging():
    state = _invoice_state()
    statement_stage = state["weekly_statement_stage"]
    statement_review = state["weekly_statement_review"]
    remote_rows = state["remote_invoice_rows"]
    action = plan_current_invoice_staging_exit(state)

    assert action is not None
    execution = execute_current_invoice_staging_exit(state, action)

    assert execution.changed is True
    assert execution.revalidated is False
    for key in (
        "orders",
        "products",
        "reviews",
        "duplicate_skipped",
        "unsupported_files",
        "processing_errors",
        "batch_id",
        "pdf_count",
        "upload_result_summary",
        "uat2_historical_commit_entries",
        "uat2_historical_commit_refresh_required",
        "uat2_historical_commit_signature",
        "manual_review_correction_drafts",
        "manual_resolution_notice",
        "validation_recovery_detail",
        "view_customize_open",
        "pdf_uploader_7",
        "data_import_current_batch_order_filter",
        "mr_income_review-key",
    ):
        assert key not in state
    assert state["uploader_version"] == 8
    assert state["data_import_step"] == 3
    assert state["import_source_type"] == "Platform Orders"
    assert state["authenticated"] is True
    assert state["weekly_statement_stage"] is statement_stage
    assert state["weekly_statement_review"] is statement_review
    assert state["weekly_statement_commit_completed"] is True
    assert state["Shopee_order_filter"] == "keep-global-preference"
    assert state["remote_invoice_rows"] is remote_rows


def test_invoice_exit_fails_closed_if_staging_changes_after_confirmation():
    state = _invoice_state()
    action = plan_current_invoice_staging_exit(state)
    assert action is not None
    state["orders"].append({"order_id": "NEWER-STAGING"})

    with pytest.raises(ValueError, match="changed after exit confirmation"):
        execute_current_invoice_staging_exit(state, action)

    assert len(state["orders"]) == 2
    assert state["batch_id"] == "invoice-exit-batch"
    assert state["reviews"]


def test_committed_invoice_evidence_never_plans_destructive_exit():
    state = _invoice_state()
    state["invoice_commit_completed"] = True
    assert plan_current_invoice_staging_exit(state) is None

    state.pop("invoice_commit_completed")
    state["uat2_historical_commit_entries"] = (
        InvoiceIntakeEntry(
            staging_id="imported",
            source_filename="invoice.pdf",
            source_hash="hash",
            order_id="ORDER-1",
            status=IntakeStatus.IMPORTED,
            message="Imported",
            bundle=None,
        ),
    )
    assert plan_current_invoice_staging_exit(state) is None


def test_non_invoice_source_never_plans_invoice_exit():
    state = _invoice_state()
    state["import_source_type"] = "Shopee Weekly Statement"

    assert plan_current_invoice_staging_exit(state) is None


def test_invoice_exit_dialog_cancel_preserves_all_staging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = _invoice_state()
    # Keep this UI fixture outside the Manual Review form internals; the
    # authoritative service test above covers correction-draft ownership.
    state.pop("manual_review_correction_drafts")
    state.pop("mr_income_review-key")
    _seed_app(app, state)
    app.run(timeout=20)

    next(
        button for button in app.button
        if button.label == "Exit Invoice Import"
    ).click().run(timeout=20)

    pending = app.session_state.filtered_state
    assert pending["orders"] == state["orders"]
    assert pending["products"] == state["products"]
    assert pending["reviews"] == state["reviews"]
    assert pending["duplicate_skipped"] == state["duplicate_skipped"]
    assert pending["batch_id"] == "invoice-exit-batch"
    assert pending["uploader_version"] == 7
    assert any(button.label == "Leave Invoice Import" for button in app.button)
    dialog_copy = " ".join(warning.value for warning in app.warning)
    assert "All uncommitted data for this Invoice import will be cleared" in dialog_copy
    assert "Previously committed data and archived source files will remain unchanged" in dialog_copy

    next(
        button for button in app.button
        if button.key == "cancel_exit_invoice_import"
    ).click().run(timeout=20)

    cancelled = app.session_state.filtered_state
    assert cancelled["data_import_step"] == 3
    assert cancelled["orders"] == state["orders"]
    assert cancelled["products"] == state["products"]
    assert cancelled["reviews"] == state["reviews"]
    assert cancelled["duplicate_skipped"] == state["duplicate_skipped"]
    assert cancelled["weekly_statement_stage"] is not None


def test_confirm_invoice_exit_returns_to_upload_and_preserves_unrelated_state(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = _invoice_state()
    state.pop("manual_review_correction_drafts")
    state.pop("mr_income_review-key")
    _seed_app(app, state)
    app.run(timeout=20)

    next(
        button for button in app.button
        if button.label == "Exit Invoice Import"
    ).click().run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Leave Invoice Import"
    ).click().run(timeout=20)

    exited = app.session_state.filtered_state
    assert app.exception == []
    assert exited["data_import_step"] == 2
    assert exited["import_source_type"] == "Platform Orders"
    assert exited["uploader_version"] == 8
    for key in (
        "orders",
        "products",
        "reviews",
        "duplicate_skipped",
        "unsupported_files",
        "processing_errors",
        "batch_id",
        "upload_result_summary",
        "uat2_historical_commit_entries",
        "uat2_historical_commit_signature",
        "manual_resolution_notice",
        "data_import_current_batch_order_filter",
        "pdf_uploader_7",
    ):
        assert key not in exited
    assert exited["authenticated"] is True
    assert exited["weekly_statement_stage"] is not None
    assert exited["weekly_statement_review"] is not None
    assert exited["weekly_statement_commit_completed"] is True
    assert exited["Shopee_order_filter"] == "keep-global-preference"
    assert exited["remote_invoice_rows"] == [{"order_id": "REMOTE-1"}]
    assert any("Removed current uncommitted Invoice staging" in item.value for item in app.success)


def test_failed_invoice_exit_keeps_current_work_and_step(tmp_path, monkeypatch):
    def fail_recovery(_state, _action):
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(
        "src.invoice_app.ui.data_import.execute_current_batch_recovery",
        fail_recovery,
    )
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = _invoice_state()
    state.pop("manual_review_correction_drafts")
    state.pop("mr_income_review-key")
    _seed_app(app, state)
    app.run(timeout=20)

    next(
        button for button in app.button
        if button.label == "Exit Invoice Import"
    ).click().run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Leave Invoice Import"
    ).click().run(timeout=20)

    current = app.session_state.filtered_state
    assert app.exception == []
    assert current["data_import_step"] == 3
    assert current["orders"] == state["orders"]
    assert current["products"] == state["products"]
    assert current["reviews"] == state["reviews"]
    assert current["batch_id"] == "invoice-exit-batch"
    assert any(
        "Unable to remove the current Invoice staging" in error.value
        for error in app.error
    )
    assert not any("Recovery complete" in item.value for item in app.success)


def test_committed_invoice_ui_has_no_destructive_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = _invoice_state()
    state["data_import_step"] = 5
    state["invoice_commit_completed"] = True
    _seed_app(app, state)

    app.run(timeout=20)

    assert app.exception == []
    assert "Historical Invoice Commit Complete." in {
        item.value for item in app.success
    }
    assert not any(
        button.label == "Exit Invoice Import" for button in app.button
    )


def test_invoice_back_remains_navigation_only_and_confirmation_free(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = _invoice_state()
    state.pop("manual_review_correction_drafts")
    state.pop("mr_income_review-key")
    _seed_app(app, state)
    app.run(timeout=20)

    next(
        button for button in app.button
        if button.key == "data_import_back_4"
    ).click().run(timeout=20)

    current = app.session_state.filtered_state
    assert app.exception == []
    assert current["data_import_step"] == 2
    assert current["orders"] == state["orders"]
    assert current["products"] == state["products"]
    assert current["reviews"] == state["reviews"]
    assert current["duplicate_skipped"] == state["duplicate_skipped"]
    assert not any(
        button.label == "Leave Invoice Import" for button in app.button
    )
