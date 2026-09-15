"""Deterministic ERP Staging Data projection from final Product Summary rows."""

from __future__ import annotations

from datetime import date

from src.invoice_app.domain.weekly_billing import (
    StagingDataRow,
    WeeklyBillingSummary,
)


STAGING_DATA_HEADERS = (
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
    "Customer Remark",
    "Customer",
    "USOFT - USOFT CODE",
    "USOFT product description",
    "Plan Date",
    "AM/PM",
    "TYPE",
    "Qty. per Unit of Measure",
    "Line Discount %",
)

SELL_TO_CUSTOMER_NO = "HC001543"
CURRENCY_CODE = "MYR"
ITEM_TYPE = "ITEM"
LOCATION_CODE = "JH02"
UNIT_OF_MEASURE_CODE = "EA"
BUSINESS_UNIT_CODE_ERP = "RETAIL"
PROJECT_CODE_ERP = "JH02"
EXTERNAL_DOC_NO = 0
TRANSFER_TO_CODE = 0


class StagingDataError(RuntimeError):
    """Staging Data cannot be proven from the finalized Product Summary."""


def build_staging_data_rows(
    summary: WeeklyBillingSummary,
    *,
    generation_date: date,
) -> tuple[StagingDataRow, ...]:
    """Map every finalized Product Summary row exactly once without regrouping."""

    reference = _your_reference(summary)
    rows = tuple(
        StagingDataRow(
            your_reference=reference,
            posting_date=generation_date,
            sell_to_customer_no=SELL_TO_CUSTOMER_NO,
            currency_code=CURRENCY_CODE,
            item_type=ITEM_TYPE,
            nav=str(product.nav),
            location_code=LOCATION_CODE,
            quantity=product.quantity,
            unit_of_measure_code=UNIT_OF_MEASURE_CODE,
            unit_price_rsp_excl_gst=product.unit_price,
            order_date=generation_date,
            shipment_date=generation_date,
            external_doc_no=EXTERNAL_DOC_NO,
            customer_outlet_code=None,
            business_unit_code_erp=BUSINESS_UNIT_CODE_ERP,
            project_code_erp=PROJECT_CODE_ERP,
            transfer_to_code=TRANSFER_TO_CODE,
            customer_remark=None,
            customer=None,
            usoft_code=None,
            usoft_product_description=None,
            plan_date=None,
            am_pm=None,
            secondary_type=None,
            quantity_per_unit_of_measure=None,
            line_discount_percent=None,
        )
        for product in summary.product_rows
    )
    _validate_staging_rows(summary, rows)
    return rows


def _your_reference(summary: WeeklyBillingSummary) -> str:
    period = summary.period
    if period.statement_period_from > period.statement_period_to:
        raise StagingDataError("Staging Data Statement period is invalid.")
    return (
        f"DF{period.statement_period_from:%Y%m%d}-"
        f"{period.statement_period_to:%Y%m%d}"
    )


def _validate_staging_rows(
    summary: WeeklyBillingSummary,
    rows: tuple[StagingDataRow, ...],
) -> None:
    if len(rows) != len(summary.product_rows):
        raise StagingDataError("Staging Data row-count control failed.")
    for product, staging in zip(summary.product_rows, rows, strict=True):
        if staging.nav != str(product.nav):
            raise StagingDataError("Staging Data No. control failed.")
        if staging.quantity != product.quantity:
            raise StagingDataError("Staging Data Quantity row control failed.")
        if staging.unit_price_rsp_excl_gst != product.unit_price:
            raise StagingDataError("Staging Data Unit Price row control failed.")
    if sum(row.quantity for row in rows) != summary.total_quantity:
        raise StagingDataError("Staging Data Quantity total control failed.")
