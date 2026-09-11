"""Pure order-level reconciliation semantics for Shopee Statements."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


MONEY_TOLERANCE = Decimal("0.02")

MATCHED = "MATCHED"
DIFFERENT = "DIFFERENT"
ESTIMATED_ONLY = "ESTIMATED_ONLY"
UNMATCHED_ORDER = "UNMATCHED_ORDER"
MISSING_COMPARISON_EVIDENCE = "MISSING_COMPARISON_EVIDENCE"

DISPLAY_STATUS = {
    MATCHED: "Matched",
    DIFFERENT: "Different",
    ESTIMATED_ONLY: "Estimated Only",
    UNMATCHED_ORDER: "Unmatched Order",
    MISSING_COMPARISON_EVIDENCE: "Missing Comparison Evidence",
}


@dataclass(frozen=True)
class StatementOrderDecision:
    comparison_source: str | None
    comparison_amount: Decimal | None
    difference: Decimal | None
    reconciliation_status: str


def compare_statement_order(
    *,
    invoice_order_found: bool,
    released_amount: Decimal,
    final_amount: Decimal | None,
    order_income: Decimal | None,
    income_type: str | None,
) -> StatementOrderDecision:
    """Compare actual release with immutable Invoice evidence.

    Final Amount has priority. An explicitly Estimated Order Income is useful
    reference evidence, but it never becomes a formal matched/different result.
    """

    if not invoice_order_found:
        return StatementOrderDecision(None, None, None, UNMATCHED_ORDER)

    if final_amount is not None:
        source, expected = "Final Amount", final_amount
    elif order_income is not None:
        source, expected = "Order Income", order_income
    else:
        return StatementOrderDecision(
            None, None, None, MISSING_COMPARISON_EVIDENCE
        )

    difference = released_amount - expected
    if source == "Order Income" and (income_type or "").strip().casefold() == "estimated":
        status = ESTIMATED_ONLY
    else:
        status = MATCHED if abs(difference) <= MONEY_TOLERANCE else DIFFERENT
    return StatementOrderDecision(source, expected, difference, status)
