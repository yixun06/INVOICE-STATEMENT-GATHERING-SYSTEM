from decimal import Decimal

from src.invoice_app.services.all_products import (
    build_cross_platform_product_rows,
    missing_sku_product_summary_rows,
    summarize_cross_platform_products,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


def _summary_row(*, sku: str, quantity: int, unit_price, actual_value, variation: str = ""):
    return {
        "seller_sku": sku,
        "product_name": "Product",
        "reporting_variation_name": variation,
        "quantity": quantity,
        "reporting_unit_selling_price": unit_price,
        "reporting_actual_selling_value": actual_value,
        # The summary must not need a precomputed row discount to be correct.
        "reporting_discount_given": None,
    }


def _master(rows):
    return ProductPriceMaster.from_rows(rows)


def test_summary_recomputes_discount_from_aggregate_master_quantity_and_actual_sales():
    summary = summarize_cross_platform_products(
        [
            _summary_row(
                sku="PROMO-SKU",
                quantity=2,
                unit_price=Decimal("4.90"),
                actual_value=Decimal("7.50"),
            ),
            _summary_row(
                sku="PROMO-SKU",
                quantity=2,
                unit_price=Decimal("4.90"),
                actual_value=Decimal("7.50"),
            ),
        ]
    )

    assert summary == [
        {
            "seller_sku": "PROMO-SKU",
            "product_name": "Product",
            "unit_selling_price": Decimal("4.90"),
            "total_quantity": 4,
            "total_selling_price": Decimal("15.00"),
            "total_discount_given": Decimal("4.60"),
        }
    ]


def test_summary_keeps_actual_sales_when_master_unit_price_is_unavailable():
    summary = summarize_cross_platform_products(
        [
            _summary_row(
                sku="NO-MASTER",
                quantity=1,
                unit_price=None,
                actual_value=Decimal("8.00"),
            )
        ]
    )

    assert summary[0]["unit_selling_price"] == "N/A"
    assert summary[0]["total_quantity"] == 1
    assert summary[0]["total_selling_price"] == Decimal("8.00")
    assert summary[0]["total_discount_given"] == "N/A"


def test_summary_keeps_master_unit_price_when_actual_sales_are_unavailable():
    summary = summarize_cross_platform_products(
        [
            _summary_row(
                sku="NO-ACTUAL",
                quantity=1,
                unit_price=Decimal("10.00"),
                actual_value=None,
            )
        ]
    )

    assert summary[0]["unit_selling_price"] == Decimal("10.00")
    assert summary[0]["total_quantity"] == 1
    assert summary[0]["total_selling_price"] == "N/A"
    assert summary[0]["total_discount_given"] == "N/A"


def test_missing_sku_rows_stay_outside_summary_without_changing_summary_totals():
    rows = [
        {
            **_summary_row(
                sku="",
                quantity=2,
                unit_price=Decimal("8.00"),
                actual_value=Decimal("12.50"),
            ),
            "order_id": "MISSING-SKU",
            "source_pdf": "missing-sku.pdf",
        },
        {
            **_summary_row(
                sku="SKU-INCLUDED",
                quantity=1,
                unit_price=Decimal("10.00"),
                actual_value=Decimal("10.00"),
            ),
            "order_id": "INCLUDED",
            "source_pdf": "included.pdf",
        },
    ]

    missing_rows = missing_sku_product_summary_rows(rows)
    summary = summarize_cross_platform_products(rows)

    assert [row["order_id"] for row in missing_rows] == ["MISSING-SKU"]
    assert missing_rows[0]["reporting_actual_selling_value"] == Decimal("12.50")
    assert summary == [
        {
            "seller_sku": "SKU-INCLUDED",
            "product_name": "Product",
            "unit_selling_price": Decimal("10.00"),
            "total_quantity": 1,
            "total_selling_price": Decimal("10.00"),
            "total_discount_given": Decimal("0.00"),
        }
    ]


def test_supported_any_n_promotion_uses_source_group_total_when_participating_quantity_exceeds_target():
    master = _master(
        [
            {
                "seller_sku": "PROMO-SKU",
                "product_name": "Product",
                "variation_name": "",
                "unit_selling_price": "4.90",
            }
        ]
    )
    rows = build_cross_platform_product_rows(
        [{"platform": "Shopee", "order_id": "PROMO-1", "order_created_date": "20/08/2026"}],
        [
            {
                "platform": "Shopee",
                "order_id": "PROMO-1",
                "product_name": "Product",
                "seller_sku": "PROMO-SKU",
                "unit_price": "4.90",
                "quantity": 5,
                "line_subtotal": "20.00",
                "promotion_group_id": "promo-1",
                "promotion_label": "Any 4 at RM20.00",
                "source_group_total": "20.00",
                "promotion_target_qty": 4,
                "promotion_member_qty": 5,
            }
        ],
        price_master=master,
    )

    assert rows[0]["reporting_actual_selling_value"] == Decimal("20.00")
    assert summarize_cross_platform_products(rows)[0] == {
        "seller_sku": "PROMO-SKU",
        "product_name": "Product",
        "unit_selling_price": Decimal("4.90"),
        "total_quantity": 5,
        "total_selling_price": Decimal("20.00"),
        "total_discount_given": Decimal("4.50"),
    }
