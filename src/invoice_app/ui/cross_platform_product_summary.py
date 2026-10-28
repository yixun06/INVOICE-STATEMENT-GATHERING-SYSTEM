"""Live, read-only Consolidated Product Summary page."""

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
    CrossPlatformDateType,
    CrossPlatformProductSummaryError,
    CrossPlatformReportingSnapshot,
    build_cross_platform_product_summary,
    cross_platform_backend_available,
)
from src.invoice_app.services.cross_platform_product_summary_barcode_table_export import (
    export_cross_platform_product_summary_barcode_table_pdf,
)
from src.invoice_app.services.cross_platform_compact_barcode_pdf import (
    export_cross_platform_compact_barcode_pdf,
)
from src.invoice_app.services.uat2_data_settings import (
    configured_uat2_data_settings,
)
from src.invoice_app.services.market_context import SHOPEE_MY, resolve_market_context
from src.invoice_app.ui.available_payout_date_calendar import (
    available_payout_date_picker,
    component_date_value,
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

_FROM_DATE_CALENDAR_KEY = "cross_platform_reporting_from_date_calendar"
_TO_DATE_CALENDAR_KEY = "cross_platform_reporting_to_date_calendar"
_DATE_SCOPE_KEY = "cross_platform_reporting_date_scope"
_PLATFORM_KEY = "cross_platform_reporting_platform"
_LEGACY_FROM_PAYOUT_CALENDAR_KEY = "cross_platform_reporting_from_payout_calendar"
_LEGACY_TO_PAYOUT_CALENDAR_KEY = "cross_platform_reporting_to_payout_calendar"
_LEGACY_ALL_DATES_KEY = "cross_platform_reporting_all_dates"
_CUSTOM_DATE_RANGE = "Selected dates"
_ALL_DATES_RANGE = "All dates"


@st.cache_data(ttl=45, show_spinner=False)
def _load_cross_platform_reporting_snapshot(
    platform: str,
    date_type: CrossPlatformDateType = CrossPlatformDateType.ORDER_CREATED,
) -> CrossPlatformReportingSnapshot:
    reader = (
        configured_uat2_data_settings(resolve_market_context(platform))
        .create_cross_platform_product_summary_reader()
    )
    if date_type is CrossPlatformDateType.ORDER_CREATED:
        load_order_created_snapshot = getattr(reader, "load_order_created_snapshot", None)
        if not callable(load_order_created_snapshot):
            raise CrossPlatformProductSummaryError(
                "Order Created Product Summary requires the PostgreSQL Full Order reader."
            )
        return load_order_created_snapshot()
    load_payout_snapshot = getattr(reader, "load_payout_snapshot", None)
    if not callable(load_payout_snapshot):
        raise CrossPlatformProductSummaryError(
            "Payout Product Summary requires the PostgreSQL Statement and Full Order readers."
        )
    return load_payout_snapshot()


def clear_cross_platform_reporting_snapshot_cache() -> None:
    """Expose the one live-summary cache invalidation point for Full Order sync."""

    _load_cross_platform_reporting_snapshot.clear()


def render_cross_platform_product_summary(
    snapshot: CrossPlatformReportingSnapshot | None = None,
) -> None:
    """Render one source snapshot; its final rowset drives both table and PDF."""

    st.title("Consolidated Product Summary")
    (
        date_type,
        platform,
        all_dates,
        from_date_col,
        to_date_col,
    ) = _render_platform_filter()
    if not cross_platform_backend_available(platform):
        st.info(f"{platform} Consolidated Product Summary is not configured yet.")
        return

    try:
        source = (
            snapshot
            if snapshot is not None
            else _load_cross_platform_reporting_snapshot(platform, date_type)
        )
    except (HistoricalInvoiceStorageError, CrossPlatformProductSummaryError) as error:
        st.error(f"Consolidated Product Summary is unavailable: {error}")
        return

    date_range = _render_date_filters(
        source,
        platform=platform,
        date_type=date_type,
        all_dates=all_dates,
        from_date_col=from_date_col,
        to_date_col=to_date_col,
    )
    if date_range is None:
        return
    from_date, to_date = date_range
    if from_date is not None and to_date is not None and from_date > to_date:
        st.error("From Date must be on or before To Date.")
        return
    try:
        summary = build_cross_platform_product_summary(
            source,
            platform=platform,
            date_type=date_type,
            from_date=from_date,
            to_date=to_date,
        )
    except CrossPlatformProductSummaryError as error:
        st.error(f"Consolidated Product Summary is unavailable: {error}")
        return

    summary_frame = _summary_frame(summary)
    currency_code = summary.currency_code
    _render_summary_dashboard(summary_frame, currency_code=currency_code)

    st.subheader("Product Summary")
    if summary_frame.empty:
        st.info(
            "No committed product rows match the selected platform and "
            f"{date_type.calendar_label}s."
        )
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
                "Unit Price", format=streamlit_currency_format(currency_code)
            ),
            "Original Sales": st.column_config.NumberColumn(
                "Original Sales", format=streamlit_currency_format(currency_code)
            ),
            "Disc Amt": st.column_config.NumberColumn(
                "Disc Amt", format=streamlit_currency_format(currency_code)
            ),
            "Amount": st.column_config.NumberColumn(
                "Amount", format=streamlit_currency_format(currency_code)
            ),
        },
        height=320,
    )
    st.download_button(
        "Consolidated Product Summary Barcode Table PDF",
        export_cross_platform_product_summary_barcode_table_pdf(summary),
        file_name="consolidated-product-summary.pdf",
        mime="application/pdf",
        icon=":material/picture_as_pdf:",
        type="primary",
    )
    st.download_button(
        "Export Compact Barcode PDF",
        export_cross_platform_compact_barcode_pdf(summary),
        file_name="consolidated-product-summary-compact-barcode.pdf",
        mime="application/pdf",
        icon=":material/qr_code_2:",
    )


def _render_platform_filter():
    date_type_col, platform_col, from_date_col, to_date_col, all_dates_col = st.columns(
        5,
        gap="small",
    )
    with date_type_col:
        date_type = st.selectbox(
            "Date Type",
            tuple(CrossPlatformDateType),
            format_func=lambda value: value.value,
            key="cross_platform_reporting_date_type",
            on_change=_clear_date_calendar_selection,
        )
        st.caption(f"Current view: {date_type.value}")
    with platform_col:
        if st.session_state.get(_PLATFORM_KEY) not in PLATFORM_OPTIONS:
            st.session_state[_PLATFORM_KEY] = SHOPEE_MY.display_name
        platform = st.selectbox(
            "Platform",
            PLATFORM_OPTIONS,
            key=_PLATFORM_KEY,
            on_change=_clear_date_calendar_selection,
        )
    with all_dates_col:
        date_scope = st.selectbox(
            "Date range",
            (_CUSTOM_DATE_RANGE, _ALL_DATES_RANGE),
            key=_DATE_SCOPE_KEY,
            on_change=_clear_date_calendar_selection,
        )
        all_dates = date_scope == _ALL_DATES_RANGE
    return date_type, platform, all_dates, from_date_col, to_date_col


def _render_date_filters(
    snapshot: CrossPlatformReportingSnapshot,
    *,
    platform: str,
    date_type: CrossPlatformDateType,
    all_dates: bool,
    from_date_col,
    to_date_col,
) -> tuple[date, date] | None:
    options = snapshot.available_dates(platform, date_type)
    _clear_invalid_date_calendar_selection(options)
    default_from = options[0] if options and all_dates else None
    default_to = options[-1] if options and all_dates else None
    with from_date_col:
        from_date = available_payout_date_picker(
            label="From Date",
            allowed_dates=options,
            value=default_from,
            key=_FROM_DATE_CALENDAR_KEY,
            disabled=not options or all_dates,
            scope_id=f"{platform}:{date_type.name}",
            on_value_change=_activate_date_filter,
        )
    to_options = (
        options
        if all_dates
        else tuple(
            candidate
            for candidate in options
            if from_date is None or candidate > from_date
        )
    )
    _clear_invalid_to_date_selection(to_options)
    with to_date_col:
        to_date = available_payout_date_picker(
            label="To Date",
            allowed_dates=to_options,
            value=default_to,
            key=_TO_DATE_CALENDAR_KEY,
            disabled=not to_options or all_dates,
            scope_id=f"{platform}:{date_type.name}",
            align_end=True,
            on_value_change=_activate_date_filter,
        )
    if not options:
        st.info(f"No {date_type.calendar_label}s are available for the selected platform.")
        return None
    if all_dates:
        return options[0], options[-1]
    if from_date is None or to_date is None:
        st.caption(f"Select a valid From and To {date_type.calendar_label}.")
        return None
    return from_date, to_date


def _clear_date_calendar_selection() -> None:
    st.session_state.pop(_FROM_DATE_CALENDAR_KEY, None)
    st.session_state.pop(_TO_DATE_CALENDAR_KEY, None)
    st.session_state.pop(_LEGACY_FROM_PAYOUT_CALENDAR_KEY, None)
    st.session_state.pop(_LEGACY_TO_PAYOUT_CALENDAR_KEY, None)
    st.session_state.pop(_LEGACY_ALL_DATES_KEY, None)


def _activate_date_filter() -> None:
    st.session_state[_DATE_SCOPE_KEY] = _CUSTOM_DATE_RANGE


def _clear_invalid_date_calendar_selection(options: tuple[date, ...]) -> None:
    allowed = set(options)
    selected = (
        component_date_value(st.session_state.get(_FROM_DATE_CALENDAR_KEY)),
        component_date_value(st.session_state.get(_TO_DATE_CALENDAR_KEY)),
    )
    if any(candidate is not None and candidate not in allowed for candidate in selected):
        _clear_date_calendar_selection()


def _clear_invalid_to_date_selection(options: tuple[date, ...]) -> None:
    selected = component_date_value(st.session_state.get(_TO_DATE_CALENDAR_KEY))
    if selected is not None and selected not in set(options):
        st.session_state.pop(_TO_DATE_CALENDAR_KEY, None)


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


def _render_summary_dashboard(summary_frame: pd.DataFrame, *, currency_code: str) -> None:
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
        st.metric("Total Original Sales", format_currency(total_original_sales, currency_code), border=True)
        st.metric("Total Discount Given", format_currency(total_discount_given, currency_code), border=True)
        st.metric("Total Amount", format_currency(total_amount, currency_code), border=True)


def _as_number(value: Decimal) -> float:
    return float(value)
