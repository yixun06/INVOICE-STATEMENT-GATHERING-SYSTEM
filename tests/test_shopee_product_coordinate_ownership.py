from decimal import Decimal

from src.invoice_app.pdf_document import PdfDocument, PdfHorizontalRule, PdfPage, PdfWord
from src.invoice_app.parsers.shopee_product_parser import (
    _Columns,
    _Row,
    _parse_positioned_item_block,
    parse_positioned_products,
    reconcile_product_candidates,
    resolve_promotion_group_totals,
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


def test_coordinate_product_name_rejoins_a_single_capital_before_short_suffix():
    columns = _Columns(
        product_left=100,
        unit_left=335,
        unit_quantity_boundary=390,
        quantity_subtotal_boundary=430,
    )
    block = [
        _row(100, _word("Jasmine with Ceylon Black T", 110, 326, 100)),
        _row(110, _word("ea", 110, 120, 110)),
        _row(120, _word("18.90", 350, 370, 120), _word("1", 405, 410, 120)),
        _row(130, _word("SKU:", 110, 130, 130), _word("SKU-1", 135, 165, 130)),
    ]

    item = _parse_positioned_item_block(block, columns, ())

    assert item is not None
    assert item["product_name"] == "Jasmine with Ceylon Black Tea"


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


def _layout_product_page(skus):
    words = [
        _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
        _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
        _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
    ]
    for index, sku in enumerate(skus):
        top = 70 + index * 30
        words.extend((
            _word(f"Product {index + 1}", 140, 250, top),
            _word(f"{10 + index}.00", 340, 370, top + 10),
            _word("1", 405, 410, top + 10),
            _word(f"{10 + index}.00", 455, 485, top + 10),
        ))
        if sku:
            words.extend((
                _word("SKU:", 140, 162, top + 20),
                _word(sku, 165, 215, top + 20),
            ))
    stop_top = 80 + len(skus) * 30
    words.extend((
        _word("Merchandise", 110, 190, stop_top),
        _word("Subtotal", 195, 245, stop_top),
    ))
    return PdfPage(number=1, width=600, height=800, text="", words=tuple(words))


def test_metric_regions_preserve_missing_first_seller_sku():
    items = parse_positioned_products(
        PdfDocument(text="Total 2 products", pages=(_layout_product_page(("", "SKU-2")),))
    )

    assert [(item["product_name"], item["seller_sku"]) for item in items] == [
        ("Product 1", ""),
        ("Product 2", "SKU-2"),
    ]
    assert items[0]["sku_missing_in_source"] is True


def test_metric_regions_preserve_missing_last_seller_sku():
    items = parse_positioned_products(
        PdfDocument(text="Total 2 products", pages=(_layout_product_page(("SKU-1", "")),))
    )

    assert [(item["product_name"], item["seller_sku"]) for item in items] == [
        ("Product 1", "SKU-1"),
        ("Product 2", ""),
    ]
    assert items[1]["sku_missing_in_source"] is True


def test_metric_regions_preserve_consecutive_missing_seller_skus():
    page = _layout_product_page(("SKU-1", "", "", "SKU-4"))

    without_declared_count = parse_positioned_products(
        PdfDocument(text="", pages=(page,))
    )
    mismatched_declared_count = parse_positioned_products(
        PdfDocument(text="Total 99 products", pages=(page,))
    )

    expected = ["SKU-1", "", "", "SKU-4"]
    assert [item["seller_sku"] for item in without_declared_count] == expected
    assert [item["seller_sku"] for item in mismatched_declared_count] == expected
    assert [item["sku_missing_in_source"] for item in without_declared_count] == [
        False, True, True, False,
    ]


def test_promotion_metric_regions_include_middle_product_without_sku():
    page = PdfPage(
        number=1,
        width=600,
        height=800,
        text="",
        words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("Any 3 at RM37.80", 100, 220, 65),
            _word("Longjing Tea", 140, 250, 75), _word("16.87", 340, 370, 85),
            _word("1", 405, 410, 85), _word("SKU:", 140, 162, 95),
            _word("SKU-LONGJING", 165, 240, 95),
            _word("Jasmine Tea", 140, 250, 105), _word("37.80", 455, 485, 110),
            _word("18.90", 340, 370, 115), _word("1", 405, 410, 115),
            _word("Rose Tea", 140, 250, 135), _word("16.87", 340, 370, 145),
            _word("1", 405, 410, 145), _word("SKU:", 140, 162, 155),
            _word("SKU-ROSE", 165, 225, 155),
            _word("Merchandise", 110, 190, 180), _word("Subtotal", 195, 245, 180),
        ),
    )

    items = parse_positioned_products(PdfDocument(text="Total 3 products", pages=(page,)))
    resolve_promotion_group_totals(items, Decimal("37.80"))

    assert [item["product_name"] for item in items] == [
        "Longjing Tea", "Jasmine Tea", "Rose Tea",
    ]
    assert [item["seller_sku"] for item in items] == [
        "SKU-LONGJING", "", "SKU-ROSE",
    ]
    assert len({item["promotion_group_id"] for item in items}) == 1
    assert {item["source_group_total"] for item in items} == {Decimal("37.80")}


def test_promotion_metric_region_retains_actual_subtotal_before_struck_original_price():
    page = PdfPage(
        number=1,
        width=600,
        height=800,
        text="",
        words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("Any 4 at RM176.40", 100, 220, 65),
            _word("Haskap Berry Elixir", 140, 260, 75),
            _word("52.20", 340, 370, 85), _word("4", 405, 410, 85),
            _word("176.40", 455, 485, 85),
            _word("208.80", 455, 485, 95),
            _word("Variation: 500ml", 140, 220, 105),
            _word("SKU:", 140, 162, 115), _word("9555208013969-New", 165, 250, 115),
            _word("Merchandise", 110, 190, 140), _word("Subtotal", 195, 245, 140),
        ),
        horizontal_rules=(PdfHorizontalRule(455, 485, 98, 98.5),),
    )

    items = parse_positioned_products(PdfDocument(text="Total 1 product", pages=(page,)))
    resolve_promotion_group_totals(items, Decimal("176.40"))

    assert len(items) == 1
    assert items[0]["promotion_label"] == "Any 4 at RM176.40"
    assert items[0]["source_group_total"] == Decimal("176.40")
    assert items[0]["source_line_subtotal"] is None
    assert items[0]["_promotion_subtotal_source_status"] == "resolved"


def test_promotion_metric_region_retains_subtotal_printed_before_metric_row():
    page = PdfPage(
        number=1,
        width=600,
        height=800,
        text="",
        words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("Any 4 at RM15.00", 100, 220, 65),
            _word("15.00", 455, 485, 75),
            _word("Dried Sweet Potato Stick", 140, 280, 80),
            _word("4.41", 340, 370, 90), _word("4", 405, 410, 90),
            _word("17.64", 455, 485, 90),
            _word("Variation: 50g", 140, 220, 100),
            _word("SKU:", 140, 162, 110), _word("SKU-PROMO", 165, 240, 110),
            _word("Merchandise", 110, 190, 135), _word("Subtotal", 195, 245, 135),
        ),
        horizontal_rules=(PdfHorizontalRule(455, 485, 93, 93.5),),
    )

    items = parse_positioned_products(PdfDocument(text="Total 1 product", pages=(page,)))
    resolve_promotion_group_totals(items, Decimal("15.00"))

    assert len(items) == 1
    assert items[0]["promotion_label"] == "Any 4 at RM15.00"
    assert items[0]["source_group_total"] == Decimal("15.00")
    assert items[0]["_promotion_subtotal_source_status"] == "resolved"


def test_promotion_group_deduplicates_one_physical_subtotal_seen_by_adjacent_members():
    page = PdfPage(
        number=1,
        width=600,
        height=800,
        text="",
        words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("Any 3 at RM37.80", 100, 220, 65),
            _word("Longjing Tea", 140, 250, 75), _word("16.87", 340, 370, 85),
            _word("1", 405, 410, 85), _word("SKU:", 140, 162, 95),
            _word("SKU-LONGJING", 165, 240, 95),
            _word("Jasmine Tea", 140, 250, 105), _word("37.80", 455, 485, 110),
            _word("18.90", 340, 370, 115), _word("1", 405, 410, 115),
            _word("Rose Tea", 140, 250, 135), _word("16.87", 340, 370, 145),
            _word("1", 405, 410, 145), _word("SKU:", 140, 162, 155),
            _word("SKU-ROSE", 165, 225, 155),
            _word("Merchandise", 110, 190, 180), _word("Subtotal", 195, 245, 180),
        ),
    )

    items = parse_positioned_products(PdfDocument(text="Total 3 products", pages=(page,)))
    resolve_promotion_group_totals(items, Decimal("37.80"))

    assert {item["source_group_total"] for item in items} == {Decimal("37.80")}
    assert all(item["_promotion_subtotal_source_status"] == "resolved" for item in items)


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


def test_next_page_product_with_own_metrics_never_merges_backwards():
    page_one = PdfPage(
        number=1, width=600, height=800, text="", words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("First Product", 140, 250, 70), _word("10.00", 340, 370, 80),
            _word("1", 405, 410, 80), _word("10.00", 455, 485, 80),
        ),
    )
    page_two = PdfPage(
        number=2, width=600, height=800, text="", words=(
            _word("Second Product", 140, 250, 30), _word("20.00", 340, 370, 40),
            _word("1", 405, 410, 40), _word("20.00", 455, 485, 40),
            _word("SKU:", 140, 162, 50), _word("SKU-SECOND", 165, 230, 50),
            _word("Merchandise", 110, 190, 80), _word("Subtotal", 195, 245, 80),
        ),
    )

    items = parse_positioned_products(
        PdfDocument(text="Total 2 products", pages=(page_one, page_two))
    )

    assert [(item["product_name"], item["seller_sku"]) for item in items] == [
        ("First Product", ""),
        ("Second Product", "SKU-SECOND"),
    ]
    assert items[0]["sku_missing_in_source"] is True


def test_promotion_container_context_and_group_identity_continue_across_pages():
    page_one = PdfPage(
        number=1, width=600, height=800, text="", words=(
            _word("No.", 100, 115, 50), _word("Product(s)", 130, 190, 50),
            _word("Unit", 330, 350, 50), _word("Price", 352, 375, 50),
            _word("Quantity", 395, 440, 50), _word("Subtotal", 450, 500, 50),
            _word("Any 2 at RM15.00", 100, 220, 65),
            _word("Promotion One", 140, 250, 75), _word("10.00", 340, 370, 85),
            _word("1", 405, 410, 85), _word("SKU:", 140, 162, 95),
            _word("PROMO-ONE", 165, 225, 95),
        ),
    )
    page_two = PdfPage(
        number=2, width=600, height=800, text="", words=(
            _word("Promotion Two", 140, 250, 30), _word("10.00", 340, 370, 40),
            _word("1", 405, 410, 40), _word("SKU:", 140, 162, 50),
            _word("PROMO-TWO", 165, 225, 50), _word("15.00", 455, 485, 50),
            _word("Normal Product", 140, 250, 70), _word("5.00", 340, 370, 80),
            _word("1", 405, 410, 80), _word("5.00", 455, 485, 80),
            _word("SKU:", 140, 162, 90), _word("NORMAL", 165, 215, 90),
            _word("Merchandise", 110, 190, 120), _word("Subtotal", 195, 245, 120),
        ),
    )

    items = parse_positioned_products(
        PdfDocument(text="Total 3 products", pages=(page_one, page_two))
    )
    resolve_promotion_group_totals(items, Decimal("20.00"))

    assert len(items) == 3
    assert {
        item["promotion_group_id"] for item in items[:2]
    } == {"shopee-promotion:p1:section1:group1"}
    assert items[2].get("promotion_group_id") is None
    assert {item["source_group_total"] for item in items[:2]} == {
        Decimal("15.00")
    }


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
