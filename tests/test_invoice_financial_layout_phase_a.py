from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
    map_accepted_shopee_invoice,
)
from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_financial_parser import (
    NORMAL_ORDER,
    RETURN_REFUND,
    UNKNOWN_OR_MIXED,
    classify_invoice_financial_layout,
    income_label_presence,
    parse_income_details,
)
from src.invoice_app.parsers.shopee_review_policy import find_shopee_review_issue
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    _deserialize_item,
    _deserialize_order,
    _serialize_item,
    _serialize_order,
)
from src.invoice_app.repositories.historical_invoice_repository import source_fact_fingerprint
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_invoice_revalidation import revalidate_shopee_invoice
from src.invoice_app.services.uat2_invoice_schema_migration import (
    UAT2InvoiceSchemaMigrationStatus,
    UAT2InvoiceSchemaMigrator,
    UAT2InvoiceSchemaState,
    build_uat2_invoice_schema_migration_requests,
    classify_uat2_invoice_schema,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    PRE_FINANCIAL_INVOICE_ITEMS_HEADERS,
    PRE_FINANCIAL_INVOICE_ORDERS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
)
from src.invoice_app.services.uat2_statement_schema_migration import SheetSchema, SpreadsheetSchemaSnapshot


NORMAL_TEXT = """
Order Received Add a Note
Order ID: SHP-PHASE-A1
SHP-PHASE-A1 07/08/2026
Hide Income Details
No. Product(s) Unit Price Quantity Subtotal
Test Product
1 Variation: Original 25.00 1 25.00
SKU: SKU-1
Total 1 products
Merchandise Subtotal RM25.00
Product Price RM25.00
Shipping Subtotal RM0.00
Shipping Fee Paid by Buyer RM0.00
Shipping Fee Charged by Logistic Provider RM0.00
Seller Paid Shipping Fee SST RM0.00
Fees & Charges -RM3.00
Commission Fee -RM1.00
Service Fee -RM1.00
Transaction Fee -RM1.00
Estimated Order Income RM22.00
"""


REFUND_TEXT = """
Order Received Add a Note
Order ID: SHP-REFUND-A1
SHP-REFUND-A1 07/08/2026
Hide Income Details
No. Product(s) Unit Price Quantity Subtotal
Return/Refund
Test Product
1 Variation: Original 10.71 1 10.71
SKU: SKU-1
Total 1 products
Merchandise Subtotal RM0.00
Product Price RM10.71
Refund Amount -RM10.71
Shipping Subtotal -RM10.38
Shipping Fee Paid by Buyer RM0.00
Shipping Fee Charged by Logistic Provider -RM4.90
Seller Paid Shipping Fee SST -RM0.29
Reverse Shipping Fee -RM4.90
Reverse Shipping Fee SST -RM0.29
Order Income -RM10.38
"""


def _bundle() -> InvoiceBundle:
    order = CanonicalInvoiceOrder(
        platform="Shopee",
        order_id="SHP-1",
        invoice_financial_layout=NORMAL_ORDER,
        reverse_shipping_fee=Decimal("-1.20"),
        reverse_shipping_fee_sst=Decimal("0.00"),
        ams_commission_fee=Decimal("-3.91"),
        first_imported_at=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    item = CanonicalInvoiceItem(
        platform="Shopee",
        order_id="SHP-1",
        item_index=0,
        seller_sku=None,
        sku_missing_in_source=True,
        product_name="Tea",
        variation="Original",
        quantity=1,
        promotion_advertised_amount=Decimal("20.00"),
        promotion_discount_percent=Decimal("10"),
    )
    return InvoiceBundle(order, (item,))


def test_exact_phase_a_invoice_headers_and_placements():
    assert len(INVOICE_ORDERS_HEADERS) == 44
    assert INVOICE_ORDERS_HEADERS[7] == "invoice_financial_layout"
    assert INVOICE_ORDERS_HEADERS[15:17] == ("reverse_shipping_fee", "reverse_shipping_fee_sst")
    assert INVOICE_ORDERS_HEADERS[25] == "ams_commission_fee"
    assert len(INVOICE_ITEMS_HEADERS) == 22
    assert INVOICE_ITEMS_HEADERS[4] == "sku_missing_in_source"
    assert INVOICE_ITEMS_HEADERS[14:16] == (
        "promotion_advertised_amount",
        "promotion_discount_percent",
    )
    assert len(STATEMENT_DATA_HEADERS) == 40
    assert len(STATEMENT_FINANCIAL_COMPONENT_HEADERS) == 17


def test_ams_reverse_and_ads_preserve_signed_zero_absent_and_presence():
    text = NORMAL_TEXT.replace(
        "Estimated Order Income RM22.00",
        "AMS Commission Fee -RM3.91\nAds Escrow Top Up Fee RM0.00\n"
        "Reverse Shipping Fee -RM4.90\nReverse Shipping Fee SST RM0.00\n"
        "Estimated Order Income RM22.00",
    )
    income = parse_income_details(text)
    labels = income_label_presence(text)
    assert income["ams_commission_fee"] == "-3.91"
    assert income["commission_fee"] == "-1.00"
    assert income["reverse_shipping_fee"] == "-4.90"
    assert income["reverse_shipping_fee_sst"] == "0.00"
    assert income["ads_escrow_top_up_fee"] == "0.00"
    assert {"ams_commission_fee", "reverse_shipping_fee", "reverse_shipping_fee_sst", "ads_escrow_top_up_fee"} <= labels
    absent = parse_income_details(NORMAL_TEXT)
    assert absent["ams_commission_fee"] == "N/A"
    assert absent["reverse_shipping_fee"] == "N/A"
    assert absent["ads_escrow_top_up_fee"] == "N/A"


def test_layout_requires_two_independent_refund_signals():
    assert classify_invoice_financial_layout(NORMAL_TEXT) == NORMAL_ORDER
    assert classify_invoice_financial_layout(NORMAL_TEXT + "\nRefund Amount -RM1.00") == UNKNOWN_OR_MIXED
    assert classify_invoice_financial_layout(REFUND_TEXT) == RETURN_REFUND


def test_valid_refund_layout_is_accepted_without_normal_fee_labels():
    extracted = extract_shopee_data(REFUND_TEXT, "refund.pdf")
    assert extracted.invoice_financial_layout == RETURN_REFUND
    assert extracted.refund_amount == Decimal("-10.71")
    assert extracted.income["reverse_shipping_fee"] == "-4.90"
    assert find_shopee_review_issue(extracted) is None


def test_visible_optional_label_with_missing_value_reviews_but_absent_is_none():
    malformed = extract_shopee_data(
        NORMAL_TEXT.replace("Estimated Order Income", "Ads Escrow Top Up Fee ???\nEstimated Order Income"),
        "malformed-ads.pdf",
    )
    issue = find_shopee_review_issue(malformed)
    assert issue is not None
    assert "Ads Escrow Top Up Fee" in issue.reason
    clean = extract_shopee_data(NORMAL_TEXT, "absent-ads.pdf")
    assert clean.income["ads_escrow_top_up_fee"] == "N/A"
    assert find_shopee_review_issue(clean) is None


def test_new_canonical_fields_round_trip_exactly():
    bundle = _bundle()
    order = _deserialize_order(_serialize_order(bundle.order), 2)
    item = _deserialize_item(_serialize_item(bundle.items[0]), 2)
    assert order.invoice_financial_layout == NORMAL_ORDER
    assert order.reverse_shipping_fee == Decimal("-1.20")
    assert order.reverse_shipping_fee_sst == Decimal("0.00")
    assert order.ams_commission_fee == Decimal("-3.91")
    assert item.sku_missing_in_source is True
    assert item.promotion_advertised_amount == Decimal("20.00")
    assert item.promotion_discount_percent == Decimal("10")


def test_new_source_facts_affect_fingerprint_but_layout_does_not():
    original = _bundle()
    assert source_fact_fingerprint(original) == source_fact_fingerprint(
        replace(original, order=replace(original.order, invoice_financial_layout=RETURN_REFUND))
    )
    assert source_fact_fingerprint(original) != source_fact_fingerprint(
        replace(original, order=replace(original.order, ams_commission_fee=Decimal("0.00")))
    )
    assert source_fact_fingerprint(original) != source_fact_fingerprint(
        replace(original, items=(replace(original.items[0], sku_missing_in_source=False),))
    )
    assert source_fact_fingerprint(original) != source_fact_fingerprint(
        replace(original, items=(replace(original.items[0], promotion_discount_percent=Decimal("0")),))
    )


def test_source_missing_sku_stays_blank_and_uses_deterministic_name_variation_master_match():
    master = ProductPriceMaster.from_rows([
        {"seller_sku": "MASTER-1", "product_name": "Tea", "variation_name": "Original", "unit_selling_price": "12.00", "nav_code": "NAV-1"}
    ])
    order = {
        "platform": "Shopee", "order_id": "SHP-1", "status": "Accepted",
        "invoice_financial_layout": NORMAL_ORDER,
    }
    product = {
        "platform": "Shopee", "order_id": "SHP-1", "status": "Accepted",
        "seller_sku": "", "sku_missing_in_source": True, "product_name": "Tea",
        "variation": "Original", "quantity": 1, "unit_price": "12.00", "line_subtotal": "12.00",
    }
    lookup = master.lookup(seller_sku="", product_name="Tea", variation_name="Original")
    bundle = map_accepted_shopee_invoice(
        order, [product], source_hash="hash",
        enriched_items=[{"unit_price": lookup.unit_selling_price, "nav": lookup.nav_code}],
    )
    assert bundle.items[0].seller_sku is None
    assert bundle.items[0].sku_missing_in_source is True
    assert bundle.items[0].nav == "NAV-1"


def test_visible_but_unparsed_sku_is_not_marked_as_source_missing():
    extracted = extract_shopee_data(
        NORMAL_TEXT.replace("SKU: SKU-1", "SKU:"), "visible-empty-sku.pdf"
    )
    assert not any(
        product.get("sku_missing_in_source") for product in extracted.product_items
    )


def test_complete_revalidation_does_not_bypass_downstream_product_totals():
    extracted = extract_shopee_data(NORMAL_TEXT, "normal.pdf")
    order = {
        "invoice_financial_layout": extracted.invoice_financial_layout,
        **extracted.income,
        "refund_amount": extracted.refund_amount,
    }
    products = [dict(extracted.product_items[0], line_total=Decimal("24.00"), source_line_subtotal=Decimal("24.00"))]
    master = ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "Test Product", "variation_name": "Original", "unit_selling_price": "25.00", "nav_code": "NAV-1"}
    ])
    result = revalidate_shopee_invoice(order, products, price_master=master, expected_product_count=1)
    assert result.error is not None
    assert "Product Amount Reconciliation Failed" in result.error


def test_invoice_schema_migration_plan_accepts_only_exact_current_four_tab_state():
    snapshot = SpreadsheetSchemaSnapshot(tabs={
        INVOICE_ORDERS_TAB: SheetSchema(1, PRE_FINANCIAL_INVOICE_ORDERS_HEADERS),
        INVOICE_ITEMS_TAB: SheetSchema(2, PRE_FINANCIAL_INVOICE_ITEMS_HEADERS),
        STATEMENT_DATA_TAB: SheetSchema(3, STATEMENT_DATA_HEADERS),
        STATEMENT_FINANCIAL_COMPONENTS_TAB: SheetSchema(4, STATEMENT_FINANCIAL_COMPONENT_HEADERS),
    })
    assert classify_uat2_invoice_schema(snapshot) is UAT2InvoiceSchemaState.ELIGIBLE_FOR_MIGRATION
    requests = build_uat2_invoice_schema_migration_requests(snapshot)
    assert len(requests) == 7
    assert requests[0]["insertDimension"]["range"]["startIndex"] == 7
    assert requests[4]["insertDimension"]["range"]["startIndex"] == 4


def test_invoice_schema_migrator_applies_one_batch_and_verifies_exact_target():
    class Gateway:
        def __init__(self):
            self.snapshot = SpreadsheetSchemaSnapshot(tabs={
                INVOICE_ORDERS_TAB: SheetSchema(1, PRE_FINANCIAL_INVOICE_ORDERS_HEADERS),
                INVOICE_ITEMS_TAB: SheetSchema(2, PRE_FINANCIAL_INVOICE_ITEMS_HEADERS),
                STATEMENT_DATA_TAB: SheetSchema(3, STATEMENT_DATA_HEADERS),
                STATEMENT_FINANCIAL_COMPONENTS_TAB: SheetSchema(4, STATEMENT_FINANCIAL_COMPONENT_HEADERS),
            })
            self.writes = []

        def read_schema(self, _spreadsheet_id):
            return self.snapshot

        def apply_schema_migration(self, _spreadsheet_id, requests):
            self.writes.append(tuple(requests))
            tabs = dict(self.snapshot.tabs)
            tabs[INVOICE_ORDERS_TAB] = SheetSchema(1, INVOICE_ORDERS_HEADERS)
            tabs[INVOICE_ITEMS_TAB] = SheetSchema(2, INVOICE_ITEMS_HEADERS)
            self.snapshot = SpreadsheetSchemaSnapshot(tabs=tabs)

    gateway = Gateway()
    result = UAT2InvoiceSchemaMigrator(
        spreadsheet_id="synthetic-sheet", gateway=gateway
    ).migrate()
    assert result.status is UAT2InvoiceSchemaMigrationStatus.MIGRATED
    assert len(gateway.writes) == 1
    assert UAT2InvoiceSchemaMigrator(
        spreadsheet_id="synthetic-sheet", gateway=gateway
    ).migrate().status is UAT2InvoiceSchemaMigrationStatus.ALREADY_MIGRATED
    assert len(gateway.writes) == 1


def test_unknown_layout_cannot_be_persisted_as_accepted():
    order = {
        "platform": "Shopee", "order_id": "SHP-1", "status": "Accepted",
        "invoice_financial_layout": UNKNOWN_OR_MIXED,
    }
    product = {
        "platform": "Shopee", "order_id": "SHP-1", "status": "Accepted",
        "seller_sku": "SKU-1", "product_name": "Tea", "quantity": 1,
        "unit_price": "1.00", "line_subtotal": "1.00",
    }
    try:
        map_accepted_shopee_invoice(
            order, [product], source_hash="hash",
            enriched_items=[{"unit_price": Decimal("1.00"), "nav": "NAV-1"}],
        )
    except ValueError as error:
        assert "UNKNOWN_OR_MIXED" in str(error)
    else:
        raise AssertionError("UNKNOWN_OR_MIXED must not be persisted as Accepted")
