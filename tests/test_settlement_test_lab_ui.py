from datetime import date
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    READY_TO_COMMIT,
    StagedShopeeWeeklyStatement,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _ready_statement_stage() -> StagedShopeeWeeklyStatement:
    statement_row = SettlementIncomeRow(
        sequence_no="1",
        view_by="Order",
        order_id="SHP-MATCH",
        product_id="",
        product_name="",
        order_creation_date=date(2026, 8, 1),
        payout_completed_date=date(2026, 8, 7),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal("12.34"),
        financial_components={},
        source_values={},
        source_row_number=2,
    )
    statement = ParsedShopeeWeeklyStatement(
        source_filename="test-statement.xlsx",
        file_hash="test-statement-hash",
        statement_period_from=date(2026, 8, 1),
        statement_period_to=date(2026, 8, 7),
        summary_total_released=Decimal("12.34"),
        adjustment_control_total=Decimal("0"),
        adjustment_footer_total=Decimal("0"),
        income_rows=(statement_row,),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )
    return StagedShopeeWeeklyStatement(
        result=READY_TO_COMMIT,
        source_filename=statement.source_filename,
        file_hash=statement.file_hash,
        statement=statement,
        validation_issues=(),
        review_reasons=(),
        rejection_reasons=(),
        duplicate_status=None,
        order_reconciliations=(),
        adjustment_reconciliations=(),
    )


def test_standalone_settlement_test_lab_is_not_a_product_navigation_destination(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Settlement Test Lab"
    app.session_state["settlement_test_lab_accepted_orders"] = [
        {
            "platform": "Shopee",
            "order_id": "SHP-MATCH",
            "status": "Accepted",
            "income_type": "Final",
            "order_income": "12.34",
            "payment_status": "Pending",
        }
    ]
    app.session_state["weekly_statement_stage"] = _ready_statement_stage()

    app.run(timeout=20)

    assert app.exception == []
    assert "Data Import" in {title.value for title in app.title}
    assert app.session_state.filtered_state["navigation"] == "Data Import"
    assert "Settlement Test Lab" not in {button.label for button in app.button}

