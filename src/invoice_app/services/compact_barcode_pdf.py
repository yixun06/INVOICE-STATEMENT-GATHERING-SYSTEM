"""Reusable portrait Compact Barcode POS Checklist PDF renderer."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from io import BytesIO
from typing import Sequence
from xml.sax.saxutils import escape

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.eanbc import Ean13BarcodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.invoice_app.services.weekly_billing_barcode_export import (
    _ensure_label_text_font,
    is_valid_ean13,
)
from src.invoice_app.services.resolve_sku import (
    ResolveSkuComponent,
    ResolveSkuIdentifierType,
)
from src.invoice_app.utils.monetary import format_currency


CENT = Decimal("0.01")
PAGE_MARGIN = 10 * mm
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN
BARCODE_AREA_WIDTH = 62 * mm
BARCODE_BAR_WIDTH = 0.33 * mm
BARCODE_HEIGHT = 17 * mm


class BarcodeStatus(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"


class BundleStatus(str, Enum):
    NORMAL = "NORMAL"
    CONFIRMED_BUNDLE = "CONFIRMED_BUNDLE"
    REVIEW_REQUIRED = "BUNDLE_REVIEW_REQUIRED"


class PriceStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    OVERRIDE_REQUIRED = "OVERRIDE_REQUIRED"
    ROUNDING_REVIEW_REQUIRED = "ROUNDING_REVIEW_REQUIRED"
    BUNDLE_PRICE_REVIEW_REQUIRED = "BUNDLE_PRICE_REVIEW_REQUIRED"


@dataclass(frozen=True)
class CompactBarcodeMetric:
    label: str
    value: str


@dataclass(frozen=True)
class CompactBarcodeComponentAllocation:
    """Operational money display for one Resolve SKU component.

    The figures are allocated only inside the Compact PDF contract.  They do
    not alter the source Product Summary row or any persisted financial fact.
    """

    pos_quantity: int
    original_sales: Decimal
    discount_given: Decimal
    amount: Decimal


@dataclass(frozen=True)
class CompactBarcodeRow:
    """One unchanged final Product Summary row with operational display facts."""

    row_number: int
    seller_sku: str
    nav: str
    description: str
    uom: str
    report_quantity: int
    unit_price: Decimal
    original_sales: Decimal
    discount_given: Decimal
    amount: Decimal
    barcode: str | None
    barcode_status: BarcodeStatus
    bundle_status: BundleStatus
    bundle_multiplier: int | None
    effective_scan_quantity: int | None
    pos_override_unit_price: Decimal | None
    price_status: PriceStatus
    resolve_sku: str | None = None
    resolve_sku_warning: str | None = None
    pos_components: tuple[ResolveSkuComponent, ...] = ()
    pos_component_allocations: tuple[CompactBarcodeComponentAllocation, ...] = ()


@dataclass(frozen=True)
class CompactBarcodeReport:
    """Validated, UI-independent input for the Compact Barcode renderer."""

    company_name: str
    report_title: str
    platform: str
    currency_code: str
    reporting_period: str
    metrics: tuple[CompactBarcodeMetric, ...]
    rows: tuple[CompactBarcodeRow, ...]


def resolve_compact_barcode(seller_sku: str) -> str | None:
    """Apply the approved existing full-SKU / hyphen-prefix EAN-13 convention."""

    candidate = seller_sku.strip().split("-", 1)[0]
    return candidate if is_valid_ean13(candidate) else None


def resolve_bundle_status(
    seller_sku: str, report_quantity: int
) -> tuple[BundleStatus, int | None, int | None]:
    """Resolve only the Product Owner-approved terminal numeric bundle suffix."""

    prefix, separator, suffix = seller_sku.strip().rpartition("-")
    if not separator or not suffix.isdigit():
        return BundleStatus.NORMAL, None, report_quantity if report_quantity > 0 else None
    multiplier = int(suffix)
    if not prefix or multiplier <= 0 or report_quantity <= 0:
        return BundleStatus.REVIEW_REQUIRED, None, None
    return (
        BundleStatus.CONFIRMED_BUNDLE,
        multiplier,
        report_quantity * multiplier,
    )


def resolve_price_status(
    *,
    bundle_status: BundleStatus,
    report_quantity: int,
    discount_given: Decimal,
    amount: Decimal,
) -> tuple[PriceStatus, Decimal | None]:
    """Return an exact non-bundle POS override only when it reproduces Amount."""

    if bundle_status is not BundleStatus.NORMAL:
        return PriceStatus.BUNDLE_PRICE_REVIEW_REQUIRED, None
    if discount_given >= Decimal("0.00"):
        return PriceStatus.NOT_REQUIRED, None
    if report_quantity <= 0:
        return PriceStatus.ROUNDING_REVIEW_REQUIRED, None
    override = (amount / Decimal(report_quantity)).quantize(CENT)
    if override * report_quantity != amount:
        return PriceStatus.ROUNDING_REVIEW_REQUIRED, None
    return PriceStatus.OVERRIDE_REQUIRED, override


def allocate_pos_component_financials(
    *,
    components: Sequence[ResolveSkuComponent],
    report_quantity: int,
    original_sales: Decimal,
    discount_given: Decimal,
    amount: Decimal,
) -> tuple[CompactBarcodeComponentAllocation, ...]:
    """Allocate Compact-PDF source money equally across physical POS units.

    Remainder cents go to the earliest physical units in Listing order.  The
    signed discount is allocated as supplied, so a negative discount remains
    negative.  Allocation is deliberately unavailable when the source row or
    a component cannot prove an exact physical-unit quantity.
    """

    if not components or report_quantity <= 0:
        return ()
    quantities = tuple(component.scan_quantity(report_quantity) for component in components)
    if any(quantity is None or quantity <= 0 for quantity in quantities):
        return ()
    original_cents = _exact_cents(original_sales)
    discount_cents = _exact_cents(discount_given)
    amount_cents = _exact_cents(amount)
    if (
        original_cents is None
        or discount_cents is None
        or amount_cents is None
        or original_cents - discount_cents != amount_cents
    ):
        return ()

    physical_component_indexes = tuple(
        component_index
        for component_index, quantity in enumerate(quantities)
        for _ in range(quantity)
    )
    original_by_unit = _equal_cent_allocations(original_cents, len(physical_component_indexes))
    discount_by_unit = _equal_cent_allocations(discount_cents, len(physical_component_indexes))
    original_by_component = [Decimal("0.00") for _ in components]
    discount_by_component = [Decimal("0.00") for _ in components]
    for component_index, allocated_original, allocated_discount in zip(
        physical_component_indexes, original_by_unit, discount_by_unit
    ):
        original_by_component[component_index] += allocated_original
        discount_by_component[component_index] += allocated_discount
    return tuple(
        CompactBarcodeComponentAllocation(
            pos_quantity=quantity,
            original_sales=original_by_component[index],
            discount_given=discount_by_component[index],
            amount=original_by_component[index] - discount_by_component[index],
        )
        for index, quantity in enumerate(quantities)
    )


def _exact_cents(value: Decimal) -> int | None:
    """Return a source money amount as cents without silently rounding it."""

    if value != value.quantize(CENT):
        return None
    return int(value / CENT)


def _equal_cent_allocations(total_cents: int, count: int) -> tuple[Decimal, ...]:
    """Split signed cents equally; earliest Listing units receive remainder cents."""

    magnitude, remainder = divmod(abs(total_cents), count)
    sign = -1 if total_cents < 0 else 1
    return tuple(
        Decimal(sign * (magnitude + (1 if index < remainder else 0))) * CENT
        for index in range(count)
    )


def render_compact_barcode_pdf(report: CompactBarcodeReport) -> bytes:
    """Render a portrait operational PDF without UI, storage, or network access."""

    text_font = _ensure_label_text_font()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN,
        title=report.report_title,
        pageCompression=1,
    )
    styles = _styles(text_font)
    story: list[object] = [_first_page_header(report, styles), Spacer(1, 3 * mm)]
    story.extend(
        _card(row, report.currency_code, styles)
        for row in report.rows
    )
    document.build(
        story,
        onFirstPage=lambda canvas, doc: _page_footer(canvas, doc, report),
        onLaterPages=lambda canvas, doc: _continuation_header(canvas, doc, report),
    )
    return output.getvalue()


def _first_page_header(report: CompactBarcodeReport, styles: dict[str, ParagraphStyle]) -> Table:
    title_data = [
        [Paragraph(escape(report.company_name), styles["company"])],
        [Paragraph(escape(report.report_title), styles["title"])],
        [Paragraph(f"Platform: {escape(report.platform)}", styles["metadata"])],
        [Paragraph(f"Reporting Period: {escape(report.reporting_period)}", styles["metadata"])],
    ]
    heading = Table(title_data, colWidths=(CONTENT_WIDTH,))
    heading.setStyle(_no_padding_style())

    metrics = Table(
        [
            [Paragraph(escape(metric.label), styles["metric_label"]) for metric in report.metrics],
            [Paragraph(escape(metric.value), styles["metric_value"]) for metric in report.metrics],
        ],
        colWidths=(CONTENT_WIDTH / len(report.metrics),) * len(report.metrics),
        rowHeights=(4.2 * mm, 6 * mm),
    )
    metrics.setStyle(
        TableStyle(
            (
                ("LINEABOVE", (0, 0), (-1, 0), 0.45, colors.HexColor("#777777")),
                ("LINEBELOW", (0, -1), (-1, -1), 0.45, colors.HexColor("#777777")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            )
        )
    )
    return Table([[heading], [Spacer(1, 2 * mm)], [metrics]], colWidths=(CONTENT_WIDTH,))


def _card(
    row: CompactBarcodeRow,
    currency_code: str,
    styles: dict[str, ParagraphStyle],
) -> KeepTogether:
    left = _barcode_area(row, currency_code, styles)
    right = [
        Paragraph(escape(row.description or "Description unavailable"), styles["description"]),
        Spacer(1, 1.2 * mm),
        Paragraph(
            f"SKU: {escape(row.seller_sku or '—')}    NAV: {escape(row.nav or '—')}    "
            f"UOM: {escape(row.uom or '—')}",
            styles["identity"],
        ),
        Spacer(1, 1.7 * mm),
        _financial_grid(row, currency_code, styles),
        Spacer(1, 1.5 * mm),
        _action_lines(row, styles),
    ]
    card = Table(
        [[left, right]],
        colWidths=(BARCODE_AREA_WIDTH, CONTENT_WIDTH - BARCODE_AREA_WIDTH),
        hAlign="LEFT",
    )
    card.setStyle(
        TableStyle(
            (
                ("BOX", (0, 0), (-1, -1), 0.45, colors.HexColor("#B0B0B0")),
                ("LINEAFTER", (0, 0), (0, 0), 0.35, colors.HexColor("#D0D0D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2.7 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.7 * mm),
            )
        )
    )
    return KeepTogether([card, Spacer(1, 2.2 * mm)])


def _barcode_area(
    row: CompactBarcodeRow,
    currency_code: str,
    styles: dict[str, ParagraphStyle],
) -> list[object]:
    if row.resolve_sku_warning:
        return [
            Paragraph("RESOLVE SKU REVIEW REQUIRED", styles["warning_center"]),
            Spacer(1, 1.5 * mm),
            Paragraph(escape(row.resolve_sku_warning), styles["manual"]),
        ]
    if row.pos_components:
        return _component_areas(row, currency_code, styles)
    if row.barcode_status is BarcodeStatus.READY and row.barcode:
        contents: list[object] = [
            Paragraph(f"ROW {row.row_number}", styles["tiny_center"]),
            Spacer(1, 1 * mm),
            _CompactEan13BarcodeFlowable(row.barcode),
            Spacer(1, 1 * mm),
            Paragraph(escape(row.barcode), styles["barcode_digits"]),
        ]
    else:
        contents = [
            Paragraph("BARCODE UNAVAILABLE", styles["warning_center"]),
            Spacer(1, 1.5 * mm),
            Paragraph("MANUAL ENTRY REQUIRED", styles["warning_center"]),
            Spacer(1, 2 * mm),
            Paragraph(f"SKU: {escape(row.seller_sku or '—')}", styles["manual"]),
            Paragraph(f"NAV: {escape(row.nav or '—')}", styles["manual"]),
        ]
    return contents


def _component_areas(
    row: CompactBarcodeRow,
    currency_code: str,
    styles: dict[str, ParagraphStyle],
) -> list[object]:
    contents: list[object] = []
    for number, component in enumerate(row.pos_components, start=1):
        if number > 1:
            contents.append(Spacer(1, 1.5 * mm))
        contents.append(Paragraph(f"COMPONENT {number}", styles["tiny_center"]))
        quantity = component.scan_quantity(row.report_quantity)
        if component.barcode:
            contents.extend(
                (
                    Spacer(1, 0.6 * mm),
                    _CompactEan13BarcodeFlowable(component.barcode),
                    Paragraph(escape(component.barcode), styles["barcode_digits"]),
                )
            )
        else:
            contents.append(Paragraph("BARCODE UNAVAILABLE", styles["warning_center"]))
            contents.append(Paragraph("MANUAL ENTRY REQUIRED", styles["warning_center"]))
            label = (
                "NAV"
                if component.identifier_type is ResolveSkuIdentifierType.NAV
                else "POS MANUAL CODE"
            )
            contents.append(
                Paragraph(f"{label}: {escape(component.identifier or component.original_text)}", styles["manual"])
            )
        contents.append(
            Paragraph(
                f"POS QTY: {quantity}" if quantity is not None else "POS QTY: REVIEW REQUIRED",
                styles["manual"],
            )
        )
        allocation = _component_allocation_for(row, number - 1)
        if allocation is not None:
            contents.extend(
                (
                    Paragraph(
                        f"POS PRICE: {format_currency(allocation.original_sales, currency_code)}",
                        styles["manual"],
                    ),
                    Paragraph(
                        f"DISCOUNT: {format_currency(allocation.discount_given, currency_code)}",
                        styles["manual"],
                    ),
                    Paragraph(
                        f"NET: {format_currency(allocation.amount, currency_code)}",
                        styles["manual"],
                    ),
                )
            )
        if component.warning:
            contents.append(Paragraph(escape(component.warning), styles["manual"]))
    return contents


def _component_allocation_for(
    row: CompactBarcodeRow, component_index: int
) -> CompactBarcodeComponentAllocation | None:
    if len(row.pos_component_allocations) != len(row.pos_components):
        return None
    return row.pos_component_allocations[component_index]


def _financial_grid(
    row: CompactBarcodeRow,
    currency_code: str,
    styles: dict[str, ParagraphStyle],
) -> Table:
    values = (
        ("QTY", str(row.report_quantity)),
        ("POS UNIT", format_currency(row.unit_price, currency_code)),
        (
            "OVERRIDE UNIT",
            format_currency(row.pos_override_unit_price, currency_code)
            if row.pos_override_unit_price is not None
            else "—",
        ),
        ("ORIGINAL SALES", format_currency(row.original_sales, currency_code)),
        ("DISC GIVEN", format_currency(row.discount_given, currency_code)),
        ("AMOUNT", format_currency(row.amount, currency_code)),
    )
    grid = Table(
        [
            [Paragraph(label, styles["grid_label"]) for label, _value in values],
            [Paragraph(escape(value), styles["grid_value"]) for _label, value in values],
        ],
        colWidths=((CONTENT_WIDTH - BARCODE_AREA_WIDTH - 6 * mm) / 6,) * 6,
        rowHeights=(4.2 * mm, 5.6 * mm),
    )
    grid.setStyle(
        TableStyle(
            (
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F1F1")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C5C5C5")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0.8 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0.8 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            )
        )
    )
    return grid


def _action_lines(row: CompactBarcodeRow, styles: dict[str, ParagraphStyle]) -> object:
    actions: list[str] = []
    if row.pos_components:
        if len(row.pos_components) > 1:
            actions.append("MULTI-COMPONENT POS INSTRUCTION - KEEP SOURCE AMOUNT ONCE")
        else:
            component = row.pos_components[0]
            quantity = component.scan_quantity(row.report_quantity)
            if component.units_per_listing_item not in (None, 1):
                actions.extend(
                    (
                        "BUNDLE SALE",
                        f"1 BUNDLE = {component.units_per_listing_item} UNITS",
                        f"REPORT QTY: {row.report_quantity} | SCAN QTY: {quantity}",
                    )
                )
            else:
                actions.append(
                    f"POS SCAN QTY: {quantity}" if quantity is not None else "POS QTY REVIEW REQUIRED"
                )
    elif row.bundle_status is BundleStatus.CONFIRMED_BUNDLE:
        actions.extend(
            (
                "BUNDLE SALE",
                f"1 BUNDLE = {row.bundle_multiplier} UNITS",
                f"REPORT QTY: {row.report_quantity} | SCAN QTY: {row.effective_scan_quantity}",
            )
        )
    elif row.bundle_status is BundleStatus.REVIEW_REQUIRED:
        actions.append("BUNDLE REVIEW REQUIRED - SCAN QTY NOT DERIVED")
    else:
        actions.append(f"SCAN QTY = REPORT QTY: {row.report_quantity}")

    if row.price_status is PriceStatus.OVERRIDE_REQUIRED:
        actions.append("OVERRIDE REQUIRED")
    elif row.price_status is PriceStatus.ROUNDING_REVIEW_REQUIRED:
        actions.append("ROUNDING REVIEW REQUIRED")
    elif row.price_status is PriceStatus.BUNDLE_PRICE_REVIEW_REQUIRED:
        actions.append("BUNDLE PRICE REVIEW REQUIRED")

    return Paragraph("<br/>".join(escape(action) for action in actions), styles["action"])


class _CompactEan13BarcodeFlowable(Flowable):
    """Vector EAN-13 barcode with ReportLab's built-in quiet zones."""

    def __init__(self, barcode: str) -> None:
        super().__init__()
        self.barcode = barcode
        widget = self._widget()
        left, bottom, right, top = widget.getBounds()
        self.width = right - left
        self.height = top - bottom

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        widget = self._widget()
        _left, _bottom, right, top = widget.getBounds()
        drawing = Drawing(right, top)
        drawing.add(widget)
        renderPDF.draw(drawing, self.canv, 0, 0)

    def _widget(self) -> Ean13BarcodeWidget:
        widget = Ean13BarcodeWidget(self.barcode)
        widget.barWidth = BARCODE_BAR_WIDTH
        widget.barHeight = BARCODE_HEIGHT
        widget.humanReadable = False
        return widget


def _continuation_header(canvas, doc, report: CompactBarcodeReport) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#A0A0A0"))
    canvas.line(PAGE_MARGIN, A4[1] - PAGE_MARGIN + 2 * mm, A4[0] - PAGE_MARGIN, A4[1] - PAGE_MARGIN + 2 * mm)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(colors.HexColor("#00421E"))
    canvas.drawString(PAGE_MARGIN, A4[1] - PAGE_MARGIN + 4 * mm, report.report_title)
    canvas.setFillColor(colors.black)
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(A4[0] - PAGE_MARGIN, A4[1] - PAGE_MARGIN + 4 * mm, f"Page {doc.page}")
    _page_footer(canvas, doc, report, saved=True)
    canvas.restoreState()


def _page_footer(canvas, doc, report: CompactBarcodeReport, *, saved: bool = False) -> None:
    if not saved:
        canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#C0C0C0"))
    canvas.line(PAGE_MARGIN, PAGE_MARGIN - 2 * mm, A4[0] - PAGE_MARGIN, PAGE_MARGIN - 2 * mm)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.setFont("Helvetica", 7)
    canvas.drawString(PAGE_MARGIN, PAGE_MARGIN - 5 * mm, f"{report.platform} | {report.reporting_period}")
    canvas.drawRightString(A4[0] - PAGE_MARGIN, PAGE_MARGIN - 5 * mm, f"Page {doc.page}")
    if not saved:
        canvas.restoreState()


def _styles(text_font: str) -> dict[str, ParagraphStyle]:
    return {
        "company": ParagraphStyle("CompactCompany", fontName="Helvetica-Bold", fontSize=14, leading=15, textColor=colors.HexColor("#00421E")),
        "title": ParagraphStyle("CompactTitle", fontName="Helvetica-Bold", fontSize=10.5, leading=12, textColor=colors.HexColor("#222222")),
        "metadata": ParagraphStyle("CompactMetadata", fontName=text_font, fontSize=7.5, leading=8.7, textColor=colors.HexColor("#444444")),
        "metric_label": ParagraphStyle("CompactMetricLabel", fontName="Helvetica-Bold", fontSize=5.9, leading=6.5, alignment=TA_LEFT),
        "metric_value": ParagraphStyle("CompactMetricValue", fontName=text_font, fontSize=7.2, leading=8, alignment=TA_LEFT),
        "description": ParagraphStyle("CompactDescription", fontName=text_font, fontSize=8.5, leading=10, textColor=colors.black),
        "identity": ParagraphStyle("CompactIdentity", fontName=text_font, fontSize=6.7, leading=8, textColor=colors.HexColor("#333333")),
        "tiny_center": ParagraphStyle("CompactTinyCenter", fontName="Helvetica-Bold", fontSize=6.2, leading=7, alignment=TA_CENTER),
        "barcode_digits": ParagraphStyle("CompactBarcodeDigits", fontName="Helvetica-Bold", fontSize=8.2, leading=9, alignment=TA_CENTER),
        "warning_center": ParagraphStyle("CompactWarningCenter", fontName="Helvetica-Bold", fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=colors.HexColor("#444444")),
        "manual": ParagraphStyle("CompactManual", fontName=text_font, fontSize=6.8, leading=8.2, alignment=TA_LEFT),
        "grid_label": ParagraphStyle("CompactGridLabel", fontName="Helvetica-Bold", fontSize=5.2, leading=5.8, alignment=TA_CENTER),
        "grid_value": ParagraphStyle("CompactGridValue", fontName=text_font, fontSize=6.2, leading=7, alignment=TA_RIGHT),
        "action": ParagraphStyle("CompactAction", fontName="Helvetica-Bold", fontSize=7.1, leading=8.5, textColor=colors.HexColor("#202020")),
    }


def _no_padding_style() -> TableStyle:
    return TableStyle(
        (
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        )
    )
