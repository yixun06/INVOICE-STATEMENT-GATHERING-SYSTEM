"""Live, read-only Cross Platform Product Summary page."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.cross_platform_product_summary import (
    PLATFORM_OPTIONS,
    CrossPlatformProductSummaryError,
    CrossPlatformReportingSnapshot,
    build_cross_platform_product_summary,
)
from src.invoice_app.services.cross_platform_product_summary_barcode_table_export import (
    export_cross_platform_product_summary_barcode_table_pdf,
)
from src.invoice_app.services.uat2_data_settings import (
    configured_uat2_data_settings,
)
from src.invoice_app.utils.monetary import format_currency, streamlit_currency_format


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
def _load_cross_platform_reporting_snapshot() -> CrossPlatformReportingSnapshot:
    return configured_uat2_data_settings().create_cross_platform_product_summary_reader().load_snapshot()


def render_cross_platform_product_summary(
    snapshot: CrossPlatformReportingSnapshot | None = None,
) -> None:
    """Render one source snapshot; its final rowset drives both table and PDF."""

    st.title("Cross Platform Summary")
    try:
        source = snapshot if snapshot is not None else _load_cross_platform_reporting_snapshot()
    except (HistoricalInvoiceStorageError, CrossPlatformProductSummaryError) as error:
        st.error(f"Cross Platform Summary is unavailable: {error}")
        return

    platform, from_date, to_date = _render_filters(source)
    if from_date is not None and to_date is not None and from_date > to_date:
        st.error("From Date must be on or before To Date.")
        return
    try:
        summary = build_cross_platform_product_summary(
            source,
            platform=platform,
            from_date=from_date,
            to_date=to_date,
        )
    except CrossPlatformProductSummaryError as error:
        st.error(f"Cross Platform Summary is unavailable: {error}")
        return

    summary_frame = _summary_frame(summary)
    _render_summary_dashboard(summary_frame)

    st.subheader("Product Summary")
    if summary_frame.empty:
        st.info("No committed product rows match the selected platform and payout dates.")
        return
    st.dataframe(
        summary_frame,
        hide_index=True,
        key="cross_platform_product_summary_table_v2",
        column_order=PRODUCT_SUMMARY_COLUMNS,
        column_config={
            "No.": st.column_config.NumberColumn("No.", format="%d", width="small"),
            "SKU Code": st.column_config.TextColumn("SKU Code"),
            "NAV": st.column_config.TextColumn("NAV"),
            "Description": st.column_config.TextColumn("Description"),
            "Qty": st.column_config.NumberColumn("Qty", format="%d", width="small"),
            "UOM": st.column_config.TextColumn("UOM", width="small"),
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
        },
        height=320,
    )
    st.download_button(
        "Cross Platform Barcode Table PDF",
        export_cross_platform_product_summary_barcode_table_pdf(summary),
        file_name="cross-platform-product-summary.pdf",
        mime="application/pdf",
        icon=":material/picture_as_pdf:",
        type="primary",
    )


def _render_filters(
    snapshot: CrossPlatformReportingSnapshot,
) -> tuple[str, date | None, date | None]:
    platform_col, from_date_col, to_date_col = st.columns(3, gap="small")
    with platform_col:
        platform = st.selectbox(
            "Platform",
            PLATFORM_OPTIONS,
            key="cross_platform_reporting_platform",
        )
    options = snapshot.available_payout_dates(platform)
    with from_date_col:
        from_date = _payout_date_selectbox(
            "From Date", options, "cross_platform_reporting_from_date"
        )
    with to_date_col:
        to_date = _payout_date_selectbox(
            "To Date", options, "cross_platform_reporting_to_date"
        )
    return platform, from_date, to_date


def _payout_date_selectbox(
    label: str,
    options: tuple[date, ...],
    key: str,
) -> date | None:
    choices: tuple[date | None, ...] = (None, *options)
    if st.session_state.get(key) not in choices:
        st.session_state.pop(key, None)
    return st.selectbox(
        label,
        choices,
        key=key,
        format_func=lambda value: "All Dates" if value is None else value.isoformat(),
    )


def _summary_frame(summary) -> pd.DataFrame:
    rows = [
        {
            "No.": row.number,
            "SKU Code": row.sku_code,
            "NAV": row.nav,
            "Description": row.product_name,
            "Qty": row.quantity,
            "UOM": row.uom or "EA",
            "Unit Price": _as_number(row.unit_price),
            "Original Sales": _as_number(row.original_sales),
            "Disc Amt": _as_number(row.discount_amount),
            "Amount": _as_number(row.amount),
        }
        for row in summary.product_rows
    ]
    return pd.DataFrame(rows, columns=PRODUCT_SUMMARY_COLUMNS)


def _render_summary_dashboard(summary_frame: pd.DataFrame) -> None:
    total_product = len(summary_frame)
    total_quantity = sum(int(value) for value in summary_frame["Qty"])
    total_original_sales = sum(
        (Decimal(str(value)) for value in summary_frame["Original Sales"]),
        Decimal("0.00"),
    )
    total_discount_given = sum(
        (Decimal(str(value)) for value in summary_frame["Disc Amt"]),
        Decimal("0.00"),
    )
    total_amount = sum(
        (Decimal(str(value)) for value in summary_frame["Amount"]),
        Decimal("0.00"),
    )

    with st.container(horizontal=True, gap="small"):
        st.metric("Total Product", f"{total_product:,}", border=True)
        st.metric("Total Quantity", f"{total_quantity:,}", border=True)
        st.metric("Total Original Sales", format_currency(total_original_sales), border=True)
        st.metric("Total Discount Given", format_currency(total_discount_given), border=True)
        st.metric("Total Amount", format_currency(total_amount), border=True)


def _as_number(value: Decimal) -> float:
    return float(value)
