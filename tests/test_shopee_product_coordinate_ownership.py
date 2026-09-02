from decimal import Decimal

from src.invoice_app.pdf_document import PdfWord
from src.invoice_app.parsers.shopee_product_parser import (
    _Columns,
    _Row,
    _parse_positioned_item_block,
    reconcile_product_candidates,
)


def _row(top, *words):
    return _Row(top=top, bottom=top + 7, words=tuple(words))


def _word(text, x0, x1, top):
    return PdfWord(text=text, x0=x0, x1=x1, top=top, bottom=top + 7)


def test_coordinate_owned_product_identity_excludes_other_columns_and_page_chrome():
    columns = _Columns(
        product_left=100,
        unit_left=310,
        unit_quantity_boundary=380,
        quantity_subtotal_boundary=440,
    )
    block = [
        _row(100, _word("Home My Orders Order Details", 35, 95, 100), _word("Simply Natural Organic Dried Sweet Potato Stick 50g", 110, 290, 100)),
        _row(110, _word("Variation: 500ml x4", 110, 190, 110), _word("19.40", 330, 350, 110), _word("1", 400, 405, 110), _word("19.40", 455, 475, 110)),
        _row(120, _word("zenxinorganicfood", 35, 95, 120), _word("SKU:", 110, 130, 120), _word("9551031010069", 135, 205, 120)),
    ]

    item = _parse_positioned_item_block(block, columns, ())

    assert item is not None
    assert item["product_name"] == "Simply Natural Organic Dried Sweet Potato Stick 50g"
    assert item["variation"] == "500ml x4"
    assert item["unit_price"] == Decimal("19.40")
    assert item["seller_sku"] == "9551031010069"


def test_coordinate_owned_identity_wins_over_contaminated_text_flow_for_same_sku():
    positioned = [{
        "seller_sku": "9551031010397",
        "product_name": "BG Veggie Chips Series Small Packet",
        "variation": "Variation: Beetroot Chips 15g",
        "quantity": 1,
        "unit_price": Decimal("19.40"),
        "line_total": Decimal("19.40"),
    }]
    text_flow = [{
        "seller_sku": "9551031010397",
        "product_name": "6 BG Veggie Chips Series Small Packet 19.40 47 - Beetroot Chips 15g",
        "variation": "",
        "quantity": 1,
        "unit_price": Decimal("19.40"),
        "line_total": Decimal("19.40"),
    }]

    item = reconcile_product_candidates(positioned, text_flow)[0]

    assert item["product_name"] == "BG Veggie Chips Series Small Packet"
    assert item["variation"] == "Variation: Beetroot Chips 15g"