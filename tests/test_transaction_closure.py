from dataclasses import replace
from datetime import date
from decimal import Decimal

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceOrder
from src.invoice_app.domain.transaction_closure import (
    TransactionClosureEvidence,
    TransactionClosureStatus,
    decide_transaction_closure,
)


def _order(**changes):
    return replace(
        CanonicalInvoiceOrder(platform="Shopee", order_id="ORDER-1"),
        **changes,
    )


def test_completed_status_and_financial_values_do_not_close_transaction():
    decision = decide_transaction_closure(
        _order(
            order_status="Completed",
            final_amount=Decimal("10.00"),
            refund_amount=Decimal("-1.00"),
        )
    )

    assert decision.status is TransactionClosureStatus.UNKNOWN
    assert decision.evidence is None


def test_valid_statement_original_payout_evidence_closes_transaction():
    decision = decide_transaction_closure(
        _order(payment_status="RELEASED", payout_completed_date=date(2026, 8, 8))
    )

    assert decision.status is TransactionClosureStatus.CLOSED
    assert decision.evidence is TransactionClosureEvidence.STATEMENT_ORIGINAL_PAYOUT


def test_invoice_fund_transfer_evidence_closes_transaction():
    decision = decide_transaction_closure(
        _order(payment_status="Released", fund_transfer_date=date(2026, 8, 8))
    )

    assert decision.status is TransactionClosureStatus.CLOSED
    assert decision.evidence is TransactionClosureEvidence.INVOICE_FUND_TRANSFER


def test_incomplete_or_conflicting_release_evidence_remains_unknown():
    payout_without_released = decide_transaction_closure(
        _order(payout_completed_date=date(2026, 8, 8))
    )
    conflicting_transfer = decide_transaction_closure(
        _order(payment_status="Pending", fund_transfer_date=date(2026, 8, 8))
    )

    assert payout_without_released.status is TransactionClosureStatus.UNKNOWN
    assert conflicting_transfer.status is TransactionClosureStatus.UNKNOWN


def test_explicit_pending_without_payout_evidence_is_open():
    decision = decide_transaction_closure(_order(payment_status="Pending"))

    assert decision.status is TransactionClosureStatus.OPEN
    assert decision.evidence is TransactionClosureEvidence.EXPLICIT_PENDING
