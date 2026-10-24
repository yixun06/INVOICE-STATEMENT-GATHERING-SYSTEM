"""Shopee MY committed-data Live Analysis page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape
import re

import altair as alt
import pandas as pd
import streamlit as st

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.shopee_my_live_analysis import (
    AnalysisFilters,
    DateBasis,
    DateRangeError,
    GroupBy,
    PayoutStatus,
    ShopeeMyAnalyticsError,
    ShopeeMyAnalyticsSnapshot,
    ShopeeMyLiveAnalysis,
    TimeSeriesPoint,
    build_shopee_my_live_analysis,
    load_shopee_my_analytics_snapshot_from_reporting_snapshot,
)
from src.invoice_app.ui.shopee_my_live_analysis_insights import (
    build_management_insight,
)
from src.invoice_app.ui.available_payout_date_calendar import (
    available_payout_date_picker,
    component_date_value,
)
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings
from src.invoice_app.utils.monetary import format_currency


_APPLIED_FILTERS_KEY = "shopee_my_live_analysis_applied_filters"
_FILTER_PREFIX = "shopee_my_live_analysis_filter"
_FROM_DATE_CALENDAR_KEY = "shopee_my_live_analysis_from_date_calendar"
_TO_DATE_CALENDAR_KEY = "shopee_my_live_analysis_to_date_calendar"
_GREEN = "#176B48"
_SAGE = "#8FAF9F"
_AMBER = "#C48A2C"


@st.cache_data(ttl=45, show_spinner=False)
def _load_shopee_my_analytics_snapshot() -> ShopeeMyAnalyticsSnapshot:
    settings = configured_uat2_data_settings()
    reporting_snapshot = settings.create_cross_platform_product_summary_reader().load_snapshot()
    return load_shopee_my_analytics_snapshot_from_reporting_snapshot(reporting_snapshot)


def render_shopee_my_live_analysis(
    snapshot: ShopeeMyAnalyticsSnapshot | None = None,
) -> None:
    """Render read-only analytics from one committed Shopee MY snapshot."""

    st.html(_analysis_page_styles())
    st.html(_analysis_eyebrow_html())
    st.title("Shopee MY · Live Analysis")
    st.caption(
        "Sales performance, product trends, and payout visibility from committed Shopee MY data"
    )

    try:
        source = snapshot if snapshot is not None else _load_shopee_my_analytics_snapshot()
    except (HistoricalInvoiceStorageError, ShopeeMyAnalyticsError) as error:
        st.error(f"Shopee MY Live Analysis is unavailable: {error}")
        return

    with st.container(border=True):
        st.subheader("Analysis controls")
        st.caption("Choose the reporting basis, available date range, and ranking depth.")
        filters = _render_filters(source)
    if filters is None:
        return

    try:
        report = build_shopee_my_live_analysis(source, filters)
    except (DateRangeError, ShopeeMyAnalyticsError, ValueError) as error:
        st.error(str(error))
        return

    _render_analysis_context(report)
    _render_kpis(report)
    _render_management_insight(report)
    if report.unknown_layout_count:
        st.warning(
            f"{report.unknown_layout_count} committed order(s) were excluded because "
            "their financial layout is not recognised as NORMAL_ORDER, "
            "CANCELLED_ORDER, or RETURN_REFUND.",
            icon=":material/warning:",
        )

    if report.current_kpis.orders == 0:
        message = (
            "No Shopee MY payout-dated sales data is available for the selected filters."
            if filters.date_basis is DateBasis.PAYOUT
            else "No Shopee MY sales data is available for the selected filters."
        )
        st.info(message, icon=":material/info:")
        return

    trend_col, sales_orders_col = st.columns([1.65, 1], gap="medium")
    with trend_col.container(border=True, height="stretch"):
        st.subheader("Sales Trend")
        st.caption("Current period compared with the equal-length preceding period")
        st.altair_chart(_sales_trend_chart(report), key="shopee_my_sales_trend")
    with sales_orders_col.container(border=True, height="stretch"):
        st.subheader("Sales vs Orders")
        st.caption("Current-period sales and order volume")
        st.altair_chart(_sales_vs_orders_chart(report), key="shopee_my_sales_vs_orders")

    sales_ranking_col, quantity_ranking_col = st.columns(2, gap="medium")
    with sales_ranking_col.container(border=True):
        st.subheader("Best Selling Products")
        _render_product_ranking(report, metric="sales")
    with quantity_ranking_col.container(border=True):
        st.subheader("Most Sold Products")
        _render_product_ranking(report, metric="quantity")


def _render_filters(snapshot: ShopeeMyAnalyticsSnapshot) -> AnalysisFilters | None:
    bounds = snapshot.available_date_bounds(DateBasis.ORDER_CREATED)
    default_from, default_to = bounds or (date.today(), date.today())
    st.session_state.setdefault(
        _APPLIED_FILTERS_KEY,
        AnalysisFilters(
            DateBasis.ORDER_CREATED,
            default_from,
            default_to,
            PayoutStatus.ALL,
            GroupBy.DAY,
            10,
        ),
    )
    applied = st.session_state[_APPLIED_FILTERS_KEY]

    columns = st.columns([1.35, 1.05, 1.05, 1.55, 0.75, 0.65, 0.95], gap="small")
    date_basis = columns[0].selectbox(
        "Date Basis",
        tuple(DateBasis),
        index=tuple(DateBasis).index(applied.date_basis),
        format_func=lambda value: value.value,
        key=f"{_FILTER_PREFIX}_date_basis",
        on_change=_clear_date_calendar_selection,
    )
    payout_options = (
        (PayoutStatus.RELEASED,)
        if date_basis is DateBasis.PAYOUT
        else tuple(PayoutStatus)
    )
    selected_payout = (
        PayoutStatus.RELEASED
        if date_basis is DateBasis.PAYOUT
        else applied.payout_status
    )
    payout_status = columns[3].selectbox(
        "Payout Status",
        payout_options,
        index=(
            payout_options.index(selected_payout)
            if selected_payout in payout_options
            else 0
        ),
        format_func=lambda value: value.value,
        help="Pending means no payout evidence is currently recorded for the order.",
        key=f"{_FILTER_PREFIX}_payout_status_{date_basis.name.lower()}",
        on_change=_clear_date_calendar_selection,
    )
    options = snapshot.available_dates(date_basis, payout_status)
    _clear_invalid_date_calendar_selection(options)
    default_from = applied.from_date if applied.from_date in options else (options[0] if options else None)
    default_to = applied.to_date if applied.to_date in options else (options[-1] if options else None)
    scope_id = f"{date_basis.name}:{payout_status.name}"
    with columns[1]:
        from_date = available_payout_date_picker(
            label="From Date",
            allowed_dates=options,
            value=default_from,
            key=_FROM_DATE_CALENDAR_KEY,
            disabled=not options,
            scope_id=scope_id,
        )
    with columns[2]:
        to_date = available_payout_date_picker(
            label="To Date",
            allowed_dates=options,
            value=default_to,
            key=_TO_DATE_CALENDAR_KEY,
            disabled=not options,
            scope_id=scope_id,
            align_end=True,
        )
    if not options:
        date_label = (
            "order-created"
            if date_basis is DateBasis.ORDER_CREATED
            else "payout"
        )
        st.info(
            f"No {date_label} dates are available for the selected payout status."
        )
    group_by = columns[4].selectbox(
        "Group By",
        tuple(GroupBy),
        index=tuple(GroupBy).index(applied.group_by),
        format_func=lambda value: value.value,
        key=f"{_FILTER_PREFIX}_group_by",
    )
    top_n = columns[5].selectbox(
        "Top N",
        (5, 10, 20),
        index=(5, 10, 20).index(applied.top_n),
        key=f"{_FILTER_PREFIX}_top_n",
    )
    submitted = columns[6].button(
        "Apply Filters",
        type="primary",
        icon=":material/filter_alt:",
    )

    if submitted:
        if not options:
            applied = AnalysisFilters(
                date_basis=date_basis,
                from_date=date.today(),
                to_date=date.today(),
                payout_status=payout_status,
                group_by=group_by,
                top_n=top_n,
            )
            st.session_state[_APPLIED_FILTERS_KEY] = applied
            return applied
        if from_date is None or to_date is None:
            st.error("Select a valid From and To date.")
            return None
        try:
            applied = AnalysisFilters(
                date_basis=date_basis,
                from_date=from_date,
                to_date=to_date,
                payout_status=payout_status,
                group_by=group_by,
                top_n=top_n,
            )
        except DateRangeError as error:
            st.error(str(error))
            return None
        st.session_state[_APPLIED_FILTERS_KEY] = applied

    return applied


def _clear_date_calendar_selection() -> None:
    st.session_state.pop(_FROM_DATE_CALENDAR_KEY, None)
    st.session_state.pop(_TO_DATE_CALENDAR_KEY, None)


def _clear_invalid_date_calendar_selection(options: tuple[date, ...]) -> None:
    allowed = set(options)
    selected = (
        component_date_value(st.session_state.get(_FROM_DATE_CALENDAR_KEY)),
        component_date_value(st.session_state.get(_TO_DATE_CALENDAR_KEY)),
    )
    if any(candidate is not None and candidate not in allowed for candidate in selected):
        _clear_date_calendar_selection()


def _render_kpis(report: ShopeeMyLiveAnalysis) -> None:
    current = report.current_kpis
    previous = report.previous_kpis
    entries = (
        (
            "Total Sales",
            format_currency(current.total_sales),
            "total_sales",
            format_currency(previous.total_sales),
        ),
        ("Orders", f"{current.orders:,}", "orders", f"{previous.orders:,}"),
        ("Units Sold", f"{current.units_sold:,}", "units_sold", f"{previous.units_sold:,}"),
        (
            "Avg. Order Value",
            format_currency(current.average_order_value),
            "average_order_value",
            format_currency(previous.average_order_value),
        ),
        (
            "Refund Amount",
            format_currency(current.refund_amount),
            "refund_amount",
            format_currency(previous.refund_amount),
        ),
    )
    with st.container(horizontal=True, gap="small"):
        for label, value, comparison_key, previous_text in entries:
            comparison = report.comparisons[comparison_key]
            st.metric(
                label,
                value,
                delta=f"{comparison.display} vs previous period",
                delta_color=comparison.delta_color,
                help=f"Previous period: {previous_text}",
                border=True,
            )


def _render_analysis_context(report: ShopeeMyLiveAnalysis) -> None:
    status = {
        PayoutStatus.ALL: "All payout statuses",
        PayoutStatus.RELEASED: "Released only",
        PayoutStatus.PENDING: "Pending / No payout evidence",
    }[report.filters.effective_payout_status]
    st.html(
        _analysis_context_html(
            basis=report.filters.date_basis.value,
            current_period=_context_period_label(
                report.current_period.from_date, report.current_period.to_date
            ),
            payout_status=status,
            previous_period=_context_period_label(
                report.previous_period.from_date, report.previous_period.to_date
            ),
        )
    )


def _render_management_insight(report: ShopeeMyLiveAnalysis) -> None:
    insight = build_management_insight(report.current_kpis, report.previous_kpis)
    if insight:
        with st.container(border=True):
            st.caption("Management insight")
            st.write(insight)


def _analysis_page_styles() -> str:
    """Apply the approved restrained dashboard treatment without altering data UI."""

    return """
    <style>
      [data-testid="stMetric"] {
        min-width: 10.5rem;
        background: #fbfdf9;
        border-color: #dce8dc;
        box-shadow: 0 1px 2px rgba(23, 107, 72, 0.04);
      }
      [data-testid="stMetricLabel"] { color: #657568; font-size: 0.78rem; font-weight: 650; }
      [data-testid="stMetricValue"] { color: #17251b; font-weight: 680; }
      [data-testid="stMetricDelta"] { font-size: 0.74rem; }
      [data-testid="stSelectbox"] label { color: #526555; font-size: 0.76rem; font-weight: 650; }
      [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #dfeadf;
        box-shadow: 0 1px 2px rgba(23, 107, 72, 0.035);
      }
      [data-testid="stCaptionContainer"] { color: #718074; }
    </style>
    """


def _analysis_eyebrow_html() -> str:
    return """
    <div class="shopee-analysis-eyebrow">
      <span>LIVE ANALYSIS</span>
      <span class="shopee-analysis-eyebrow-divider" aria-hidden="true"></span>
      <span>READ-ONLY COMMITTED DATA</span>
    </div>
    <style>
      .shopee-analysis-eyebrow {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        margin-bottom: -0.35rem;
        color: #176b48;
        font-family: inherit;
        font-size: 0.7rem;
        font-weight: 750;
        letter-spacing: 0.08em;
      }
      .shopee-analysis-eyebrow-divider { width: 0.22rem; height: 0.22rem; border-radius: 50%; background: #9bb8a4; }
    </style>
    """


def _analysis_context_html(
    *, basis: str, current_period: str, payout_status: str, previous_period: str
) -> str:
    return """
    <section class="shopee-analysis-context" aria-label="Applied analysis context">
      <div class="shopee-analysis-context-current">
        <span class="shopee-analysis-context-label">VIEWING</span>
        <strong>{basis}</strong>
        <span>{current_period}</span>
        <span>{payout_status}</span>
      </div>
      <div class="shopee-analysis-context-previous">
        <span class="shopee-analysis-context-label">COMPARED WITH</span>
        <span>{previous_period}</span>
      </div>
    </section>
    <style>
      .shopee-analysis-context {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.8rem 1.25rem;
        margin: 0.85rem 0;
        padding: 0.75rem 0.9rem;
        border: 1px solid #dce8dc;
        border-radius: 0.55rem;
        background: #f8fbf7;
        color: #425544;
        font-family: inherit;
        font-size: 0.78rem;
      }
      .shopee-analysis-context-current, .shopee-analysis-context-previous {
        display: flex;
        align-items: baseline;
        flex-wrap: wrap;
        gap: 0.35rem;
      }
      .shopee-analysis-context-label { color: #728075; font-size: 0.66rem; font-weight: 750; letter-spacing: 0.065em; }
      .shopee-analysis-context strong { color: #176b48; }
      @media (max-width: 720px) {
        .shopee-analysis-context { align-items: flex-start; flex-direction: column; }
      }
    </style>
    """.replace("{basis}", escape(basis)).replace(
        "{current_period}", escape(current_period)
    ).replace(
        "{payout_status}", escape(payout_status)
    ).replace(
        "{previous_period}", escape(previous_period)
    )


def _sales_trend_chart(report: ShopeeMyLiveAnalysis) -> alt.Chart:
    frame = _sales_trend_frame(report)
    bucket_order = [
        _present_bucket(point, report.filters.group_by).label
        for point in report.current_trend
    ]
    return (
        alt.Chart(frame)
        .mark_line(point=True, strokeWidth=3)
        .encode(
            x=alt.X(
                "Current bucket:N",
                title=None,
                sort=bucket_order,
                axis=alt.Axis(labelAngle=-35, labelLimit=110),
            ),
            y=alt.Y("Sales:Q", title="Sales (RM)", axis=alt.Axis(format=",.2f")),
            color=alt.Color(
                "Series:N",
                scale=alt.Scale(
                    domain=["Current period", "Previous period"],
                    range=[_GREEN, _SAGE],
                ),
                legend=alt.Legend(title=None, orient="top"),
            ),
            strokeDash=alt.StrokeDash(
                "Series:N",
                scale=alt.Scale(
                    domain=["Current period", "Previous period"],
                    range=[[1, 0], [6, 4]],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Series:N"),
                alt.Tooltip("Current date range:N"),
                alt.Tooltip("Current sales:N"),
                alt.Tooltip("Previous date range:N"),
                alt.Tooltip("Previous sales:N"),
                alt.Tooltip("Change:N"),
            ],
        )
        .properties(height=300)
    )


def _sales_vs_orders_chart(report: ShopeeMyLiveAnalysis) -> alt.LayerChart:
    frame = _sales_vs_orders_frame(report)
    bucket_order = [
        _present_bucket(point, report.filters.group_by).label
        for point in report.current_trend
    ]
    base = alt.Chart(frame).encode(
        x=alt.X(
            "Date bucket:N",
            title=None,
            sort=bucket_order,
            axis=alt.Axis(labelAngle=-35, labelLimit=110),
        )
    )
    sales = base.mark_bar(color=_GREEN, opacity=0.85).encode(
        y=alt.Y("Sales:Q", title="Sales (RM)", axis=alt.Axis(format=",.2f")),
        tooltip=[
            alt.Tooltip("Actual date range:N", title="Date bucket"),
            alt.Tooltip("Sales display:N", title="Sales"),
            alt.Tooltip("Orders:Q", format=","),
        ],
    )
    orders = base.mark_line(color=_AMBER, point=True, strokeWidth=3).encode(
        y=alt.Y("Orders:Q", title="Orders"),
        tooltip=[
            alt.Tooltip("Actual date range:N", title="Date bucket"),
            alt.Tooltip("Sales display:N", title="Sales"),
            alt.Tooltip("Orders:Q", format=","),
        ],
    )
    return alt.layer(sales, orders).resolve_scale(y="independent").properties(height=300)


@dataclass(frozen=True)
class ProductRankingPresentation:
    rank: int
    compact_display_name: str
    full_product_name: str
    seller_sku: str
    nav: str
    bar_width: float
    actual_value: str


def _render_product_ranking(report: ShopeeMyLiveAnalysis, *, metric: str) -> None:
    rows = _product_ranking_presentation(report, metric=metric)
    if not rows:
        st.caption("No ranked products are available for the selected filters.")
        return
    st.html(_product_ranking_html(rows, metric=metric))


def _product_ranking_presentation(
    report: ShopeeMyLiveAnalysis, *, metric: str
) -> tuple[ProductRankingPresentation, ...]:
    rows = (
        report.top_products_by_sales
        if metric == "sales"
        else report.top_products_by_quantity
    )
    if metric not in {"sales", "quantity"}:
        raise ValueError("Product ranking metric must be 'sales' or 'quantity'.")
    values = [
        float(row.product_sales if metric == "sales" else row.quantity)
        for row in rows
    ]
    maximum = max(values, default=0.0)
    return tuple(
        ProductRankingPresentation(
            rank=rank,
            compact_display_name=_compact_product_label(row.description),
            full_product_name=row.description,
            seller_sku=row.seller_sku,
            nav=row.nav,
            bar_width=(
                float(row.product_sales if metric == "sales" else row.quantity) / maximum
                if maximum
                else 0.0
            ),
            actual_value=(
                format_currency(row.product_sales)
                if metric == "sales"
                else f"{row.quantity:,} units"
            ),
        )
        for rank, row in enumerate(rows, start=1)
    )


def _compact_product_label(full_name: str, *, limit: int = 88) -> str:
    """Normalize whitespace and ellipsize a source name without inventing a title."""

    normalized = re.sub(r"\s+", " ", full_name).strip()
    if len(normalized) <= limit:
        return normalized
    boundary = normalized[: limit - 1].rsplit(" ", maxsplit=1)[0].rstrip()
    return f"{boundary or normalized[: limit - 1].rstrip()}…"


def _product_ranking_html(
    rows: tuple[ProductRankingPresentation, ...], *, metric: str
) -> str:
    """Build a text-free-bar ranking list without dataframe controls or grid chrome."""

    metric_label = "sales" if metric == "sales" else "quantity"
    items = "".join(
        """
        <article class="shopee-ranking-row">
          <div class="shopee-ranking-rank">#{rank}</div>
          <div class="shopee-ranking-identity">
            <div class="shopee-ranking-name" title="{full_name}">{display_name}</div>
            <div class="shopee-ranking-meta">SKU {sku}<span aria-hidden="true">&nbsp;·&nbsp;</span>NAV {nav}</div>
          </div>
          <div class="shopee-ranking-track" aria-label="Relative {metric_label} ranking">
            <span class="shopee-ranking-fill" style="width: {bar_width:.4f}%"></span>
          </div>
          <div class="shopee-ranking-value">{actual_value}</div>
        </article>
        """.format(
            rank=row.rank,
            full_name=escape(row.full_product_name, quote=True),
            display_name=escape(row.compact_display_name),
            sku=escape(row.seller_sku),
            nav=escape(row.nav),
            metric_label=metric_label,
            bar_width=max(0.0, min(1.0, row.bar_width)) * 100,
            actual_value=escape(row.actual_value),
        )
        for row in rows
    )
    return f"""
    <style>
      .shopee-ranking-list {{ color: #18231b; font-family: inherit; }}
      .shopee-ranking-row {{
        display: grid;
        grid-template-columns: 2.35rem minmax(10rem, 1.15fr) minmax(6.5rem, 0.85fr) max-content;
        align-items: center;
        column-gap: 0.75rem;
        min-height: 4.7rem;
        padding: 0.6rem 0;
        border-bottom: 1px solid #e7eee8;
      }}
      .shopee-ranking-row:last-child {{ border-bottom: 0; padding-bottom: 0.1rem; }}
      .shopee-ranking-rank {{ color: #176b48; font-size: 0.86rem; font-weight: 700; }}
      .shopee-ranking-identity {{ min-width: 0; }}
      .shopee-ranking-name {{
        overflow: hidden;
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
        color: #17251b;
        font-size: 0.91rem;
        font-weight: 650;
        line-height: 1.3;
      }}
      .shopee-ranking-meta {{
        overflow: hidden;
        margin-top: 0.22rem;
        color: #728075;
        font-size: 0.74rem;
        line-height: 1.2;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      .shopee-ranking-track {{
        height: 0.46rem;
        overflow: hidden;
        border-radius: 99px;
        background: #edf2ee;
      }}
      .shopee-ranking-fill {{ display: block; height: 100%; border-radius: inherit; background: #176b48; }}
      .shopee-ranking-value {{
        color: #17251b;
        font-size: 0.84rem;
        font-weight: 700;
        text-align: right;
        white-space: nowrap;
      }}
      @media (max-width: 720px) {{
        .shopee-ranking-row {{
          grid-template-columns: 2.35rem minmax(0, 1fr) max-content;
          row-gap: 0.45rem;
          min-height: 0;
          padding: 0.75rem 0;
        }}
        .shopee-ranking-track {{ grid-column: 2 / 4; }}
      }}
    </style>
    <section class="shopee-ranking-list" aria-label="Product ranking by {metric_label}">
      {items}
    </section>
    """


def _sales_vs_orders_frame(report: ShopeeMyLiveAnalysis) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date bucket": _present_bucket(point, report.filters.group_by).label,
            "Actual date range": _period_label(point.bucket_start, point.bucket_end),
            "Sales": float(point.sales),
            "Sales display": format_currency(point.sales),
            "Orders": point.orders,
        }
        for point in report.current_trend
    )


def _sales_trend_frame(report: ShopeeMyLiveAnalysis) -> pd.DataFrame:
    previous_by_position = {point.position: point for point in report.previous_trend}
    rows = []
    for current in report.current_trend:
        previous = previous_by_position.get(current.position)
        current_bucket = _present_bucket(current, report.filters.group_by)
        previous_label = (
            _period_label(previous.bucket_start, previous.bucket_end)
            if previous is not None
            else "No comparable bucket"
        )
        previous_sales = previous.sales if previous is not None else Decimal("0")
        change = _percent_change_label(current.sales, previous_sales)
        common = {
            "Current bucket": current_bucket.label,
            "Current date range": _period_label(current.bucket_start, current.bucket_end),
            "Current sales": format_currency(current.sales),
            "Previous date range": previous_label,
            "Previous sales": format_currency(previous_sales),
            "Change": change,
        }
        rows.append({**common, "Series": "Current period", "Sales": float(current.sales)})
        if previous is not None:
            rows.append({**common, "Series": "Previous period", "Sales": float(previous.sales)})
    return pd.DataFrame(rows)


def _period_label(start: date, end: date) -> str:
    return start.strftime("%d %b %Y") if start == end else f"{start:%d %b %Y} – {end:%d %b %Y}"


@dataclass(frozen=True)
class BucketPresentation:
    bucket_label: str
    bucket_start: date
    bucket_end: date

    @property
    def label(self) -> str:
        return self.bucket_label


def _present_bucket(point: TimeSeriesPoint, group_by: GroupBy) -> BucketPresentation:
    start = point.bucket_start
    end = point.bucket_end
    if group_by is GroupBy.MONTH:
        label = start.strftime("%b %Y")
    elif start == end:
        label = f"{start.day} {start:%b}"
    elif start.year == end.year and start.month == end.month:
        label = f"{start.day}–{end.day} {start:%b}"
    elif start.year == end.year:
        label = f"{start.day} {start:%b} – {end.day} {end:%b}"
    else:
        label = f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"
    return BucketPresentation(label, start, end)


def _context_period_label(start: date, end: date) -> str:
    if start == end:
        return f"{start.day} {start:%b %Y}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {start:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
    return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"


def _percent_change_label(current: Decimal, previous: Decimal) -> str:
    if previous == 0:
        return "—" if current == 0 else "New"
    return f"{((current - previous) / previous * Decimal('100')):+.1f}%"
