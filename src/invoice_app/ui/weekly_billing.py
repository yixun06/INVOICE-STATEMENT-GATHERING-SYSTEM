"""Business-facing Weekly Billing Product Summary over committed UAT2 data."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.invoice_app.domain.weekly_billing import WeeklyBillingSummary
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.uat2_data_settings import (
    configured_uat2_data_settings,
)
from src.invoice_app.services.weekly_billing import (
    WeeklyBillingDataset,
    WeeklyBillingError,
    build_weekly_billing_summary,
)
from src.invoice_app.services.weekly_billing_export import (
    export_weekly_billing_summary,
)


WEEKLY_BILLING_PAGE = "Weekly Billing"
PRODUCT_SUMMARY_COLUMNS = (
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


@st.cache_data(ttl=45, show_spinner=False)
def _load_weekly_billing_dataset() -> WeeklyBillingDataset:
    settings = configured_uat2_data_settings()
    return settings.create_weekly_billing_reader().load_dataset()


def render_weekly_billing(
    dataset: WeeklyBillingDataset | None = None,
) -> None:
    """Render one summary model for both preview and downloadable Excel."""

    st.title(WEEKLY_BILLING_PAGE)
    st.caption(
        "Product Summary from an existing committed Shopee Statement period."
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
        summary = build_weekly_billing_summary(source, period)
    except WeeklyBillingError as error:
        st.error(f"Weekly Billing controls failed: {error}")
        return

    _render_metrics(summary)
    st.subheader("Product Summary")
    st.dataframe(
        _summary_frame(summary),
        hide_index=True,
        key="weekly_billing_product_summary",
        column_config={
            "No.": st.column_config.NumberColumn("No.", format="%d"),
            "Item/Barcode": st.column_config.TextColumn("Item/Barcode"),
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
    export_bytes = export_weekly_billing_summary(summary)
    st.download_button(
        "Export Excel",
        export_bytes,
        file_name=(
            "weekly-billing-product-summary-"
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
                "Item/Barcode": row.nav,
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
