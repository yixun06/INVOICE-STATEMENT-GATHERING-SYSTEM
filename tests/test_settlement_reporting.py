from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    SettlementIncomeRow,
)
from src.invoice_app.services.settlement_reporting import (
    build_shopee_settlement_reporting,
)


def _statement(*income_rows: SettlementIncomeRow, adjustments=()):
    return ParsedShopeeWeeklyStatement(
        source_filename="settlement.xlsx",
        file_hash="settlement-hash",
        statement_period_from=date(2026, 8, 1),
        statement_period_to=date(2026, 8, 7),
        summary_total_released=sum(
            (row.total_released_amount or Decimal("0") for row in income_rows),
            Decimal("0"),
        ),
        adjustment_control_total=Decimal("0"),
        adjustment_footer_total=Decimal("0"),
        income_rows=income_rows,
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=adjustments,
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )


def _statement_order(
    order_id: str,
    released_amount: str | None = "100.00",
    refund_amount: Decimal | None = Decimal("0.00"),
    payout_date: date | None = date(2026, 8, 7),
    view_by: str = "Order",
) -> SettlementIncomeRow:
    components = {} if refund_amount is None else {"Refund Amount": refund_amount}
    return SettlementIncomeRow(
        sequence_no="1",
        view_by=view_by,
        order_id=order_id,
        product_id="",
        product_name="",
        order_creation_date=date(2026, 8, 1),
        payout_completed_date=payout_date,
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal(released_amount) if released_amount is not None else None,
        financial_components=components,
        source_values={},
        source_row_number=2,
    )


def _invoice(order_id: str, **extra: object) -> dict[str, object]:
    return {
        "platform": "Shopee",
        "order_id": order_id,
        "order_created_date": "01/08/2026",
        "income_type": "Final",
        "order_income": "100.00",
        "payment_status": "Pending",
        **extra,
    }


def _row(invoice, statement):
    return build_shopee_settlement_reporting([invoice], statement).rows[0]


def test_valid_order_evidence_marks_settled_and_ready_without_mutating_invoice():
    invoice = _invoice("SHP-VALID")
    row = _row(invoice, _statement(_statement_order("SHP-VALID")))

    assert row.settlement_status == "Settled"
    assert row.ready_to_invoice == "Ready to Invoice"
    assert row.refund_validation == "No Refund"
    assert row.effective_payment_status == "Settled"
    assert row.payment_transition == "Pending → Settled"
    assert invoice["payment_status"] == "Pending"


def test_no_matching_order_is_no_settlement_evidence_not_unpaid():
    row = _row(_invoice("SHP-NO-STATEMENT"), _statement(_statement_order("OTHER")))

    assert row.settlement_status == "No Settlement Evidence"
    assert row.ready_to_invoice == "No Settlement Evidence"
    assert "Unpaid" not in {row.settlement_status, row.ready_to_invoice}


@pytest.mark.parametrize("replacement", [
    {"payout_date": None},
    {"released_amount": None},
])
def test_target_missing_required_settlement_evidence_needs_review(replacement):
    row = _row(
        _invoice("SHP-MISSING"),
        _statement(_statement_order("SHP-MISSING", **replacement)),
    )

    assert row.settlement_status == "Needs Review"
    assert row.ready_to_invoice == "Needs Review"


def test_unrelated_malformed_and_footer_like_rows_do_not_block_valid_target():
    valid = _statement_order("SHP-VALID")
    unrelated = replace(valid, view_by="", order_id="", payout_completed_date=None, total_released_amount=None)
    footer = replace(valid, view_by="Summary", order_id="", product_name="Order Income")

    row = _row(_invoice("SHP-VALID"), _statement(valid, unrelated, footer))

    assert row.settlement_status == "Settled"
    assert row.ready_to_invoice == "Ready to Invoice"


def test_malformed_row_with_target_order_id_needs_review():
    valid = _statement_order("SHP-TARGET")
    malformed = replace(valid, view_by="", payout_completed_date=None, total_released_amount=None)

    row = _row(_invoice("SHP-TARGET"), _statement(valid, malformed))

    assert row.settlement_status == "Needs Review"
    assert row.ready_to_invoice == "Needs Review"


def test_conflicting_duplicates_need_review_and_identical_duplicates_deduplicate():
    invoice = _invoice("SHP-DUP")
    identical = _statement(_statement_order("SHP-DUP"), _statement_order("SHP-DUP"))
    conflicting = _statement(
        _statement_order("SHP-DUP"),
        _statement_order("SHP-DUP", released_amount="99.00"),
    )

    assert _row(invoice, identical).settlement_status == "Settled"
    assert _row(invoice, conflicting).settlement_status == "Needs Review"


@pytest.mark.parametrize("released_amount", ["0.00", "-12.34"])
def test_zero_and_negative_released_amount_are_valid_settlement_evidence(released_amount):
    row = _row(
        _invoice("SHP-AMOUNT", order_income=released_amount),
        _statement(_statement_order("SHP-AMOUNT", released_amount=released_amount)),
    )

    assert row.settlement_status == "Settled"
    assert row.ready_to_invoice == "Ready to Invoice"


def test_matching_signed_refund_is_matched_and_ready_to_invoice():
    row = _row(
        _invoice("SHP-REFUND", refund_amount="-27.67"),
        _statement(_statement_order("SHP-REFUND", refund_amount=Decimal("-27.67"))),
    )

    assert row.invoice_refund_amount == Decimal("-27.67")
    assert row.statement_refund_amount == Decimal("-27.67")
    assert row.refund_validation == "Matched"
    assert row.ready_to_invoice == "Ready to Invoice"


@pytest.mark.parametrize(
    ("invoice_refund", "statement_refund"),
    [
        ("-27.67", Decimal("-20.00")),
        (None, Decimal("-27.67")),
        ("-27.67", Decimal("0.00")),
    ],
)
def test_refund_mismatches_keep_settlement_but_block_ready_to_invoice(
    invoice_refund, statement_refund
):
    row = _row(
        _invoice("SHP-REFUND", refund_amount=invoice_refund),
        _statement(_statement_order("SHP-REFUND", refund_amount=statement_refund)),
    )

    assert row.settlement_status == "Settled"
    assert row.refund_validation == "Mismatch"
    assert row.ready_to_invoice == "Needs Review"


def test_absent_invoice_refund_and_explicit_zero_are_equivalent_no_refund():
    row = _row(
        _invoice("SHP-ZERO", refund_amount="N/A"),
        _statement(_statement_order("SHP-ZERO", refund_amount=Decimal("0.00"))),
    )

    assert row.invoice_refund_amount is None
    assert row.statement_refund_amount == Decimal("0.00")
    assert row.refund_validation == "No Refund"
    assert row.ready_to_invoice == "Ready to Invoice"


def test_missing_statement_refund_needs_review_but_does_not_reverse_settlement():
    row = _row(
        _invoice("SHP-UNAVAILABLE"),
        _statement(_statement_order("SHP-UNAVAILABLE", refund_amount=None)),
    )

    assert row.settlement_status == "Settled"
    assert row.refund_validation == "Needs Review"
    assert row.ready_to_invoice == "Needs Review"


def test_released_difference_is_informational_only():
    row = _row(
        _invoice("SHP-DIFFERENT", order_income="100.00"),
        _statement(_statement_order("SHP-DIFFERENT", released_amount="97.50")),
    )

    assert row.difference == Decimal("-2.50")
    assert row.ready_to_invoice == "Ready to Invoice"


def test_later_adjustment_does_not_reopen_settled_order():
    adjustment = SettlementAdjustment(
        sequence_no="1",
        adjustment_complete_date=date(2026, 8, 16),
        adjustment_type="Return Refund Adjustment After Order Completed",
        adjustment_reason="Return",
        adjustment_amount=Decimal("-129.06"),
        linked_order_id="SHP-ADJUSTED",
        payout_completed_date=date(2026, 8, 16),
        source_row_number=5,
    )
    row = _row(
        _invoice("SHP-ADJUSTED"),
        _statement(_statement_order("SHP-ADJUSTED"), adjustments=(adjustment,)),
    )

    assert row.settlement_status == "Settled"
    assert row.ready_to_invoice == "Ready to Invoice"
