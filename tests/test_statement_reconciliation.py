from decimal import Decimal

import pytest

from src.invoice_app.services.statement_reconciliation import (
    DIFFERENT,
    ESTIMATED_ONLY,
    MATCHED,
    MISSING_COMPARISON_EVIDENCE,
    UNMATCHED_ORDER,
    compare_statement_order,
)


@pytest.mark.parametrize(
    ("found", "final", "income", "income_type", "released", "source", "difference", "status"),
    [
        (True, "10.00", "99.00", "Estimated", "10.02", "Final Amount", "0.02", MATCHED),
        (True, "10.00", "10.00", "Final", "10.03", "Final Amount", "0.03", DIFFERENT),
        (True, None, "10.00", "Estimated", "10.00", "Order Income", "0.00", ESTIMATED_ONLY),
        (True, None, "10.00", "Estimated", "8.00", "Order Income", "-2.00", ESTIMATED_ONLY),
        (True, None, "10.00", "Final", "10.00", "Order Income", "0.00", MATCHED),
        (True, None, None, "Estimated", "10.00", None, None, MISSING_COMPARISON_EVIDENCE),
        (False, None, None, None, "10.00", None, None, UNMATCHED_ORDER),
    ],
)
def test_locked_statement_order_decision_table(
    found, final, income, income_type, released, source, difference, status
):
    decision = compare_statement_order(
        invoice_order_found=found,
        released_amount=Decimal(released),
        final_amount=Decimal(final) if final is not None else None,
        order_income=Decimal(income) if income is not None else None,
        income_type=income_type,
    )

    assert decision.comparison_source == source
    assert decision.difference == (Decimal(difference) if difference is not None else None)
    assert decision.reconciliation_status == status
