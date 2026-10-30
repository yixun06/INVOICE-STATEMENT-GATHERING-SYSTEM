"""Reusable Streamlit renderer for canonical Shopee analysis view models."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from src.invoice_app.services.shopee_analysis import (
    AnalysisDateBasis,
    ReconciliationStatus,
    ShopeeAnalysisFilters,
    ShopeeAnalysisReport,
    ShopeeAnalysisSnapshot,
    available_fee_types,
    available_statuses,
    build_shopee_analysis,
    product_options,
)
from src.invoice_app.services.shopee_order_financial_projection import OrderFinancialProjectionState
from src.invoice_app.utils.monetary import currency_symbol, format_currency, streamlit_currency_format


ZERO = Decimal("0.00")
TREND_AGGREGATIONS = ("Daily", "Weekly", "Monthly")
FINANCIAL_METRIC_FIELDS = {"Product Sales with Statement Evidence": "Settled Product Sales"}

# The keyed Analysis module container and accessible group name restrict these
# rules to the shared MY/SG navigation, leaving other Streamlit widgets alone.
ANALYSIS_NAV_CSS = """
<style>
.st-key-analysis_modules [role="radiogroup"][aria-label="Analysis workspace"] {
    border: 1px solid #dce7da;
    border-radius: 9px;
    overflow: hidden;
    gap: 0;
}
.st-key-analysis_modules [role="radiogroup"][aria-label="Analysis workspace"] > button {
    min-height: 34px;
    padding: 5px 10px;
    border: 0;
    border-radius: 0;
    background: #fff;
    color: #34433a;
    box-shadow: none;
}
.st-key-analysis_modules [role="radiogroup"][aria-label="Analysis workspace"] > button + button {
    border-left: 1px solid #dce7da;
}
.st-key-analysis_modules [role="radiogroup"][aria-label="Analysis workspace"] > button:hover {
    background: #f2f7ef;
    color: #14532d;
}
.st-key-analysis_modules [role="radiogroup"][aria-label="Analysis workspace"] > button[aria-checked="true"] {
    background: #e8f3e3;
    color: #14532d;
}
.st-key-analysis_modules [role="radiogroup"][aria-label="Analysis workspace"] > button:focus-visible {
    outline: 2px solid #166534;
    outline-offset: -3px;
    position: relative;
    z-index: 1;
}
</style>
"""


def render_shopee_analysis_dashboard(snapshot: ShopeeAnalysisSnapshot) -> None:
    """Render a market-neutral dashboard without repository or table knowledge."""

    presentation = snapshot.presentation
    st.title(f"{presentation.market_label} - Analysis V2")
    st.caption(
        "Canonical Full Order product facts and committed Statement settlement evidence. "
        "Adjustments remain independent from released amount."
    )
    st.html(ANALYSIS_NAV_CSS)
    with st.container(gap="xsmall"):
        filters = _render_filters(snapshot)
        if filters is None:
            return
        report = build_shopee_analysis(snapshot, filters)
        with st.container(key="analysis_modules", gap="xsmall"):
            module_key = f"analysis_module_{presentation.market_label}"
            modules = ("Overview", "Product Analysis", "Financial & Reconciliation")
            if st.session_state.get(module_key) not in modules:
                st.session_state[module_key] = modules[0]
            selected_module = st.segmented_control(
                "Analysis workspace", modules, key=module_key, required=True,
                label_visibility="collapsed", width="stretch", persist_state="session",
            )
            if selected_module == "Overview":
                _render_overview(report)
            elif selected_module == "Product Analysis":
                _render_product_analysis(report)
            else:
                _render_financial_analysis(report)


def _render_filters(snapshot: ShopeeAnalysisSnapshot) -> ShopeeAnalysisFilters | None:
    presentation = snapshot.presentation
    prefix = f"{presentation.market_label}_analysis_"
    applied_key = prefix + "applied_filters"
    default_basis = tuple(AnalysisDateBasis)[0]
    default_dates = snapshot.available_dates(default_basis)
    if applied_key not in st.session_state and default_dates:
        st.session_state[applied_key] = ShopeeAnalysisFilters(
            date_basis=default_basis, from_date=default_dates[0], to_date=default_dates[-1]
        )
    with st.container(border=True):
        st.subheader("Filters")
        st.caption("Choose your filters, then apply them to update the report.")
        date_controls = st.columns((1, 1.5))
        with date_controls[0]:
            basis = st.selectbox(
                "Date basis", tuple(AnalysisDateBasis), format_func=lambda value: value.value,
                key=f"{presentation.market_label}_analysis_date_basis",
            )
        dates = snapshot.available_dates(basis)
        if not dates:
            st.info("No canonical dates are available for this source authority.", icon=":material/info:")
            if st.button("Reset filters", key=prefix + "reset_empty"):
                for key in list(st.session_state):
                    if key.startswith(prefix):
                        del st.session_state[key]
                st.rerun()
            return st.session_state.get(applied_key)
        minimum, maximum = dates[0], dates[-1]
        with date_controls[1]:
            current_range = st.date_input(
                "Date range", value=(minimum, maximum), min_value=minimum, max_value=maximum,
                key=f"{presentation.market_label}_analysis_date_range_{basis.name}",
            )
        options = product_options(snapshot)
        option_map = {option.label: option.key for option in options}
        audience_controls = st.columns((2, 1.5))
        with audience_controls[0]:
            selected_labels = st.multiselect(
                "Product", tuple(option_map), placeholder="All products",
                key=f"{presentation.market_label}_analysis_products",
            )
        with audience_controls[1]:
            selected_statuses = st.multiselect(
                "Order status", available_statuses(snapshot), placeholder="All eligible statuses",
                key=f"{presentation.market_label}_analysis_statuses",
            )
        with st.expander("More filters"):
            first, second, third = st.columns(3)
            order_id_query = first.text_input("Order ID contains", key=f"{presentation.market_label}_analysis_order_id")
            settlement_states = second.multiselect(
                "Settlement state", tuple(OrderFinancialProjectionState),
                format_func=lambda value: value.value,
                key=f"{presentation.market_label}_analysis_settlement_states",
            )
            fee_types = third.multiselect(
                "Fee or component type", available_fee_types(snapshot),
                key=f"{presentation.market_label}_analysis_fee_types",
            )
            fourth, fifth = st.columns(2)
            refund_value = fourth.selectbox(
                "Refund evidence", ("All", "Has refund", "No refund"),
                key=f"{presentation.market_label}_analysis_refund",
            )
            adjustment_value = fifth.selectbox(
                "Adjustment evidence", ("All", "Has adjustment", "No adjustment"),
                key=f"{presentation.market_label}_analysis_adjustment",
            )
        st.divider()
        summary_col, action_col = st.columns((3, 2), vertical_alignment="center")
        summary = summary_col.empty()
        with action_col:
            with st.container(horizontal=True, horizontal_alignment="right"):
                reset_clicked = st.button("Reset filters", key=prefix + "reset")
                apply_clicked = st.button("Apply filters", type="primary", key=prefix + "apply")
        if reset_clicked:
            for key in list(st.session_state):
                if key.startswith(prefix):
                    del st.session_state[key]
            st.rerun()
    if not isinstance(current_range, tuple) or len(current_range) != 2:
        if apply_clicked:
            st.error("Select a valid From and To date.")
        return st.session_state.get(applied_key)
    try:
        draft = ShopeeAnalysisFilters(
            date_basis=basis,
            from_date=current_range[0],
            to_date=current_range[1],
            product_keys=frozenset(option_map[label] for label in selected_labels),
            order_statuses=frozenset(selected_statuses),
            order_id_query=order_id_query,
            settlement_states=frozenset(settlement_states),
            has_refund={"Has refund": True, "No refund": False}.get(refund_value),
            has_adjustment={"Has adjustment": True, "No adjustment": False}.get(adjustment_value),
            fee_types=frozenset(fee_types),
        )
    except ValueError as error:
        if apply_clicked:
            st.error(str(error))
        return st.session_state.get(applied_key)
    if apply_clicked:
        st.session_state[applied_key] = draft
    with summary.container():
        applied = st.session_state.get(applied_key)
        if applied is not None:
            _render_filter_context(applied)
        if draft != applied:
            st.caption("Filters changed — apply to update.")
    return st.session_state.get(applied_key)


def _render_filter_context(filters: ShopeeAnalysisFilters) -> None:
    parts = [
        filters.date_basis.value,
        _period_label(filters.from_date, filters.to_date),
        "All products" if not filters.product_keys else f"{len(filters.product_keys)} product selection(s)",
        "All eligible statuses" if not filters.order_statuses else ", ".join(sorted(filters.order_statuses)),
    ]
    if filters.order_id_query:
        parts.append(f"Order ID: {filters.order_id_query}")
    if filters.settlement_states:
        parts.append("Settlement: " + ", ".join(state.value for state in sorted(filters.settlement_states, key=lambda value: value.value)))
    if filters.has_refund is not None:
        parts.append("Refund: " + ("Yes" if filters.has_refund else "No"))
    if filters.has_adjustment is not None:
        parts.append("Adjustment: " + ("Yes" if filters.has_adjustment else "No"))
    if filters.fee_types:
        parts.append(f"Components: {len(filters.fee_types)}")
    st.caption("Applied filters: " + " | ".join(parts))


def _render_overview(report: ShopeeAnalysisReport) -> None:
    currency = report.presentation.currency_code
    st.header("Overview")
    st.caption("Business summary of product and settlement performance.")
    with st.container(border=True):
        st.subheader("Product Performance")
        st.caption("Order and product activity for the applied filters.")
        _render_metric_grid((
            ("Product Sales", _money(report.product_sales, currency), None),
            ("Total Orders", report.total_orders, None),
            ("Units Sold", report.units_sold, None),
            ("Average Order Value", _money(report.average_order_value, currency), None),
        ))
    with st.container(border=True):
        st.subheader("Settlement Performance")
        st.caption("Committed Statement evidence for the selected orders.")
        _render_metric_grid((
            ("Product Sales with Statement Evidence", _money(report.settled_product_sales, currency), None),
            ("Released Amount", _money(report.released_amount, currency), None),
            ("Settlement Difference", _money(report.settlement_difference, currency), None),
            ("Orders with committed Statement evidence", f"{report.settled_orders:,} / {report.total_orders:,}",
             f"{report.settlement_coverage:.1%} evidenced" if report.total_orders else None),
        ))
        st.caption("Statement-evidenced Product Sales minus Released Amount.")
        st.caption(f"{report.settled_orders:,} of {report.total_orders:,} selected orders have committed Statement evidence.")
        if report.filters.product_keys:
            st.caption("Product filters do not allocate order-level settlement facts; settlement comparison is unavailable for this selection.")
        if report.rows:
            _render_settlement_breakdown(report)
    if not report.rows:
        st.info("No canonical orders match the selected filters.", icon=":material/info:")
        return
    _render_reconciliation_summary(report)
    with st.container(border=True):
        st.subheader("Financial Adjustments")
        st.caption(f"Refund amount: {_money(report.refund_amount, currency)}  |  Adjustment total: {_money(report.adjustment_total, currency)}")


def _render_metric_grid(values: tuple[tuple[str, object, str | None], ...]) -> None:
    """Render two deliberate metric columns so dashboard cards never clip."""

    for start in range(0, len(values), 2):
        left, right = st.columns(2)
        for column, (label, value, delta) in zip((left, right), values[start:start + 2]):
            with column:
                st.metric(label, value, delta=delta, border=True)


def _render_settlement_breakdown(report: ShopeeAnalysisReport) -> None:
    currency = report.presentation.currency_code
    with st.container(border=True):
        st.subheader("Settlement breakdown")
        st.caption(
            "Settlement values use only orders with committed Statement evidence. "
            "Financial components remain order-level and are not allocated to products."
        )
        if report.filters.product_keys:
            st.info("Settlement comparison is unavailable when product filtering would require fee allocation.", icon=":material/info:")
            return
        expected = report.expected_released_amount
        other = None if expected is None else (
            expected - report.settled_product_sales - report.statement_charges
            - report.signed_refund_amount - report.statement_credits
        )
        composition = [
            ("Product Sales with Statement Evidence", report.settled_product_sales),
            ("Charges", report.statement_charges),
            ("Refunds", report.signed_refund_amount),
            ("Credits", report.statement_credits),
            ("Released Amount", report.released_amount),
        ]
        _render_signed_bar(pd.DataFrame({"Fact": [fact for fact, _ in composition], "Amount": [float(amount or ZERO) for _, amount in composition]}), "Fact", "Amount", currency)
        facts = composition[:-1]
        if other not in (None, ZERO):
            facts.append(("Other signed components", other))
        facts.extend((
            ("Expected Released", expected),
            ("Released Amount", report.released_amount),
            ("Difference", report.reconciliation_difference),
        ))
        st.dataframe(
            pd.DataFrame({"Fact": [fact for fact, _ in facts], "Amount": [amount for _, amount in facts]}),
            hide_index=True,
            column_config={"Amount": st.column_config.NumberColumn(format=streamlit_currency_format(currency))},
        )
        if expected is None:
            st.caption("Expected Released is shown only when every settled order has deterministic component evidence.")


def _render_reconciliation_summary(report: ShopeeAnalysisReport) -> None:
    with st.container(border=True):
        st.subheader("Reconciliation coverage")
        frame, other_pending = _reconciliation_status_frame(report)
        counts = dict(zip(frame["Status"], frame["Orders"], strict=True))
        first, second, third = st.columns(3)
        for column, status in zip((first, second, third), frame["Status"], strict=True):
            row = frame.loc[frame["Status"] == status].iloc[0]
            with column:
                column.metric(status, int(row["Orders"]), delta=f"{row['Percent']:.1%}")
        _render_reconciliation_bar(frame)
        if counts["Pending Statement evidence"]:
            st.caption("Missing Statement is a normal settlement lifecycle state, not a mismatch.")
        if other_pending:
            st.caption(f"{other_pending:,} settled order(s) still need additional component evidence.")


def _reconciliation_status_frame(report: ShopeeAnalysisReport) -> tuple[pd.DataFrame, int]:
    """Build display-only reconciliation coverage without altering status semantics."""

    counts = report.reconciliation_counts
    pending_statement = report.missing_statement_orders
    other_pending = max(0, counts[ReconciliationStatus.INSUFFICIENT_EVIDENCE] - pending_statement)
    frame = pd.DataFrame(
        {
            "Status": ("Reconciled", "Pending Statement evidence", "Mismatch"),
            "Orders": (
                counts[ReconciliationStatus.RECONCILED],
                pending_statement,
                counts[ReconciliationStatus.MISMATCH],
            ),
        }
    )
    total = int(frame["Orders"].sum())
    frame["Percent"] = frame["Orders"] / total if total else 0.0
    return frame, other_pending


def _render_reconciliation_bar(frame: pd.DataFrame) -> None:
    visible = frame[frame["Orders"] > 0].copy()
    if visible.empty:
        st.caption("No selected orders have reconciliation status evidence.")
        return
    chart = (
        alt.Chart(visible.assign(Cohort="Selected orders"))
        .mark_bar(size=28)
        .encode(
            x=alt.X("Orders:Q", stack="normalize", axis=alt.Axis(format="%", title=None, grid=False)),
            y=alt.Y("Cohort:N", title=None, axis=None),
            color=alt.Color(
                "Status:N",
                scale=alt.Scale(
                    domain=["Reconciled", "Pending Statement evidence", "Mismatch"],
                    range=["#287a5a", "#7a8492", "#b45309"],
                ),
                legend=alt.Legend(orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("Status:N"),
                alt.Tooltip("Orders:Q"),
                alt.Tooltip("Percent:Q", format=".1%"),
            ],
        )
        .properties(height=105)
    )
    st.altair_chart(chart, width="stretch")


def _render_product_analysis(report: ShopeeAnalysisReport) -> None:
    currency = report.presentation.currency_code
    st.subheader("Canonical Full Order product facts")
    st.caption("Explore product performance and rankings within the applied selection.")
    if not report.products:
        st.info("No Full Order item rows match the selected filters.", icon=":material/info:")
        return
    with st.container(horizontal=True):
        st.metric("Product Sales", _money(report.product_sales, currency), border=True)
        st.metric("Units Sold", report.units_sold, border=True)
        st.metric("Products", len(report.products), border=True)
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.subheader("Trend")
            st.caption("Trend over the applied date range")
            metric = st.selectbox("Product analysis trend", ("Product Sales", "Orders", "Units Sold"), key="analysis_product_trend")
            _render_trend(_product_trend_frame(report, metric), metric=metric, report=report, financial=False, key="product")
    with right:
        with st.container(border=True):
            st.subheader("Rankings")
            st.caption("Top 10 products; full names are available in the detail below.")
            ranking = st.selectbox("Rank products by", ("Product Sales", "Quantity"), key="analysis_product_rank")
            rows = sorted(
                report.products,
                key=(lambda row: (-row.product_sales, row.seller_sku)) if ranking == "Product Sales" else (lambda row: (-row.quantity, row.seller_sku)),
            )[:10]
            ranking_frame = pd.DataFrame({
                "Product": [_product_chart_label(row.description) for row in rows],
                "Full product": [row.description for row in rows],
                ranking: [float(row.product_sales if ranking == "Product Sales" else row.quantity) for row in rows],
            })
            _render_ranked_bar(ranking_frame, label="Product", metric=ranking, currency=currency if ranking == "Product Sales" else None)
            with st.expander("Product ranking detail"):
                st.dataframe(
                    ranking_frame.drop(columns="Product"),
                    hide_index=True,
                    column_config={"Product Sales": st.column_config.NumberColumn(format=streamlit_currency_format(currency))},
                )
    total = report.product_sales
    st.subheader("Product detail")
    st.dataframe(
        pd.DataFrame({
            "Description": [row.description for row in report.products],
            "Variation": [row.variation for row in report.products],
            "NAV": [row.nav for row in report.products],
            "SKU": [row.seller_sku for row in report.products],
            "resolve_sku": [row.resolve_sku for row in report.products],
            "Qty": [row.quantity for row in report.products],
            "Unit Price": [row.unit_price for row in report.products],
            "Product Sales": [row.product_sales for row in report.products],
            "Contribution %": [float((row.product_sales / total * Decimal("100")) if total else ZERO) for row in report.products],
        }),
        hide_index=True,
        column_config={
            "Unit Price": st.column_config.NumberColumn(format=streamlit_currency_format(currency)),
            "Product Sales": st.column_config.NumberColumn(format=streamlit_currency_format(currency)),
            "Contribution %": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )


def _render_financial_analysis(report: ShopeeAnalysisReport) -> None:
    currency = report.presentation.currency_code
    if not report.rows:
        st.info("No canonical orders match the selected filters.", icon=":material/info:")
        return
    st.subheader("Financial summary")
    st.caption("Settlement and adjustment evidence is shown at order level.")
    with st.container(horizontal=True):
        st.metric("Product Sales with Statement Evidence", _money(report.settled_product_sales, currency), border=True)
        st.metric("Released Amount", _money(report.released_amount, currency), border=True)
        st.metric("Charges", _money(report.statement_charges, currency), border=True)
    with st.container(horizontal=True):
        st.metric("Credits", _money(report.statement_credits, currency), border=True)
        st.metric("Refund amount", _money(report.refund_amount, currency), border=True)
        st.metric("Adjustment total", _money(report.adjustment_total, currency), border=True)
    trend_col, breakdown_col = st.columns(2)
    with trend_col:
        with st.container(border=True):
            st.subheader("Financial trend")
            st.caption("Follow settlement activity over the applied date range.")
            metric = st.selectbox(
                "Financial analysis trend",
                ("Released Amount", "Product Sales with Statement Evidence", "Settlement Difference", "Charges", "Refund Amount", "Adjustments"),
                key="analysis_financial_trend",
            )
            _render_trend(_financial_trend_frame(report, metric), metric=metric, report=report, financial=True, key="financial")
    with breakdown_col:
        _render_settlement_breakdown(report)
    _render_reconciliation_summary(report)
    st.subheader("Native Statement component breakdown")
    st.caption("Signed components retain their original settlement meaning.")
    components = report.component_totals
    impact_components = tuple(row for row in components if row.component_name.casefold() != "product price")
    if components:
        if impact_components:
            component_frame = pd.DataFrame({"Component": [row.component_name for row in impact_components], "Signed Amount": [float(row.amount) for row in impact_components]})
            _render_signed_bar(component_frame, "Component", "Signed Amount", currency, sort_by_impact=True)
        else:
            st.caption("No non-base Statement components are available for impact comparison.")
        with st.expander("Native Statement component detail"):
            st.dataframe(
                pd.DataFrame({"Component": [row.component_name for row in components], "Signed Amount": [row.amount for row in components]}),
                hide_index=True,
                column_config={"Signed Amount": st.column_config.NumberColumn(format=streamlit_currency_format(currency))},
            )
    else:
        st.info("No committed ORDER-level Statement components are available for this selection.", icon=":material/info:")
    _render_order_inspector(report)
    st.subheader("Order reconciliation")
    st.dataframe(
        pd.DataFrame({
            "Order ID": [row.order_id for row in report.rows],
            "Order date": [row.order_created_date for row in report.rows],
            "Payout date": [row.payout_completed_date for row in report.rows],
            "Product sales": [row.product_sales for row in report.rows],
            "Released": [row.released_amount for row in report.rows],
            "Charges": [row.charges for row in report.rows],
            "Refund": [row.refund_amount for row in report.rows],
            "Difference": [row.reconciliation_difference for row in report.rows],
            "Status": [row.reconciliation_status.value for row in report.rows],
        }),
        hide_index=True,
        column_config={name: st.column_config.NumberColumn(format=streamlit_currency_format(currency)) for name in ("Product sales", "Released", "Charges", "Refund", "Difference")},
    )


def _render_order_inspector(report: ShopeeAnalysisReport) -> None:
    currency = report.presentation.currency_code
    with st.container(border=True):
        st.subheader("Inspect an Order")
        query = st.text_input(
            "Search Order ID...",
            placeholder="Type an Order ID to inspect",
            key=f"{report.presentation.market_label}_analysis_order_detail_query",
        ).strip()
        if not query:
            st.caption("Search for an Order ID to inspect canonical product, settlement, component, and adjustment facts.")
            return
        candidates = tuple(row.order_id for row in report.rows if query.casefold() in row.order_id.casefold())[:50]
        if not candidates:
            st.info("No selected canonical order matches that Order ID.", icon=":material/search_off:")
            return
        order_id = st.selectbox(
            "Matching Order ID", candidates, index=None,
            placeholder="Select a matching Order ID...", key=f"{report.presentation.market_label}_analysis_order_detail",
        )
        if order_id is None:
            return
        row = report.order(order_id)
        if row is None:
            return
        st.caption(row.reconciliation_reason)
        left, right = st.columns(2)
        with left:
            st.markdown("**Product facts**")
            st.dataframe(
                pd.DataFrame({
                    "Description": [item.product_name for item in row.items],
                    "Variation": [item.variation for item in row.items],
                    "NAV": [item.nav for item in row.items],
                    "SKU": [item.resolved_seller_sku for item in row.items],
                    "resolve_sku": [item.resolve_sku for item in row.items],
                    "Qty": [item.quantity for item in row.items],
                    "Unit Price": [item.unit_price for item in row.items],
                    "Product subtotal": [item.product_subtotal for item in row.items],
                }), hide_index=True,
                column_config={
                    "Unit Price": st.column_config.NumberColumn(format=streamlit_currency_format(currency)),
                    "Product subtotal": st.column_config.NumberColumn(format=streamlit_currency_format(currency)),
                },
            )
        with right:
            st.markdown("**Settlement facts**")
            if row.released_amount is None:
                st.info("Statement not available yet", icon=":material/schedule:")
                st.caption("This order is pending settlement evidence, not a reconciliation mismatch.")
            else:
                st.caption(f"Released amount: {_money(row.released_amount, currency)} | Payout date: {row.payout_completed_date or '-'}")
                st.dataframe(
                    pd.DataFrame({
                        "Record type": [item.record_type for item in row.financial_components],
                        "Component": [item.component_name for item in row.financial_components],
                        "Amount": [item.component_amount for item in row.financial_components],
                    }), hide_index=True,
                    column_config={"Amount": st.column_config.NumberColumn(format=streamlit_currency_format(currency))},
                )
        st.markdown("**Independent adjustment facts**")
        if row.adjustment_events:
            st.dataframe(
                pd.DataFrame({
                    "Event date": [event.adjustment_complete_date for event in row.adjustment_events],
                    "Type": [event.adjustment_type for event in row.adjustment_events],
                    "Amount": [event.adjustment_amount for event in row.adjustment_events],
                }), hide_index=True,
                column_config={"Amount": st.column_config.NumberColumn(format=streamlit_currency_format(currency))},
            )
        else:
            st.caption("No linked post-payout adjustment events are available for this order.")


def _render_trend(
    frame: pd.DataFrame,
    *,
    metric: str,
    report: ShopeeAnalysisReport,
    financial: bool,
    key: str,
) -> None:
    if frame.empty:
        st.info("No dated evidence is available for this metric.", icon=":material/info:")
        return
    aggregation = st.selectbox(
        "Aggregation",
        TREND_AGGREGATIONS,
        index=TREND_AGGREGATIONS.index(_default_trend_aggregation(report.filters)),
        key=f"{key}_trend_aggregation",
    )
    basis = "Order Created Date" if report.filters.date_basis is AnalysisDateBasis.ORDER_CREATED else "Payout Date"
    st.markdown(f"**{metric} by {basis} · {aggregation}**")
    aggregated = _aggregate_trend_frame(frame, metric=metric, aggregation=aggregation, filters=report.filters)
    st.altair_chart(_trend_chart(aggregated, metric=metric, report=report, financial=financial), width="stretch")


def _default_trend_aggregation(filters: ShopeeAnalysisFilters) -> str:
    days = (filters.to_date - filters.from_date).days + 1
    if days > 365:
        return "Monthly"
    if days > 90:
        return "Weekly"
    return "Daily"


def _aggregate_trend_frame(
    frame: pd.DataFrame,
    *,
    metric: str,
    aggregation: str,
    filters: ShopeeAnalysisFilters,
) -> pd.DataFrame:
    """Aggregate dated display points without widening the selected authority range."""

    if aggregation not in TREND_AGGREGATIONS:
        raise ValueError(f"Unsupported trend aggregation: {aggregation}")
    if frame.empty or aggregation == "Daily":
        return frame.copy()
    points = frame.copy()
    points["Date"] = pd.to_datetime(points["Date"]).dt.date

    def bucket(point: date) -> date:
        if aggregation == "Weekly":
            return filters.from_date + timedelta(days=((point - filters.from_date).days // 7) * 7)
        month_start = point.replace(day=1)
        return max(month_start, filters.from_date)

    points["Bucket"] = points["Date"].map(bucket)
    grouped = points.groupby("Bucket", as_index=False)[metric].sum().rename(columns={"Bucket": "Date"})
    grouped = grouped[(grouped["Date"] >= filters.from_date) & (grouped["Date"] <= filters.to_date)]
    return grouped.sort_values("Date", ignore_index=True)


def _trend_chart(frame: pd.DataFrame, *, metric: str, report: ShopeeAnalysisReport, financial: bool) -> alt.Chart:
    """Build one bounded trend chart without inventing dates outside the filter."""

    money = financial and metric not in {"Orders", "Units Sold"}
    y_title = f"{metric} ({currency_symbol(report.presentation.currency_code)})" if money else metric
    return (
        alt.Chart(frame)
        .mark_line(point=True, strokeWidth=2.5, color="#287a5a")
        .encode(
            x=alt.X(
                "Date:T",
                title=None,
                scale=alt.Scale(domain=[report.filters.from_date.isoformat(), report.filters.to_date.isoformat()], nice=False),
                axis=alt.Axis(format="%d %b", tickCount=6),
            ),
            y=alt.Y(f"{metric}:Q", title=y_title),
            tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip(f"{metric}:Q", format=",.2f" if money else ",")],
        )
        .properties(height=250)
    )


def _render_signed_bar(
    frame: pd.DataFrame,
    category: str,
    amount: str,
    currency: str,
    *,
    sort_by_impact: bool = False,
) -> None:
    if frame.empty:
        return
    chart_frame = frame.assign(_impact=frame[amount].abs()) if sort_by_impact else frame
    category_sort: object = alt.SortField("_impact", order="descending") if sort_by_impact else frame[category].tolist()
    chart = (
        alt.Chart(chart_frame)
        .transform_calculate(_signed_value=f"datum['{amount}']")
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            y=alt.Y(f"{category}:N", sort=category_sort, title=None, axis=alt.Axis(labelLimit=210)),
            x=alt.X(f"{amount}:Q", title=f"Amount ({currency_symbol(currency)})", axis=alt.Axis(gridColor="#e5e7eb")),
            color=alt.condition("datum._signed_value >= 0", alt.value("#287a5a"), alt.value("#697386")),
            tooltip=[alt.Tooltip(f"{category}:N"), alt.Tooltip(f"{amount}:Q", format=",.2f")],
        )
        .properties(height=max(130, 28 * len(frame)))
    )
    st.altair_chart(chart, width="stretch")


def _render_ranked_bar(frame: pd.DataFrame, *, label: str, metric: str, currency: str | None) -> None:
    tooltip = [alt.Tooltip("Full product:N", title="Product"), alt.Tooltip(f"{metric}:Q", format=",.2f")]
    chart = (
        alt.Chart(frame)
        .mark_bar(color="#287a5a", cornerRadiusEnd=3)
        .encode(
            y=alt.Y(f"{label}:N", sort=alt.SortField(metric, order="descending"), title=None, axis=alt.Axis(labelLimit=220)),
            x=alt.X(f"{metric}:Q", title=f"{metric} ({currency_symbol(currency)})" if currency else metric),
            tooltip=tooltip,
        )
        .properties(height=max(180, 28 * len(frame)))
    )
    st.altair_chart(chart, width="stretch")


def _product_chart_label(value: str, *, limit: int = 42) -> str:
    """Return a deterministic compact label while retaining the source value elsewhere."""

    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit - 1].rstrip() + "…"


def _product_trend_frame(report: ShopeeAnalysisReport, metric: str) -> pd.DataFrame:
    grouped: dict[date, dict[str, Decimal]] = {}
    for row in report.rows:
        point = row.order_created_date if report.filters.date_basis is AnalysisDateBasis.ORDER_CREATED else row.payout_completed_date
        if point is None:
            continue
        values = grouped.setdefault(point, {"Product Sales": ZERO, "Orders": ZERO, "Units Sold": ZERO})
        values["Product Sales"] += row.product_sales
        values["Orders"] += Decimal("1")
        values["Units Sold"] += Decimal(row.quantity)
    return _trend_frame(grouped, metric, report)


def _financial_trend_frame(report: ShopeeAnalysisReport, metric: str) -> pd.DataFrame:
    source_metric = FINANCIAL_METRIC_FIELDS.get(metric, metric)
    grouped: dict[date, dict[str, Decimal]] = {}
    for row in report.rows:
        point = row.order_created_date if report.filters.date_basis is AnalysisDateBasis.ORDER_CREATED else row.payout_completed_date
        if point is None:
            continue
        values = grouped.setdefault(point, {name: ZERO for name in ("Released Amount", "Settled Product Sales", "Settlement Difference", "Charges", "Refund Amount", "Adjustments")})
        settled = row.settlement_state is OrderFinancialProjectionState.AVAILABLE and row.released_amount is not None
        if settled:
            values["Released Amount"] += row.released_amount
            values["Settled Product Sales"] += row.product_sales
            values["Charges"] += row.charges
            values["Refund Amount"] += abs(row.refund_amount)
            if not report.filters.product_keys:
                values["Settlement Difference"] += row.settlement_difference or ZERO
        values["Adjustments"] += row.adjustment_total
    frame = _trend_frame(grouped, source_metric, report)
    return frame.rename(columns={source_metric: metric})


def _trend_frame(grouped: dict[date, dict[str, Decimal]], metric: str, report: ShopeeAnalysisReport) -> pd.DataFrame:
    dates = [point for point in sorted(grouped) if report.filters.from_date <= point <= report.filters.to_date]
    return pd.DataFrame({"Date": dates, metric: [float(grouped[point][metric]) for point in dates]})


def _period_label(start: date, end: date) -> str:
    return start.strftime("%d %b %Y") if start == end else f"{start:%d %b}–{end:%d %b %Y}"


def _money(value: Decimal | None, currency: str) -> str:
    return format_currency(value, currency, missing_value="-")
