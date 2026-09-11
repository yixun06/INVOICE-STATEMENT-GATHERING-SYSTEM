from __future__ import annotations

from hashlib import sha256
import pytest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.import_result_adapters import adapt_platform_orders_import_result
from src.invoice_app.services.import_result_contract import RecoveryAction
from src.invoice_app.services.validation_recovery import (
    REMOVE_SOURCE,
    execute_current_batch_recovery,
)
from src.invoice_app.services.workflow_navigation import (
    begin_workflow_activity,
    end_workflow_activity,
    request_navigation,
)
from src.invoice_app.services.historical_invoice_intake import IntakeStatus, InvoiceIntakeEntry
from src.invoice_app.review_reason_codes import (
    INCOME_EXTRACTION_MISSING,
    INCOME_SOURCE_INCOMPLETE,
)



APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _historical_signature(state):
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
    return sha256(repr((state.get("batch_id"), tuple(rows))).encode("utf-8")).hexdigest()


def _state(**overrides):
    state = {
        "orders": [
            {
                "platform": "Shopee",
                "order_id": "SHP-GOOD",
                "source_pdf": "good.pdf",
                "status": "Accepted",
            }
        ],
        "products": [],
        "reviews": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
        "upload_result_summary": {"pdfs_processed": 2},
    }
    state.update(overrides)
    return state


def _result(state):
    return adapt_platform_orders_import_result(
        batch_id="batch-recovery",
        orders=state["orders"],
        products=state["products"],
        reviews=state["reviews"],
        processing_errors=state["processing_errors"],
        duplicate_skipped=state["duplicate_skipped"],
        unsupported_files=state["unsupported_files"],
    )


def _action(result, layer: str):
    issue = next(issue for issue in (*result.validation.blocking_issues, *result.validation.warnings) if issue.layer == layer)
    return next(action for action in issue.recovery_actions if action.destructive)


def test_manual_review_source_remove_revalidates_remaining_batch_to_ready():
    state = _state(
        reviews=[
            {
                "platform": "Shopee",
                "order_id": "SHP-BAD",
                "source_pdf": "manual.pdf",
                "status": "Manual Review",
                "reason": "Product Count Mismatch",
            }
        ]
    )
    before = _result(state)

    execution = execute_current_batch_recovery(state, _action(before, "manual_review"))
    after = _result(state)

    assert execution.changed is True
    assert execution.revalidated is True
    assert state["reviews"] == []
    assert after.validation.has_blocking_issues is False
    assert after.commit_readiness.ready is True


def test_duplicate_remove_revalidates_without_removing_original_accepted_order():
    state = _state(
        duplicate_skipped=[
            {
                "source_pdf": "duplicate.pdf",
                "platform": "Shopee",
                "order_id": "SHP-GOOD",
                "status": "Duplicate Skipped",
                "reason": "Duplicate Order",
            }
        ]
    )

    execution = execute_current_batch_recovery(state, _action(_result(state), "duplicate"))

    assert execution.revalidated is True
    assert state["duplicate_skipped"] == []
    assert state["orders"][0]["order_id"] == "SHP-GOOD"


def test_unsupported_and_processing_error_sources_can_be_removed_separately():
    state = _state(
        unsupported_files=[{"source_pdf": "unsupported.pdf", "status": "Unsupported", "message": "Unsupported layout"}],
        processing_errors=[{"source_pdf": "broken.pdf", "status": "Processing Error", "message": "Unreadable PDF"}],
    )
    before = _result(state)

    processing_execution = execute_current_batch_recovery(state, _action(before, "ingestion"))
    assert processing_execution.changed is True
    assert _result(state).commit_readiness.ready is True

    unsupported_action = _action(_result(state), "ingestion")
    unsupported_execution = execute_current_batch_recovery(state, unsupported_action)
    assert unsupported_execution.changed is True
    assert state["unsupported_files"] == []


def test_force_pass_is_not_an_allowed_recovery_operation():
    state = _state()
    action = RecoveryAction(
        action_id="force-pass",
        action_type="force_pass",
        label="Force Pass",
        affected_item="good.pdf",
        allowed=True,
        destructive=False,
        requires_revalidation=False,
    )

    with pytest.raises(ValueError, match="Unsupported recovery action"):
        execute_current_batch_recovery(state, action)


def test_navigation_is_free_when_idle_and_blocked_only_during_workflow_activity():
    state = {"navigation": "Data Import", "batch_id": "active"}

    assert request_navigation(state, "Dashboard") is True
    assert state["navigation"] == "Dashboard"

    begin_workflow_activity(state, "Revalidating")
    assert request_navigation(state, "Settlement Test Lab") is False
    assert state["navigation"] == "Dashboard"
    assert state["workflow_navigation_blocked"]["activity"] == "Revalidating"

    end_workflow_activity(state)
    assert request_navigation(state, "Settlement Test Lab") is True
    assert state["navigation"] == "Settlement Test Lab"


def test_recovery_remove_requires_confirmation_before_current_batch_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "recovery-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = [{"platform": "Shopee", "order_id": "SHP-GOOD", "source_pdf": "good.pdf"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = [{"platform": "Shopee", "order_id": "SHP-BAD", "source_pdf": "manual.pdf", "status": "Manual Review", "reason": "Product Count Mismatch"}]
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    app.run(timeout=20)

    next(button for button in app.button if button.label == "Remove source from current batch").click().run(timeout=20)
    assert app.session_state.filtered_state["reviews"][0]["source_pdf"] == "manual.pdf"
    assert any(button.label == "Confirm removal and revalidate" for button in app.button)

    next(button for button in app.button if button.label == "Confirm removal and revalidate").click().run(timeout=20)
    assert app.exception == []
    assert app.session_state.filtered_state["reviews"] == []


def test_historical_conflict_remove_reuses_confirmed_current_batch_recovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "historical-conflict-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = [{"platform": "Shopee", "order_id": "SHP-CONFLICT", "source_pdf": "conflict.pdf", "status": "Accepted"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    app.session_state["uat2_historical_commit_entries"] = (
        InvoiceIntakeEntry(
            staging_id="conflict", source_filename="conflict.pdf", source_hash="hash",
            order_id="SHP-CONFLICT", status=IntakeStatus.SOURCE_CONFLICT,
            message="Stored historical invoice has different material source facts.", bundle=None,
        ),
    )
    app.session_state["uat2_historical_commit_signature"] = _historical_signature(
        app.session_state.filtered_state
    )
    app.run(timeout=20)

    assert "Historical Invoice Status" not in {element.value for element in app.subheader}
    assert all("Historical Status" not in frame.value.columns for frame in app.dataframe)

    app.session_state["data_import_step"] = 4
    app.run(timeout=20)

    historical_table = next(
        frame.value
        for frame in app.dataframe
        if tuple(frame.value.columns)
        == ("Source PDF", "Order ID", "Historical Status", "Reason / Message")
    )
    assert historical_table.to_dict("records") == [
        {
            "Source PDF": "conflict.pdf",
            "Order ID": "SHP-CONFLICT",
            "Historical Status": "SOURCE_CONFLICT",
            "Reason / Message": "Stored historical invoice has different material source facts.",
        }
    ]
    assert "Check Historical Status" not in {button.label for button in app.button}
    next(button for button in app.button if button.label == "Remove conflict.pdf from current batch").click().run(timeout=20)
    assert app.session_state.filtered_state["orders"][0]["source_pdf"] == "conflict.pdf"
    next(button for button in app.button if button.label == "Confirm removal and revalidate").click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert state["orders"] == []
    assert state["uat2_historical_commit_entries"] == ()


def test_review_commit_readiness_includes_historical_option_a_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "historical-blocked-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 5
    app.session_state["orders"] = [
        {
            "platform": "Shopee",
            "order_id": "SHP-DUP",
            "source_pdf": "duplicate.pdf",
            "status": "Accepted",
        }
    ]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    app.session_state["uat2_historical_commit_entries"] = (
        InvoiceIntakeEntry(
            staging_id="duplicate",
            source_filename="duplicate.pdf",
            source_hash="hash",
            order_id="SHP-DUP",
            status=IntakeStatus.ALREADY_IMPORTED,
            message="Same material historical invoice is already imported.",
            bundle=None,
        ),
    )
    app.session_state["uat2_historical_commit_signature"] = _historical_signature(
        app.session_state.filtered_state
    )

    app.run(timeout=20)

    assert app.exception == []
    assert not any("Ready to Commit" in success.value for success in app.success)
    assert any(
        "Return to Reconcile and resolve/remove all non-NEW sources before Commit."
        in warning.value
        for warning in app.warning
    )
    commit_button = next(
        button
        for button in app.button
        if button.label == "Commit Accepted Shopee Invoices"
    )
    assert commit_button.disabled
    assert "Check Historical Status" not in {button.label for button in app.button}


def test_data_import_has_no_stale_check_historical_status_text():
    source = (APP_PATH.parent / "src" / "invoice_app" / "ui" / "data_import.py").read_text(
        encoding="utf-8"
    )

    assert "Check Historical Status" not in source


def test_non_resolvable_manual_review_details_render_without_changing_readiness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "incomplete-source-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = []
    app.session_state["products"] = []
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    app.session_state["reviews"] = [{
        "platform": "Shopee", "order_id": "SHP-INCOMPLETE", "source_pdf": "incomplete.pdf",
        "status": "Manual Review",
        "reason_code": INCOME_SOURCE_INCOMPLETE,
        "reason": "Income Completion Anchor Missing: Source Document Is Incomplete.",
    }]
    app.run(timeout=20)

    assert app.exception == []
    assert "Apply & Revalidate" not in {button.label for button in app.button}
    assert not any(
        "Income Completion Anchor Missing" in warning.value for warning in app.warning
    )
    assert not adapt_platform_orders_import_result(
        batch_id="incomplete-source-batch", orders=[], products=[],
        reviews=app.session_state.filtered_state["reviews"], processing_errors=[],
        duplicate_skipped=[], unsupported_files=[],
    ).commit_readiness.ready
    next(button for button in app.button if button.label == "View Details").click().run(timeout=20)

    assert app.exception == []
    assert "Details" in {caption.value for caption in app.caption}
    assert app.session_state.filtered_state["reviews"][0]["status"] == "Manual Review"


def test_source_supported_income_manual_review_renders_confirmation_form(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "income-extraction-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = []
    app.session_state["products"] = []
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    app.session_state["reviews"] = [{
        "platform": "Shopee",
        "order_id": "SHP-INCOME",
        "source_pdf": "income.pdf",
        "status": "Manual Review",
        "reason_code": INCOME_EXTRACTION_MISSING,
        "reason": "Income Completion Anchor Missing: Order Income was not extracted.",
        "order_payload": {"order_income": "N/A"},
    }]

    app.run(timeout=20)

    assert app.exception == []
    assert "Apply & Revalidate" in {button.label for button in app.button}
    assert {
        "I verified these values are visible in the original Invoice source"
    } <= {checkbox.label for checkbox in app.checkbox}
    assert {"Order Income", "Income Type"} <= {
        widget.label for widget in (*app.text_input, *app.selectbox)
    }


def test_sidebar_blocks_navigation_only_during_processing_and_restores_afterward(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Dashboard"
    app.session_state["batch_id"] = "active-batch"
    app.session_state["workflow_activity"] = "Processing"
    app.run(timeout=20)

    next(button for button in app.button if button.label == "Data Import").click().run(timeout=20)
    assert app.session_state.filtered_state["navigation"] == "Dashboard"
    assert any("Processing is still running" in info.value for info in app.info)

    del app.session_state["workflow_activity"]
    app.run(timeout=20)
    next(button for button in app.button if button.label == "Data Import").click().run(timeout=20)
    assert app.session_state.filtered_state["navigation"] == "Data Import"
