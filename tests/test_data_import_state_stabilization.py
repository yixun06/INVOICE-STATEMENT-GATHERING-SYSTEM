from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.services.data_import_state import (
    clear_statement_upload_attempt,
    has_unfinished_session_work,
    invoice_upload_downstream_eligibility,
    reset_invoice_upload_attempt,
    statement_stage_review_consistency,
)
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
)
from src.invoice_app.services.validation_recovery import (
    REMOVE_STAGED_SOURCE,
    execute_current_batch_recovery,
    execute_current_invoice_staging_exit,
    plan_current_invoice_staging_exit,
    recovery_actions_for_source,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _historical_signature(state: dict) -> str:
    rows = []
    for bucket in (
        "orders",
        "products",
        "reviews",
        "processing_errors",
        "duplicate_skipped",
        "unsupported_files",
    ):
        for record in state.get(bucket, []):
            if isinstance(record, dict):
                rows.append(
                    (
                        bucket,
                        tuple(
                            sorted(
                                (str(key), repr(value))
                                for key, value in record.items()
                            )
                        ),
                    )
                )
    return sha256(
        repr((state.get("batch_id"), tuple(rows))).encode("utf-8")
    ).hexdigest()


def _new_historical_entry(order_id: str = "OLD-1") -> InvoiceIntakeEntry:
    bundle = InvoiceBundle(
        order=CanonicalInvoiceOrder(
            platform="Shopee",
            order_id=order_id,
            income_type="Final",
            order_income=Decimal("10.00"),
            payment_status="Released",
            source_pdf="old.pdf",
            source_hash="source-hash",
            first_imported_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
        ),
        items=(
            CanonicalInvoiceItem(
                platform="Shopee",
                order_id=order_id,
                item_index=0,
                seller_sku="SKU-1",
                product_name="Old product",
                quantity=1,
                unit_price=Decimal("10.00"),
                actual_selling_unit_price=Decimal("10.00"),
                line_subtotal=Decimal("10.00"),
                source_pdf="old.pdf",
                source_hash="source-hash",
            ),
        ),
    )
    return InvoiceIntakeEntry(
        staging_id="ready-new-entry",
        source_filename="old.pdf",
        source_hash="a" * 64,
        order_id=order_id,
        status=IntakeStatus.NEW,
        message=None,
        bundle=bundle,
    )


def _seed_old_invoice_batch(app: AppTest, *, attempt: str, step: int) -> None:
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "import_source_type": "Platform Orders",
        "data_import_step": step,
        "batch_id": "old-ready-batch",
        "orders": [
            {
                "platform": "Shopee",
                "order_id": "OLD-1",
                "status": "Accepted",
                "source_pdf": "old.pdf",
            }
        ],
        "products": [
            {
                "platform": "Shopee",
                "order_id": "OLD-1",
                "product_name": "Old product",
                "quantity": 1,
                "status": "Accepted",
                "source_pdf": "old.pdf",
            }
        ],
        "reviews": [],
        "upload_result_summary": {"pdfs_processed": 1, "orders_imported": 1},
        "uat2_historical_commit_entries": ("older-ready-classification",),
        "uat2_historical_commit_signature": "older-ready-signature",
        "invoice_upload_attempt": attempt,
    }.items():
        app.session_state[key] = value


@pytest.mark.parametrize(
    ("attempt", "step"),
    (("selected", 4), ("failed", 4), ("interrupted", 5)),
)
def test_old_ready_invoice_batch_cannot_bypass_current_upload(
    tmp_path, monkeypatch, attempt: str, step: int
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_old_invoice_batch(app, attempt=attempt, step=step)

    app.run(timeout=20)

    assert app.exception == []
    assert any("Upload not completed" in item.value for item in (*app.info, *app.error))
    assert not any(
        button.label
        in {"Continue to review & commit", "Commit Accepted Shopee Invoices"}
        and not button.disabled
        for button in app.button
    )


@pytest.mark.parametrize("attempt", ("selected", "processing", "failed", "interrupted"))
def test_invoice_downstream_eligibility_blocks_every_unresolved_attempt(attempt: str):
    eligibility = invoice_upload_downstream_eligibility(
        {
            "invoice_upload_attempt": attempt,
            "batch_id": "old",
            "upload_result_summary": {"pdfs_processed": 1},
        }
    )
    assert eligibility.eligible is False


def test_resolved_or_previously_resolved_invoice_upload_allows_normal_downstream_checks():
    assert invoice_upload_downstream_eligibility(
        {"invoice_upload_attempt": "resolved"}
    ).eligible
    assert invoice_upload_downstream_eligibility(
        {"upload_result_summary": {"pdfs_processed": 1}}
    ).eligible


@pytest.mark.parametrize("step", (4, 5))
def test_resolved_upload_with_current_new_classification_allows_downstream_happy_path(
    tmp_path, monkeypatch, step: int
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_old_invoice_batch(app, attempt="resolved", step=step)
    app.session_state["uat2_historical_commit_entries"] = (_new_historical_entry(),)
    app.session_state["uat2_historical_commit_signature"] = _historical_signature(
        app.session_state.filtered_state
    )

    app.run(timeout=20)

    assert app.exception == []
    if step == 4:
        assert any(
            button.label == "Continue to review & commit" and not button.disabled
            for button in app.button
        )
    else:
        assert any("Ready to Commit" in success.value for success in app.success)
        assert any(
            button.label == "Commit Accepted Shopee Invoices" and not button.disabled
            for button in app.button
        )


def test_current_unresolved_upload_overrides_valid_new_historical_classification(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_old_invoice_batch(app, attempt="selected", step=5)
    app.session_state["uat2_historical_commit_entries"] = (_new_historical_entry(),)
    app.session_state["uat2_historical_commit_signature"] = _historical_signature(
        app.session_state.filtered_state
    )

    app.run(timeout=20)

    assert app.exception == []
    assert any("Upload not completed" in item.value for item in (*app.info, *app.error))
    assert not any(
        button.label == "Commit Accepted Shopee Invoices" and not button.disabled
        for button in app.button
    )


def test_stale_processing_activity_blocks_then_safe_reset_preserves_staging():
    orders = [{"order_id": "OLD-1"}]
    state = {
        "invoice_upload_attempt": "processing",
        "workflow_activity": "Processing",
        "workflow_navigation_blocked": {"page": "Dashboard"},
        "pdf_uploader_4": ["retry.pdf"],
        "uploader_version": 4,
        "orders": orders,
        "batch_id": "old-ready-batch",
    }
    assert not invoice_upload_downstream_eligibility(state).eligible

    reset_invoice_upload_attempt(state)

    assert "invoice_upload_attempt" not in state
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state
    assert "pdf_uploader_4" not in state
    assert state["uploader_version"] == 5
    assert state["orders"] is orders
    assert state["batch_id"] == "old-ready-batch"


@pytest.mark.parametrize("attempt", ("failed", "interrupted"))
def test_failed_or_interrupted_upload_exposes_executable_narrow_recovery(
    tmp_path, monkeypatch, attempt: str
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_old_invoice_batch(app, attempt=attempt, step=2)
    expected_orders = list(app.session_state["orders"])

    app.run(timeout=20)

    clear_button = next(
        button
        for button in app.button
        if button.label == "Clear incomplete upload attempt"
    )
    assert not clear_button.disabled
    assert app.file_uploader
    clear_button.click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert "invoice_upload_attempt" not in state
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state
    assert state["orders"] == expected_orders
    assert state["batch_id"] == "old-ready-batch"


def test_invoice_selected_back_is_truthful_and_keeps_processed_staging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_old_invoice_batch(app, attempt="resolved", step=2)
    expected_orders = list(app.session_state["orders"])
    del app.session_state["invoice_upload_attempt"]
    del app.session_state["upload_result_summary"]

    app.run(timeout=20)
    app.file_uploader[0].upload("new.pdf", b"pdf", "application/pdf").run(timeout=20)
    next(
        button for button in app.button if button.key == "data_import_upload_back_1"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert "invoice_upload_attempt" not in state
    assert state["orders"] == expected_orders
    assert state["batch_id"] == "old-ready-batch"


def test_statement_selected_back_is_truthful_and_keeps_unrelated_staging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    invoice_orders = [{"order_id": "KEEP-INVOICE"}]
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 2,
        "orders": invoice_orders,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    app.file_uploader[0].upload(
        "statement.xlsx",
        b"not processed",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).run(timeout=20)
    next(button for button in app.button if button.key == "statement_upload_back").click().run(
        timeout=20
    )

    state = app.session_state.filtered_state
    assert app.exception == []
    assert "weekly_statement_upload_selected" not in state
    assert state["orders"] == invoice_orders
    assert "weekly_statement_stage" not in state


@pytest.mark.parametrize(
    ("state", "expected"),
    (
        ({}, "empty"),
        ({"weekly_statement_stage": object()}, "stage_only"),
        ({"weekly_statement_review": object()}, "review_only"),
    ),
)
def test_statement_stage_review_partial_states_fail_closed(state: dict, expected: str):
    consistency = statement_stage_review_consistency(state)
    assert consistency.state == expected
    assert consistency.coherent is (expected == "empty")


def test_coherent_statement_stage_review_pair_is_accepted():
    stage = object()
    state = {
        "weekly_statement_stage": stage,
        "weekly_statement_review": SimpleNamespace(stage=stage),
    }
    consistency = statement_stage_review_consistency(state)
    assert consistency.coherent
    assert consistency.state == "coherent"


def test_statement_recheck_pair_restores_consistency_after_partial_state_is_cleared():
    state = {"weekly_statement_stage": object()}
    assert not statement_stage_review_consistency(state).coherent

    clear_statement_upload_attempt(state)
    rebuilt_stage = object()
    state["weekly_statement_stage"] = rebuilt_stage
    state["weekly_statement_review"] = SimpleNamespace(stage=rebuilt_stage)

    assert statement_stage_review_consistency(state).state == "coherent"


@pytest.mark.parametrize("step", (2, 3, 4, 5))
@pytest.mark.parametrize("partial", ("stage_only", "review_only"))
def test_direct_statement_destinations_block_partial_state(
    tmp_path, monkeypatch, step: int, partial: str
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": step,
        (
            "weekly_statement_stage"
            if partial == "stage_only"
            else "weekly_statement_review"
        ): object(),
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    assert any("Statement check required" in item.value for item in app.error)
    assert not any(
        button.label == "Commit Statement" and not button.disabled
        for button in app.button
    )
    assert not any(button.label == "Continue to validate" for button in app.button)


def test_clear_inconsistent_statement_attempt_clears_pair_and_activity_only():
    invoice_rows = [{"order_id": "KEEP"}]
    state = {
        "weekly_statement_stage": object(),
        "weekly_statement_review": object(),
        "weekly_statement_upload_selected": True,
        "weekly_statement_uploader_2": ["statement.xlsx"],
        "weekly_statement_uploader_version": 2,
        "workflow_activity": "Validating",
        "workflow_navigation_blocked": {"page": "Dashboard"},
        "orders": invoice_rows,
    }

    clear_statement_upload_attempt(state)

    assert "weekly_statement_stage" not in state
    assert "weekly_statement_review" not in state
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state
    assert state["weekly_statement_uploader_version"] == 3
    assert state["orders"] is invoice_rows


def test_statement_exit_clears_stale_activity_and_both_statement_facts():
    state = {
        "weekly_statement_stage": object(),
        "weekly_statement_review": object(),
        "workflow_activity": "Validating",
        "workflow_navigation_blocked": {"page": "Dashboard"},
    }
    action = recovery_actions_for_source(
        source="statement.xlsx",
        action_type=REMOVE_STAGED_SOURCE,
        remove_label="Remove staged source",
        include_details=False,
    )[0]

    execution = execute_current_batch_recovery(state, action)

    assert execution.changed
    assert "weekly_statement_stage" not in state
    assert "weekly_statement_review" not in state
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state


def test_invoice_exit_clears_stale_activity_with_uncommitted_staging():
    state = {
        "import_source_type": "Platform Orders",
        "batch_id": "invoice-exit",
        "orders": [{"order_id": "CURRENT"}],
        "workflow_activity": "Processing",
        "workflow_navigation_blocked": {"page": "Dashboard"},
    }
    action = plan_current_invoice_staging_exit(state)
    assert action is not None

    execution = execute_current_invoice_staging_exit(state, action)

    assert execution.changed
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state


@pytest.mark.parametrize(
    "state",
    (
        {"invoice_upload_attempt": "selected"},
        {"invoice_upload_attempt": "failed"},
        {"invoice_upload_attempt": "unknown"},
        {"weekly_statement_upload_selected": True},
        {"weekly_statement_stage": object()},
        {"weekly_statement_review": object()},
        {"workflow_activity": "Processing"},
        {"workflow_navigation_blocked": {"page": "Dashboard"}},
        {"orders": [{"order_id": "1"}], "weekly_statement_stage": object()},
    ),
)
def test_logout_detection_covers_selected_unresolved_activity_and_mixed_work(state: dict):
    assert has_unfinished_session_work(state)


def test_logout_detection_does_not_warn_for_genuinely_empty_or_completed_state():
    assert not has_unfinished_session_work({})
    assert not has_unfinished_session_work(
        {
            "invoice_commit_completed": True,
            "batch_id": "completed",
            "orders": [{"order_id": "DONE"}],
        }
    )


def test_logout_without_unfinished_work_does_not_show_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert state["authenticated"] is False
    assert not any(
        button.key == "confirm_logout_with_unfinished_work" for button in app.button
    )


@pytest.mark.parametrize(
    "unfinished",
    (
        {"invoice_upload_attempt": "selected"},
        {"invoice_upload_attempt": "interrupted"},
        {"weekly_statement_upload_selected": True},
        {"weekly_statement_stage": object()},
        {
            "orders": [{"order_id": "MIXED"}],
            "weekly_statement_review": object(),
        },
    ),
)
def test_logout_confirmation_is_shown_for_transient_and_mixed_work(
    tmp_path, monkeypatch, unfinished: dict
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    for key, value in unfinished.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)

    assert app.exception == []
    assert any(
        button.key == "confirm_logout_with_unfinished_work" for button in app.button
    )
    assert app.session_state.filtered_state["authenticated"] is True


def test_confirmed_logout_clears_stale_activity_and_navigation_lock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "workflow_activity": "Processing",
        "workflow_navigation_blocked": {"page": "Dashboard", "activity": "Processing"},
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)
    assert any(
        button.key == "confirm_logout_with_unfinished_work" for button in app.button
    )
    next(
        button
        for button in app.button
        if button.key == "confirm_logout_with_unfinished_work"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert state["authenticated"] is False
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state


def test_confirmed_logout_clears_statement_selection_and_widget_state(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "weekly_statement_upload_selected": True,
        "weekly_statement_uploader_version": 7,
        "weekly_statement_uploader_7": ["statement.xlsx"],
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Logout").click().run(timeout=20)
    next(
        button
        for button in app.button
        if button.key == "confirm_logout_with_unfinished_work"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert state["authenticated"] is False
    assert "weekly_statement_upload_selected" not in state
    assert "weekly_statement_uploader_7" not in state


def test_confirmed_discard_clears_stale_activity_and_restores_navigation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "discard-stale-activity",
        "orders": [{"order_id": "CURRENT"}],
        "workflow_activity": "Processing",
        "workflow_navigation_blocked": {"page": "Dashboard", "activity": "Processing"},
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(
        button for button in app.button if button.label == "Discard current batch"
    ).click().run(timeout=20)
    next(
        button for button in app.button if button.key == "confirm_discard_current_batch"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert "workflow_activity" not in state
    assert "workflow_navigation_blocked" not in state
    assert "batch_id" not in state
    assert any(button.label == "Dashboard" for button in app.button)
