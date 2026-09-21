from decimal import Decimal

from src.invoice_app.pdf_document import PdfDocument, PdfPage, PdfWord
from src.invoice_app.parsers.shopee_product_parser import (
    _Columns,
    _Row,
    _parse_positioned_item_block,
    parse_positioned_products,
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


def test_coordinate_product_name_rejoins_a_single_letter_word_split_at_line_wrap():
    columns = _Columns(
        product_left=100,
        unit_left=335,
        unit_quantity_boundary=390,
        quantity_subtotal_boundary=430,
    )
    block = [
        _row(100, _word("[HALAL] Sea Buckthorn Elixir | influenz", 110, 326, 100)),
        _row(110, _word("a, High Vitamin C", 110, 190, 110)),
        _row(120, _word("58.80", 350, 370, 120), _word("1", 405, 410, 120), _word("58.80", 440, 460, 120)),
        _row(130, _word("Variation: 500ml", 110, 190, 130)),
        _row(140, _word("SKU:", 110, 130, 140), _word("9555208013938", 135, 205, 140)),
    ]

    item = _parse_positioned_item_block(block, columns, ())

    assert item is not None
    assert item["product_name"] == "[HALAL] Sea Buckthorn Elixir | influenza, High Vitamin C"
    assert item["variation"] == "500ml"


def test_coordinate_product_name_rejoins_a_two_letter_prefix_before_pipe_separator():
    columns = _Columns(
        product_left=100,
        unit_left=335,
        unit_quantity_boundary=390,
        quantity_subtotal_boundary=430,
    )
    block = [
        _row(100, _word("[HALAL] Siberian Haskap Berry Elixir | 100% Pu", 110, 326, 100)),
        _row(110, _word("re | Natural Antioxidant Superfood", 110, 250, 110)),
        _row(120, _word("51.74", 350, 370, 120), _word("3", 405, 410, 120), _word("155.22", 440, 460, 120)),
        _row(130, _word("SKU:", 110, 130, 130), _word("9555208013969-New", 135, 225, 130)),
    ]

    item = _parse_positioned_item_block(block, columns, ())

    assert item is not None
    assert item["product_name"] == "[HALAL] Siberian Haskap Berry Elixir | 100% Pure | Natural Antioxidant Superfood"


def test_coordinate_product_name_keeps_space_for_an_ordinary_wrapped_word():
    columns = _Columns(
        product_left=100,
        unit_left=335,
        unit_quantity_boundary=390,
        quantity_subtotal_boundary=430,
    )
    block = [
        _row(100, _word("[HALAL] Organic", 110, 326, 100)),
        _row(110, _word("Sea Buckthorn Elixir", 110, 210, 110)),
        _row(120, _word("58.80", 350, 370, 120), _word("1", 405, 410, 120), _word("58.80", 440, 460, 120)),
        _row(130, _word("SKU:", 110, 130, 130), _word("SKU-1", 135, 165, 130)),
    ]

    item = _parse_positioned_item_block(block, columns, ())

    assert item is not None
    assert item["product_name"] == "[HALAL] Organic Sea Buckthorn Elixir"


def test_mixed_sku_rows_preserve_a_deterministic_source_missing_sku_item():
    page = PdfPage(
        number=1,
        width=600,
        height=800,
        text="",
        words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("Anchored One", 140, 250, 70), _word("10.00", 340, 370, 70),
            _word("1", 405, 410, 70), _word("10.00", 455, 485, 70),
            _word("Variation:", 140, 195, 75), _word("Original", 198, 245, 75),
            _word("SKU:", 140, 162, 80), _word("SKU-ONE", 165, 215, 80),
            _word("Source Missing SKU", 140, 265, 95), _word("17.55", 340, 370, 95),
            _word("1", 405, 410, 95), _word("17.55", 455, 485, 95),
            _word("Anchored Two", 140, 250, 110), _word("20.00", 340, 370, 110),
            _word("1", 405, 410, 110), _word("20.00", 455, 485, 110),
            _word("Variation:", 140, 195, 115), _word("1 box", 198, 245, 115),
            _word("SKU:", 140, 162, 120), _word("SKU-TWO", 165, 215, 120),
            _word("Merchandise", 110, 190, 145), _word("Subtotal", 195, 245, 145),
        ),
    )

    items = parse_positioned_products(PdfDocument(text="Total 3 products", pages=(page,)))

    assert [(item["seller_sku"], item["product_name"], item["quantity"], item["line_total"]) for item in items] == [
        ("SKU-ONE", "Anchored One", 1, Decimal("10.00")),
        ("", "Source Missing SKU", 1, Decimal("17.55")),
        ("SKU-TWO", "Anchored Two", 1, Decimal("20.00")),
    ]
    assert items[1]["sku_missing_in_source"] is True
    assert [item["variation"] for item in items] == ["Original", "", "1 box"]
