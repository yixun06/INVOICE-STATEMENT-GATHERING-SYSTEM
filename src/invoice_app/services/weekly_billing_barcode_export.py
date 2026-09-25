"""Vector EAN-13 barcode labels for finalized Weekly Billing Product Summary."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.eanbc import Ean13BarcodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen.canvas import Canvas

from src.invoice_app.domain.weekly_billing import ProductSummaryRow, WeeklyBillingSummary


# TEMPLATE V0.1: provisional single-label page dimensions, intentionally isolated
# until the Product Owner confirms the physical printer/media specification.
LABEL_WIDTH = 100 * mm
LABEL_HEIGHT = 80 * mm
LABEL_MARGIN = 6 * mm
BARCODE_BAR_WIDTH = 0.33 * mm
BARCODE_HEIGHT = 20 * mm
BARCODE_TOP_GAP = 5 * mm
BARCODE_SKU_FONT_SIZE = 9
FIELD_FONT_SIZE = 9
PRODUCT_NAME_FONT_SIZE = 9.0
PRODUCT_NAME_MIN_FONT_SIZE = 1.0
PRODUCT_NAME_FONT_STEP = 0.25
PRODUCT_NAME_LEADING_RATIO = 1.12
# This is the fixed vertical space above the divider and field block.  The
# physical label and all required business fields remain unchanged.
PRODUCT_NAME_AREA_HEIGHT = 13.5 * mm
LINE_SPACING = 3.8 * mm
LABEL_TEXT_FONT = "WeeklyBillingBarcodeNotoSansSC"
# This open-source Simplified-Chinese font is deployed with the application;
# do not replace it with a host font fallback.  A host fallback could produce
# tofu on a clean Linux Streamlit deployment and would alter fitting metrics.
LABEL_TEXT_FONT_PATH = (
    Path(__file__).resolve().parents[3]
    / "assets"
    / "fonts"
    / "NotoSansSC-Variable.ttf"
)


@dataclass(frozen=True)
class BarcodeExportSummary:
    """Non-blocking barcode availability summary for one final Product Summary."""

    label_count: int
    valid_ean13_count: int
    unavailable_count: int
    unavailable_sku_codes: tuple[str, ...]


@dataclass(frozen=True)
class ProductNameLayout:
    """The deterministic on-label layout for one immutable Description."""

    font_size: float
    leading: float
    lines: tuple[str, ...]


@dataclass(frozen=True)
class ProductNameLayoutSummary:
    """Description fitting facts for one final Product Summary."""

    smallest_font_size: float
    maximum_line_count: int


def is_valid_ean13(value: str) -> bool:
    """Return whether the trimmed value is a complete EAN-13 with a valid check digit."""

    sku = value.strip()
    if len(sku) != 13 or not sku.isdigit():
        return False
    digits = [int(character) for character in sku]
    expected_check_digit = (10 - (sum(
        digit * (3 if index % 2 else 1)
        for index, digit in enumerate(digits[:12])
    ) % 10)) % 10
    return digits[-1] == expected_check_digit


def summarize_product_summary_barcodes(
    product_summary: WeeklyBillingSummary,
) -> BarcodeExportSummary:
    unavailable = tuple(
        row.sku_code
        for row in product_summary.product_rows
        if not is_valid_ean13(row.sku_code)
    )
    return BarcodeExportSummary(
        label_count=len(product_summary.product_rows),
        valid_ean13_count=len(product_summary.product_rows) - len(unavailable),
        unavailable_count=len(unavailable),
        unavailable_sku_codes=unavailable,
    )


def export_product_summary_barcode_pdf(
    product_summary: WeeklyBillingSummary,
) -> bytes:
    """Render one vector PDF page per final Product Summary row, in existing order."""

    output = BytesIO()
    pdf = Canvas(output, pagesize=(LABEL_WIDTH, LABEL_HEIGHT), pageCompression=1)
    pdf.setTitle("Weekly Billing Product Summary Barcodes")
    for row in product_summary.product_rows:
        _draw_label(pdf, row)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def summarize_product_name_layouts(
    product_summary: WeeklyBillingSummary,
) -> ProductNameLayoutSummary:
    """Report the adaptive Description fitting used without changing any row."""

    text_font = _ensure_label_text_font()
    layouts = tuple(
        _fit_product_name(row.product_name, text_font)
        for row in product_summary.product_rows
    )
    if not layouts:
        return ProductNameLayoutSummary(
            smallest_font_size=PRODUCT_NAME_FONT_SIZE,
            maximum_line_count=0,
        )
    return ProductNameLayoutSummary(
        smallest_font_size=min(layout.font_size for layout in layouts),
        maximum_line_count=max(len(layout.lines) for layout in layouts),
    )


def _draw_label(pdf: Canvas, row: ProductSummaryRow) -> None:
    label_text_font = _ensure_label_text_font()
    pdf.setStrokeColorRGB(0, 0, 0)
    pdf.setLineWidth(1)
    pdf.rect(0.5 * mm, 0.5 * mm, LABEL_WIDTH - mm, LABEL_HEIGHT - mm)

    top = LABEL_HEIGHT - LABEL_MARGIN
    barcode_bottom = top - BARCODE_TOP_GAP - BARCODE_HEIGHT
    sku = row.sku_code.strip()
    if is_valid_ean13(sku):
        _draw_ean13(pdf, sku, LABEL_MARGIN, barcode_bottom)
    else:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(LABEL_MARGIN, barcode_bottom + BARCODE_HEIGHT / 2, "Barcode unavailable")

    pdf.setFont("Helvetica", BARCODE_SKU_FONT_SIZE)
    pdf.drawString(LABEL_MARGIN, barcode_bottom - 4 * mm, row.sku_code)
    barcode_divider_y = barcode_bottom - 6 * mm
    pdf.line(LABEL_MARGIN, barcode_divider_y, LABEL_WIDTH - LABEL_MARGIN, barcode_divider_y)

    product_top = barcode_divider_y - 3.5 * mm
    product_bottom = _draw_product_name(
        pdf,
        row.product_name,
        product_top,
        label_text_font,
    )
    product_divider_y = product_bottom - 2 * mm
    pdf.line(LABEL_MARGIN, product_divider_y, LABEL_WIDTH - LABEL_MARGIN, product_divider_y)

    field_y = product_divider_y - 4.5 * mm
    for label, value in (
        ("NO", str(row.number)),
        ("QUANTITY", str(row.quantity)),
        ("UOM", row.uom or ""),
        ("SALES AMOUNT", _format_money(row.amount)),
        ("DISCOUNT AMOUNT", _format_money(row.discount_amount)),
    ):
        pdf.setFont("Helvetica-Bold", FIELD_FONT_SIZE)
        pdf.drawString(LABEL_MARGIN, field_y, f"{label}:")
        label_width = stringWidth(f"{label}: ", "Helvetica-Bold", FIELD_FONT_SIZE)
        pdf.setFont("Helvetica", FIELD_FONT_SIZE)
        pdf.drawString(LABEL_MARGIN + label_width, field_y, value)
        field_y -= LINE_SPACING


def _draw_ean13(pdf: Canvas, sku: str, x: float, y: float) -> None:
    barcode = Ean13BarcodeWidget(sku)
    barcode.barWidth = BARCODE_BAR_WIDTH
    barcode.barHeight = BARCODE_HEIGHT
    barcode.humanReadable = False
    _left, _bottom, right, top = barcode.getBounds()
    drawing = Drawing(right, top)
    drawing.add(barcode)
    renderPDF.draw(drawing, pdf, x, y)


def _draw_product_name(
    pdf: Canvas,
    product_name: str,
    top: float,
    text_font: str,
) -> float:
    layout = _fit_product_name(product_name, text_font)
    prefix = "PRODUCT NAME: "
    prefix_width = stringWidth(prefix, "Helvetica-Bold", layout.font_size)
    y = top
    for index, line in enumerate(layout.lines):
        if index == 0:
            pdf.setFont("Helvetica-Bold", layout.font_size)
            pdf.drawString(LABEL_MARGIN, y, prefix)
            x = LABEL_MARGIN + prefix_width
        else:
            x = LABEL_MARGIN
        pdf.setFont(text_font, layout.font_size)
        pdf.drawString(x, y, line)
        y -= layout.leading
    return y


def _fit_product_name(product_name: str, text_font: str) -> ProductNameLayout:
    """Fit a whole Description into the reserved label area without omission."""

    available_width = LABEL_WIDTH - 2 * LABEL_MARGIN
    font_size = PRODUCT_NAME_FONT_SIZE
    while font_size >= PRODUCT_NAME_MIN_FONT_SIZE:
        prefix_width = stringWidth("PRODUCT NAME: ", "Helvetica-Bold", font_size)
        lines = _wrap_text(
            product_name,
            available_width,
            font_size,
            text_font,
            first_line_reserved_width=prefix_width,
        )
        leading = font_size * PRODUCT_NAME_LEADING_RATIO
        if len(lines) * leading <= PRODUCT_NAME_AREA_HEIGHT:
            return ProductNameLayout(
                font_size=font_size,
                leading=leading,
                lines=tuple(lines),
            )
        font_size -= PRODUCT_NAME_FONT_STEP
    # A normal Product Summary Description reaches a readable fitting size
    # above.  This fail-closed error is reserved for a genuine physical-label
    # impossibility, not the former four-line policy.
    raise ValueError(
        "Product Summary Description cannot fit the fixed Barcode PDF label "
        f"at {PRODUCT_NAME_MIN_FONT_SIZE:.2f}pt without truncation."
    )


def _wrap_text(
    text: str,
    available_width: float,
    font_size: float,
    text_font: str,
    *,
    first_line_reserved_width: float,
) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        # A source Description can contain a long unbroken identifier. Split it
        # deterministically so it cannot cross the physical label boundary.
        max_word_width = (
            available_width - first_line_reserved_width
            if not lines and not current
            else available_width
        )
        if stringWidth(word, text_font, font_size) > max_word_width:
            word_parts = _split_word_to_width(
                word,
                max_word_width,
                font_size,
                text_font,
            )
            for part in word_parts[:-1]:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(part)
            word = word_parts[-1]
        candidate = f"{current} {word}".strip()
        current_width = (
            available_width - first_line_reserved_width
            if not lines
            else available_width
        )
        if current and stringWidth(candidate, text_font, font_size) > current_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _split_word_to_width(
    word: str,
    available_width: float,
    font_size: float,
    text_font: str,
) -> list[str]:
    parts: list[str] = []
    current = ""
    for character in word:
        candidate = current + character
        if current and stringWidth(candidate, text_font, font_size) > available_width:
            parts.append(current)
            current = character
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _format_money(value: Decimal) -> str:
    return f"RM {value:,.2f}"


def _ensure_label_text_font() -> str:
    if LABEL_TEXT_FONT in pdfmetrics.getRegisteredFontNames():
        return LABEL_TEXT_FONT
    if not LABEL_TEXT_FONT_PATH.is_file():
        raise RuntimeError(
            "Barcode PDF requires its packaged Noto Sans SC font, but it is "
            f"unavailable: {LABEL_TEXT_FONT_PATH}"
        )
    try:
        pdfmetrics.registerFont(TTFont(LABEL_TEXT_FONT, str(LABEL_TEXT_FONT_PATH)))
    except (OSError, TTFError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Barcode PDF packaged Noto Sans SC font is unavailable or corrupted: "
            f"{LABEL_TEXT_FONT_PATH}"
        ) from error
    return LABEL_TEXT_FONT
