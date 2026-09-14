"""Excel export for the shared Weekly Billing summary model."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from src.invoice_app.domain.weekly_billing import WeeklyBillingSummary


PRODUCT_SUMMARY_HEADERS = (
    "No.",
    "Item/Barcode",
    "Description",
    "Qty",
    "UOM",
    "Unit Price",
    "Dis%",
    "Disc Amt",
    "Amount",
)


def export_weekly_billing_summary(summary: WeeklyBillingSummary) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Product Summary"
    sheet.append(PRODUCT_SUMMARY_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:I{max(1, len(summary.product_rows) + 1)}"

    for row in summary.product_rows:
        sheet.append(
            (
                row.number,
                row.nav,
                row.product_name,
                row.quantity,
                row.uom,
                float(row.unit_price),
                row.discount_percent,
                float(row.discount_amount),
                float(row.amount),
            )
        )
    for column in (6, 8, 9):
        for cells in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
            cells[0].number_format = '"RM" #,##0.00'
    widths = {
        "A": 8,
        "B": 18,
        "C": 58,
        "D": 10,
        "E": 10,
        "F": 16,
        "G": 10,
        "H": 16,
        "I": 16,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
