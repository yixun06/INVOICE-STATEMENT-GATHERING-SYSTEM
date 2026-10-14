from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder
from src.invoice_app.services.shopee_my_live_analysis import (
    AnalysisFilters,
    DateBasis,
    GroupBy,
    KpiValues,
    PayoutStatus,
    ShopeeMyAnalyticsSnapshot,
    TimeSeriesPoint,
    build_shopee_my_live_analysis,
)
from src.invoice_app.ui.shopee_my_live_analysis import (
    _analysis_page_styles,
    _compact_product_label,
    _present_bucket,
    _percent_change_label,
    _product_ranking_html,
    _product_ranking_presentation,
    _sales_trend_frame,
    _sales_vs_orders_frame,
)
from src.invoice_app.ui.shopee_my_live_analysis_insights import (
    build_management_insight,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_analysis_styles_do_not_target_shared_sidebar_buttons():
    assert '[data-testid="stButton"]' not in _analysis_page_styles()


def _app(source: str) -> AppTest:
    return AppTest.from_string(
        f'''
from datetime import date
from decimal import Decimal

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder
from src.invoice_app.services.shopee_my_live_analysis import ShopeeMyAnalyticsSnapshot
from src.invoice_app.ui.shopee_my_live_analysis import render_shopee_my_live_analysis

{source}
'''
    )


def test_shopee_my_live_analysis_renders_valid_data_and_rm_kpis():
    app = _app(
        '''
snapshot = ShopeeMyAnalyticsSnapshot(
    orders=(
        CanonicalInvoiceOrder(
            platform="Shopee", order_id="SHP-1",
            order_created_date=date(2026, 8, 10), payout_completed_date=date(2026, 8, 15),
            invoice_financial_layout="NORMAL_ORDER", final_amount=Decimal("125.40"),
        ),
        CanonicalInvoiceOrder(
            platform="Shopee", order_id="SHP-R",
            order_created_date=date(2026, 8, 11), payout_completed_date=date(2026, 8, 16),
            invoice_financial_layout="RETURN_REFUND", final_amount=Decimal("-20.00"),
            refund_amount=Decimal("-20.00"),
        ),
    ),
    items=(
        CanonicalInvoiceItem(
            platform="Shopee", order_id="SHP-1", item_index=0,
            seller_sku="SKU-1", resolved_seller_sku="SKU-1", nav="300001",
            product_name="Organic Tea", variation="500g", quantity=2,
            unit_price=Decimal("70.00"), line_subtotal=Decimal("120.00"),
        ),
        CanonicalInvoiceItem(
            platform="Shopee", order_id="SHP-R", item_index=0,
            seller_sku="SKU-R", resolved_seller_sku="SKU-R", nav="300002",
            product_name="Refunded Coffee", quantity=1,
            unit_price=Decimal("20.00"), line_subtotal=Decimal("20.00"),
        ),
    ),
)
render_shopee_my_live_analysis(snapshot)
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [title.value for title in app.title] == ["Shopee MY · Live Analysis"]
    assert [element.label for element in app.selectbox] == [
        "Date Basis",
        "Payout Status",
        "Group By",
        "Top N",
    ]
    assert app.date_input == []
    assert [json.loads(calendar.proto.json) for calendar in app.get("bidi_component")] == [
        {
            "label": "From Date",
            "allowed_dates": ["2026-08-10", "2026-08-11"],
            "min_date": "2026-08-10",
            "max_date": "2026-08-11",
            "value": "2026-08-10",
            "disabled": False,
            "scope_id": "ORDER_CREATED:ALL",
            "align_end": False,
        },
        {
            "label": "To Date",
            "allowed_dates": ["2026-08-10", "2026-08-11"],
            "min_date": "2026-08-10",
            "max_date": "2026-08-11",
            "value": "2026-08-11",
            "disabled": False,
            "scope_id": "ORDER_CREATED:ALL",
            "align_end": True,
        },
    ]
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Total Sales", "RM 125.40"),
        ("Orders", "1"),
        ("Units Sold", "2"),
        ("Avg. Order Value", "RM 125.40"),
        ("Refund Amount", "RM 20.00"),
    ]
    assert [heading.value for heading in app.subheader] == [
        "Analysis controls",
        "Sales Trend",
        "Sales vs Orders",
        "Best Selling Products",
        "Most Sold Products",
    ]
    assert len(app.get("vega_lite_chart")) == 2
    assert app.dataframe == []


def _report_for_presentation(
    *,
    basis: DateBasis = DateBasis.ORDER_CREATED,
    payout: PayoutStatus = PayoutStatus.ALL,
    group_by: GroupBy = GroupBy.WEEK,
    top_n: int = 20,
):
    orders = tuple(
        CanonicalInvoiceOrder(
            platform="Shopee",
            order_id=f"O{index}",
            order_created_date=date(2026, 9, index),
            payout_completed_date=date(2026, 9, index + 1) if index % 2 else None,
            invoice_financial_layout="NORMAL_ORDER",
            final_amount=Decimal(str(index * 10)),
        )
        for index in range(1, 21)
    )
    items = tuple(
        CanonicalInvoiceItem(
            platform="Shopee",
            order_id=order.order_id,
            item_index=0,
            seller_sku=f"SKU-{index}",
            resolved_seller_sku=f"SKU-{index}",
            nav=f"NAV-{index}",
            product_name=(
                "Distinctive organic extra virgin cold pressed product with a full "
                "descriptive name for management review"
                if index == 20
                else f"Distinctive organic product name {index}"
            ),
            quantity=index,
            unit_price=Decimal("10"),
            line_subtotal=Decimal(str(index * 10)),
        )
        for index, order in enumerate(orders, start=1)
    )
    return build_shopee_my_live_analysis(
        ShopeeMyAnalyticsSnapshot(orders, items),
        AnalysisFilters(
            basis,
            date(2026, 9, 1),
            date(2026, 9, 30),
            payout,
            group_by,
            top_n,
        ),
    )


def test_bucket_presentations_use_real_day_week_partial_week_and_month_labels():
    assert _present_bucket(
        TimeSeriesPoint(date(2026, 9, 1), date(2026, 9, 1), Decimal("0"), 0, 1),
        GroupBy.DAY,
    ).label == "1 Sep"
    assert _present_bucket(
        TimeSeriesPoint(date(2026, 9, 1), date(2026, 9, 7), Decimal("0"), 0, 1),
        GroupBy.WEEK,
    ).label == "1–7 Sep"
    assert _present_bucket(
        TimeSeriesPoint(date(2026, 9, 29), date(2026, 9, 30), Decimal("0"), 0, 5),
        GroupBy.WEEK,
    ).label == "29–30 Sep"
    assert _present_bucket(
        TimeSeriesPoint(date(2026, 9, 1), date(2026, 9, 30), Decimal("0"), 0, 1),
        GroupBy.MONTH,
    ).label == "Sep 2026"


def test_sales_trend_metadata_keeps_current_and_previous_actual_date_ranges():
    report = _report_for_presentation()
    frame = _sales_trend_frame(report)

    assert "1–7 Sep" in set(frame["Current bucket"])
    assert "01 Sep 2026 – 07 Sep 2026" in set(frame["Current date range"])
    assert "02 Aug 2026 – 08 Aug 2026" in set(frame["Previous date range"])
    assert {"Current period", "Previous period"} <= set(frame["Series"])


def test_sales_trend_tooltip_formats_nonzero_previous_period_change():
    assert _percent_change_label(Decimal("114"), Decimal("120")) == "-5.0%"


def test_sales_vs_orders_uses_current_period_only():
    report = _report_for_presentation()
    frame = _sales_vs_orders_frame(report)

    assert list(frame["Date bucket"]) == [
        _present_bucket(point, report.filters.group_by).label
        for point in report.current_trend
    ]
    assert list(frame["Sales"]) == [float(point.sales) for point in report.current_trend]
    assert list(frame["Orders"]) == [point.orders for point in report.current_trend]
    assert "Previous period" not in frame.columns


@pytest.mark.parametrize("top_n", (5, 10, 20))
def test_product_rankings_preserve_top_n_identity_and_full_name(top_n):
    report = _report_for_presentation(top_n=top_n)
    rows = _product_ranking_presentation(report, metric="sales")

    assert len(rows) == top_n
    assert [row.rank for row in rows] == list(range(1, top_n + 1))
    first = report.top_products_by_sales[0]
    assert rows[0].full_product_name == first.description
    assert rows[0].seller_sku == first.seller_sku
    assert rows[0].nav == first.nav
    assert rows[0].compact_display_name


def test_product_ranking_keeps_full_long_product_name_available():
    report = _report_for_presentation(top_n=5)
    long_name = (
        "Distinctive organic extra virgin cold pressed product with a full "
        "descriptive name for management review"
    )

    rows = _product_ranking_presentation(report, metric="sales")
    assert long_name in {row.full_product_name for row in rows}
    assert len(long_name) > 84
    assert _compact_product_label(long_name).endswith("…")


@pytest.mark.parametrize(
    ("metric", "actual_value"),
    (("sales", "RM 200.00"), ("quantity", "20 units")),
)
def test_product_ranking_bar_is_normalized_but_visible_text_is_actual_business_value(
    metric, actual_value
):
    report = _report_for_presentation(top_n=5)
    rows = _product_ranking_presentation(report, metric=metric)
    html = _product_ranking_html(rows, metric=metric)

    assert rows[0].bar_width == 1.0
    assert rows[-1].bar_width < 1.0
    assert rows[0].actual_value == actual_value
    assert ">1.0000<" not in html
    assert ">100%<" not in html
    assert "ProgressColumn" not in html
    assert "dataframe" not in html


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    (
        (
            KpiValues(Decimal("120"), 120, 0, Decimal("1"), Decimal("0")),
            KpiValues(Decimal("100"), 100, 0, Decimal("1"), Decimal("0")),
            "Sales increased alongside higher order volume.",
        ),
        (
            KpiValues(Decimal("80"), 80, 0, Decimal("1"), Decimal("0")),
            KpiValues(Decimal("100"), 100, 0, Decimal("1"), Decimal("0")),
            "Sales declined as order volume decreased.",
        ),
        (
            KpiValues(Decimal("97.1"), 83, 0, Decimal("1.175"), Decimal("0")),
            KpiValues(Decimal("100"), 100, 0, Decimal("1"), Decimal("0")),
            "Sales remained relatively stable (-2.9%) despite 17.0% fewer orders, "
            "supported by a 17.5% increase in average order value.",
        ),
    ),
)
def test_management_insight_is_deterministic(current, previous, expected):
    assert build_management_insight(current, previous) == expected


def test_shopee_my_live_analysis_empty_state_renders_without_crash():
    app = _app(
        '''
render_shopee_my_live_analysis(ShopeeMyAnalyticsSnapshot(orders=(), items=()))
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Total Sales", "RM 0.00"),
        ("Orders", "0"),
        ("Units Sold", "0"),
        ("Avg. Order Value", "RM 0.00"),
        ("Refund Amount", "RM 0.00"),
    ]
    assert [message.value for message in app.info] == [
        "No order-created dates are available for the selected payout status.",
        "No Shopee MY sales data is available for the selected filters.",
    ]
    assert app.get("vega_lite_chart") == []


def test_shopee_my_live_analysis_rejects_reversed_calendar_endpoints():
    app = _app(
        '''
import streamlit as st

st.session_state["shopee_my_live_analysis_from_date_calendar"] = {"value": "2026-08-20"}
st.session_state["shopee_my_live_analysis_to_date_calendar"] = {"value": "2026-08-10"}
snapshot = ShopeeMyAnalyticsSnapshot(
    orders=(
        CanonicalInvoiceOrder(
            platform="Shopee", order_id="SHP-1", order_created_date=date(2026, 8, 10),
            invoice_financial_layout="NORMAL_ORDER", final_amount=Decimal("10.00"),
        ),
        CanonicalInvoiceOrder(
            platform="Shopee", order_id="SHP-2", order_created_date=date(2026, 8, 20),
            invoice_financial_layout="NORMAL_ORDER", final_amount=Decimal("10.00"),
        ),
    ),
    items=(
        CanonicalInvoiceItem(
            platform="Shopee", order_id="SHP-1", item_index=0, seller_sku="SKU-1",
            nav="300001", product_name="Tea", quantity=1, unit_price=Decimal("10.00"),
            line_subtotal=Decimal("10.00"),
        ),
        CanonicalInvoiceItem(
            platform="Shopee", order_id="SHP-2", item_index=0, seller_sku="SKU-2",
            nav="300002", product_name="Coffee", quantity=1, unit_price=Decimal("10.00"),
            line_subtotal=Decimal("10.00"),
        ),
    ),
)
render_shopee_my_live_analysis(snapshot)
'''
    )
    app.run(timeout=20)
    app.button[0].click().run(timeout=20)

    assert app.exception == []
    assert [message.value for message in app.error] == [
        "From Date must be on or before To Date."
    ]
    assert app.metric == []


def test_calendar_enables_only_dates_with_committed_evidence_in_selected_scope():
    app = _app(
        '''
snapshot = ShopeeMyAnalyticsSnapshot(
    orders=(
        CanonicalInvoiceOrder(
            platform="Shopee", order_id="RELEASED", order_created_date=date(2026, 8, 10),
            payout_completed_date=date(2026, 8, 15), invoice_financial_layout="NORMAL_ORDER",
            final_amount=Decimal("10.00"),
        ),
        CanonicalInvoiceOrder(
            platform="Shopee", order_id="PENDING", order_created_date=date(2026, 8, 12),
            payout_completed_date=None, invoice_financial_layout="NORMAL_ORDER",
            final_amount=Decimal("20.00"),
        ),
    ),
    items=(
        CanonicalInvoiceItem(
            platform="Shopee", order_id="RELEASED", item_index=0, seller_sku="SKU-1",
            nav="300001", product_name="Tea", quantity=1, unit_price=Decimal("10.00"),
            line_subtotal=Decimal("10.00"),
        ),
        CanonicalInvoiceItem(
            platform="Shopee", order_id="PENDING", item_index=0, seller_sku="SKU-2",
            nav="300002", product_name="Coffee", quantity=1, unit_price=Decimal("20.00"),
            line_subtotal=Decimal("20.00"),
        ),
    ),
)
render_shopee_my_live_analysis(snapshot)
'''
    )
    app.run(timeout=20)

    calendars = [json.loads(calendar.proto.json) for calendar in app.get("bidi_component")]
    assert [calendar["allowed_dates"] for calendar in calendars] == [
        ["2026-08-10", "2026-08-12"],
        ["2026-08-10", "2026-08-12"],
    ]

    next(element for element in app.selectbox if element.label == "Payout Status").set_value(
        "Released"
    ).run(timeout=20)
    calendars = [json.loads(calendar.proto.json) for calendar in app.get("bidi_component")]
    assert [calendar["allowed_dates"] for calendar in calendars] == [
        ["2026-08-10"],
        ["2026-08-10"],
    ]

    next(element for element in app.selectbox if element.label == "Date Basis").set_value(
        "Payout Date"
    ).run(timeout=20)
    calendars = [json.loads(calendar.proto.json) for calendar in app.get("bidi_component")]
    assert [calendar["allowed_dates"] for calendar in calendars] == [
        ["2026-08-15"],
        ["2026-08-15"],
    ]


def test_payout_date_mode_forces_released_and_shows_specific_empty_state():
    app = _app(
        '''
snapshot = ShopeeMyAnalyticsSnapshot(
    orders=(CanonicalInvoiceOrder(
        platform="Shopee", order_id="PENDING", order_created_date=date(2026, 8, 10),
        payout_completed_date=None, invoice_financial_layout="NORMAL_ORDER",
        final_amount=Decimal("10.00"),
    ),),
    items=(CanonicalInvoiceItem(
        platform="Shopee", order_id="PENDING", item_index=0, seller_sku="SKU-1",
        nav="300001", product_name="Tea", quantity=1, unit_price=Decimal("10.00"),
        line_subtotal=Decimal("10.00"),
    ),),
)
render_shopee_my_live_analysis(snapshot)
'''
    )
    app.run(timeout=20)
    app.selectbox[0].set_value("Payout Date")
    app.button[0].click().run(timeout=20)

    assert app.exception == []
    payout_control = next(
        element for element in app.selectbox if element.label == "Payout Status"
    )
    assert payout_control.options == ["Released"]
    assert payout_control.value == "Released"
    assert [message.value for message in app.info] == [
        "No payout dates are available for the selected payout status.",
        "No Shopee MY payout-dated sales data is available for the selected filters.",
    ]


def test_shopee_my_navigation_uses_the_committed_live_analysis_renderer():
    source = APP_PATH.read_text(encoding="utf-8")
    branch = source[source.index('elif selected_page == "Shopee MY":') :]

    assert "render_shopee_my_live_analysis()" in branch
    assert 'show_platform_tab(\n        "Shopee"' not in branch
