from __future__ import annotations

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



APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
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

