from datetime import date
from decimal import Decimal

from src.invoice_app.services.all_products import (
    build_cross_platform_product_rows,
    filter_cross_platform_product_rows,
    summarize_cross_platform_products,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


def _master(rows):
    return ProductPriceMaster.from_rows(rows)


def test_cross_platform_summary_uses_master_price_and_platform_actual_values():
    master = _master(
        [{"seller_sku": "SKU-SHARED", "product_name": "Universal", "variation_name": "", "unit_selling_price": "10.00"}]
    )
    orders = [
        {"platform": "Shopee", "order_id": "SHP-1", "order_created_date": "07/08/2026"},
        {"platform": "Lazada", "order_id": "LZD-1", "order_date": "08 08 2026"},
        {"platform": "ZENXIN", "order_id": "ZNX-1", "invoice_date": "09/08/2026"},
    ]
    products = [
        {"platform": "Shopee", "order_id": "SHP-1", "product_name": "Universal", "seller_sku": "SKU-SHARED", "unit_price": "9.00", "quantity": 2, "line_subtotal": "18.00", "source_line_subtotal": "18.00"},
        {"platform": "Lazada", "order_id": "LZD-1", "product_name": "Universal", "seller_sku": "SKU-SHARED", "unit_price": "9.00", "quantity": 1, "paid_price": "8.00", "voucher_applied": "99.00"},
        {"platform": "ZENXIN", "order_id": "ZNX-1", "product_name": "Universal", "seller_sku": "SKU-SHARED", "unit_price": "9.00", "quantity": 1, "line_total_inc_tax": "10.00", "delivery_fee": "99.00"},
    ]

    rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert [row["reporting_actual_selling_value"] for row in rows] == [
        Decimal("18.00"), Decimal("8.00"), Decimal("10.00")
    ]
    assert summarize_cross_platform_products(rows) == [{
        "seller_sku": "SKU-SHARED",
        "product_name": "Universal",
        "unit_selling_price": Decimal("10.00"),
        "total_quantity": 4,
        "total_selling_price": Decimal("36.00"),
        "total_discount_given": Decimal("4.00"),
    }]


def test_shopee_promotion_allocation_is_reused_by_summary():
    master = _master([
        {"seller_sku": "SKU-A", "product_name": "Alpha", "variation_name": "", "unit_selling_price": "20.00"},
        {"seller_sku": "SKU-B", "product_name": "Beta", "variation_name": "", "unit_selling_price": "20.00"},
    ])
    orders = [{"platform": "Shopee", "order_id": "SHP-PROMO", "order_created_date": "07/08/2026"}]
    products = [
        {"platform": "Shopee", "order_id": "SHP-PROMO", "product_name": "Alpha", "seller_sku": "SKU-A", "unit_price": "20.00", "quantity": 1, "source_line_subtotal": "20.00", "line_subtotal": "15.27", "promotion_group_id": "group-1", "promotion_label": "Any 3 at RM45.80", "promotion_group_total": "45.80", "promotion_target_qty": 3, "promotion_member_qty": 1},
        {"platform": "Shopee", "order_id": "SHP-PROMO", "product_name": "Beta", "seller_sku": "SKU-B", "unit_price": "20.00", "quantity": 2, "source_line_subtotal": "40.00", "line_subtotal": "30.53", "promotion_group_id": "group-1", "promotion_label": "Any 3 at RM45.80", "promotion_group_total": "45.80", "promotion_target_qty": 3, "promotion_member_qty": 2},
    ]

    summary = summarize_cross_platform_products(build_cross_platform_product_rows(orders, products, price_master=master))
    assert summary == [
        {"seller_sku": "SKU-A", "product_name": "Alpha", "unit_selling_price": Decimal("20.00"), "total_quantity": 1, "total_selling_price": Decimal("15.27"), "total_discount_given": Decimal("4.73")},
        {"seller_sku": "SKU-B", "product_name": "Beta", "unit_selling_price": Decimal("20.00"), "total_quantity": 2, "total_selling_price": Decimal("30.53"), "total_discount_given": Decimal("9.47")},
    ]


def test_price_failure_preserves_quantity_and_reliable_actual_selling_value():
    master = _master([
        {"seller_sku": "CONFLICT", "product_name": "Same", "variation_name": "", "unit_selling_price": "10.00"},
        {"seller_sku": "CONFLICT", "product_name": "Same", "variation_name": "", "unit_selling_price": "11.00"},
    ])
    orders = [
        {"platform": "Shopee", "order_id": "SHP-NOT-FOUND", "order_created_date": "07/08/2026"},
        {"platform": "Lazada", "order_id": "LZD-CONFLICT", "order_date": "08 08 2026"},
    ]
    products = [
        {"platform": "Shopee", "order_id": "SHP-NOT-FOUND", "product_name": "No listing", "seller_sku": "NOT-FOUND", "unit_price": "9.00", "quantity": 2, "source_line_subtotal": "17.00", "line_subtotal": "17.00"},
        {"platform": "Lazada", "order_id": "LZD-CONFLICT", "product_name": "Same", "seller_sku": "CONFLICT", "unit_price": "9.00", "quantity": 1, "paid_price": "8.00"},
    ]

    summary = summarize_cross_platform_products(build_cross_platform_product_rows(orders, products, price_master=master))
    assert summary == [
        {"seller_sku": "CONFLICT", "product_name": "Same", "unit_selling_price": "N/A", "total_quantity": 1, "total_selling_price": Decimal("8.00"), "total_discount_given": "N/A"},
        {"seller_sku": "NOT-FOUND", "product_name": "No listing", "unit_selling_price": "N/A", "total_quantity": 2, "total_selling_price": Decimal("17.00"), "total_discount_given": "N/A"},
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
        {"platform": "Shopee", "order_id": "SHP-INCOMPLETE", "product_name": "Incomplete", "seller_sku": "SKU-INCOMPLETE", "unit_price": "10.00", "quantity": 1, "source_line_subtotal": "10.00", "line_subtotal": "10.00", "promotion_label": "Any 3 at RM20.00", "promotion_metadata_status": "incomplete"},
    ]

    rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert rows[0]["reporting_pricing_status"] == "promotion_evidence_incomplete"
    assert summarize_cross_platform_products(rows)[0] == {
        "seller_sku": "SKU-INCOMPLETE", "product_name": "Incomplete", "unit_selling_price": Decimal("10.00"), "total_quantity": 1, "total_selling_price": "N/A", "total_discount_given": "N/A"
    }


def test_pricing_anomaly_is_preserved_without_clamping():
    master = _master([
        {"seller_sku": "ANOM", "product_name": "Anomaly", "variation_name": "", "unit_selling_price": "10.00"},
    ])
    orders = [{"platform": "Lazada", "order_id": "LZD-ANOM", "order_date": "07 08 2026"}]
    products = [
        {"platform": "Lazada", "order_id": "LZD-ANOM", "product_name": "Anomaly", "seller_sku": "ANOM", "unit_price": "10.00", "quantity": 1, "paid_price": "12.50"},
    ]

    rows = build_cross_platform_product_rows(orders, products, price_master=master)
    assert rows[0]["reporting_pricing_status"] == "pricing_anomaly"
    assert summarize_cross_platform_products(rows)[0]["total_discount_given"] == Decimal("-2.50")