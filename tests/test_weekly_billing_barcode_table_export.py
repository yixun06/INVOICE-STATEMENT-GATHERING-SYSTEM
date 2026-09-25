from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm

from src.invoice_app.domain.weekly_billing import (
    BillingPeriod,
    ProductSummaryRow,
    WeeklyBillingSummary,
)
from src.invoice_app.services import weekly_billing_barcode_table_export as table_export
from src.invoice_app.services.weekly_billing_barcode_table_export import (
    BRANDING_LOGO_PATH,
    FIRST_PAGE_SUMMARY_GAP,
    FIRST_PAGE_SUMMARY_HEIGHT,
    TABLE_COLUMN_WIDTHS_MM,
    TABLE_CONTENT_WIDTH,
    TABLE_HEADERS,
    build_product_summary_barcode_table_header,
    build_product_summary_barcode_table_rows,
    export_product_summary_barcode_table_pdf,
)


PERIOD = BillingPeriod(date(2026, 8, 10), date(2026, 8, 16), "batch", "b" * 64)


def _row(number: int = 1, **overrides) -> ProductSummaryRow:
    values = dict(
        number=number,
        sku_code="9555208107347",
        nav=f"500{number:04d}",
        product_name=f"Product {number}",
        uom="EA",
        unit_price=Decimal("54.90"),
        quantity=10,
        discount_percent=None,
        discount_amount=Decimal("198.36"),
        amount=Decimal("899.64"),
        source_item_count=1,
    )
    values.update(overrides)
    return ProductSummaryRow(**values)


def _summary(*rows: ProductSummaryRow) -> WeeklyBillingSummary:
    return WeeklyBillingSummary(
        period=PERIOD,
        order_count=1,
        invoice_item_count=len(rows),
        source_items=(),
        product_rows=rows,
        total_quantity=sum(row.quantity for row in rows),
        total_standard_amount=sum(
            (row.unit_price * row.quantity for row in rows), Decimal("0")
        ),
        total_discount_amount=sum(
            (row.discount_amount for row in rows), Decimal("0")
        ),
        total_amount=sum((row.amount for row in rows), Decimal("0")),
        normal_amount_total=Decimal("0"),
        promotion_amount_total=Decimal("0"),
        promotion_group_count=0,
        same_price_promotion_count=0,
        mixed_price_promotion_count=0,
    )


def _reader(summary: WeeklyBillingSummary) -> PdfReader:
    pdf = export_product_summary_barcode_table_pdf(summary)
    assert pdf.startswith(b"%PDF-")
    return PdfReader(BytesIO(pdf))


def _compact(value: str) -> str:
    return "".join(value.split())


def test_valid_ean13_row_uses_a_graphical_barcode_and_all_required_values(monkeypatch):
    draw_calls: list[str] = []
    original_draw = table_export._Ean13BarcodeFlowable.draw

    def record_draw(flowable):
        draw_calls.append(flowable.sku)
        return original_draw(flowable)

    monkeypatch.setattr(table_export._Ean13BarcodeFlowable, "draw", record_draw)
    row = _row()
    text = "\n".join(page.extract_text() for page in _reader(_summary(row)).pages)

    assert draw_calls == [row.sku_code]
    assert row.sku_code in text
    assert "Barcode unavailable" not in text
    assert "RM 54.90" in text
    assert "RM 549.00" in text
    assert "RM 198.36" in text
    assert "RM 899.64" in text


def test_invalid_ean13_keeps_full_sku_and_uses_unavailable_cell(monkeypatch):
    draw_calls: list[str] = []
    monkeypatch.setattr(
        table_export._Ean13BarcodeFlowable,
        "draw",
        lambda flowable: draw_calls.append(flowable.sku),
    )
    sku = "9555208106944-6"
    text = _reader(_summary(_row(sku_code=sku))).pages[0].extract_text()

    assert sku in text
    assert "Barcode unavailable" in text
    assert draw_calls == []


def test_normal_english_description_is_preserved():
    description = "Organic traditional honey from carefully selected flowers"
    text = _reader(_summary(_row(product_name=description))).pages[0].extract_text()
    assert _compact(description) in _compact(text)


def test_long_english_description_wraps_without_truncation():
    description = "Long English product description with complete source wording " * 9
    pages = _reader(_summary(_row(product_name=description))).pages
    assert len(pages) == 1
    assert _compact(description) in _compact(pages[0].extract_text())


def test_chinese_description_uses_portable_font_without_text_loss():
    description = "天然有机沙棘果汁营养饮料家庭装"
    page = _reader(_summary(_row(product_name=description))).pages[0]
    assert description in page.extract_text()
    fonts = page["/Resources"]["/Font"].get_object().values()
    assert any(font.get_object().get("/ToUnicode") is not None for font in fonts)


def test_mixed_english_chinese_description_is_preserved():
    description = "Better Gourmet Sesame Seaweed Crisps 海苔脆片 Family Pack"
    text = _reader(_summary(_row(product_name=description))).pages[0].extract_text()
    assert _compact(description) in _compact(text)


def test_first_page_summary_uses_authoritative_period_and_reconciled_totals():
    summary = _summary(
        _row(1, quantity=2, unit_price=Decimal("10.00"), discount_amount=Decimal("1.50"), amount=Decimal("18.50")),
        _row(2, sku_code="invalid-sku", quantity=3, unit_price=Decimal("20.00"), discount_amount=Decimal("0.00"), amount=Decimal("60.00")),
    )
    header = build_product_summary_barcode_table_header(summary)
    first_page = _reader(summary).pages[0].extract_text()

    assert header.sales_period == "10 Aug 2026 – 16 Aug 2026"
    assert header.product_rows == len(summary.product_rows) == 2
    assert header.total_quantity == summary.total_quantity == 5
    assert header.total_original_sales == summary.total_standard_amount == Decimal("80.00")
    assert header.total_discount_given == summary.total_discount_amount == Decimal("1.50")
    assert header.total_amount == summary.total_amount == Decimal("78.50")
    assert (header.barcode_ready, header.barcode_unavailable) == (1, 1)
    for value in (
        "WEEKLY BILLING PRODUCT SUMMARY",
        "Zenxin Agriculture Sdn Bhd",
        "Sales Period: 10 Aug 2026 – 16 Aug 2026",
        "Source: Shopee Weekly Statement",
        "Product Rows",
        "Total Qty",
        "Total Original Sales",
        "Total Discount Given",
        "Total Amount",
        "RM 80.00",
        "RM 1.50",
        "RM 78.50",
        "Barcode Ready: 1",
        "Barcode Unavailable: 1",
    ):
        assert value in first_page
    assert BRANDING_LOGO_PATH.is_file()
    x_objects = (
        _reader(summary).pages[0]["/Resources"].get("/XObject", {}).get_object().values()
    )
    assert any(image.get_object().get("/Subtype") == "/Image" for image in x_objects)


def test_first_page_summary_appears_once_and_table_header_repeats_on_later_pages():
    rows = tuple(_row(number=index) for index in range(1, 35))
    pages = _reader(_summary(*rows)).pages

    assert len(pages) > 1
    all_text = [page.extract_text() for page in pages]
    assert sum("WEEKLY BILLING PRODUCT SUMMARY" in text for text in all_text) == 1
    assert "WEEKLY BILLING PRODUCT SUMMARY" not in all_text[1]
    for page in pages:
        page_text = page.extract_text()
        for header in TABLE_HEADERS:
            assert header in page_text
    assert "Product 34" in pages[-1].extract_text()


def test_each_source_row_retains_its_own_barcode_alignment_and_order():
    rows = (
        _row(4, sku_code="9555208107347", product_name="First"),
        _row(9, sku_code="invalid-sku", product_name="Second"),
        _row(12, sku_code="9555141906663", product_name="Third"),
    )
    projected = build_product_summary_barcode_table_rows(_summary(*rows))

    assert tuple(item.source for item in projected) == rows
    assert tuple(item.values[1] for item in projected) == tuple(row.sku_code for row in rows)
    assert tuple(item.has_graphical_barcode for item in projected) == (True, False, True)


def test_table_projection_equals_final_product_summary_values_exactly():
    row = _row(
        number=7,
        sku_code="invalid-sku-complete",
        nav="NAV-77",
        product_name="完整 Description & source",
        quantity=3,
        uom="BOX",
        unit_price=Decimal("12.30"),
        discount_percent=Decimal("5.50"),
        discount_amount=Decimal("1.20"),
        amount=Decimal("35.70"),
    )
    projected = build_product_summary_barcode_table_rows(_summary(row))[0]

    assert projected.source is row
    assert projected.values == (
        "7",
        "invalid-sku-complete",
        "Barcode unavailable",
        "NAV-77",
        "完整 Description & source",
        "3",
        "BOX",
        "RM 12.30",
        "RM 36.90",
        "RM 1.20",
        "RM 35.70",
    )


def test_blank_uom_stays_blank_and_explicit_zero_money_stays_zero():
    projected = build_product_summary_barcode_table_rows(
        _summary(
            _row(1, uom=None, discount_amount=Decimal("0.00"), amount=Decimal("0.00")),
        )
    )
    assert projected[0].values[6] == ""
    assert projected[0].values[9] == "RM 0.00"
    assert projected[0].values[10] == "RM 0.00"


def test_template_is_a4_landscape_with_exact_v01_columns_and_widths():
    page = _reader(_summary(_row())).pages[0]
    expected_width, expected_height = landscape(A4)

    assert tuple(round(float(value), 3) for value in page.mediabox[2:]) == (
        round(expected_width, 3),
        round(expected_height, 3),
    )
    assert TABLE_HEADERS == (
        "No.", "SKU Code", "Barcode", "NAV", "Description", "Qty", "UOM",
        "Unit Price", "Original Sales", "Disc given", "Amount",
    )
    assert TABLE_COLUMN_WIDTHS_MM == (9, 20, 50, 18, 78, 12, 12, 15, 20, 15, 15)
    assert sum(TABLE_COLUMN_WIDTHS_MM) == 264
    assert TABLE_CONTENT_WIDTH == pytest.approx(264 * mm)


def test_first_page_summary_height_is_compact_and_deterministic():
    header = build_product_summary_barcode_table_header(_summary(_row()))
    flowable = table_export._first_page_summary_flowable(header, "Helvetica")
    _width, height = flowable.wrap(281 * mm, 1000)

    assert height == pytest.approx(FIRST_PAGE_SUMMARY_HEIGHT)
    assert 25 * mm <= height + FIRST_PAGE_SUMMARY_GAP <= (38 * mm + 0.000001)


def test_table_export_has_no_uat2_write_boundary():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "invoice_app"
        / "services"
        / "weekly_billing_barcode_table_export.py"
    ).read_text(encoding="utf-8").casefold()
    assert "google_sheets" not in source
    assert "create_repository" not in source
    assert ".write(" not in source
