from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from src.invoice_app.domain.weekly_billing import (
    BillingPeriod,
    ProductSummaryRow,
    WeeklyBillingSummary,
)
from src.invoice_app.services.weekly_billing_barcode_export import (
    LABEL_TEXT_FONT_PATH,
    export_product_summary_barcode_pdf,
    is_valid_ean13,
    summarize_product_name_layouts,
    summarize_product_summary_barcodes,
)


PERIOD = BillingPeriod(date(2026, 8, 31), date(2026, 9, 6), "batch", "a" * 64)


def _row(**overrides) -> ProductSummaryRow:
    values = dict(
        number=1, sku_code="9555208107347", nav="5000001", product_name="Simply Natural Fresh Raw Honey",
        uom="EA", unit_price=Decimal("54.90"), quantity=10, discount_percent=None,
        discount_amount=Decimal("0.00"), amount=Decimal("549.00"), source_item_count=1,
    )
    values.update(overrides)
    return ProductSummaryRow(**values)


def _summary(*rows: ProductSummaryRow) -> WeeklyBillingSummary:
    return WeeklyBillingSummary(
        period=PERIOD, order_count=1, invoice_item_count=len(rows), source_items=(), product_rows=rows,
        total_quantity=sum(row.quantity for row in rows), total_standard_amount=Decimal("0"),
        total_discount_amount=sum(row.discount_amount for row in rows), total_amount=sum(row.amount for row in rows),
        normal_amount_total=Decimal("0"), promotion_amount_total=Decimal("0"), promotion_group_count=0,
        same_price_promotion_count=0, mixed_price_promotion_count=0,
    )


def _pages(summary: WeeklyBillingSummary):
    pdf = export_product_summary_barcode_pdf(summary)
    assert pdf.startswith(b"%PDF-")
    return PdfReader(BytesIO(pdf)).pages


def test_ean13_validation_accepts_proper_check_digit_and_rejects_invalid_values():
    assert is_valid_ean13("9555208107347")
    assert not is_valid_ean13("9555208107348")
    assert not is_valid_ean13("9555208106944-6")
    assert not is_valid_ean13("not-a-barcode")


def test_valid_ean13_label_renders_one_pdf_page_with_all_final_values():
    page = _pages(_summary(_row()))[0]
    text = page.extract_text()
    assert "9555208107347" in text
    assert "Barcode unavailable" not in text
    assert "QUANTITY:" in text and "10" in text
    assert "UOM:" in text and "EA" in text
    assert "SALES AMOUNT:" in text and "RM 549.00" in text
    assert "DISCOUNT AMOUNT:" in text and "RM 0.00" in text


def test_invalid_suffix_retains_complete_sku_and_still_gets_label():
    pages = _pages(_summary(_row(sku_code="9555208106944-6")))
    assert len(pages) == 1
    text = pages[0].extract_text()
    assert "Barcode unavailable" in text
    assert "9555208106944-6" in text


def test_each_row_maps_to_one_page_in_existing_product_summary_order_without_mutation():
    first = _row(number=7, sku_code="9555208107347", product_name="First product")
    second = _row(number=3, sku_code="invalid-sku", product_name="Second product", quantity=2)
    summary = _summary(first, second)
    pages = _pages(summary)
    assert len(pages) == 2
    assert "First product" in pages[0].extract_text()
    assert "Second product" in pages[1].extract_text()
    assert summary.product_rows == (first, second)


def test_barcode_summary_counts_only_valid_ean13_rows():
    result = summarize_product_summary_barcodes(_summary(_row(), _row(sku_code="invalid-sku")))
    assert result.label_count == 2
    assert result.valid_ean13_count == 1
    assert result.unavailable_count == 1
    assert result.unavailable_sku_codes == ("invalid-sku",)


def test_normal_english_product_name_renders_without_truncation():
    description = "Organic traditional honey from carefully selected flowers"
    page = _pages(_summary(_row(product_name=description)))[0]
    assert "".join(description.split()) in "".join(page.extract_text().split())


def test_description_longer_than_previous_four_line_limit_adapts_without_truncation():
    description = "Organic traditional honey from carefully selected flowers " * 12
    summary = _summary(_row(product_name=description))
    page = _pages(summary)[0]
    assert "".join(description.split()) in "".join(page.extract_text().split())
    assert summarize_product_name_layouts(summary).maximum_line_count > 4


def test_product_name_supports_embedded_chinese_glyphs():
    chinese_name = "GBT Body Cleansing Gel 300ml | \u9999\u8305 | Lemongrass"
    page = _pages(_summary(_row(product_name=chinese_name)))[0]
    assert "".join(chinese_name.split()) in "".join(page.extract_text().split())
    fonts = page["/Resources"]["/Font"].get_object().values()
    assert any(font.get_object().get("/ToUnicode") is not None for font in fonts)


def test_chinese_font_is_packaged_with_the_project_not_resolved_from_windows():
    assert LABEL_TEXT_FONT_PATH.is_file()
    assert LABEL_TEXT_FONT_PATH.name == "NotoSansSC-Variable.ttf"
    assert "windows" not in str(LABEL_TEXT_FONT_PATH).casefold()
    license_notice = LABEL_TEXT_FONT_PATH.with_name("NotoSansSC-OFL.txt")
    assert "SIL OPEN FONT LICENSE" in license_notice.read_text(encoding="utf-8")


def test_long_chinese_and_mixed_product_names_adapt_without_truncation():
    chinese_name = "\u9999\u8305\u6d17\u53d1\u6c34\u6c90\u6d74\u9732\u5929\u7136\u690d\u7269\u6e05\u723d\u914d\u65b9" * 8
    mixed_name = "GBT Body Cleansing Gel \u9999\u8305 Lemongrass " * 10
    pages = _pages(_summary(
        _row(product_name=chinese_name),
        _row(number=2, product_name=mixed_name),
    ))
    assert "".join(chinese_name.split()) in "".join(pages[0].extract_text().split())
    assert "".join(mixed_name.split()) in "".join(pages[1].extract_text().split())


def test_long_unbroken_token_and_invalid_barcode_still_render_full_label():
    token = "X" * 900
    row = _row(sku_code="9555208106944-6", product_name=token)
    page = _pages(_summary(row))[0]
    text = page.extract_text()
    assert "Barcode unavailable" in text
    assert "9555208106944-6" in text
    assert "".join(token.split()) in "".join(text.split())


def test_difficult_label_does_not_prevent_later_labels_from_rendering():
    difficult = "Natural \u9999\u8305 product " * 15
    later = "Later product stays present"
    pages = _pages(_summary(
        _row(product_name=difficult),
        _row(number=2, sku_code="invalid-sku", product_name=later),
    ))
    assert len(pages) == 2
    assert "".join(difficult.split()) in "".join(pages[0].extract_text().split())
    assert later in pages[1].extract_text()


def test_barcode_export_has_no_uat2_write_boundary():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "invoice_app"
        / "services"
        / "weekly_billing_barcode_export.py"
    ).read_text(encoding="utf-8").casefold()
    assert "google_sheets" not in source
    assert "create_repository" not in source
    assert ".write(" not in source
