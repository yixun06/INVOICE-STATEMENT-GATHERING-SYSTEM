from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.import_result_adapters import (
    PLATFORM_ORDERS,
    SHOPEE_WEEKLY_STATEMENT,
    adapt_platform_orders_import_result,
    adapt_shopee_weekly_statement_import_result,
)
from src.invoice_app.services.import_result_contract import ValidationIssue
from src.invoice_app.services.shopee_weekly_statement_service import (
    stage_shopee_weekly_statement,
)


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
WEEKLY_SAMPLE = (
    ROOT / "tests" / "fixtures" / "shopee_weekly_statement"
    / "Income.released.my.20260427_20260503.xlsx"
)


def _platform_result(**overrides):
    values = {
        "batch_id": "platform-batch",
        "orders": [{"platform": "Shopee", "order_id": "SHP-1"}],
        "products": [{"platform": "Shopee", "order_id": "SHP-1", "seller_sku": "SKU-1"}],
        "reviews": [],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
    }
    values.update(overrides)
    return adapt_platform_orders_import_result(**values)


def test_platform_orders_adapter_projects_existing_session_payload_to_common_contract():
    result = _platform_result()

    assert result.source_type == PLATFORM_ORDERS
    assert result.source_summary.items[0].label == "Accepted Orders"
    assert result.source_summary.items[0].value == 1
    assert result.reconciliation.available is False
    assert result.reconciliation.status == "Not Applicable"
    assert result.source_specific_details["orders"] == ({"platform": "Shopee", "order_id": "SHP-1"},)
    assert result.session_state.label == "Applied to Current Session"
    assert result.session_state.database_state.startswith("Not committed")


def test_platform_blocking_issue_blocks_system_derived_commit_readiness():
    result = _platform_result(
        processing_errors=[{"filename": "broken.pdf", "error": "Unreadable PDF"}],
    )

    assert result.validation.has_blocking_issues is True
    assert result.validation.blocking_issues[0].blocking is True
    assert result.commit_readiness.ready is False
    assert result.commit_readiness.status == "Not Ready"


def test_platform_without_blocking_or_review_is_ready_for_future_commit():
    result = _platform_result()

    assert result.validation.has_blocking_issues is False
    assert result.commit_readiness.ready is True
    assert result.commit_readiness.database_commit_available is False


def test_validation_severity_and_blocking_remain_independent_fields():
    issue = ValidationIssue(
        layer="presentation_test",
        severity="error",
        blocking=False,
        reason="Display-only error classification",
    )

    assert issue.severity == "error"
    assert issue.blocking is False


def test_weekly_statement_adapter_preserves_staged_source_specific_details():
    stage = stage_shopee_weekly_statement(WEEKLY_SAMPLE)
    result = adapt_shopee_weekly_statement_import_result(stage, batch_id="weekly-batch")

    assert result.source_type == SHOPEE_WEEKLY_STATEMENT
    assert result.source_summary.items[0].label == "Statement Period"
    assert result.reconciliation.available is True
    assert result.source_specific_details["stage"] is stage
    assert result.source_specific_details["statement"] is stage.statement
    assert result.reconciliation.source_specific_details["order_reconciliations"] == stage.order_reconciliations
    unmatched_adjustment = next(item for item in result.reconciliation.exceptions if item.status == "Unmatched Adjustment")
    assert unmatched_adjustment.affected_item == stage.adjustment_reconciliations[0].linked_order_id
    assert result.commit_readiness.ready is stage.eligible_for_future_atomic_commit


def test_data_import_validation_displays_platform_contract_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "platform-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = [{"platform": "Shopee", "order_id": "SHP-1"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []

    app.run(timeout=20)

    assert app.exception == []
    assert ("Accepted Orders", "1") in {(metric.label, metric.value) for metric in app.metric}
    assert any("Current batch:" in caption.value for caption in app.caption)
    assert any("No validation issues in the current batch." in success.value for success in app.success)
