"""Business-facing Weekly Billing Product Summary over committed UAT2 data."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.invoice_app.domain.weekly_billing import (
    WeeklyBillingFinancialSummary,
    WeeklyBillingReport,
    WeeklyBillingSummary,
)
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.uat2_data_settings import (
    configured_uat2_data_settings,
)
from src.invoice_app.services.product_master_source import (
    ProductMasterSourceError,
    load_configured_product_price_master,
)
from src.invoice_app.services.weekly_billing import (
    WeeklyBillingDataset,
    WeeklyBillingError,
    build_weekly_billing_report,
)
from src.invoice_app.services.weekly_billing_export import (
    export_weekly_billing_report,
)
from src.invoice_app.services.weekly_billing_staging import StagingDataError


WEEKLY_BILLING_PAGE = "Weekly Billing"
PRODUCT_SUMMARY_COLUMNS = (
    "No.",
    "SKU Code",
    "NAV",
    "Description",
    "Qty",
    "UOM",
    "Unit Price",
    "Dis%",
    "Disc Amt",
    "Amount",
)


@st.cache_data(ttl=45, show_spinner=False)
def _load_weekly_billing_dataset() -> WeeklyBillingDataset:
    settings = configured_uat2_data_settings()
    return settings.create_weekly_billing_reader().load_dataset()


def render_weekly_billing(
    dataset: WeeklyBillingDataset | None = None,
) -> None:
    """Render one committed-period report for preview and downloadable Excel."""

    st.title(WEEKLY_BILLING_PAGE)
    st.caption(
        "Product and Financial Summary from one committed Shopee Statement period."
    )
    try:
        source = dataset if dataset is not None else _load_weekly_billing_dataset()
    except (HistoricalInvoiceStorageError, WeeklyBillingError) as error:
        st.error(f"Weekly Billing is unavailable: {error}")
        return
    if not source.periods:
        st.info("No committed Statement period is available for Weekly Billing.")
        return

    period = st.selectbox(
        "Statement Period",
        source.periods,
        format_func=lambda value: value.label,
        key="weekly_billing_statement_period",
    )
    try:
        report = build_weekly_billing_report(source, period)
    except WeeklyBillingError as error:
        st.error(f"Weekly Billing controls failed: {error}")
        return

    _render_metrics(report.product_summary)
    st.subheader("Product Summary")
    st.dataframe(
        _summary_frame(report.product_summary),
        hide_index=True,
        key="weekly_billing_product_summary",
        column_config={
            "No.": st.column_config.NumberColumn("No.", format="%d"),
            "SKU Code": st.column_config.TextColumn("SKU Code"),
            "NAV": st.column_config.TextColumn("NAV"),
            "Description": st.column_config.TextColumn("Description"),
            "Qty": st.column_config.NumberColumn("Qty", format="%d"),
            "UOM": st.column_config.TextColumn("UOM"),
            "Unit Price": st.column_config.NumberColumn(
                "Unit Price", format="RM %.2f"
            ),
            "Dis%": st.column_config.TextColumn("Dis%"),
            "Disc Amt": st.column_config.NumberColumn("Disc Amt", format="RM %.2f"),
            "Amount": st.column_config.NumberColumn("Amount", format="RM %.2f"),
        },
    )
    st.subheader("Financial Summary")
    st.dataframe(
        _financial_summary_frame(report.financial_summary),
        hide_index=True,
        key="weekly_billing_financial_summary",
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Amount": st.column_config.NumberColumn("Amount", format="RM %.2f"),
        },
    )
    _render_financial_readiness(report)
    try:
        product_master, _source_label = load_configured_product_price_master()
    except ProductMasterSourceError as error:
        st.error(f"Weekly Billing export is unavailable: {error}")
        return
    try:
        export_bytes = export_weekly_billing_report(
            report,
            product_master_records=product_master.records,
        )
    except StagingDataError as error:
        st.error(f"Weekly Billing export is unavailable: {error}")
        return
    st.download_button(
        "Export Weekly Billing Excel",
        export_bytes,
        file_name=(
            "weekly-billing-"
            f"{period.statement_period_from.isoformat()}-to-"
            f"{period.statement_period_to.isoformat()}.xlsx"
        ),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="weekly_billing_export",
        icon=":material/download:",
        type="primary",
    )


def _render_metrics(summary: WeeklyBillingSummary) -> None:
    with st.container(horizontal=True, gap="small"):
        st.metric("Orders", f"{summary.order_count:,}", border=True)
        st.metric("Products", f"{len(summary.product_rows):,}", border=True)
        st.metric("Total Quantity", f"{summary.total_quantity:,}", border=True)
        st.metric(
            "Total Amount",
            f"RM {summary.total_amount:,.2f}",
            border=True,
        )


def _summary_frame(summary: WeeklyBillingSummary) -> pd.DataFrame:
    return pd.DataFrame(
        (
            {
                "No.": row.number,
                "SKU Code": row.sku_code,
                "NAV": row.nav,
                "Description": row.product_name,
                "Qty": row.quantity,
                "UOM": row.uom,
                "Unit Price": float(row.unit_price),
                "Dis%": row.discount_percent,
                "Disc Amt": float(row.discount_amount),
                "Amount": float(row.amount),
            }
            for row in summary.product_rows
        ),
        columns=PRODUCT_SUMMARY_COLUMNS,
    )


def _financial_summary_frame(summary: WeeklyBillingFinancialSummary) -> pd.DataFrame:
    return pd.DataFrame(
        (
            {
                "Description": (
                    f"  {row.native_label}"
                    if row.parent_source_row_number is not None
                    else row.native_label
                ),
                "Amount": None if row.amount is None else float(row.amount),
            }
            for row in summary.rows
        ),
        columns=("Description", "Amount"),
    )


def _render_financial_readiness(report: WeeklyBillingReport) -> None:
    controls = report.financial_summary.controls
    st.caption(
        "Financial controls passed: "
        f"{sum(control.passed for control in controls)}/{len(controls)} "
        "(native Summary compared with ORDER evidence)."
    )
