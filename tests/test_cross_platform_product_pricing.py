from datetime import date
from decimal import Decimal

from src.invoice_app.services.all_products import (
    build_cross_platform_product_rows,
    filter_cross_platform_product_rows,
    summarize_cross_platform_products,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.batch_service import apply_batch_rules


def _master(rows):
    return ProductPriceMaster.from_rows(rows)


def test_cross_platform_summary_uses_master_price_and_platform_actual_values():
    master = _master([{"seller_sku": "SKU-SHARED", "product_name": "Universal", "variation_name": "", "unit_selling_price": "10.00"}])
    orders = [
        {"platform": "Shopee", "order_id": "SHP-1", "order_created_date": "07/08/2026"},
        {"platform": "Lazada", "order_id": "LZD-1", "order_date": "08 08 2026"},
        {"platform": "ZENXIN", "order_id": "ZNX-1", "invoice_date": "09/08/2026"},
    ]
    products = [
        {"platform": "Shopee", "order_id": "SHP-1", "product_name": "Universal", "seller_sku": "SKU-SHARED", "unit_price": "9.00", "quantity": 2, "line_subtotal": "18.00", "source_line_subtotal": "18.00"},
        {"platform": "Lazada", "order_id": "LZD-1", "product_name": "Universal", "seller_sku": "SKU-SHARED", "unit_price": "9.00", "quantity": 1, "paid_price": "8.00"},
        {"platform": "ZENXIN", "order_id": "ZNX-1", "product_name": "Universal", "seller_sku": "SKU-SHARED", "unit_price": "9.00", "quantity": 1, "line_total_inc_tax": "10.00"},
    ]
    rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert [row["reporting_actual_selling_value"] for row in rows] == [Decimal("18.00"), Decimal("8.00"), Decimal("10.00")]
    assert [row["reporting_pricing_status"] for row in rows] == ["normal_priced", "platform_source_only", "platform_source_only"]
    assert summarize_cross_platform_products(rows)[0]["total_discount_given"] == "N/A"

def test_shopee_normal_line_subtotal_is_used_when_legacy_source_field_is_absent():
    master = _master([
        {"seller_sku": "SKU-NORMAL", "product_name": "Normal", "variation_name": "", "unit_selling_price": "10.00"},
    ])
    rows = build_cross_platform_product_rows(
        [{"platform": "Shopee", "order_id": "SHP-NORMAL", "order_created_date": "07/08/2026"}],
        [{"platform": "Shopee", "order_id": "SHP-NORMAL", "product_name": "Normal", "seller_sku": "SKU-NORMAL", "unit_price": "10.00", "quantity": 2, "line_subtotal": "18.00"}],
        price_master=master,
    )

    assert rows[0]["reporting_actual_selling_value"] == Decimal("18.00")
    assert rows[0]["reporting_discount_given"] == Decimal("2.00")
    assert summarize_cross_platform_products(rows) == [
        {"seller_sku": "SKU-NORMAL", "product_name": "Normal", "unit_selling_price": Decimal("10.00"), "total_quantity": 2, "total_selling_price": Decimal("18.00"), "total_discount_given": Decimal("2.00")},
    ]


def test_price_failure_preserves_quantity_and_reliable_actual_selling_value():
    master = _master([])
    rows = build_cross_platform_product_rows(
        [{"platform": "Lazada", "order_id": "LZD-1", "order_date": "08 08 2026"}],
        [{"platform": "Lazada", "order_id": "LZD-1", "product_name": "Source", "seller_sku": "CONFLICT", "unit_price": "9.00", "quantity": 1, "paid_price": "8.00"}],
        price_master=master,
    )
    assert rows[0]["reporting_unit_selling_price"] == Decimal("9.00")
    assert rows[0]["reporting_actual_selling_value"] == Decimal("8.00")
    assert rows[0]["reporting_price_lookup_status"] == "not_applicable"

def test_shopee_promotion_and_normal_source_rows_stay_independent_until_pricing():
    master = _master([
        {"seller_sku": "9555208013938", "product_name": "Sea Buckthorn Elixir", "variation_name": "500ml", "unit_selling_price": "58.80"}
    ])
    orders, products, reviews = apply_batch_rules(
        [{"platform": "Shopee", "order_id": "260828J247W9SW", "source_pdf": "260828J247W9SW.pdf"}],
        [
            {
                "platform": "Shopee", "order_id": "260828J247W9SW", "source_pdf": "260828J247W9SW.pdf",
                "product_name": "Sea Buckthorn Elixir", "variation_name": "500ml", "seller_sku": "9555208013938",
                "unit_price": "58.80", "quantity": 4, "line_total": "235.20",
                "promotion_group_id": "promo-260828J247W9SW", "promotion_label": "Any 4 at RM176.40",
                "source_group_total": "176.40",
                "promotion_target_qty": 4, "promotion_member_qty": 4,
            },
            {
                "platform": "Shopee", "order_id": "260828J247W9SW", "source_pdf": "260828J247W9SW.pdf",
                "product_name": "Sea Buckthorn Elixir", "variation_name": "500ml", "seller_sku": "9555208013938",
                "unit_price": "58.80", "quantity": 1, "line_total": "58.80", "source_line_subtotal": "58.80",
            },
        ],
        [],
    )

    assert reviews == []
    assert [(row["quantity"], row["line_total"]) for row in products] == [(4, "235.20"), (1, "58.80")]

    pricing_rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert [row["reporting_actual_selling_value"] for row in pricing_rows] == [Decimal("176.40"), Decimal("58.80")]
    assert summarize_cross_platform_products(pricing_rows) == [
        {
            "seller_sku": "9555208013938",
            "product_name": "Sea Buckthorn Elixir — 500ml",
            "unit_selling_price": Decimal("58.80"),
            "total_quantity": 5,
            "total_selling_price": Decimal("235.20"),
            "total_discount_given": Decimal("58.80"),
        }
    ]


def test_same_sku_different_product_name_and_variation_remain_separate_price_rows():
    master = _master([
        {"seller_sku": "MULTI", "product_name": "Product", "variation_name": "Small", "unit_selling_price": "8.00"},
        {"seller_sku": "MULTI", "product_name": "Product", "variation_name": "Large", "unit_selling_price": "12.00"},
    ])
    orders = [{"platform": "Shopee", "order_id": "SHP-MULTI", "order_created_date": "07/08/2026"}]
    products = [
        {"platform": "Shopee", "order_id": "SHP-MULTI", "product_name": "Product", "variation_name": "Small", "seller_sku": "MULTI", "unit_price": "8.00", "quantity": 1, "source_line_subtotal": "8.00", "line_subtotal": "8.00"},
        {"platform": "Shopee", "order_id": "SHP-MULTI", "product_name": "Product", "variation_name": "Large", "seller_sku": "MULTI", "unit_price": "12.00", "quantity": 1, "source_line_subtotal": "11.00", "line_subtotal": "11.00"},
    ]

    rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert summarize_cross_platform_products(rows) == [
        {"seller_sku": "MULTI", "product_name": "Product — Large", "unit_selling_price": Decimal("12.00"), "total_quantity": 1, "total_selling_price": Decimal("11.00"), "total_discount_given": Decimal("1.00")},
        {"seller_sku": "MULTI", "product_name": "Product — Small", "unit_selling_price": Decimal("8.00"), "total_quantity": 1, "total_selling_price": Decimal("8.00"), "total_discount_given": Decimal("0.00")},
    ]
    assert filter_cross_platform_product_rows(rows, start_date=date(2026, 8, 7)) == rows
    assert [row["unit_price"] for row in rows] == ["8.00", "12.00"]


def test_incomplete_promotion_does_not_contribute_unreliable_selling_or_discount_values():
    master = _master([
        {"seller_sku": "SKU-INCOMPLETE", "product_name": "Incomplete", "variation_name": "", "unit_selling_price": "10.00"},
    ])
    orders = [{"platform": "Shopee", "order_id": "SHP-INCOMPLETE", "order_created_date": "07/08/2026"}]
    products = [
        {"platform": "Shopee", "order_id": "SHP-INCOMPLETE", "product_name": "Incomplete", "seller_sku": "SKU-INCOMPLETE", "unit_price": "10.00", "quantity": 1, "line_subtotal": "10.00", "promotion_label": "Any 3 at RM20.00", "promotion_metadata_status": "incomplete"},
    ]

    rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert rows[0]["reporting_pricing_status"] == "promotion_evidence_incomplete"
    assert summarize_cross_platform_products(rows)[0] == {
        "seller_sku": "SKU-INCOMPLETE", "product_name": "Incomplete", "unit_selling_price": Decimal("10.00"), "total_quantity": 1, "total_selling_price": "N/A", "total_discount_given": "N/A"
    }


def test_pricing_anomaly_is_preserved_without_clamping():
    rows = build_cross_platform_product_rows(
        [{"platform": "Lazada", "order_id": "LZD-ANOM", "order_date": "07 08 2026"}],
        [{"platform": "Lazada", "order_id": "LZD-ANOM", "product_name": "Anomaly", "seller_sku": "ANOM", "unit_price": "10.00", "quantity": 1, "paid_price": "12.50"}],
        price_master=_master([]),
    )
    assert rows[0]["reporting_pricing_status"] == "platform_source_only"
    assert rows[0]["reporting_discount_given"] is None
