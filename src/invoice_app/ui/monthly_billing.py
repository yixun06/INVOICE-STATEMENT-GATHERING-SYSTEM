"""Business-facing Monthly Billing over committed full-calendar-month UAT2 data."""

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
from src.invoice_app.services.market_context import (
    BILLING_PLATFORM_OPTIONS,
    MarketConfigurationUnavailable,
    MarketContext,
    MarketKey,
    SHOPEE_MY,
    resolve_market_context,
)
from src.invoice_app.services.monthly_billing import (
    MONTHLY_SOURCE_TABS,
    MonthlyBillingError,
    build_monthly_billing_report,
    month_label,
)
from src.invoice_app.services.monthly_billing_export import (
    export_monthly_product_summary_barcode_table_pdf,
)
from src.invoice_app.services.product_master_source import (
    ProductMasterSourceError,
    load_configured_product_price_master,
)
from src.invoice_app.utils.monetary import format_currency, streamlit_currency_format
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings
from src.invoice_app.services.weekly_billing_barcode_export import (
    summarize_product_summary_barcodes,
)
from src.invoice_app.services.weekly_billing import WeeklyBillingDataset
from src.invoice_app.services.weekly_billing_export import export_weekly_billing_report
from src.invoice_app.services.weekly_billing_staging import (
    StagingDataError,
)


MONTHLY_BILLING_PAGE = "Monthly Billing"
PRODUCT_SUMMARY_COLUMNS = (
    "No.",
    "SKU Code",
    "NAV",
    "Description",
    "Qty",
    "UOM",
    "Unit Price",
    "Original Sales",
    "Disc Amt",
    "Amount",
)


@st.cache_data(ttl=45, show_spinner=False)
def _load_monthly_billing_dataset(
    context: MarketContext | MarketKey | str = SHOPEE_MY,
    spreadsheet_id: str = "",
    source_contract: tuple[str, ...] = MONTHLY_SOURCE_TABS,
) -> WeeklyBillingDataset:
    """Cache one market- and source-contract-scoped Monthly source snapshot."""

    if source_contract != MONTHLY_SOURCE_TABS:
        raise MonthlyBillingError("Monthly Billing source contract is unsupported.")
    settings = configured_uat2_data_settings(resolve_market_context(context))
    if spreadsheet_id != settings.google_spreadsheet_id:
        raise MonthlyBillingError("Monthly Billing spreadsheet identity changed during load.")
    return settings.create_monthly_billing_reader().load_dataset()


def render_monthly_billing(
    dataset: WeeklyBillingDataset | None = None,
    *,
    market_context: MarketContext | MarketKey | str = SHOPEE_MY,
) -> None:
    """Render one committed Monthly statement without touching the Weekly UI path."""

    st.title(MONTHLY_BILLING_PAGE)
    requested_context = resolve_market_context(market_context)
    platform_col, month_col = st.columns(2, gap="small")
    with platform_col:
        selected_platform = st.selectbox(
            "Platform",
            BILLING_PLATFORM_OPTIONS,
            index=BILLING_PLATFORM_OPTIONS.index(requested_context.display_name),
            key="monthly_billing_platform",
        )
    context = resolve_market_context(selected_platform)
    if not context.weekly_billing_available:
        st.info(f"{context.display_name} billing is not configured yet.")
        return
    st.caption(f"Platform: {context.display_name}")
    st.caption(
        "Product and Financial Summary from one committed full-calendar-month Shopee Statement."
    )
    try:
        if dataset is not None:
            source = dataset
        else:
            settings = configured_uat2_data_settings(context)
            source = _load_monthly_billing_dataset(
                context,
                settings.google_spreadsheet_id,
                MONTHLY_SOURCE_TABS,
            )
    except (
        HistoricalInvoiceStorageError,
        MarketConfigurationUnavailable,
        MonthlyBillingError,
    ) as error:
        st.error(f"Monthly Billing is unavailable: {error}")
        return
    if not source.periods:
        st.info("No committed full-calendar-month Statement is available for Monthly Billing.")
        return

    with month_col:
        period = st.selectbox(
            "Month",
            source.periods,
            format_func=month_label,
            key="monthly_billing_month",
        )
    try:
        report = build_monthly_billing_report(source, period)
    except MonthlyBillingError as error:
        st.error(f"Monthly Billing controls failed: {error}")
        return

    _render_metrics(report.product_summary)
    st.subheader("Product Summary")
    st.dataframe(
        _summary_frame(report.product_summary),
        hide_index=True,
        key="monthly_billing_product_summary",
        column_config=_product_summary_column_config(),
    )
    st.subheader("Financial Summary")
    st.dataframe(
        _financial_summary_frame(report.financial_summary),
        hide_index=True,
        key="monthly_billing_financial_summary",
        column_config={
            "Description": st.column_config.TextColumn("Description"),
            "Amount": st.column_config.NumberColumn(
                "Amount", format=streamlit_currency_format()
            ),
        },
    )
    _render_financial_readiness(report)
    _render_barcode_export(report.product_summary)

    try:
        product_master, _source_label = load_configured_product_price_master()
    except ProductMasterSourceError as error:
        st.error(f"Monthly Billing export is unavailable: {error}")
        return
    try:
        export_bytes = export_weekly_billing_report(
            report,
            product_master_records=product_master.records,
        )
    except StagingDataError as error:
        st.error(f"Monthly Billing export is unavailable: {error}")
        return
    st.download_button(
        "Export Monthly Billing Excel",
        export_bytes,
        file_name=f"Monthly_Billing_{period.statement_period_from:%Y-%m}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="monthly_billing_export",
        icon=":material/download:",
        type="primary",
    )


def _render_barcode_export(summary: WeeklyBillingSummary) -> None:
    barcode_summary = summarize_product_summary_barcodes(summary)
    st.caption(
        "Barcode labels: "
        f"{barcode_summary.label_count:,} | "
        f"Valid EAN-13: {barcode_summary.valid_ean13_count:,} | "
        f"Barcode unavailable: {barcode_summary.unavailable_count:,}"
    )
    try:
        barcode_table_pdf = export_monthly_product_summary_barcode_table_pdf(summary)
    except (RuntimeError, ValueError) as error:
        st.error(f"Monthly Product Summary Barcode PDF is unavailable: {error}")
        return
    period = summary.period
    st.download_button(
        "Export Product Summary Barcode PDF",
        barcode_table_pdf,
        file_name=f"Monthly_Billing_Product_Summary_Barcodes_{period.statement_period_from:%Y%m}.pdf",
        mime="application/pdf",
        key="monthly_billing_product_summary_barcode_pdf_export",
        icon=":material/table_view:",
    )


def _render_metrics(summary: WeeklyBillingSummary) -> None:
    with st.container(horizontal=True, gap="small"):
        st.metric("Orders", f"{summary.order_count:,}", border=True)
        st.metric("Products", f"{len(summary.product_rows):,}", border=True)
        st.metric("Total Quantity", f"{summary.total_quantity:,}", border=True)
        st.metric(
            "Total Amount",
            format_currency(summary.total_amount),
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
                "Original Sales": float(row.original_sales),
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
        "(native Monthly Summary compared with ORDER evidence)."
    )


def _product_summary_column_config() -> dict[str, object]:
    return {
        "No.": st.column_config.NumberColumn("No.", format="%d"),
        "SKU Code": st.column_config.TextColumn("SKU Code"),
        "NAV": st.column_config.TextColumn("NAV"),
        "Description": st.column_config.TextColumn("Description"),
        "Qty": st.column_config.NumberColumn("Qty", format="%d"),
        "UOM": st.column_config.TextColumn("UOM"),
        "Unit Price": st.column_config.NumberColumn(
            "Unit Price", format=streamlit_currency_format()
        ),
        "Original Sales": st.column_config.NumberColumn(
            "Original Sales", format=streamlit_currency_format()
        ),
        "Disc Amt": st.column_config.NumberColumn(
            "Disc Amt", format=streamlit_currency_format()
        ),
        "Amount": st.column_config.NumberColumn(
            "Amount", format=streamlit_currency_format()
        ),
    }
