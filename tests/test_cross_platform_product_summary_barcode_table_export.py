from datetime import date
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader

from src.invoice_app.domain.weekly_billing import ProductSummaryRow
from src.invoice_app.services.cross_platform_product_summary import (
    CrossPlatformProductSummary,
)
from src.invoice_app.services.weekly_billing_barcode_table_export import TABLE_HEADERS


def test_cross_platform_pdf_uses_the_final_summary_rows_and_cross_platform_metadata():
    from src.invoice_app.services.cross_platform_product_summary_barcode_table_export import (
        export_cross_platform_product_summary_barcode_table_pdf,
    )

    rows = (
        ProductSummaryRow(
            number=1,
            sku_code="9555208107347",
            nav="300001",
            product_name="Persisted Tea — 500ml",
            uom="EA",
            unit_price=Decimal("10.00"),
            quantity=2,
            discount_percent=None,
            discount_amount=Decimal("-4.40"),
            amount=Decimal("24.40"),
            source_item_count=1,
        ),
        ProductSummaryRow(
            number=2,
            sku_code="SKU-FULL-UNAVAILABLE-12345",
            nav="N/A",
            product_name="Unavailable barcode",
            uom="EA",
            unit_price=Decimal("5.00"),
            quantity=1,
            discount_percent=None,
            discount_amount=Decimal("10.00"),
            amount=Decimal("-5.00"),
            source_item_count=1,
        ),
    )
    summary = CrossPlatformProductSummary(
        platform="Shopee",
        from_date=date(2026, 8, 8),
        to_date=date(2026, 8, 10),
        source_items=(),
        product_rows=rows,
    )

    pdf = export_cross_platform_product_summary_barcode_table_pdf(summary)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)

    assert pdf.startswith(b"%PDF-")
    assert "Zenxin Agri-Organic Food (AH) Sdn Bhd" in text
    assert "CROSS PLATFORM PRODUCT SUMMARY" in text
    assert "Platform: Shopee" in text
    assert "Reporting Period: 08 Aug 2026 – 10 Aug 2026" in text
    assert "Source: Shopee Weekly Statement" not in text
    for metric in (
        "Total Product",
        "Total Qty",
        "Total Original Sales",
        "Total Discount Given",
        "Total Amount",
        "Barcode Ready",
        "Barcode Unavailable",
    ):
        assert metric in text
    for value in ("2", "3", "RM 25.00", "RM 5.60", "RM 19.40", "1"):
        assert value in text
    assert "SKU-FULL-UNAVAILABLE-12345" in "".join(text.split())
    assert "Barcode unavailable" in text
    assert rows[0].sku_code in text
    assert TABLE_HEADERS == (
        "No.", "SKU Code", "Barcode", "Qty", "NAV", "Description", "UOM",
        "Unit Price", "Original Sales", "Disc given", "Amount",
    )


def test_cross_platform_pdf_passes_the_exact_final_product_rows_and_matching_ui_totals_to_the_shared_renderer(monkeypatch):
    from src.invoice_app.services import cross_platform_product_summary_barcode_table_export as exporter
    from src.invoice_app.ui import cross_platform_product_summary as cross_ui

    rows = (
        ProductSummaryRow(
            number=1, sku_code="9555208107347", nav="300001",
            product_name="Persisted Tea", uom="EA", unit_price=Decimal("10.00"),
            quantity=2, discount_percent=None, discount_amount=Decimal("2.00"),
            amount=Decimal("18.00"), source_item_count=1,
        ),
    )
    summary = CrossPlatformProductSummary(
        platform="All", from_date=None, to_date=None, source_items=(), product_rows=rows,
    )
    received = {}

    def fake_renderer(*, product_rows, presentation):
        received["rows"] = product_rows
        received["presentation"] = presentation
        return b"%PDF-test"

    monkeypatch.setattr(exporter, "render_product_summary_barcode_table_pdf", fake_renderer)

    assert exporter.export_cross_platform_product_summary_barcode_table_pdf(summary) == b"%PDF-test"
    assert received["rows"] is rows
    assert received["presentation"].metrics == (
        ("Total Product", "1"),
        ("Total Qty", "2"),
        ("Total Original Sales", "RM 20.00"),
        ("Total Discount Given", "RM 2.00"),
        ("Total Amount", "RM 18.00"),
        ("Barcode Ready", "1"),
        ("Barcode Unavailable", "0"),
    )
    ui_frame = cross_ui._summary_frame(summary)
    assert tuple(ui_frame.columns) == cross_ui.PRODUCT_SUMMARY_COLUMNS
    assert ui_frame["Original Sales"].sum() == 20.0
    assert ui_frame["Disc Amt"].sum() == 2.0
    assert ui_frame["Amount"].sum() == 18.0
