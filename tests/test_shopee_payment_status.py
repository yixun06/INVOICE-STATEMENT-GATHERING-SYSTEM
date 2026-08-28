from dataclasses import replace

import pytest

from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_mapper import (
    map_shopee_records,
    map_shopee_review_payloads,
    resolve_shopee_payment_status,
)
from src.invoice_app.parsers.shopee_review_policy import find_shopee_review_issue


def extracted_shopee_income(financial_lines: str):
    return extract_shopee_data(
        f"""
        Order Received Add a Note
        Order ID: SHP-PAY-1
        SHP-PAY-1 07/08/2026
        Hide Income Details
        {financial_lines}
        """,
        "payment-status.pdf",
    )


@pytest.mark.parametrize(
    ("fund_transfer_date", "income_type", "expected"),
    (
        ("", "Estimated", "Pending"),
        ("10/08/2026 10:06", "Estimated", "Released"),
        ("", "Final", "N/A"),
        ("N/A", "N/A", "N/A"),
    ),
)
def test_shopee_payment_status_resolver_uses_only_transfer_date_and_income_type(
    fund_transfer_date: str,
    income_type: str,
    expected: str,
):
    assert resolve_shopee_payment_status(fund_transfer_date, income_type) == expected


def test_final_amount_does_not_override_estimated_payment_status():
    extracted = extracted_shopee_income(
        "Final Amount RM24.93\nEstimated Order Income RM24.93"
    )

    order, _ = map_shopee_records(extracted, "batch-payment")

    assert order["final_amount"] == "24.93"
    assert order["income_type"] == "Estimated"
    assert order["payment_status"] == "Pending"


def test_manual_review_payload_uses_the_same_payment_status_resolver():
    estimated = extracted_shopee_income("Estimated Order Income RM24.93")
    released = replace(estimated, fund_transfer_date="10/08/2026 10:06")

    pending_payload, _ = map_shopee_review_payloads(estimated, "batch-payment")
    released_payload, _ = map_shopee_review_payloads(released, "batch-payment")

    assert pending_payload is not None
    assert pending_payload["status"] == "Manual Review"
    assert pending_payload["income_type"] == "Estimated"
    assert pending_payload["payment_status"] == "Pending"
    assert released_payload is not None
    assert released_payload["fund_transfer_date"] == "10/08/2026 10:06"
    assert released_payload["payment_status"] == "Released"


def test_payment_status_does_not_change_manual_review_validation():
    extracted = extracted_shopee_income("Estimated Order Income RM24.93")
    released = replace(extracted, fund_transfer_date="10/08/2026 10:06")

    assert find_shopee_review_issue(extracted) == find_shopee_review_issue(released)
