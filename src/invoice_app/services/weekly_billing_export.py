"""Excel export for the shared Weekly Billing summary model."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from src.invoice_app.domain.weekly_billing import WeeklyBillingReport, WeeklyBillingSummary
from src.invoice_app.services.weekly_billing_staging import (
    STAGING_DATA_HEADERS,
    build_staging_data_rows,
)


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
FINANCIAL_SUMMARY_HEADERS = ("Description", "Amount")


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


def export_weekly_billing_report(
    report: WeeklyBillingReport,
    *,
    generation_date: date | None = None,
) -> bytes:
    """Export the exact Product/Financial previews from one committed batch."""

    if report.product_summary.period != report.financial_summary.period:
        raise ValueError("Weekly Billing Product and Financial Summary batches differ.")
    if not report.financial_summary.export_ready:
        raise ValueError("Financial Summary is not export-ready.")
    export_date = generation_date or date.today()
    staging_rows = build_staging_data_rows(
        report.product_summary,
        generation_date=export_date,
    )

    workbook = Workbook()
    product_sheet = workbook.active
    product_sheet.title = "Product Summary"
    _write_product_summary(product_sheet, report.product_summary)

    staging_sheet = workbook.create_sheet("Staging Data")
    _write_staging_data(staging_sheet, staging_rows)

    financial_sheet = workbook.create_sheet("Financial Summary")
    financial_sheet.append(FINANCIAL_SUMMARY_HEADERS)
    for cell in financial_sheet[1]:
        cell.font = Font(bold=True)
    financial_sheet.freeze_panes = "A2"
    financial_sheet.column_dimensions["A"].width = 54
    financial_sheet.column_dimensions["B"].width = 18
    for row in report.financial_summary.rows:
        financial_sheet.append((row.native_label, None if row.amount is None else float(row.amount)))
        excel_row = financial_sheet.max_row
        description = financial_sheet.cell(excel_row, 1)
        amount = financial_sheet.cell(excel_row, 2)
        description.alignment = Alignment(
            horizontal="left",
            vertical="center",
            indent=1 if row.parent_source_row_number is not None else 0,
        )
        if row.amount is not None:
            amount.number_format = '"RM" #,##0.00;[Red]-"RM" #,##0.00'
        if row.line_type == "TOTAL":
            _emphasize_financial_row(financial_sheet, excel_row, bold=True, fill="D9EAD3")
        elif row.line_type == "SUBTOTAL":
            _emphasize_financial_row(financial_sheet, excel_row, bold=True, fill="E2F0D9")
        elif row.line_type == "SECTION_HEADER":
            _emphasize_financial_row(financial_sheet, excel_row, bold=True, fill="D9EAF7")
        elif row.line_type == "REFERENCE":
            _emphasize_financial_row(financial_sheet, excel_row, bold=False, fill="FFF2CC")
    financial_sheet.auto_filter.ref = f"A1:B{financial_sheet.max_row}"

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _write_staging_data(sheet, rows) -> None:
    sheet.append(STAGING_DATA_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:Q{max(1, len(rows) + 1)}"
    for row in rows:
        sheet.append((
            row.your_reference,
            row.posting_date,
            row.sell_to_customer_no,
            row.currency_code,
            row.item_type,
            row.nav,
            row.location_code,
            row.quantity,
            row.unit_of_measure_code,
            row.order_date,
            row.shipment_date,
            row.customer_outlet_code,
            row.business_unit_code_erp,
            row.project_code_erp,
            row.external_doc_no,
            float(row.unit_price_excl_gst),
            row.ship_to_code,
        ))
    for row_number in range(2, sheet.max_row + 1):
        sheet.cell(row_number, 2).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 6).number_format = "@"
        sheet.cell(row_number, 8).number_format = "#,##0"
        sheet.cell(row_number, 10).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 11).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 16).number_format = "#,##0.00"
    for column, width in {
        "A": 27,
        "B": 14,
        "C": 22,
        "D": 15,
        "E": 12,
        "F": 18,
        "G": 16,
        "H": 12,
        "I": 24,
        "J": 14,
        "K": 15,
        "L": 22,
        "M": 24,
        "N": 18,
        "O": 18,
        "P": 22,
        "Q": 16,
    }.items():
        sheet.column_dimensions[column].width = width


def _write_product_summary(sheet, summary: WeeklyBillingSummary) -> None:
    sheet.append(PRODUCT_SUMMARY_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:I{max(1, len(summary.product_rows) + 1)}"
    for row in summary.product_rows:
        sheet.append((
            row.number, row.nav, row.product_name, row.quantity, row.uom,
            float(row.unit_price), row.discount_percent, float(row.discount_amount),
            float(row.amount),
        ))
    for column in (6, 8, 9):
        for cells in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
            cells[0].number_format = '"RM" #,##0.00'
    for column, width in {
        "A": 8, "B": 18, "C": 58, "D": 10, "E": 10,
        "F": 16, "G": 10, "H": 16, "I": 16,
    }.items():
        sheet.column_dimensions[column].width = width


def _emphasize_financial_row(sheet, row: int, *, bold: bool, fill: str) -> None:
    for cell in sheet[row]:
        cell.font = Font(bold=bold)
        cell.fill = PatternFill("solid", fgColor=fill)
