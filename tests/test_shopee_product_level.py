from decimal import Decimal

from src.invoice_app.services.all_products import build_shopee_product_level_rows
from src.invoice_app.services.batch_service import MISSING_VALUE_PLACEHOLDER
from src.invoice_app.services.product_price_master import ProductPriceMaster


def test_shopee_product_level_uses_master_price_and_summary_style_variation_label():
    products = [
        {
            "order_id": "SHP-1",
            "product_name": "Product",
            "variation_name": "Large",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "unit_price": "9.00",
            "line_subtotal": "9.00",
        }
    ]
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "product_name": "Product",
                "variation_name": "Large",
                "unit_selling_price": "12.00",
            }
        ]
    )

    rows = build_shopee_product_level_rows(products, price_master=master)

    assert rows == [{**products[0], "product_name": "Product — Large", "unit_price": Decimal("12.00")}]
    assert products[0]["product_name"] == "Product"
    assert products[0]["unit_price"] == "9.00"


def test_shopee_product_level_keeps_unresolved_master_price_as_na():
    rows = build_shopee_product_level_rows(
        [
            {
                "order_id": "SHP-1",
                "product_name": "Unknown",
                "variation_name": "500g",
                "seller_sku": "MISSING",
                "unit_price": "9.00",
            }
        ],
        price_master=ProductPriceMaster.from_rows([]),
    )

    assert rows[0]["product_name"] == "Unknown — 500g"
    assert rows[0]["unit_price"] == MISSING_VALUE_PLACEHOLDER
