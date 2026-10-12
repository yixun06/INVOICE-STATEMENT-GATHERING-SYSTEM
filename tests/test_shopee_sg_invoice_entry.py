from decimal import Decimal
from dataclasses import replace

from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_product_parser import parse_text_products
from src.invoice_app.parsers.shopee_review_policy import find_shopee_review_issue
from src.invoice_app.parsers.shopee_sg_adapter import normalize_shopee_sg_text
from src.invoice_app.services.batch_service import process_pdf_text_with_outcome
from src.invoice_app.services.manual_review_resolution import (
    surface_product_identity_reviews,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.product_price_master import ProductPriceMaster


SG_NORMAL_SOURCE = """
seller.shopee.sg
Order Received Add a Note
Order ID: SHP123
SHP123 07/08/2026
Hide Income Details
No. Product(s) Unit Price Quantity Subtotal
Test Product 1
1 Variation: Original $12.50 2 $25.00
SKU: ABC-001
Total 1 products
Merchandise Subtotal $25.00
Shipping Fee Paid by Buyer $0.00
Product Price $25.00
Shipping Subtotal $0.00
Shipping Fee Charged by Logistic Provider $0.00
Fees & Charges -$3.00
Commission Fee (Incl.GST) -$1.00
Service Fee (incl. GST) -$1.00
Transaction Fee (Incl. Gst) -$1.00
Ads Escrow Top Up Fee $0.00
Order Income $22.00
Final Amount $22.00
"""


def _process(source: str, *, context=SHOPEE_SG):
    return process_pdf_text_with_outcome(
        "sg.pdf",
        source,
        "batch-sg",
        expected_platform="Shopee",
        selected_platform_label=context.display_name,
        market_context=context,
    )


def _sg_master(rows):
    base = ProductPriceMaster.from_rows(rows, source_filename="sg-listing")
    return ProductPriceMaster(
        base.records,
        base.metadata,
        sku_parent_peer_keys=True,
        allow_less_suffix_alias=False,
    )


def test_sg_host_routes_to_sg_parser_and_normalizes_dollar_money():
    result = _process(SG_NORMAL_SOURCE)

    assert result.unsupported_files == []
    assert result.processing_errors == []
    assert result.reviews == []
    assert result.orders[0]["platform"] == "Shopee SG"
    assert result.orders[0]["order_income"] == "22.00"
    assert result.orders[0]["final_amount"] == "22.00"
    assert result.orders[0]["transaction_fee"] == "-1.00"
    assert result.products[0]["unit_price"] == "12.50"
    assert result.products[0]["line_subtotal"] == "25.00"


def test_sg_dollar_symbol_without_sg_host_fails_closed():
    result = _process(SG_NORMAL_SOURCE.replace("seller.shopee.sg\n", ""))

    assert result.orders == []
    assert result.products == []
    assert result.reviews == []
    assert result.unsupported_files[0]["status"] == "PLATFORM MISMATCH"
    assert "seller.shopee.sg" in result.unsupported_files[0]["message"]


def test_sg_source_missing_sku_reaches_product_master_resolution_stage():
    extracted = extract_shopee_data(
        normalize_shopee_sg_text(SG_NORMAL_SOURCE), "sg.pdf"
    )
    source_missing_item = dict(extracted.product_items[0])
    source_missing_item.update({"seller_sku": "", "sku_missing_in_source": True})
    extracted = replace(extracted, product_items=(source_missing_item,))

    assert find_shopee_review_issue(extracted).reason_code == "SKU_RESOLUTION_REQUIRED"
    assert find_shopee_review_issue(extracted, allow_missing_source_sku=True) is None


def test_my_market_rejects_sg_host_without_changing_my_rm_parser():
    sg_result = _process(SG_NORMAL_SOURCE, context=SHOPEE_MY)
    my_source = SG_NORMAL_SOURCE.replace("seller.shopee.sg\n", "").replace("$", "RM")
    my_result = _process(my_source, context=SHOPEE_MY)

    assert sg_result.orders == []
    assert sg_result.unsupported_files[0]["status"] == "PLATFORM MISMATCH"
    assert my_result.unsupported_files == []
    assert my_result.orders[0]["platform"] == "Shopee"
    assert my_result.orders[0]["order_income"] == "22.00"


def test_sg_explicit_source_sku_treats_sku_and_parent_sku_as_peer_keys():
    master = _sg_master(
        [
            {
                "seller_sku": "OTHER",
                "parent_sku": "SOURCE-1",
                "product_name": "Parent match",
                "variation_name": "One",
                "unit_selling_price": "8.50",
                "nav_code": "NAV-PARENT",
            },
            {
                "seller_sku": "SOURCE-1",
                "parent_sku": "DIFFERENT",
                "product_name": "SKU match",
                "variation_name": "Two",
                "unit_selling_price": "9.50",
                "nav_code": "NAV-SKU",
            },
        ]
    )

    parent = master.lookup(
        seller_sku="SOURCE-1", product_name="Parent match", variation_name="One"
    )
    sku = master.lookup(
        seller_sku="SOURCE-1", product_name="SKU match", variation_name="Two"
    )

    assert parent.nav_code == "NAV-PARENT"
    assert parent.unit_selling_price == Decimal("8.50")
    assert sku.nav_code == "NAV-SKU"
    assert sku.unit_selling_price == Decimal("9.50")


def test_missing_sg_source_sku_can_resolve_by_exact_name_variation():
    master = _sg_master(
        [
            {
                "seller_sku": "LISTING-SKU",
                "parent_sku": "LISTING-PARENT",
                "product_name": "Exact Product",
                "variation_name": "Red",
                "unit_selling_price": "7.90",
                "nav_code": "NAV-1",
            }
        ]
    )
    state = {
        "orders": [
            {
                "platform": "Shopee SG",
                "order_id": "SG-1",
                "source_pdf": "sg.pdf",
                "status": "Accepted",
            }
        ],
        "products": [
            {
                "platform": "Shopee SG",
                "order_id": "SG-1",
                "source_pdf": "sg.pdf",
                "status": "Accepted",
                "product_name": "Exact Product",
                "variation_name": "Red",
                "seller_sku": "",
                "sku_missing_in_source": True,
            }
        ],
        "reviews": [],
    }

    created = surface_product_identity_reviews(
        state,
        price_master=master,
        platform="Shopee SG",
        include_not_found=True,
        enrich_resolved=True,
    )

    assert created == 0
    assert state["orders"][0]["status"] == "Accepted"
    assert state["products"][0]["nav"] == "NAV-1"
    assert state["products"][0]["master_unit_price"] == Decimal("7.90")


def test_unresolved_sg_identity_moves_to_explainable_manual_review():
    master = _sg_master([])
    state = {
        "orders": [
            {
                "platform": "Shopee SG",
                "order_id": "SG-2",
                "source_pdf": "sg.pdf",
                "status": "Accepted",
            }
        ],
        "products": [
            {
                "platform": "Shopee SG",
                "order_id": "SG-2",
                "source_pdf": "sg.pdf",
                "status": "Accepted",
                "product_name": "Unknown",
                "variation_name": "",
                "seller_sku": "BAD-SKU",
                "sku_missing_in_source": False,
            }
        ],
        "reviews": [],
    }

    created = surface_product_identity_reviews(
        state,
        price_master=master,
        platform="Shopee SG",
        include_not_found=True,
        enrich_resolved=True,
    )

    assert created == 1
    assert state["orders"] == []
    assert state["products"] == []
    assert state["reviews"][0]["platform"] == "Shopee SG"
    assert state["reviews"][0]["product_identity_affected"][0]["issue_type"] == "sku_not_found"


def test_sg_price_reference_mismatch_is_visible_manual_review_evidence():
    master = _sg_master(
        [
            {
                "seller_sku": "SKU-PRICE",
                "parent_sku": "PARENT-PRICE",
                "product_name": "Price Product",
                "variation_name": "One",
                "unit_selling_price": "7.90",
                "nav_code": "NAV-PRICE",
            }
        ]
    )
    state = {
        "orders": [
            {
                "platform": "Shopee SG",
                "order_id": "SG-PRICE",
                "source_pdf": "sg.pdf",
                "status": "Accepted",
            }
        ],
        "products": [
            {
                "platform": "Shopee SG",
                "order_id": "SG-PRICE",
                "source_pdf": "sg.pdf",
                "status": "Accepted",
                "product_name": "Price Product",
                "variation_name": "One",
                "seller_sku": "SKU-PRICE",
                "unit_price": "8.90",
            }
        ],
        "reviews": [],
    }

    created = surface_product_identity_reviews(
        state,
        price_master=master,
        platform="Shopee SG",
        include_not_found=True,
        enrich_resolved=True,
        review_price_mismatch=True,
    )

    assert created == 1
    assert state["reviews"][0]["product_identity_affected"][0]["issue_type"] == (
        "pricing_reference_mismatch"
    )


def test_sg_promotion_money_is_normalized_for_existing_promotion_logic():
    text = SG_NORMAL_SOURCE.replace(
        "Test Product 1\n", "Any 3 at $15.80\nTest Product 1\n"
    ).replace("$12.50 2 $25.00", "$7.90 3 $15.80").replace(
        "$25.00", "$15.80"
    ).replace("$22.00", "$12.80")

    normalized = normalize_shopee_sg_text(text)
    products = parse_text_products(normalized)

    assert "Any 3 at RM15.80" in normalized
    assert products[0]["promotion_advertised_amount"] == Decimal("15.80")
    assert products[0]["quantity"] == 3
