from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.services.shopee_my_live_analysis import (
    AnalysisFilters,
    DateBasis,
    DateRangeError,
    GroupBy,
    PayoutStatus,
    ShopeeMyAnalyticsError,
    ShopeeMyAnalyticsSnapshot,
    build_shopee_my_live_analysis,
    compare_kpi,
    load_shopee_my_analytics_snapshot,
    load_shopee_my_analytics_snapshot_from_reporting_snapshot,
)


def _order(
    order_id: str,
    *,
    created: date,
    payout: date | None = None,
    layout: str = "NORMAL_ORDER",
    final: str | None = "100.00",
    refund: str | None = None,
    platform: str = "Shopee",
) -> CanonicalInvoiceOrder:
    return CanonicalInvoiceOrder(
        platform=platform,
        order_id=order_id,
        order_created_date=created,
        payout_completed_date=payout,
        invoice_financial_layout=layout,
        final_amount=Decimal(final) if final is not None else None,
        refund_amount=Decimal(refund) if refund is not None else None,
    )


def _item(
    order_id: str,
    index: int,
    *,
    sku: str = "SKU-A",
    nav: str = "300001",
    name: str = "Organic Tea",
    variation: str = "500g",
    quantity: int = 1,
    line_subtotal: str = "80.00",
    unit_price: str = "100.00",
    platform: str = "Shopee",
) -> CanonicalInvoiceItem:
    return CanonicalInvoiceItem(
        platform=platform,
        order_id=order_id,
        item_index=index,
        seller_sku=sku,
        resolved_seller_sku=sku,
        nav=nav,
        product_name=name,
        variation=variation,
        quantity=quantity,
        unit_price=Decimal(unit_price),
        line_subtotal=Decimal(line_subtotal),
    )


def _filters(
    *,
    basis: DateBasis = DateBasis.ORDER_CREATED,
    start: date = date(2026, 8, 1),
    end: date = date(2026, 8, 31),
    payout: PayoutStatus = PayoutStatus.ALL,
    group_by: GroupBy = GroupBy.DAY,
    top_n: int = 10,
) -> AnalysisFilters:
    return AnalysisFilters(basis, start, end, payout, group_by, top_n)


def test_date_basis_inclusive_ranges_and_pending_membership():
    orders = (
        _order("START", created=date(2026, 8, 1), payout=None, final="10"),
        _order("END", created=date(2026, 8, 31), payout=date(2026, 9, 2), final="20"),
        _order("PAYOUT", created=date(2026, 7, 10), payout=date(2026, 8, 15), final="30"),
    )
    items = tuple(_item(order.order_id, 0, quantity=1, line_subtotal="1") for order in orders)
    snapshot = ShopeeMyAnalyticsSnapshot(orders, items)

    created = build_shopee_my_live_analysis(snapshot, _filters())
    payout = build_shopee_my_live_analysis(
        snapshot, _filters(basis=DateBasis.PAYOUT)
    )

    assert created.current_kpis.orders == 2
    assert created.current_kpis.total_sales == Decimal("30")
    assert payout.current_kpis.orders == 1
    assert payout.current_kpis.total_sales == Decimal("30")
    assert payout.filters.effective_payout_status is PayoutStatus.RELEASED


def test_available_dates_follow_date_basis_and_payout_status_without_changing_filters():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("RELEASED", created=date(2026, 8, 2), payout=date(2026, 8, 5)),
            _order("PENDING", created=date(2026, 8, 3), payout=None),
        ),
        (_item("RELEASED", 0), _item("PENDING", 0)),
    )

    assert snapshot.available_dates(DateBasis.ORDER_CREATED, PayoutStatus.ALL) == (
        date(2026, 8, 2),
        date(2026, 8, 3),
    )
    assert snapshot.available_dates(DateBasis.ORDER_CREATED, PayoutStatus.RELEASED) == (
        date(2026, 8, 2),
    )
    assert snapshot.available_dates(DateBasis.ORDER_CREATED, PayoutStatus.PENDING) == (
        date(2026, 8, 3),
    )
    assert snapshot.available_dates(DateBasis.PAYOUT, PayoutStatus.ALL) == (
        date(2026, 8, 5),
    )


def test_from_after_to_is_rejected():
    with pytest.raises(DateRangeError, match="From Date"):
        _filters(start=date(2026, 8, 2), end=date(2026, 8, 1))


@pytest.mark.parametrize(
    ("status", "expected_orders"),
    (
        (PayoutStatus.ALL, 2),
        (PayoutStatus.RELEASED, 1),
        (PayoutStatus.PENDING, 1),
    ),
)
def test_created_date_payout_status_filters(status, expected_orders):
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("RELEASED", created=date(2026, 8, 2), payout=date(2026, 8, 5)),
            _order("PENDING", created=date(2026, 8, 3), payout=None),
        ),
        (_item("RELEASED", 0), _item("PENDING", 0)),
    )

    report = build_shopee_my_live_analysis(snapshot, _filters(payout=status))

    assert report.current_kpis.orders == expected_orders


def test_payout_date_forces_released_and_cannot_include_pending():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("RELEASED", created=date(2026, 7, 1), payout=date(2026, 8, 5)),
            _order("PENDING", created=date(2026, 8, 5), payout=None),
        ),
        (_item("RELEASED", 0), _item("PENDING", 0)),
    )
    filters = _filters(basis=DateBasis.PAYOUT, payout=PayoutStatus.PENDING)

    report = build_shopee_my_live_analysis(snapshot, filters)

    assert filters.effective_payout_status is PayoutStatus.RELEASED
    assert report.current_kpis.orders == 1


def test_financial_layouts_drive_sales_refunds_and_fail_closed_unknowns():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("NORMAL", created=date(2026, 8, 2), final="120.00"),
            _order(
                "CANCELLED",
                created=date(2026, 8, 3),
                layout="CANCELLED_ORDER",
                final="0.00",
            ),
            _order(
                "REFUND",
                created=date(2026, 8, 4),
                layout="RETURN_REFUND",
                final="-40.00",
                refund="-40.00",
            ),
            _order("UNKNOWN", created=date(2026, 8, 5), layout="OTHER", final="999.00"),
        ),
        (
            _item("NORMAL", 0, quantity=2, line_subtotal="90.00"),
            _item("CANCELLED", 0, quantity=5, line_subtotal="500.00"),
            _item("REFUND", 0, quantity=3, line_subtotal="300.00"),
            _item("UNKNOWN", 0, quantity=9, line_subtotal="900.00"),
        ),
    )

    report = build_shopee_my_live_analysis(snapshot, _filters())

    assert report.current_kpis.total_sales == Decimal("120.00")
    assert report.current_kpis.orders == 1
    assert report.current_kpis.units_sold == 2
    assert report.current_kpis.refund_amount == Decimal("40.00")
    assert report.unknown_layout_count == 1
    assert sum(row.product_sales for row in report.top_products_by_sales) == Decimal("90.00")


def test_kpis_use_final_amount_distinct_orders_items_and_zero_safe_aov():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("A", created=date(2026, 8, 2), final="100.00"),
            _order("B", created=date(2026, 8, 3), final="50.00"),
        ),
        (
            _item("A", 0, quantity=2, line_subtotal="70.00"),
            _item("A", 1, sku="SKU-B", nav="300002", quantity=3, line_subtotal="20.00"),
            _item("B", 0, quantity=4, line_subtotal="40.00"),
        ),
    )

    report = build_shopee_my_live_analysis(snapshot, _filters())
    empty = build_shopee_my_live_analysis(
        snapshot, _filters(start=date(2027, 1, 1), end=date(2027, 1, 2))
    )

    assert report.current_kpis.total_sales == Decimal("150.00")
    assert report.current_kpis.orders == 2
    assert report.current_kpis.units_sold == 9
    assert report.current_kpis.average_order_value == Decimal("75.00")
    assert empty.current_kpis.average_order_value == Decimal("0.00")


def test_previous_period_uses_same_day_count_and_non_date_filters():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("CURRENT", created=date(2026, 8, 15), payout=None, final="100"),
            _order("PREVIOUS-PENDING", created=date(2026, 7, 24), payout=None, final="50"),
            _order(
                "PREVIOUS-RELEASED",
                created=date(2026, 7, 25),
                payout=date(2026, 7, 26),
                final="900",
            ),
        ),
        (
            _item("CURRENT", 0),
            _item("PREVIOUS-PENDING", 0),
            _item("PREVIOUS-RELEASED", 0),
        ),
    )

    report = build_shopee_my_live_analysis(
        snapshot,
        _filters(
            start=date(2026, 8, 15),
            end=date(2026, 9, 5),
            payout=PayoutStatus.PENDING,
        ),
    )

    assert report.current_period.day_count == report.previous_period.day_count == 22
    assert report.previous_period.from_date == date(2026, 7, 24)
    assert report.previous_period.to_date == date(2026, 8, 14)
    assert report.previous_kpis.total_sales == Decimal("50")


@pytest.mark.parametrize("group_by", tuple(GroupBy))
def test_group_by_changes_only_calendar_aggregation_granularity(group_by):
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("A", created=date(2026, 8, 15), final="10"),
            _order("B", created=date(2026, 8, 16), final="20"),
            _order("C", created=date(2026, 9, 1), final="30"),
        ),
        (_item("A", 0), _item("B", 0), _item("C", 0)),
    )

    report = build_shopee_my_live_analysis(
        snapshot,
        _filters(
            start=date(2026, 8, 15),
            end=date(2026, 9, 5),
            group_by=group_by,
        ),
    )

    assert report.current_kpis.orders == 3
    assert report.current_kpis.total_sales == Decimal("60")
    assert sum(point.orders for point in report.current_trend) == 3
    assert report.current_trend[0].bucket_start == date(2026, 8, 15)
    assert report.current_trend[-1].bucket_end == date(2026, 9, 5)


def test_week_buckets_anchor_to_selected_period_and_keep_partial_final_week():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("FIRST", created=date(2026, 9, 1), final="10"),
            _order("LAST", created=date(2026, 9, 30), final="20"),
        ),
        (_item("FIRST", 0), _item("LAST", 0)),
    )

    report = build_shopee_my_live_analysis(
        snapshot,
        _filters(
            start=date(2026, 9, 1),
            end=date(2026, 9, 30),
            group_by=GroupBy.WEEK,
        ),
    )

    assert [
        (point.bucket_start, point.bucket_end)
        for point in report.current_trend
    ] == [
        (date(2026, 9, 1), date(2026, 9, 7)),
        (date(2026, 9, 8), date(2026, 9, 14)),
        (date(2026, 9, 15), date(2026, 9, 21)),
        (date(2026, 9, 22), date(2026, 9, 28)),
        (date(2026, 9, 29), date(2026, 9, 30)),
    ]


def test_payout_coverage_counts_normal_orders_without_changing_kpis():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (
            _order("RELEASED", created=date(2026, 8, 2), payout=date(2026, 8, 5), final="20"),
            _order("PENDING", created=date(2026, 8, 3), payout=None, final="30"),
            _order(
                "REFUND",
                created=date(2026, 8, 4),
                payout=None,
                layout="RETURN_REFUND",
                final="-5",
                refund="-5",
            ),
        ),
        (_item("RELEASED", 0), _item("PENDING", 0), _item("REFUND", 0)),
    )

    report = build_shopee_my_live_analysis(snapshot, _filters())

    assert report.payout_coverage.released_orders == 1
    assert report.payout_coverage.pending_orders == 1
    assert report.current_kpis.total_sales == Decimal("50")
    assert report.current_kpis.orders == 2


def test_comparison_zero_baselines_and_refund_colour_semantics():
    assert compare_kpi(Decimal("10"), Decimal("0")).display == "New"
    assert compare_kpi(Decimal("0"), Decimal("0")).display == "—"
    assert compare_kpi(Decimal("0"), Decimal("10")).display == "-100.0%"
    refund_increase = compare_kpi(Decimal("20"), Decimal("10"), inverse=True)
    refund_decrease = compare_kpi(Decimal("5"), Decimal("10"), inverse=True)
    assert (refund_increase.direction, refund_increase.delta_color) == (
        "negative",
        "inverse",
    )
    assert (refund_decrease.direction, refund_decrease.delta_color) == (
        "positive",
        "inverse",
    )


def test_product_analytics_use_line_subtotal_stable_identity_top_n_and_concentration():
    orders = tuple(
        _order(f"O{index}", created=date(2026, 8, index + 1), final="999.00")
        for index in range(1, 8)
    )
    items = (
        _item("O1", 0, sku="SAME", nav="NAV-1", name="Tea old", quantity=1, line_subtotal="10"),
        _item("O2", 0, sku="SAME", nav="NAV-1", name="Tea new", quantity=2, line_subtotal="20"),
        _item("O3", 0, sku="SAME", nav="NAV-2", name="Tea", quantity=3, line_subtotal="30"),
        _item("O4", 0, sku="SKU-4", nav="NAV-4", quantity=4, line_subtotal="40"),
        _item("O5", 0, sku="SKU-5", nav="NAV-5", quantity=5, line_subtotal="50"),
        _item("O6", 0, sku="SKU-6", nav="NAV-6", quantity=6, line_subtotal="60"),
        _item("O7", 0, sku="SKU-7", nav="NAV-7", quantity=7, line_subtotal="70"),
    )
    snapshot = ShopeeMyAnalyticsSnapshot(orders, items)

    report = build_shopee_my_live_analysis(snapshot, _filters(top_n=5))
    complete_ranking = build_shopee_my_live_analysis(snapshot, _filters(top_n=10))

    same_nav_1 = next(
        row
        for row in report.top_products_by_sales
        if row.seller_sku == "SAME" and row.nav == "NAV-1"
    )
    assert same_nav_1.product_sales == Decimal("30")
    assert same_nav_1.quantity == 3
    assert any(
        row.seller_sku == "SAME" and row.nav == "NAV-2"
        for row in complete_ranking.top_products_by_sales
    )
    assert len(report.top_products_by_sales) == 5
    assert len(report.top_products_by_quantity) == 5
    assert [row.label for row in report.sales_contribution][-1] == "Others"
    assert sum(row.product_sales for row in report.sales_contribution) == Decimal("280")
    assert report.current_kpis.total_sales == Decimal("6993.00")
    assert same_nav_1.display_label.startswith("[SAME] ")
    assert "Tea" in same_nav_1.display_label
def test_snapshot_rejects_sg_and_loader_uses_only_read_methods():
    with pytest.raises(ShopeeMyAnalyticsError, match="outside persisted platform"):
        ShopeeMyAnalyticsSnapshot(
            (_order("SG", created=date(2026, 8, 1), platform="Shopee SG"),),
            (_item("SG", 0, platform="Shopee SG"),),
        )

    class ReadSpy:
        def __init__(self):
            self.calls = []

        def list_orders(self, **kwargs):
            self.calls.append(("list_orders", kwargs))
            return (_order("MY", created=date(2026, 8, 1)),)

        def get_items_by_order_ids(self, platform, order_ids):
            self.calls.append(("get_items_by_order_ids", platform, tuple(order_ids)))
            return {"MY": (_item("MY", 0),)}

        def import_invoice(self, *_args, **_kwargs):
            raise AssertionError("Live Analysis must not write")

    reader = ReadSpy()
    snapshot = load_shopee_my_analytics_snapshot(reader)

    assert [order.order_id for order in snapshot.orders] == ["MY"]
    assert reader.calls == [
        ("list_orders", {"platform": "Shopee"}),
        ("get_items_by_order_ids", "Shopee", ("MY",)),
    ]


def test_live_analysis_overwrites_legacy_dates_with_full_order_and_statement_authority():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformReportingSnapshot,
        ReportingDateAuthority,
    )

    reporting_snapshot = CrossPlatformReportingSnapshot(
        eligible_items=(),
        date_authority=ReportingDateAuthority(
            order_created_dates={
                ("shopee_my", "MY"): date(2026, 9, 10),
            },
            payout_completed_dates={
                ("shopee_my", "MY"): date(2026, 9, 17),
            },
        ),
        canonical_orders=(
            _order("MY", created=date(2026, 8, 1), payout=date(2026, 8, 8), final="20"),
            _order("NO-MATCH", created=date(2026, 8, 2), payout=date(2026, 8, 9), final="30"),
        ),
        canonical_items=(
            _item("MY", 0),
            _item("NO-MATCH", 0),
        ),
    )

    snapshot = load_shopee_my_analytics_snapshot_from_reporting_snapshot(reporting_snapshot)

    assert [(order.order_id, order.order_created_date, order.payout_completed_date) for order in snapshot.orders] == [
        ("MY", date(2026, 9, 10), date(2026, 9, 17)),
        ("NO-MATCH", None, None),
    ]
    assert snapshot.available_dates(DateBasis.ORDER_CREATED) == (date(2026, 9, 10),)
    assert snapshot.available_dates(DateBasis.PAYOUT) == (date(2026, 9, 17),)


def test_missing_canonical_product_identity_fails_closed():
    snapshot = ShopeeMyAnalyticsSnapshot(
        (_order("A", created=date(2026, 8, 1)),),
        (_item("A", 0, nav=""),),
    )

    with pytest.raises(ShopeeMyAnalyticsError, match="identity is incomplete"):
        build_shopee_my_live_analysis(snapshot, _filters())
