from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest

from src.invoice_app.domain.weekly_billing import (
    BillingPeriod,
    FinancialSummaryRow,
    ProductSummaryRow,
    WeeklyBillingFinancialSummary,
    WeeklyBillingReport,
    WeeklyBillingSummary,
)
from src.invoice_app.services.weekly_billing_export import (
    PRODUCT_SUMMARY_HEADERS,
    export_weekly_billing_report,
)
from src.invoice_app.services.weekly_billing_staging import (
    STAGING_DATA_HEADERS,
    StagingDataError,
    build_staging_data_rows,
)


PERIOD = BillingPeriod(
    date(2026, 8, 31),
    date(2026, 9, 6),
    "batch-golden",
    "a" * 64,
)
GENERATION_DATE = date(2026, 9, 15)


def _product_rows() -> tuple[ProductSummaryRow, ...]:
    rows = [
        ProductSummaryRow(
            number=1,
            nav="5000000",
            product_name="Oil | 1000ml",
            uom=None,
            unit_price=Decimal("42.90"),
            quantity=539,
            discount_percent=None,
            discount_amount=Decimal("0.00"),
            amount=Decimal("23123.10"),
            source_item_count=1,
        ),
        ProductSummaryRow(
            number=2,
            nav="5000000",
            product_name="Oil | 1000ml",
            uom=None,
            unit_price=Decimal("43.90"),
            quantity=1,
            discount_percent=None,
            discount_amount=Decimal("0.00"),
            amount=Decimal("43.90"),
            source_item_count=1,
        ),
        ProductSummaryRow(
            number=3,
            nav="000123",
            product_name="Identity fallback row | Original",
            uom=None,
            unit_price=Decimal("10.00"),
            quantity=1,
            discount_percent=None,
            discount_amount=Decimal("0.00"),
            amount=Decimal("10.00"),
            source_item_count=1,
        ),
    ]
    rows.extend(
        ProductSummaryRow(
            number=number,
            nav=str(5000000 + number),
            product_name=f"Product {number}",
            uom=None,
            unit_price=Decimal("1.00"),
            quantity=1,
            discount_percent=None,
            discount_amount=Decimal("0.00"),
            amount=Decimal("1.00"),
            source_item_count=1,
        )
        for number in range(4, 178)
    )
    return tuple(rows)


def _summary() -> WeeklyBillingSummary:
    product_rows = _product_rows()
    return WeeklyBillingSummary(
        period=PERIOD,
        order_count=296,
        invoice_item_count=442,
        source_items=(),
        product_rows=product_rows,
        total_quantity=sum(row.quantity for row in product_rows),
        total_standard_amount=sum(
            (row.unit_price * row.quantity for row in product_rows),
            Decimal("0.00"),
        ),
        total_discount_amount=Decimal("0.00"),
        total_amount=sum((row.amount for row in product_rows), Decimal("0.00")),
        normal_amount_total=Decimal("0.00"),
        promotion_amount_total=Decimal("0.00"),
        promotion_group_count=0,
        same_price_promotion_count=0,
        mixed_price_promotion_count=0,
    )


def _report() -> WeeklyBillingReport:
    financial = WeeklyBillingFinancialSummary(
        period=PERIOD,
        currency="MYR",
        rows=(
            FinancialSummaryRow(
                statement_source_row_number=1,
                native_label="1. Total Revenue",
                line_type="TOTAL",
                parent_source_row_number=None,
                amount=Decimal("14767.32"),
                currency="MYR",
            ),
        ),
        controls=(),
        export_ready=True,
        validation_failures=(),
    )
    return WeeklyBillingReport(_summary(), financial)


def test_staging_mapper_is_one_to_one_and_conserves_golden_quantity():
    summary = _summary()

    rows = build_staging_data_rows(summary, generation_date=GENERATION_DATE)

    assert len(summary.product_rows) == len(rows) == 177
    assert summary.total_quantity == sum(row.quantity for row in rows) == 715
    assert {row.your_reference for row in rows} == {"DF20260831-20260906"}
    assert {row.posting_date for row in rows} == {GENERATION_DATE}
    assert {row.order_date for row in rows} == {GENERATION_DATE}
    assert {row.shipment_date for row in rows} == {GENERATION_DATE}
    assert (rows[0].nav, rows[0].quantity, rows[0].unit_price_rsp_excl_gst) == (
        summary.product_rows[0].nav,
        summary.product_rows[0].quantity,
        summary.product_rows[0].unit_price,
    )
    assert [(row.nav, row.unit_price_rsp_excl_gst) for row in rows[:2]] == [
        ("5000000", Decimal("42.90")),
        ("5000000", Decimal("43.90")),
    ]
    assert rows[2].nav == "000123"


def test_staging_mapper_applies_exact_erp_constants_and_blank_outlet():
    row = build_staging_data_rows(
        _summary(), generation_date=GENERATION_DATE
    )[0]

    assert (
        row.sell_to_customer_no,
        row.currency_code,
        row.item_type,
        row.location_code,
        row.unit_of_measure_code,
        row.customer_outlet_code,
        row.business_unit_code_erp,
        row.project_code_erp,
        row.external_doc_no,
        row.transfer_to_code,
    ) == ("HC001543", "MYR", "ITEM", "JH02", "EA", None, "RETAIL", "JH02", 0, 0)


def test_staging_mapper_fails_when_quantity_control_differs():
    summary = _summary()

    with pytest.raises(StagingDataError, match="Quantity total control"):
        build_staging_data_rows(
            replace(summary, total_quantity=714),
            generation_date=GENERATION_DATE,
        )


def test_unified_workbook_writes_exact_staging_contract_and_types():
    report = _report()
    workbook = openpyxl.load_workbook(
        BytesIO(
            export_weekly_billing_report(
                report,
                generation_date=GENERATION_DATE,
            )
        ),
        data_only=True,
    )

    assert workbook.sheetnames == ["Product Summary", "Staging Data", "Financial Summary"]
    product = workbook["Product Summary"]
    staging = workbook["Staging Data"]
    financial = workbook["Financial Summary"]
    assert tuple(cell.value for cell in product[1]) == PRODUCT_SUMMARY_HEADERS
    assert tuple(cell.value for cell in staging[1]) == (
        "Your Reference",
        "Posting Date",
        "Sell-To Customer No.",
        "Currency Code",
        "Type",
        "No.",
        "Location Code",
        "Quantity",
        "Unit of Measure Code",
        "Unit Price [RSP] Excl.GST",
        "Order Date",
        "Shipment Date",
        "External Doc No.",
        "Customer Outlet Code",
        "Business Unit Code ERP",
        "Project Code ERP",
        "Transfer-to Code",
        "Customer ",
        "Customer",
        "USOFT - USOFT CODE",
        "USOFT product description",
        "Plan Date",
        "AM/PM",
        "TYPE",
        "Qty. per Unit of Measure",
        "Line Discount %",
    )
    assert staging.max_row == product.max_row == 178
    assert staging.max_column == 26
    assert staging.freeze_panes is None
    assert staging.auto_filter.ref is None
    assert staging.column_dimensions["A"].width == pytest.approx(20.28515625)
    assert staging.column_dimensions["I"].width == pytest.approx(20.7109375)
    assert staging.column_dimensions["U"].width == pytest.approx(55.140625)
    assert staging.cell(1, 1).font.name == "Calibri"
    assert staging.cell(1, 1).font.sz == 11
    assert staging.cell(1, 1).font.bold is True
    assert staging.cell(1, 1).alignment.horizontal == "left"
    assert staging.cell(1, 17).alignment.horizontal is None
    assert staging.cell(2, 1).value == "DF20260831-20260906"
    assert staging.cell(2, 2).value.date() == GENERATION_DATE
    assert staging.cell(2, 11).value.date() == GENERATION_DATE
    assert staging.cell(2, 12).value.date() == GENERATION_DATE
    assert staging.cell(2, 6).value == "5000000"
    assert staging.cell(3, 6).value == "5000000"
    assert staging.cell(4, 6).value == "000123"
    assert staging.cell(2, 8).value == 539
    assert staging.cell(2, 10).value == 42.9
    assert staging.cell(3, 10).value == 43.9
    assert staging.cell(2, 14).value is None
    assert staging.cell(2, 17).value == 0
    assert all(staging.cell(2, column).value is None for column in range(18, 27))
    assert staging.cell(2, 2).data_type == "d"
    assert staging.cell(2, 6).data_type == "s"
    assert staging.cell(2, 8).data_type == "n"
    assert staging.cell(2, 10).data_type == "n"
    assert staging.cell(2, 2).number_format == "yyyy\\-mm\\-dd"
    assert staging.cell(2, 6).number_format == "General"
    assert staging.cell(2, 8).number_format == "General"
    assert staging.cell(2, 10).number_format == "General"
    assert staging.cell(2, 22).number_format == "yyyy\\-mm\\-dd"
    assert staging.cell(2, 5).font.name == "Arial"
    assert staging.cell(2, 5).font.sz == 10
    assert staging.cell(2, 5).alignment.horizontal == "left"
    assert staging.cell(2, 5).alignment.vertical == "top"
    assert sum(staging.cell(row, 8).value for row in range(2, 179)) == 715
    assert product.cell(2, 2).value == "5000000"
    assert product.cell(2, 6).value == 42.9
    assert financial.cell(2, 2).value == 14767.32
