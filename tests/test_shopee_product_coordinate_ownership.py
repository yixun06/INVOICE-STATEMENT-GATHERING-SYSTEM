from decimal import Decimal

from src.invoice_app.pdf_document import PdfDocument, PdfHorizontalRule, PdfPage, PdfWord
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


def test_page_break_sku_continuation_is_not_a_second_product_row():
    page_one = PdfPage(
        number=1,
        width=600,
        height=800,
        text="",
        words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("BG Veggie Chips Series Small Packet", 140, 295, 70),
            _word("15.00", 340, 370, 70), _word("1", 405, 410, 70), _word("15.00", 455, 485, 70),
            _word("Variation: Dried CornKernel 20g", 140, 285, 80),
        ),
    )
    page_two = PdfPage(
        number=2,
        width=600,
        height=800,
        text="",
        words=(
            _word("Home My Orders Order Details", 35, 95, 20),
            _word("zenxinorganicfood", 35, 95, 30),
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("SKU:", 140, 162, 70), _word("9551031010373", 165, 220, 70),
        ),
    )

    items = parse_positioned_products(PdfDocument(text="Total 1 product", pages=(page_one, page_two)))

    assert len(items) == 1
    assert items[0]["seller_sku"] == "9551031010373"
    assert items[0]["product_name"] == "BG Veggie Chips Series Small Packet"
    assert items[0]["variation"] == "Dried CornKernel 20g"
    assert items[0]["quantity"] == 1
    assert items[0]["unit_price"] == Decimal("15.00")
    assert items[0]["line_total"] == Decimal("15.00")
    assert items[0]["evidence"] == "positioned-page-continuation"


def test_headerless_continuation_page_owns_products_and_excludes_chrome_sidebar_and_promotion_money():
    page_one = PdfPage(
        number=1, width=600, height=800, text="", words=(
            _word("No.", 65, 76, 50), _word("Product(s)", 85, 118, 50),
            _word("Unit", 373, 386, 50), _word("Price", 387, 403, 50),
            _word("Quantity", 411, 437, 50), _word("Subtotal", 450, 476, 50),
            _word("Boundary Product", 120, 220, 70), _word("4.80", 390, 404, 80),
            _word("1", 433, 437, 80), _word("4.80", 462, 476, 80),
            _word("Variation:", 120, 145, 90), _word("Original", 147, 180, 90),
        ),
    )
    page_two = PdfPage(
        number=2, width=600, height=800, text="", words=(
            _word("SKU:", 120, 133, 30), _word("BOUNDARY-SKU", 134, 190, 30),
            _word("Home", 58, 80, 39), _word("My Orders Order Details", 96, 195, 39),
            _word("shop-account", 489, 548, 39),
            _word("Any 2 at RM15.00", 74, 121, 54),
            _word("Promotion Product One", 117, 240, 71), _word("4.90", 393, 407, 82),
            _word("1", 437, 441, 82), _word("Variation: One", 117, 190, 85),
            _word("SKU:", 117, 130, 94), _word("PROMO-ONE", 131, 180, 94), _word("14", 556, 563, 94),
            _word("6", 69, 74, 102), _word("Promotion Product Two", 117, 240, 102), _word("19.01", 460, 476, 102),
            _word("4.80", 393, 407, 113), _word("1", 437, 441, 113),
            _word("Variation: Two", 117, 190, 116),
            _word("SKU:", 117, 130, 124), _word("PROMO-TWO", 131, 180, 124), _word("15.00", 458, 476, 124),
            _word("Hide", 417, 430, 150), _word("Income", 431, 451, 150), _word("Details", 453, 471, 150),
        ),
        horizontal_rules=(PdfHorizontalRule(460, 476, 105, 105.5),),
    )

    items = parse_positioned_products(PdfDocument(text="Total 3 products", pages=(page_one, page_two)))

    assert len(items) == 3
    assert [item["seller_sku"] for item in items] == ["BOUNDARY-SKU", "PROMO-ONE", "PROMO-TWO"]
    assert items[0]["product_name"] == "Boundary Product"
    assert items[0]["variation"] == "Original"
    assert [item["product_name"] for item in items[1:]] == ["Promotion Product One", "Promotion Product Two"]
    assert {item.get("promotion_group_id") for item in items[1:]} == {"shopee-promotion:p2:section1:group1"}
    assert all("Home" not in item["product_name"] and "19.01" not in item["product_name"] for item in items)


def test_text_flow_numeric_tails_do_not_duplicate_coordinate_owned_skus():
    positioned = [
        {"seller_sku": "SKU-A", "product_name": "First", "quantity": 1},
        {"seller_sku": "SKU-B", "product_name": "Second", "quantity": 1},
    ]
    text_flow = [
        {"seller_sku": "SKU-A 14", "product_name": "Chrome First", "quantity": 1},
        {"seller_sku": "SKU-B 15.00 1", "product_name": "Second 19.01", "quantity": 1},
    ]

    reconciled = reconcile_product_candidates(positioned, text_flow)
    assert len(reconciled) == 2
    assert [item["seller_sku"] for item in reconciled] == ["SKU-A", "SKU-B"]
    assert [item["product_name"] for item in reconciled] == ["First", "Second"]
