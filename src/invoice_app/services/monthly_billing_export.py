"""Monthly wording adapter for the shared Product Summary barcode-table renderer."""

from __future__ import annotations

from src.invoice_app.domain.weekly_billing import WeeklyBillingSummary
from src.invoice_app.services.weekly_billing_barcode_export import is_valid_ean13
from src.invoice_app.services.weekly_billing_barcode_table_export import (
    ProductSummaryBarcodeTablePresentation,
    render_product_summary_barcode_table_pdf,
)
from src.invoice_app.utils.monetary import format_currency


def export_monthly_product_summary_barcode_table_pdf(
    summary: WeeklyBillingSummary,
) -> bytes:
    """Render the exact final Monthly Product Summary rowset in Monthly wording."""

    rows = summary.product_rows
    barcode_ready = sum(is_valid_ean13(row.sku_code) for row in rows)
    presentation = ProductSummaryBarcodeTablePresentation(
        document_title="Monthly Billing Product Summary Barcodes",
        heading="MONTHLY BILLING PRODUCT SUMMARY",
        metadata_lines=(
            f"Month: {summary.period.statement_period_from:%B %Y}",
            "Source: Shopee Monthly Statement",
        ),
        metrics=(
            ("Total Product", str(len(rows))),
            ("Total Qty", str(summary.total_quantity)),
            ("Total Original Sales", format_currency(summary.total_standard_amount)),
            ("Total Discount Given", format_currency(summary.total_discount_amount)),
            ("Total Amount", format_currency(summary.total_amount)),
            ("Barcode Ready", str(barcode_ready)),
            ("Barcode Unavailable", str(len(rows) - barcode_ready)),
        ),
        barcode_ready=barcode_ready,
        barcode_unavailable=len(rows) - barcode_ready,
        show_barcode_status_line=False,
    )
    return render_product_summary_barcode_table_pdf(
        product_rows=rows,
        presentation=presentation,
    )
