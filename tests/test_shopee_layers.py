from dataclasses import replace
from decimal import Decimal

import pytest

from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_financial_parser import parse_buyer_payment, parse_income_details
from src.invoice_app.parsers.shopee_mapper import map_shopee_records, map_shopee_review_payloads
from src.invoice_app.parsers.shopee_review_policy import find_shopee_review_issue
from src.invoice_app.parsers.validation import (
    extract_expected_product_count,
    validate_shopee_financial_reconciliation,
    validate_shopee_product_amounts,
)
from src.invoice_app.services.batch_service import apply_batch_rules


VALID_SHOPEE_TEXT = """
Order Received Add a Note
Order ID: SHP123
SHP123 07/08/2026
Hide Income Details
No. Product(s) Unit Price Quantity Subtotal
Test Product 1
1 Variation: Original 12.50 2 25.00
SKU: ABC-001
Total 1 products
Merchandise Subtotal RM25.00
Shipping Fee Paid by Buyer (excl. SST) RM0.00
Product Price RM25.00
Shipping Subtotal RM0.00
Shipping Fee Charged by Logistic Provider RM0.00
Seller Paid Shipping Fee SST RM0.00
Fees & Charges -RM3.00
Commission Fee (Incl.SST) -RM1.00
Service Fee -RM1.00
Transaction Fee (Incl. SST) -RM1.00
Ads Escrow Top Up Fee RM0.00
Estimated Order Income RM22.00
"""


def shopee_text_with_financials(
    financial_lines: str,
    *,
    status: str = "Order Received",
) -> str:
    return f"""
{status} Add a Note
Order ID: SHP-FIN-1
SHP-FIN-1 07/08/2026
Hide Income Details
No. Product(s) Unit Price Quantity Subtotal
Test Product 1
1 Variation: Original 12.50 2 25.00
SKU: ABC-001
Merchandise Subtotal RM25.00
Shipping Fee Paid by Buyer (excl. SST) RM0.00
Product Price RM25.00
Shipping Subtotal RM0.00
Shipping Fee Charged by Logistic Provider RM0.00
Seller Paid Shipping Fee SST RM0.00
Fees & Charges -RM3.00
Commission Fee (Incl.SST) -RM1.00
Service Fee -RM1.00
Transaction Fee (Incl. SST) -RM1.00
Ads Escrow Top Up Fee RM0.00
{financial_lines}
"""


def mapped_order_from_financials(financial_lines: str, *, status: str = "Order Received") -> dict[str, str]:
    extracted = extract_shopee_data(
        shopee_text_with_financials(financial_lines, status=status),
        "financial-contract.pdf",
    )
    order, _ = map_shopee_records(extracted, "batch-financial-contract")
    return order


def test_shopee_extraction_layer_returns_source_facts_only():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "sample-shopee.pdf")

    assert extracted.source_pdf == "sample-shopee.pdf"
    assert extracted.order_id == "SHP123"
    assert extracted.order_status == "Order Received"
    assert extracted.order_created_date == "07/08/2026"
    assert extracted.income["merchandise_subtotal"] == "25.00"
    assert len(extracted.product_items) == 1
    assert extracted.product_items[0]["seller_sku"] == "ABC-001"
    assert extracted.product_items[0]["quantity"] == 2
    assert extracted.product_items[0]["line_total"] == Decimal("25.00")
    assert not hasattr(extracted, "batch_id")
    assert find_shopee_review_issue(extracted) is None


def test_shopee_valid_normal_product_and_financial_reconciliation_are_accepted():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "valid-shopee.pdf")

    assert extract_expected_product_count(extracted.normalized_text) == 1
    assert validate_shopee_product_amounts(
        list(extracted.product_items),
        extracted.income["merchandise_subtotal"],
    ) is None
    assert validate_shopee_financial_reconciliation(extracted.income) is None
    assert find_shopee_review_issue(extracted) is None


def test_shopee_product_count_mismatch_requires_manual_review():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "count-mismatch.pdf")
    extracted = replace(
        extracted,
        normalized_text=extracted.normalized_text.replace("Total 1 products", "Total 2 products"),
    )

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("Product Count Mismatch:")


def test_shopee_no_valid_product_requires_manual_review():
    extracted = replace(
        extract_shopee_data(VALID_SHOPEE_TEXT, "no-product.pdf"),
        product_items=(),
    )

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("No Valid Product Extracted:")


def test_shopee_normal_line_arithmetic_failure_requires_manual_review():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "line-arithmetic.pdf")
    invalid_item = dict(extracted.product_items[0], line_total=Decimal("24.97"))
    extracted = replace(extracted, product_items=(invalid_item,))

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("Product Amount Reconciliation Failed:")
    assert "quantity x unit price" in issue.reason


def test_shopee_product_subtotal_mismatch_requires_manual_review():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "subtotal-mismatch.pdf")
    income = dict(extracted.income, merchandise_subtotal="25.03")
    extracted = replace(extracted, income=income)

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("Product Amount Reconciliation Failed:")
    assert "seller Merchandise Subtotal" in issue.reason


def test_shopee_financial_reconciliation_tolerance_and_failure_boundary():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "financial-tolerance.pdf")
    within_tolerance = replace(
        extracted,
        income=dict(extracted.income, order_income="22.02"),
    )
    outside_tolerance = replace(
        extracted,
        income=dict(extracted.income, order_income="22.03"),
    )

    assert find_shopee_review_issue(within_tolerance) is None
    issue = find_shopee_review_issue(outside_tolerance)
    assert issue is not None
    assert issue.reason.startswith("Financial Reconciliation Failed:")


def test_shopee_product_amount_tolerance_accepts_two_cents():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "product-tolerance.pdf")
    tolerated_item = dict(extracted.product_items[0], line_total=Decimal("25.02"))
    extracted = replace(extracted, product_items=(tolerated_item,))

    assert find_shopee_review_issue(extracted) is None


def test_shopee_missing_income_anchor_has_clear_manual_review_reason():
    text = VALID_SHOPEE_TEXT.replace("Estimated Order Income RM22.00", "")
    extracted = extract_shopee_data(text, "missing-income-anchor.pdf")

    issue = find_shopee_review_issue(extracted)

    assert extracted.income["order_income"] == "N/A"
    assert issue is not None
    assert issue.reason.startswith("Income Completion Anchor Missing:")


def test_shopee_missing_ads_escrow_fee_stays_missing_but_is_not_incomplete():
    text = VALID_SHOPEE_TEXT.replace("Ads Escrow Top Up Fee RM0.00\n", "")
    extracted = extract_shopee_data(text, "missing-ads-escrow-fee.pdf")

    assert extracted.income["ads_escrow_top_up_fee"] == "N/A"
    assert validate_shopee_financial_reconciliation(extracted.income) is None
    assert find_shopee_review_issue(extracted) is None


def test_shopee_na_required_financial_value_is_not_treated_as_zero():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "na-financial.pdf")
    income = dict(extracted.income, product_price="N/A", order_income="-3.00")
    extracted = replace(extracted, income=income)

    assert validate_shopee_financial_reconciliation(extracted.income) is None
    issue = find_shopee_review_issue(extracted)
    assert issue is not None
    assert issue.reason.startswith("Source Document Appears Incomplete:")
    assert "Product Price" in issue.reason


def test_shopee_mapping_layer_applies_current_record_contract():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "sample-shopee.pdf")

    order, products = map_shopee_records(extracted, "batch-layered")

    assert order["batch_id"] == "batch-layered"
    assert order["platform"] == "Shopee"
    assert order["order_id"] == "SHP123"
    assert order["delivery_fee"] == "0.00"
    assert order["status"] == "Accepted"
    assert "adjustment_complete_date" not in order
    assert "adjustment_reason" not in order
    assert "released_amount" not in order
    assert "remarks" not in order
    assert products[0]["batch_id"] == "batch-layered"
    assert products[0]["line_total"] == "25.00"
    assert "remarks" not in products[0]
    assert products[0]["status"] == "Accepted"


def test_shopee_review_payload_mapping_does_not_derive_a_missing_amount():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "review-shopee.pdf")
    source_item = dict(extracted.product_items[0], line_total=None)
    extracted = replace(extracted, product_items=(source_item,))

    order_payload, product_payloads = map_shopee_review_payloads(extracted, "batch-review")

    assert order_payload is not None
    assert order_payload["delivery_fee"] == "0.00"
    assert order_payload["status"] == "Manual Review"
    assert product_payloads[0]["unit_price"] == "12.50"
    assert product_payloads[0]["line_total"] == "N/A"
    assert product_payloads[0]["line_subtotal"] == "N/A"
    assert product_payloads[0]["status"] == "Manual Review"


def test_shopee_income_label_normalization_sets_order_income_and_income_type():
    estimated_order = mapped_order_from_financials(
        "Estimated Order Income RM24.93",
        status="Shipped",
    )
    final_order = mapped_order_from_financials(
        "Final Amount RM24.53\nOrder Income RM24.53",
        status="Completed",
    )

    assert estimated_order["order_income"] == "24.93"
    assert estimated_order["income_type"] == "Estimated"
    assert final_order["order_income"] == "24.53"
    assert final_order["income_type"] == "Final"


def test_shopee_income_type_comes_from_source_label_not_order_status_or_final_amount():
    estimated_completed_order = mapped_order_from_financials(
        "Final Amount RM24.93\nEstimated Order Income RM24.93",
        status="Completed",
    )
    final_shipped_order = mapped_order_from_financials(
        "Order Income RM24.53",
        status="Shipped",
    )

    assert estimated_completed_order["income_type"] == "Estimated"
    assert final_shipped_order["income_type"] == "Final"


def test_shopee_final_amount_and_order_income_remain_independent():
    order = mapped_order_from_financials(
        "Final Amount RM24.53\nOrder Income RM24.53",
        status="Delivered",
    )

    assert order["final_amount"] == "24.53"
    assert order["order_income"] == "24.53"


@pytest.mark.parametrize(
    ("source_label", "canonical_field"),
    (
        ("Estimated Shipping Subtotal", "shipping_subtotal"),
        ("Shipping Subtotal", "shipping_subtotal"),
        (
            "Estimated Shipping Fee Charged by Logistic Provider",
            "shipping_fee_charged_by_logistic_provider",
        ),
        (
            "Shipping Fee Charged by Logistic Provider",
            "shipping_fee_charged_by_logistic_provider",
        ),
        ("Shipping Fee Rebate From Shopee", "shipping_fee_rebate_from_shopee"),
        ("Shipping Fee Rebate from Shopee", "shipping_fee_rebate_from_shopee"),
        ("Estimated Shipping Fee Rebate from Shopee", "shipping_fee_rebate_from_shopee"),
    ),
)
def test_shopee_shipping_label_aliases_map_to_canonical_fields(source_label, canonical_field):
    income = parse_income_details(f"Hide Income Details\n{source_label} RM1.23")

    assert income[canonical_field] == "1.23"


def test_shopee_buyer_payment_fields_remain_separate_from_seller_income_fields():
    text = """
    Hide Income Details
    Merchandise Subtotal RM25.00
    Order Income RM24.53
    Buyer Payment
    Merchandise Subtotal RM70.87
    Shipping Fee RM0.00
    Shopee Voucher -RM3.00
    Seller Voucher -RM2.00
    Total Buyer Payment RM65.87
    Order Adjustment
    """

    income = parse_income_details(text)
    buyer_payment = parse_buyer_payment(text)

    assert income["merchandise_subtotal"] == "25.00"
    assert income["order_income"] == "24.53"
    assert buyer_payment["buyer_merchandise_subtotal"] == "70.87"
    assert buyer_payment["buyer_shipping_fee"] == "0.00"
    assert buyer_payment["shopee_voucher"] == "-3.00"
    assert buyer_payment["seller_voucher"] == "-2.00"
    assert buyer_payment["total_buyer_payment"] == "65.87"


def test_shopee_identical_labels_in_different_sections_resolve_by_section():
    text = """
    Buyer Payment
    Merchandise Subtotal RM70.87
    Total Buyer Payment RM70.87
    Order Adjustment

    Hide Income Details
    Merchandise Subtotal RM25.00
    Order Income RM24.53
    """

    income = parse_income_details(text)
    buyer_payment = parse_buyer_payment(text)

    assert income["merchandise_subtotal"] == "25.00"
    assert buyer_payment["buyer_merchandise_subtotal"] == "70.87"


@pytest.mark.parametrize(
    ("commission_label", "transaction_label"),
    (
        ("Commission Fee (Incl.SST)", "Transaction Fee (Incl.SST)"),
        ("Commission Fee (Incl. SST)", "Transaction Fee (Incl. SST)"),
    ),
)
def test_shopee_fee_wording_variants_map_to_same_canonical_keys(commission_label, transaction_label):
    income = parse_income_details(
        f"""
        Hide Income Details
        {commission_label} -RM1.11
        {transaction_label} -RM2.22
        """
    )

    assert income["commission_fee"] == "-1.11"
    assert income["transaction_fee"] == "-2.22"


def test_shopee_missing_financial_fields_remain_missing_while_explicit_zero_is_preserved():
    text = """
        Hide Income Details
        Merchandise Subtotal RM0.00
        Shipping Fee Paid by Buyer RM0.00
        """
    income = parse_income_details(text)

    assert income["merchandise_subtotal"] == "0.00"
    assert income["shipping_fee_paid_by_buyer"] == "0.00"
    assert income["shipping_fee_rebate_from_shopee"] == "N/A"
    assert income["final_amount"] == "N/A"

    extracted = extract_shopee_data(shopee_text_with_financials(""), "missing-values.pdf")
    order, products = map_shopee_records(extracted, "batch-missing-values")
    normalized_orders, _, _ = apply_batch_rules([order], products, [])

    assert normalized_orders[0]["shipping_fee_rebate_from_shopee"] == "N/A"
    assert normalized_orders[0]["final_amount"] == "N/A"


def test_shopee_review_policy_rejects_courier_only_documents_first():
    extracted = extract_shopee_data(
        "SPX Express Website Order Details\nTracking Number: SPX123",
        "courier.pdf",
    )

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.order_id == "N/A"
    assert "Courier-only Shopee document" in issue.reason
