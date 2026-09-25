"""A4 table-style barcode PDF for finalized Weekly Billing Product Summary."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.eanbc import Ean13BarcodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.invoice_app.domain.weekly_billing import ProductSummaryRow, WeeklyBillingSummary
from src.invoice_app.services.weekly_billing_barcode_export import (
    _ensure_label_text_font,
    is_valid_ean13,
)


TABLE_PAGE_SIZE = landscape(A4)
TABLE_PAGE_MARGIN = 8 * mm
TABLE_HEADERS = (
    "No.",
    "SKU Code",
    "Barcode",
    "NAV",
    "Description",
    "Qty",
    "UOM",
    "Unit Price",
    "Original Sales", #unit price x quantity
    "Disc given",
    "Amount",
)
# The approved table geometry is retained and fits within the 281 mm printable width.
TABLE_COLUMN_WIDTHS_MM = (9, 20, 50, 18, 78, 12, 12, 15, 20, 15,15)
TABLE_COLUMN_WIDTHS = tuple(width * mm for width in TABLE_COLUMN_WIDTHS_MM)
TABLE_CONTENT_WIDTH = sum(TABLE_COLUMN_WIDTHS)
TABLE_BODY_FONT_SIZE = 6.5
TABLE_BODY_LEADING = 8
TABLE_BARCODE_BAR_WIDTH = 0.28 * mm
TABLE_BARCODE_HEIGHT = 12 * mm
FIRST_PAGE_SUMMARY_HEIGHT = 36 * mm
FIRST_PAGE_SUMMARY_GAP = 2 * mm
BRANDING_LOGO_PATH = (
    Path(__file__).resolve().parents[3] / "assets" / "branding" / "zenxin-logo.png"
)
BRANDING_LOGO_WIDTH = 28 * mm


@dataclass(frozen=True)
class ProductSummaryBarcodeTableRow:
    """Display-only projection of one unchanged final Product Summary row."""

    source: ProductSummaryRow
    values: tuple[str, ...]
    has_graphical_barcode: bool


@dataclass(frozen=True)
class ProductSummaryBarcodeTableHeader:
    """First-page operational facts derived from one final Product Summary."""

    sales_period: str
    product_rows: int
    total_quantity: int
    total_original_sales: Decimal
    total_discount_given: Decimal
    total_amount: Decimal
    barcode_ready: int
    barcode_unavailable: int


def build_product_summary_barcode_table_rows(
    product_summary: WeeklyBillingSummary,
) -> tuple[ProductSummaryBarcodeTableRow, ...]:
    """Map final rows to their PDF display values without regrouping or recalculation."""

    return tuple(
        ProductSummaryBarcodeTableRow(
            source=row,
            values=(
                str(row.number),
                row.sku_code,
                "" if is_valid_ean13(row.sku_code) else "Barcode unavailable",
                row.nav,
                row.product_name,
                str(row.quantity),
                row.uom or "",
                _format_money(row.unit_price),
                _format_money(row.original_sales),
                _format_money(row.discount_amount),
                _format_money(row.amount),
            ),
            has_graphical_barcode=is_valid_ean13(row.sku_code),
        )
        for row in product_summary.product_rows
    )


def build_product_summary_barcode_table_header(
    product_summary: WeeklyBillingSummary,
) -> ProductSummaryBarcodeTableHeader:
    """Return the first-page facts from the same final rows rendered in the table."""

    barcode_ready = sum(
        is_valid_ean13(row.sku_code) for row in product_summary.product_rows
    )
    product_rows = len(product_summary.product_rows)
    return ProductSummaryBarcodeTableHeader(
        sales_period=(
            f"{product_summary.period.statement_period_from:%d %b %Y} – "
            f"{product_summary.period.statement_period_to:%d %b %Y}"
        ),
        product_rows=product_rows,
        total_quantity=product_summary.total_quantity,
        total_original_sales=product_summary.total_standard_amount,
        total_discount_given=product_summary.total_discount_amount,
        total_amount=product_summary.total_amount,
        barcode_ready=barcode_ready,
        barcode_unavailable=product_rows - barcode_ready,
    )


def export_product_summary_barcode_table_pdf(
    product_summary: WeeklyBillingSummary,
) -> bytes:
    """Render one non-splitting table row per final Product Summary row."""

    text_font = _ensure_label_text_font()
    table_rows = build_product_summary_barcode_table_rows(product_summary)
    summary_header = build_product_summary_barcode_table_header(product_summary)
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=TABLE_PAGE_SIZE,
        leftMargin=TABLE_PAGE_MARGIN,
        rightMargin=TABLE_PAGE_MARGIN,
        topMargin=TABLE_PAGE_MARGIN,
        bottomMargin=TABLE_PAGE_MARGIN,
        title="Weekly Billing Product Summary Barcodes",
        pageCompression=1,
    )
    styles = _table_paragraph_styles(text_font)
    data: list[list[object]] = [
        [Paragraph(escape(header), styles["header"]) for header in TABLE_HEADERS]
    ]
    for table_row in table_rows:
        values = table_row.values
        barcode_cell: object = (
            _Ean13BarcodeFlowable(table_row.source.sku_code.strip())
            if table_row.has_graphical_barcode
            else Paragraph(escape(values[2]), styles["center"])
        )
        data.append(
            [
                Paragraph(escape(values[0]), styles["center"]),
                Paragraph(escape(values[1]), styles["left"]),
                barcode_cell,
                Paragraph(escape(values[3]), styles["left"]),
                Paragraph(escape(values[4]), styles["left"]),
                Paragraph(escape(values[5]), styles["center"]),
                Paragraph(escape(values[6]), styles["center"]),
                Paragraph(escape(values[7]), styles["right"]),
                Paragraph(escape(values[8]), styles["center"]),
                Paragraph(escape(values[9]), styles["right"]),
                Paragraph(escape(values[10]), styles["right"]),
            ]
        )

    table = LongTable(
        data,
        colWidths=TABLE_COLUMN_WIDTHS,
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            (
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("ALIGN", (2, 1), (2, -1), "CENTER"),
            )
        )
    )
    document.build(
        [
            KeepTogether(
                [
                    _first_page_summary_flowable(summary_header, text_font),
                    Spacer(1, FIRST_PAGE_SUMMARY_GAP),
                ]
            ),
            table,
        ]
    )
    return output.getvalue()


class _Ean13BarcodeFlowable(Flowable):
    """Vector EAN-13 sized to remain inside one table cell."""

    def __init__(self, sku: str) -> None:
        super().__init__()
        self.sku = sku
        barcode = Ean13BarcodeWidget(sku)
        barcode.barWidth = TABLE_BARCODE_BAR_WIDTH
        barcode.barHeight = TABLE_BARCODE_HEIGHT
        barcode.humanReadable = True
        barcode.fontName = "Helvetica"
        barcode.fontSize = 7
        left, bottom, right, top = barcode.getBounds()
        self.width = right - left
        self.height = top - bottom

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        barcode = Ean13BarcodeWidget(self.sku)
        barcode.barWidth = TABLE_BARCODE_BAR_WIDTH
        barcode.barHeight = TABLE_BARCODE_HEIGHT
        barcode.humanReadable = True
        barcode.fontName = "Helvetica"
        barcode.fontSize = 7
        _left, _bottom, right, top = barcode.getBounds()
        drawing = Drawing(right, top)
        drawing.add(barcode)
        renderPDF.draw(drawing, self.canv, 0, 0)


def _table_paragraph_styles(text_font: str) -> dict[str, ParagraphStyle]:
    common = dict(
        fontName=text_font,
        fontSize=TABLE_BODY_FONT_SIZE,
        leading=TABLE_BODY_LEADING,
        textColor=colors.black,
        wordWrap="CJK",
        spaceBefore=0,
        spaceAfter=0,
    )
    return {
        "header": ParagraphStyle(
            "BarcodeTableHeader",
            fontName="Helvetica-Bold",
            fontSize=6.2,
            leading=7.2,
            alignment=TA_CENTER,
            textColor=colors.black,
            wordWrap="CJK",
            spaceBefore=0,
            spaceAfter=0,
        ),
        "left": ParagraphStyle("BarcodeTableLeft", alignment=TA_LEFT, **common),
        "center": ParagraphStyle("BarcodeTableCenter", alignment=TA_CENTER, **common),
        "right": ParagraphStyle("BarcodeTableRight", alignment=TA_RIGHT, **common),
    }


def _first_page_summary_flowable(
    summary: ProductSummaryBarcodeTableHeader,
    text_font: str,
) -> Table:
    """Create the compact, once-only warehouse document heading for page one."""

    title = ParagraphStyle(
        "BarcodeTableSummaryTitle",
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=12.5,
        textColor=colors.HexColor("#202020"),
        spaceBefore=0,
        spaceAfter=0,
    )
    company = ParagraphStyle(
        "BarcodeTableSummaryCompany",
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=16,
        textColor=colors.HexColor("#00421E"),
        spaceBefore=0,
        spaceAfter=0,
    )
    period = ParagraphStyle(
        "BarcodeTableSummaryPeriod",
        fontName=text_font,
        fontSize=8.5,
        leading=9.5,
        textColor=colors.HexColor("#303030"),
        spaceBefore=0,
        spaceAfter=0,
    )
    source = ParagraphStyle(
        "BarcodeTableSummarySource",
        fontName=text_font,
        fontSize=7.5,
        leading=8.5,
        textColor=colors.HexColor("#606060"),
        spaceBefore=0,
        spaceAfter=0,
    )
    metric_label = ParagraphStyle(
        "BarcodeTableSummaryMetricLabel",
        fontName="Helvetica-Bold",
        fontSize=7.2,
        leading=8,
        alignment=TA_LEFT,
        textColor=colors.black,
        spaceBefore=0,
        spaceAfter=0,
    )
    metric_value = ParagraphStyle(
        "BarcodeTableSummaryMetricValue",
        fontName=text_font,
        fontSize=9.5,
        leading=11,
        alignment=TA_LEFT,
        textColor=colors.black,
        spaceBefore=0,
        spaceAfter=0,
    )
    status = ParagraphStyle(
        "BarcodeTableSummaryStatus",
        fontName=text_font,
        fontSize=7,
        leading=8,
        textColor=colors.black,
        spaceBefore=0,
        spaceAfter=0,
    )
    metrics = (
        ("Product Rows", str(summary.product_rows)),
        ("Total Qty", str(summary.total_quantity)),
        ("Total Original Sales", _format_money(summary.total_original_sales)),
        ("Total Discount Given", _format_money(summary.total_discount_given)),
        ("Total Amount", _format_money(summary.total_amount)),
    )
    metric_table = Table(
        [
            [Paragraph(escape(label), metric_label) for label, _value in metrics],
            [Paragraph(escape(value), metric_value) for _label, value in metrics],
        ],
        colWidths=(TABLE_CONTENT_WIDTH / 5,) * 5,
        rowHeights=(3.4 * mm, 5.6 * mm),
        hAlign="LEFT",
    )
    metric_table.setStyle(
        TableStyle(
            (
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.black),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1.1 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            )
        )
    )
    title_and_company = Table(
        [
            [Paragraph("Zenxin Agriculture Sdn Bhd", company)],
            [Paragraph("WEEKLY BILLING PRODUCT SUMMARY", title)],
            [Paragraph(f"Sales Period: {escape(summary.sales_period)}", period)],
            [Paragraph("Source: Shopee Weekly Statement", source)],
        ],
        colWidths=(TABLE_CONTENT_WIDTH - 38 * mm,),
        rowHeights=(5.8 * mm, 5.2 * mm, 3.8 * mm, 3.3 * mm),
        hAlign="LEFT",
    )
    title_and_company.setStyle(
        TableStyle(
            (
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            )
        )
    )
    branding_row = Table(
        [[title_and_company, _branding_logo_flowable()]],
        colWidths=(TABLE_CONTENT_WIDTH - 38 * mm, 38 * mm),
        rowHeights=(24 * mm,),
        hAlign="LEFT",
    )
    branding_row.setStyle(
        TableStyle(
            (
                ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                ("VALIGN", (1, 0), (1, 0), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            )
        )
    )
    summary_table = Table(
        [
            [branding_row],
            [metric_table],
            [
                Paragraph(
                    "Barcode Ready: "
                    f"{summary.barcode_ready}    |    "
                    f"Barcode Unavailable: {summary.barcode_unavailable}",
                    status,
                )
            ],
        ],
        colWidths=(TABLE_CONTENT_WIDTH,),
        rowHeights=(24 * mm, 8.5 * mm, 3.5 * mm),
        hAlign="LEFT",
    )
    summary_table.setStyle(
        TableStyle(
            (
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            )
        )
    )
    return summary_table


def _branding_logo_flowable() -> Image:
    """Load the version-controlled Zenxin logo at its natural aspect ratio."""
    if not BRANDING_LOGO_PATH.is_file():
        raise RuntimeError(
            "Zenxin branding logo is unavailable: expected "
            f"{BRANDING_LOGO_PATH}."
        )
    image_width, image_height = ImageReader(str(BRANDING_LOGO_PATH)).getSize()
    if image_width <= 0 or image_height <= 0:
        raise RuntimeError(f"Zenxin branding logo is invalid: {BRANDING_LOGO_PATH}.")
    return Image(
        str(BRANDING_LOGO_PATH),
        width=BRANDING_LOGO_WIDTH,
        height=BRANDING_LOGO_WIDTH * image_height / image_width,
        mask="auto",
    )


def _format_money(value: Decimal) -> str:
    return f"RM {value:,.2f}"
