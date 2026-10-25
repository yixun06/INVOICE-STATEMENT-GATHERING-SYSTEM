"""Read-only Shopee SG financial Analysis Phase A page."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.services.shopee_order_adjustment_read import OrderAdjustmentReadError
from src.invoice_app.services.shopee_sg_analysis import (
    SgAnalysisDateBasis,
    SgAnalysisFilters,
    ShopeeSgAnalysis,
    ShopeeSgAnalysisSnapshot,
    build_shopee_sg_analysis,
    load_shopee_sg_analysis_snapshot,
)
from src.invoice_app.services.shopee_statement_financial_read import StatementFinancialReadError
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings
from src.invoice_app.utils.monetary import format_currency


@st.cache_data(ttl=45, show_spinner=False)
def _load_shopee_sg_analysis_snapshot() -> ShopeeSgAnalysisSnapshot:
    settings = configured_uat2_data_settings(SHOPEE_SG, require_explicit_context=True)
    return load_shopee_sg_analysis_snapshot(
        full_order_repository=settings.create_full_order_current_repository(),
        statement_reader=settings.create_statement_writer(),
    )


def render_shopee_sg_analysis(snapshot: ShopeeSgAnalysisSnapshot | None = None) -> None:
    """Render only Full Order, Statement, component, and adjustment authorities."""

    st.title("Shopee SG · Live Analysis")
    st.caption(
        "Full Order operational facts and immutable Shopee settlement evidence. "
        "Adjustments are shown separately and never change released amount."
    )
    try:
        source = snapshot if snapshot is not None else _load_shopee_sg_analysis_snapshot()
    except (HistoricalInvoiceStorageError, StatementFinancialReadError, OrderAdjustmentReadError) as error:
        st.error(f"Shopee SG Live Analysis is unavailable: {error}")
        return

    filters = _render_filters(source)
    if filters is None:
        return
    report = build_shopee_sg_analysis(source, filters)
    _render_overview(report)
    _render_orders(report)
    _render_financial_detail(report)
    _render_adjustment_timeline(report)


def _render_filters(snapshot: ShopeeSgAnalysisSnapshot) -> SgAnalysisFilters | None:
    with st.container(border=True):
        st.subheader("Analysis controls")
        basis = st.selectbox("Date basis", tuple(SgAnalysisDateBasis), format_func=lambda value: value.value)
        available = snapshot.available_dates(basis)
        if not available:
            st.info(
                "No committed dates are available for this source authority.",
                icon=":material/info:",
            )
            return None
        values = st.date_input(
            "Date range",
            value=(available[0], available[-1]),
            min_value=available[0],
            max_value=available[-1],
        )
    if not isinstance(values, tuple) or len(values) != 2:
        st.error("Select a valid From and To date.")
        return None
    try:
        return SgAnalysisFilters(basis, values[0], values[1])
    except ValueError as error:
        st.error(str(error))
        return None


def _render_overview(report: ShopeeSgAnalysis) -> None:
    with st.container(horizontal=True):
        if report.filters.date_basis is SgAnalysisDateBasis.ORDER_CREATED:
            st.metric("Orders", report.order_count, border=True)
            st.metric("Item quantity", report.item_quantity, border=True)
            st.metric("Product subtotal", format_currency(report.product_subtotal, "SGD"), border=True)
            st.metric("Settlement unavailable", sum(row.released_amount is None for row in report.rows), border=True)
            return
        st.metric("Settled orders", report.settled_order_count, border=True)
        st.metric("Total released amount", format_currency(report.released_amount, "SGD"), border=True)
        st.metric("Payout range", f"{report.filters.from_date:%d %b %Y} – {report.filters.to_date:%d %b %Y}", border=True)


def _render_orders(report: ShopeeSgAnalysis) -> None:
    st.subheader("Orders")
    if not report.rows:
        st.info("No Shopee SG orders match the selected date range.", icon=":material/info:")
        return
    frame = pd.DataFrame(
        {
            "Order ID": [row.order_id for row in report.rows],
            "Order status": [row.order_status for row in report.rows],
            "Order created date": [row.order_created_date for row in report.rows],
            "Payout date": [row.payout_completed_date for row in report.rows],
            "Quantity": [row.quantity for row in report.rows],
            "Product subtotal": [row.product_subtotal for row in report.rows],
            "Settlement evidence": [row.settlement_state.value for row in report.rows],
            "Released amount": [row.released_amount for row in report.rows],
        }
    )
    st.dataframe(
        frame,
        hide_index=True,
        column_config={
            "Product subtotal": st.column_config.NumberColumn(format="S$ %.2f"),
            "Released amount": st.column_config.NumberColumn(format="S$ %.2f"),
        },
    )


def _render_financial_detail(report: ShopeeSgAnalysis) -> None:
    if not report.rows:
        return
    st.subheader("Settlement financial detail")
    selected_order_id = st.selectbox(
        "Order for financial detail",
        tuple(row.order_id for row in report.rows),
    )
    record = report.financial_record(selected_order_id)
    if record is None or record.released_amount is None:
        st.info("Settlement is not available for this Full Order yet; no zero value is inferred.")
        return
    st.metric("Statement released amount", format_currency(record.released_amount, "SGD"), border=True)
    components = report.components_for_order(selected_order_id)
    if not components:
        st.info("No persisted financial components are available for this settled order.")
        return
    detail = pd.DataFrame(
        {
            "Component": [row.component_name for row in components],
            "Amount": [row.component_amount for row in components],
            "Record type": [row.record_type for row in components],
            "Source sheet": [row.statement_source_sheet for row in components],
            "Source row": [row.statement_source_row_number for row in components],
            "Sequence": [row.sequence_no for row in components],
        }
    )
    st.dataframe(detail, hide_index=True, column_config={"Amount": st.column_config.NumberColumn(format="S$ %.2f")})
    totals = report.component_totals_for_order(selected_order_id)
    st.caption("Component totals aggregate only identical native component names; source rows above remain unchanged.")
    st.dataframe(
        pd.DataFrame({"Component": [row.component_name for row in totals], "Amount": [row.amount for row in totals]}),
        hide_index=True,
        column_config={"Amount": st.column_config.NumberColumn(format="S$ %.2f")},
    )


def _render_adjustment_timeline(report: ShopeeSgAnalysis) -> None:
    st.subheader("Post-payout adjustment timeline")
    st.caption("Independent lifecycle events; these do not alter Statement released amount.")
    if not report.adjustments:
        st.info("No committed Shopee SG adjustment events are available.", icon=":material/info:")
        return
    frame = pd.DataFrame(
        {
            "Order ID": [event.linked_order_id for event in report.adjustments],
            "Event date": [event.adjustment_complete_date for event in report.adjustments],
            "Payout date": [event.payout_completed_date for event in report.adjustments],
            "Type": [event.adjustment_type for event in report.adjustments],
            "Amount": [event.adjustment_amount for event in report.adjustments],
            "Fingerprint": [event.adjustment_event_fingerprint for event in report.adjustments],
            "Statement batch": [event.statement_batch_id for event in report.adjustments],
            "Source file": [event.statement_source_filename for event in report.adjustments],
        }
    )
    st.metric(
        "Adjustment total (separate)",
        format_currency(sum((event.adjustment_amount for event in report.adjustments), Decimal("0.00")), "SGD"),
        border=True,
    )
    st.dataframe(frame, hide_index=True, column_config={"Amount": st.column_config.NumberColumn(format="S$ %.2f")})
