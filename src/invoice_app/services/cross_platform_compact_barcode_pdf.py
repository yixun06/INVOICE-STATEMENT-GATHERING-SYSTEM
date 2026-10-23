"""Cross Platform adapter for the reusable Compact Barcode PDF contract."""

from __future__ import annotations

from decimal import Decimal

from src.invoice_app.services.compact_barcode_pdf import (
    BarcodeStatus,
    BundleStatus,
    CompactBarcodeMetric,
    CompactBarcodeReport,
    CompactBarcodeRow,
    allocate_pos_component_financials,
    resolve_bundle_status,
    resolve_compact_barcode,
    resolve_price_status,
    render_compact_barcode_pdf,
)
from src.invoice_app.services.resolve_sku import parse_resolve_sku
from src.invoice_app.services.cross_platform_product_summary import (
    CrossPlatformProductSummary,
)
from src.invoice_app.utils.monetary import format_currency


def build_cross_platform_compact_barcode_report(
    summary: CrossPlatformProductSummary,
) -> CompactBarcodeReport:
    """Adapt one already-filtered final rowset without querying or regrouping it."""

    rows = tuple(_compact_row(row) for row in summary.product_rows)
    total_original_sales = sum((row.original_sales for row in summary.product_rows), Decimal("0.00"))
    total_discount_given = sum((row.discount_amount for row in summary.product_rows), Decimal("0.00"))
    total_amount = sum((row.amount for row in summary.product_rows), Decimal("0.00"))
    barcode_ready = sum(row.barcode_status is BarcodeStatus.READY for row in rows)
    return CompactBarcodeReport(
        company_name="Zenxin Agri-Organic Food (AH) Sdn Bhd",
        report_title="CONSOLIDATED PRODUCT SUMMARY",
        platform=summary.platform,
        currency_code=summary.currency_code,
        reporting_period=_reporting_period_label(summary),
        metrics=(
            CompactBarcodeMetric("Total Product", str(len(rows))),
            CompactBarcodeMetric("Total Qty", str(sum(row.quantity for row in summary.product_rows))),
            CompactBarcodeMetric("Total Original Sales", format_currency(total_original_sales, summary.currency_code)),
            CompactBarcodeMetric("Total Discount Given", format_currency(total_discount_given, summary.currency_code)),
            CompactBarcodeMetric("Total Amount", format_currency(total_amount, summary.currency_code)),
            CompactBarcodeMetric("Barcode Ready", str(barcode_ready)),
            CompactBarcodeMetric("Barcode Unavailable", str(len(rows) - barcode_ready)),
        ),
        rows=rows,
    )


def export_cross_platform_compact_barcode_pdf(
    summary: CrossPlatformProductSummary,
) -> bytes:
    """Render the current final Cross Platform rows as a Compact Barcode PDF."""

    return render_compact_barcode_pdf(build_cross_platform_compact_barcode_report(summary))


def _compact_row(row) -> CompactBarcodeRow:
    resolve_sku = row.resolve_sku or None
    components = () if row.resolve_sku_warning else parse_resolve_sku(resolve_sku or row.sku_code)
    single_component = components[0] if len(components) == 1 else None
    barcode = single_component.barcode if single_component else None
    has_physical_bundle = len(components) > 1 or any(
        component.units_per_listing_item not in (None, 1)
        for component in components
    )
    if has_physical_bundle:
        bundle_status = BundleStatus.CONFIRMED_BUNDLE
        multiplier = (
            single_component.units_per_listing_item
            if single_component and single_component.units_per_listing_item not in (None, 1)
            else None
        )
        scan_quantity = single_component.scan_quantity(row.quantity) if single_component else None
    else:
        bundle_status, multiplier, scan_quantity = resolve_bundle_status(
            row.sku_code, row.quantity
        )
    price_status, override = resolve_price_status(
        bundle_status=bundle_status,
        report_quantity=row.quantity,
        discount_given=row.discount_amount,
        amount=row.amount,
    )
    component_allocations = (
        allocate_pos_component_financials(
            components=components,
            report_quantity=row.quantity,
            original_sales=row.original_sales,
            discount_given=row.discount_amount,
            amount=row.amount,
        )
        if has_physical_bundle
        else ()
    )
    return CompactBarcodeRow(
        row_number=row.number,
        seller_sku=row.sku_code,
        nav=row.nav,
        description=row.product_name,
        uom=row.uom or "",
        report_quantity=row.quantity,
        unit_price=row.unit_price,
        original_sales=row.original_sales,
        discount_given=row.discount_amount,
        amount=row.amount,
        barcode=barcode,
        barcode_status=BarcodeStatus.READY if barcode else BarcodeStatus.UNAVAILABLE,
        bundle_status=bundle_status,
        bundle_multiplier=multiplier,
        effective_scan_quantity=scan_quantity,
        pos_override_unit_price=override,
        price_status=price_status,
        resolve_sku=resolve_sku,
        resolve_sku_warning=row.resolve_sku_warning,
        pos_components=components,
        pos_component_allocations=component_allocations,
    )


def _reporting_period_label(summary: CrossPlatformProductSummary) -> str:
    if summary.from_date is not None and summary.to_date is not None:
        return f"{summary.from_date:%d %b %Y} - {summary.to_date:%d %b %Y}"
    if summary.from_date is not None:
        return f"From {summary.from_date:%d %b %Y}"
    if summary.to_date is not None:
        return f"To {summary.to_date:%d %b %Y}"
    return "All Dates"
