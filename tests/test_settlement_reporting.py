from datetime import date
from decimal import Decimal

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.services.settlement_reporting import (
    build_shopee_settlement_reporting,
)


def _statement(*order_rows: SettlementIncomeRow) -> ParsedShopeeWeeklyStatement:
    return ParsedShopeeWeeklyStatement(
        source_filename="settlement.xlsx",
        file_hash="settlement-hash",
        statement_period_from=date(2026, 8, 1),
        statement_period_to=date(2026, 8, 7),
        summary_total_released=sum(
            (row.total_released_amount or Decimal("0") for row in order_rows),
            Decimal("0"),
        ),
        adjustment_control_total=Decimal("0"),
        adjustment_footer_total=Decimal("0"),
        income_rows=order_rows,
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )


def _statement_order(order_id: str, released_amount: str = "100.00") -> SettlementIncomeRow:
    return SettlementIncomeRow(
        sequence_no="1",
        view_by="Order",
        order_id=order_id,
        product_id="",
        product_name="",
        order_creation_date=date(2026, 8, 1),
        payout_completed_date=date(2026, 8, 7),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal(released_amount),
        financial_components={},
        source_values={},
        source_row_number=2,
    )


def _invoice(order_id: str, payment_status: str = "Pending") -> dict[str, object]:
    return {
        "platform": "Shopee",
        "order_id": order_id,
        "order_created_date": "01/08/2026",
        "income_type": "Final",
        "order_income": "100.00",
        "payment_status": payment_status,
    }


def test_pending_invoice_with_matched_statement_projects_released_without_mutating_source():
    invoice = _invoice("SHP-PENDING")

    result = build_shopee_settlement_reporting(
        [invoice], _statement(_statement_order("SHP-PENDING"))
    )

    row = result.rows[0]
    assert row.invoice_payment_signal == "Pending"
    assert row.effective_payment_status == "Released"
    assert row.payment_evidence_source == "Weekly Statement"
    assert row.settlement_status == "Released"
    assert row.payment_transition == "Pending → Released"
    assert invoice["payment_status"] == "Pending"
    assert result.summary.pending_to_released == 1


def test_released_invoice_with_matched_statement_remains_released():
    result = build_shopee_settlement_reporting(
        [_invoice("SHP-RELEASED", "Released")],
        _statement(_statement_order("SHP-RELEASED")),
    )

    row = result.rows[0]
    assert row.invoice_payment_signal == "Released"
    assert row.effective_payment_status == "Released"
    assert row.settlement_status == "Released"
    assert row.payment_transition == "Already Released → Released"
    assert result.summary.already_released_to_released == 1


def test_pending_invoice_without_statement_match_keeps_invoice_signal_without_unpaid_inference():
    result = build_shopee_settlement_reporting([_invoice("SHP-NO-STATEMENT")], None)

    row = result.rows[0]
    assert row.invoice_payment_signal == "Pending"
    assert row.effective_payment_status == "Pending"
    assert row.settlement_status == "No Settlement Evidence"
    assert row.payment_evidence_source == "Invoice payment signal"
    assert result.summary.no_settlement_evidence == 1
    assert "Unpaid" not in {row.effective_payment_status, row.settlement_status}


def test_duplicate_session_or_statement_records_do_not_double_count_the_reporting_projection():
    invoice = _invoice("SHP-DUPLICATE")
    duplicate_statement_rows = (
        _statement_order("SHP-DUPLICATE"),
        _statement_order("SHP-DUPLICATE"),
        _statement_order("SHP-STATEMENT-ONLY"),
        _statement_order("SHP-STATEMENT-ONLY"),
    )

    result = build_shopee_settlement_reporting(
        [invoice, dict(invoice)], _statement(*duplicate_statement_rows)
    )

    assert [row.order_id for row in result.rows] == ["SHP-DUPLICATE"]
    assert result.summary.total_shopee_orders == 1
    assert result.summary.statement_matched == 1
    assert result.summary.unmatched_statement_orders == 1


def test_difference_is_amount_only_for_final_income_and_never_changes_invoice_payment_status():
    invoice = _invoice("SHP-DIFFERENT")

    result = build_shopee_settlement_reporting(
        [invoice], _statement(_statement_order("SHP-DIFFERENT", "97.50"))
    )

    assert result.rows[0].difference == Decimal("-2.50")
    assert result.summary.different_amount == 1
    assert invoice["payment_status"] == "Pending"
