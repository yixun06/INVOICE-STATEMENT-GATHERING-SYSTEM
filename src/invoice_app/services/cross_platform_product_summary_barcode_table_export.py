"""Consolidated Product Summary Barcode Table PDF over one final rowset."""

from __future__ import annotations

from decimal import Decimal

from src.invoice_app.services.cross_platform_product_summary import (
    CrossPlatformProductSummary,
)
from src.invoice_app.services.weekly_billing_barcode_export import is_valid_ean13
from src.invoice_app.services.weekly_billing_barcode_table_export import (
    ProductSummaryBarcodeTablePresentation,
    render_product_summary_barcode_table_pdf,
)
from src.invoice_app.utils.monetary import format_currency


def export_cross_platform_product_summary_barcode_table_pdf(
    summary: CrossPlatformProductSummary,
) -> bytes:
    """Render the exact summary rowset currently visible in Cross Platform."""

    rows = summary.product_rows
    total_quantity = sum(row.quantity for row in rows)
    total_original_sales = sum(
        (row.original_sales for row in rows), Decimal("0.00")
    )
    total_discount_given = sum(
        (row.discount_amount for row in rows), Decimal("0.00")
    )
    total_amount = sum((row.amount for row in rows), Decimal("0.00"))
    barcode_ready = sum(_barcode_candidate(row.sku_code) is not None for row in rows)
    presentation = ProductSummaryBarcodeTablePresentation(
        document_title="Consolidated Product Summary Barcodes",
        heading="CONSOLIDATED PRODUCT SUMMARY",
        metadata_lines=(
            f"Platform: {summary.platform}",
            f"Reporting Period: {_reporting_period_label(summary)}",
        ),
        metrics=(
            ("Total Product", str(len(rows))),
            ("Total Qty", str(total_quantity)),
            ("Total Original Sales", format_currency(total_original_sales, summary.currency_code)),
            ("Total Discount Given", format_currency(total_discount_given, summary.currency_code)),
            ("Total Amount", format_currency(total_amount, summary.currency_code)),
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
        barcode_candidate_resolver=_barcode_candidate,
    )


def _barcode_candidate(sku_code: str) -> str | None:
    """Use a valid EAN-13 SKU prefix only for barcode rendering."""

    visible_sku = sku_code.strip()
    candidate = visible_sku.split("-", 1)[0]
    return candidate if is_valid_ean13(candidate) else None


def _reporting_period_label(summary: CrossPlatformProductSummary) -> str:
    from_date = summary.from_date
    to_date = summary.to_date
    if from_date is not None and to_date is not None:
        return f"{from_date:%d %b %Y} – {to_date:%d %b %Y}"
    if from_date is not None:
        return f"From {from_date:%d %b %Y}"
    if to_date is not None:
        return f"To {to_date:%d %b %Y}"
    return "All Dates"
