from dataclasses import replace
from decimal import Decimal

import pytest

from src.invoice_app.parsers.shopee_extractor import extract_order_date, extract_shopee_data
from src.invoice_app.parsers.shopee_financial_parser import (
    RETURN_REFUND,
    UNKNOWN_OR_MIXED,
    parse_buyer_payment,
    parse_income_details,
)
from src.invoice_app.parsers.shopee_mapper import map_shopee_records, map_shopee_review_payloads
from src.invoice_app.parsers.shopee_parser import ShopeeParser
from src.invoice_app.parsers.shopee_review_policy import (
    find_shopee_review_issue,
    source_incomplete_evidence,
)
from src.invoice_app.parsers.validation import (
    extract_expected_product_count,
    validate_shopee_financial_reconciliation,
    validate_shopee_product_amounts,
)
from src.invoice_app.pdf_document import PdfDocument, PdfPage
from src.invoice_app.review_reason_codes import (
    INCOME_DETAILS_REQUIRED_FIELD_MISSING,
    INCOME_EXTRACTION_MISSING,
    INCOME_SOURCE_INCOMPLETE,
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


def refund_order_data(
    refund_amount: Decimal | None,
    *,
    merchandise_subtotal: str = "352.79",
    order_income: str = "249.84",
):
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "refund-validation.pdf")
    gross_product = dict(
        extracted.product_items[0],
        quantity=2,
        unit_price=Decimal("190.23"),
        line_total=Decimal("380.46"),
    )
    income = dict(
        extracted.income,
        merchandise_subtotal=merchandise_subtotal,
        product_price="380.46",
        shipping_subtotal="-9.54",
        vouchers_rebates_total="-9.27",
        fees_charges_total="-84.14",
        order_income=order_income,
        income_type="Final",
        shipping_fee_charged_by_logistic_provider="-9.00",
        seller_paid_shipping_fee_sst="-0.54",
        commission_fee="-30.00",
        service_fee="-30.00",
        transaction_fee="-24.14",
    )
    return replace(
        extracted,
        product_items=(gross_product,),
        refund_amount=refund_amount,
        income=income,
        invoice_financial_layout=RETURN_REFUND,
    )


def test_shopee_extraction_layer_returns_source_facts_only():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "sample-shopee.pdf")

    assert extracted.source_pdf == "sample-shopee.pdf"
    assert extracted.order_id == "SHP123"
    assert extracted.order_status == "Order Received"
    assert extracted.order_created_date == "07/08/2026"
    assert extracted.income["merchandise_subtotal"] == "25.00"
    assert extracted.refund_amount is None
    assert len(extracted.product_items) == 1
    assert extracted.product_items[0]["seller_sku"] == "ABC-001"
    assert extracted.product_items[0]["quantity"] == 2
    assert extracted.product_items[0]["line_total"] == Decimal("25.00")
    assert not hasattr(extracted, "batch_id")
    assert find_shopee_review_issue(extracted) is None


def test_shopee_explicit_refund_amount_preserves_negative_source_sign():
    extracted = extract_shopee_data(
        f"{VALID_SHOPEE_TEXT}\nRefund Amount -RM27.67",
        "refund-negative.pdf",
    )
    order, _ = map_shopee_records(extracted, "batch-refund-negative")

    assert extracted.refund_amount == Decimal("-27.67")
    assert order["refund_amount"] == "-27.67"


def test_shopee_explicit_zero_refund_amount_is_not_missing():
    extracted = extract_shopee_data(
        f"{VALID_SHOPEE_TEXT}\nRefund Amount RM0.00",
        "refund-zero.pdf",
    )
    order, _ = map_shopee_records(extracted, "batch-refund-zero")

    assert extracted.refund_amount == Decimal("0.00")
    assert order["refund_amount"] == "0.00"


def test_shopee_missing_refund_amount_stays_empty_without_manual_review():
    orders, products, reviews = ShopeeParser().parse(
        VALID_SHOPEE_TEXT,
        "no-refund-label.pdf",
        "batch-no-refund-label",
    )

    assert len(orders) == 1
    assert len(products) == 1
    assert reviews == []
    assert orders[0]["refund_amount"] == "N/A"


def test_shopee_return_refund_product_text_does_not_infer_refund_amount():
    extracted = extract_shopee_data(
        VALID_SHOPEE_TEXT.replace("Test Product 1", "Return/Refund product"),
        "refund-product-text-only.pdf",
    )
    order, _ = map_shopee_records(extracted, "batch-refund-product-text")

    assert extracted.refund_amount is None
    assert order["refund_amount"] == "N/A"
    assert extracted.invoice_financial_layout == UNKNOWN_OR_MIXED
    assert find_shopee_review_issue(extracted).reason_code == "FINANCIAL_LAYOUT_UNRESOLVED"


def test_shopee_order_id_date_prefix_fills_only_a_missing_explicit_created_date():
    assert extract_order_date("Order ID: 2609016SVMTG5", "2609016SVMTG5") == "01/09/2026"
    assert extract_order_date("2609016SVMTG5 02/09/2026 11:34", "2609016SVMTG5") == "02/09/2026 11:34"
    assert extract_order_date("", "2602316SVMTG5") == ""
    assert extract_order_date("", "SHP-260901") == ""


def test_shopee_valid_normal_product_and_financial_reconciliation_are_accepted():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "valid-shopee.pdf")

    assert extract_expected_product_count(extracted.normalized_text) == 1
    assert validate_shopee_product_amounts(
        list(extracted.product_items),
        extracted.income["merchandise_subtotal"],
    ) is None
    assert validate_shopee_financial_reconciliation(extracted.income) is None
    assert find_shopee_review_issue(extracted) is None


def test_shopee_explicit_refund_reconciles_gross_product_total_to_merchandise_subtotal():
    extracted = refund_order_data(Decimal("-27.67"))

    assert validate_shopee_product_amounts(
        list(extracted.product_items),
        extracted.income["merchandise_subtotal"],
        extracted.refund_amount,
    ) is None
    assert find_shopee_review_issue(extracted) is None
    assert extracted.product_items[0]["quantity"] == 2


def test_shopee_wrong_explicit_refund_still_requires_product_amount_manual_review():
    extracted = refund_order_data(Decimal("-20.00"))

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason_code == "PRODUCT_AMOUNT_RECONCILIATION_FAILED"
    assert "Refund Amount -20.00" in issue.reason


def test_shopee_explicit_zero_refund_uses_existing_non_refund_product_arithmetic():
    extracted = refund_order_data(
        Decimal("0.00"),
        merchandise_subtotal="380.46",
        order_income="277.51",
    )

    assert validate_shopee_product_amounts(
        list(extracted.product_items),
        extracted.income["merchandise_subtotal"],
        extracted.refund_amount,
    ) is None


def test_shopee_refund_financial_reconciliation_starts_from_net_merchandise_subtotal():
    extracted = refund_order_data(Decimal("-27.67"))

    assert validate_shopee_financial_reconciliation(
        extracted.income,
        extracted.refund_amount,
        layout=RETURN_REFUND,
    ) is None
    assert find_shopee_review_issue(extracted) is None


def test_shopee_refund_is_not_double_subtracted_in_financial_reconciliation():
    extracted = refund_order_data(Decimal("-27.67"), order_income="222.17")

    error = validate_shopee_financial_reconciliation(
        extracted.income,
        extracted.refund_amount,
    )

    assert error is not None
    assert error.startswith("Financial Reconciliation Failed:")


def test_shopee_refund_financial_reconciliation_still_rejects_wrong_totals():
    extracted = refund_order_data(Decimal("-27.67"), order_income="249.87")

    error = validate_shopee_financial_reconciliation(
        extracted.income,
        extracted.refund_amount,
    )

    assert error is not None
    assert error.startswith("Financial Reconciliation Failed:")


def test_shopee_product_count_mismatch_requires_manual_review():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "count-mismatch.pdf")
    extracted = replace(
        extracted,
        normalized_text=extracted.normalized_text.replace("Total 1 products", "Total 2 products"),
    )

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("Product Count Mismatch:")
    assert issue.reason_code == "PRODUCT_COUNT_MISMATCH"


def test_shopee_no_valid_product_requires_manual_review():
    extracted = replace(
        extract_shopee_data(VALID_SHOPEE_TEXT, "no-product.pdf"),
        product_items=(),
    )

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("No Valid Product Extracted:")
    assert issue.reason_code == "NO_VALID_PRODUCTS"


def test_shopee_normal_line_arithmetic_failure_requires_manual_review():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "line-arithmetic.pdf")
    invalid_item = dict(extracted.product_items[0], line_total=Decimal("24.97"))
    extracted = replace(extracted, product_items=(invalid_item,))

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("Product Amount Reconciliation Failed:")
    assert "quantity x unit price" in issue.reason
    assert issue.reason_code == "PRODUCT_AMOUNT_RECONCILIATION_FAILED"


def test_shopee_product_subtotal_mismatch_requires_manual_review():
    extracted = extract_shopee_data(VALID_SHOPEE_TEXT, "subtotal-mismatch.pdf")
    income = dict(extracted.income, merchandise_subtotal="25.03")
    extracted = replace(extracted, income=income)

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason.startswith("Product Amount Reconciliation Failed:")
    assert "seller Merchandise Subtotal" in issue.reason
    assert issue.reason_code == "PRODUCT_AMOUNT_RECONCILIATION_FAILED"


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


def test_shopee_missing_income_anchor_is_extraction_missing_without_incomplete_source_evidence():
    text = VALID_SHOPEE_TEXT.replace("Estimated Order Income RM22.00", "")
    extracted = extract_shopee_data(text, "missing-income-anchor.pdf")

    issue = find_shopee_review_issue(extracted)

    assert extracted.income["order_income"] == "N/A"
    assert issue is not None
    assert issue.reason.startswith("Income Completion Anchor Missing:")
    assert "Source Document Is Incomplete" not in issue.reason
    assert issue.reason_code == INCOME_EXTRACTION_MISSING


def test_shopee_page_total_gap_is_deterministic_source_incomplete_evidence():
    text = VALID_SHOPEE_TEXT.replace("Estimated Order Income RM22.00", "")
    document = PdfDocument(
        text=f"{text}\nPage 1 of 2",
        pages=(
            PdfPage(
                number=1,
                width=100,
                height=100,
                text=f"{text}\nPage 1 of 2",
                words=(),
            ),
        ),
    )

    evidence = source_incomplete_evidence(document)
    orders, products, reviews = ShopeeParser().parse_document(
        document,
        "missing-page.pdf",
        "batch-incomplete",
    )

    assert evidence == "Source pagination shows page 1 of 2, but this PDF contains only 1 page(s)."
    assert orders == []
    assert products == []
    assert reviews[0]["reason_code"] == INCOME_SOURCE_INCOMPLETE
    assert "re-upload" in reviews[0]["reason"]


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
    assert issue.reason.startswith("Income Details require source review")
    assert "Product Price" in issue.reason
    assert issue.reason_code == INCOME_DETAILS_REQUIRED_FIELD_MISSING


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


@pytest.mark.parametrize(("source", "expected"), (
    ("Final Amount RM27.48", "27.48"),
    ("Order Adjustment\nFinal Amount RM0.00", "0.00"),
    ("Final Amount -RM5.20", "-5.20"),
))
def test_shopee_final_amount_is_label_anchored_outside_income_details(source, expected):
    income = parse_income_details(f"Hide Income Details\nEstimated Order Income RM1.00\nOrder Adjustment\n{source}\nBuyer Payment")
    assert income["final_amount"] == expected


def test_shopee_final_amount_does_not_collide_with_other_money_labels():
    income = parse_income_details("Hide Income Details\nOrder Income RM9.00\nRefund Amount -RM9.00\nTotal Buyer Payment RM9.00")
    assert income["final_amount"] == "N/A"


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
