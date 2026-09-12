from decimal import Decimal

from src.invoice_app.pdf_document import PdfWord
from src.invoice_app.parsers.shopee_product_parser import (
    _Columns,
    _Row,
    _parse_positioned_item_block,
    parse_text_products,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


def _row(top, *words):
    return _Row(top=top, bottom=top + 7, words=tuple(words))


def _word(text, x0, x1, top):
    return PdfWord(text=text, x0=x0, x1=x1, top=top, bottom=top + 7)


_COLUMNS = _Columns(
    product_left=100,
    unit_left=310,
    unit_quantity_boundary=380,
    quantity_subtotal_boundary=440,
)


def test_standalone_hot_listing_badge_is_not_merged_into_product_identity():
    block = [
        _row(100, _word("Hot Listing", 110, 165, 100)),
        _row(110, _word("Yes Natural Brown Rice Coffee (No Added Sugar)(10x30g) HALAL", 110, 300, 110)),
        _row(120, _word("19.71", 330, 350, 120), _word("3", 400, 405, 120), _word("59.13", 455, 475, 120)),
        _row(130, _word("SKU:", 110, 130, 130), _word("9555235993012", 135, 205, 130)),
    ]

    item = _parse_positioned_item_block(block, _COLUMNS, ())

    assert item is not None
    assert item["product_name"] == "Yes Natural Brown Rice Coffee (No Added Sugar)(10x30g) HALAL"
    assert item["variation"] == ""
    assert item["seller_sku"] == "9555235993012"
    assert item["quantity"] == 3
    assert item["unit_price"] == Decimal("19.71")
    assert item["source_line_subtotal"] == Decimal("59.13")

    master = ProductPriceMaster.from_rows([
        {
            "seller_sku": "",
            "parent_sku": "9555235993012",
            "product_name": "Yes Natural Brown Rice Coffee (No Added Sugar)(10x30g) HALAL",
            "variation_name": "",
            "unit_selling_price": "19.71",
            "nav_code": "5001587",
        },
        {
            "seller_sku": "9555235993012",
            "parent_sku": "",
            "product_name": "Yes Natural Brown Rice Coffee, Teh Tarik & Cocoa (10's x 30g) | HALAL",
            "variation_name": "Coffee (No sugar)",
            "unit_selling_price": "19.71",
            "nav_code": "5001964",
        },
    ])
    lookup = master.lookup(
        seller_sku=item["seller_sku"],
        product_name=item["product_name"],
        variation_name=item["variation"],
    )
    assert lookup.unit_selling_price == Decimal("19.71")
    assert lookup.nav_code == "5001587"


def test_hot_listing_words_inside_an_actual_product_name_are_preserved():
    block = [
        _row(100, _word("Hot Listing Coffee Gift Set", 110, 290, 100)),
        _row(110, _word("10.00", 330, 350, 110), _word("1", 400, 405, 110), _word("10.00", 455, 475, 110)),
        _row(120, _word("SKU:", 110, 130, 120), _word("COFFEE-1", 135, 205, 120)),
    ]

    item = _parse_positioned_item_block(block, _COLUMNS, ())

    assert item is not None
    assert item["product_name"] == "Hot Listing Coffee Gift Set"
    assert item["seller_sku"] == "COFFEE-1"


def test_sku_with_spaces_is_preserved_for_positioned_and_text_product_parsing():
    block = [
        _row(100, _word("Simply Natural Organic Chia Seeds 250g", 110, 290, 100)),
        _row(110, _word("23.90", 330, 350, 110), _word("1", 400, 405, 110), _word("23.90", 455, 475, 110)),
        _row(120, _word("Variation: Btl 250g", 110, 205, 120)),
        _row(130, _word("SKU:", 110, 130, 130), _word("btl", 135, 150, 130), _word("250g-9555208108993", 155, 260, 130), _word("23.90", 455, 475, 130)),
    ]

    positioned = _parse_positioned_item_block(block, _COLUMNS, ())
    text_items = parse_text_products("""
No. Product(s) Unit Price Quantity Subtotal
Simply Natural Organic Chia Seeds 250g 23.90 1 23.90
Variation: Btl 250g
SKU: btl 250g-9555208108993
Merchandise Subtotal RM23.90
""")

    assert positioned is not None
    assert positioned["seller_sku"] == "btl 250g-9555208108993"
    assert positioned["variation"] == "Btl 250g"
    assert text_items[0]["seller_sku"] == "btl 250g-9555208108993"
    assert text_items[0]["variation"] == "Btl 250g"
