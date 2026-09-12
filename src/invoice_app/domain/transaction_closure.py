"""Pure transaction-closure decision for persisted Invoice orders."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceOrder


class TransactionClosureStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class TransactionClosureEvidence(str, Enum):
    STATEMENT_ORIGINAL_PAYOUT = "STATEMENT_ORIGINAL_PAYOUT"
    INVOICE_FUND_TRANSFER = "INVOICE_FUND_TRANSFER"
    EXPLICIT_PENDING = "EXPLICIT_PENDING"


@dataclass(frozen=True)
class TransactionClosureDecision:
    status: TransactionClosureStatus
    evidence: TransactionClosureEvidence | None = None


def decide_transaction_closure(
    order: CanonicalInvoiceOrder,
) -> TransactionClosureDecision:
    """Decide closure from persisted authoritative evidence, never order status.

    A Statement commit records both ``payment_status=RELEASED`` and the original
    Order View ``payout_completed_date``.  An Invoice ``fund_transfer_date`` is
    the secondary source proof.  Conflicting or incomplete signals stay
    UNKNOWN; ``Completed`` and financial amounts are intentionally ignored.
    """

    payment_status = str(order.payment_status or "").strip().casefold()
    statement_released = payment_status == "released"
    explicitly_pending = payment_status == "pending"

    if order.payout_completed_date is not None and statement_released:
        return TransactionClosureDecision(
            TransactionClosureStatus.CLOSED,
            TransactionClosureEvidence.STATEMENT_ORIGINAL_PAYOUT,
        )

    if order.fund_transfer_date is not None and not explicitly_pending:
        return TransactionClosureDecision(
            TransactionClosureStatus.CLOSED,
            TransactionClosureEvidence.INVOICE_FUND_TRANSFER,
        )

    if (
        explicitly_pending
        and order.payout_completed_date is None
        and order.fund_transfer_date is None
    ):
        return TransactionClosureDecision(
            TransactionClosureStatus.OPEN,
            TransactionClosureEvidence.EXPLICIT_PENDING,
        )

    return TransactionClosureDecision(TransactionClosureStatus.UNKNOWN)
