"""Read-only Shopee MY Live Analysis business calculations.

The service consumes canonical committed Invoice records.  It deliberately has
no Streamlit or persistence-write dependency so its financial and filtering
rules can be verified independently from the page renderer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Iterable, Protocol

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.services.product_summary_identity import (
    product_summary_group_key,
    resolve_product_summary_identity,
)
from src.invoice_app.services.cross_platform_product_summary import (
    CrossPlatformReportingSnapshot,
)
SHOPEE_MY_PERSISTED_PLATFORM = "Shopee"
NORMAL_ORDER = "NORMAL_ORDER"
RETURN_REFUND = "RETURN_REFUND"
ZERO_MONEY = Decimal("0.00")


class ShopeeMyAnalyticsError(RuntimeError):
    """Committed Shopee MY evidence cannot produce a trustworthy report."""


class DateRangeError(ValueError):
    """The requested analytical interval is invalid."""


class DateBasis(str, Enum):
    ORDER_CREATED = "Order Created Date"
    PAYOUT = "Payout Date"


class PayoutStatus(str, Enum):
    ALL = "All"
    RELEASED = "Released"
    PENDING = "Pending / No Payout Evidence"


class GroupBy(str, Enum):
    DAY = "Day"
    WEEK = "Week"
    MONTH = "Month"


class HistoricalInvoiceAnalyticsReader(Protocol):
    """Narrow read-only boundary used by Live Analysis."""

    def list_orders(
        self,
        *,
        platform: str | None = None,
        order_created_from: date | None = None,
        order_created_to: date | None = None,
    ) -> tuple[CanonicalInvoiceOrder, ...]: ...

    def get_items_by_order_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, tuple[CanonicalInvoiceItem, ...]]: ...


@dataclass(frozen=True)
class ShopeeMyAnalyticsSnapshot:
    orders: tuple[CanonicalInvoiceOrder, ...]
    items: tuple[CanonicalInvoiceItem, ...]

    def __post_init__(self) -> None:
        foreign_orders = sorted(
            {order.platform for order in self.orders if order.platform != SHOPEE_MY_PERSISTED_PLATFORM}
        )
        foreign_items = sorted(
            {item.platform for item in self.items if item.platform != SHOPEE_MY_PERSISTED_PLATFORM}
        )
        if foreign_orders or foreign_items:
            raise ShopeeMyAnalyticsError(
                "Shopee MY Live Analysis received records outside persisted platform 'Shopee'."
            )

        order_ids = {order.order_id for order in self.orders}
        orphan_items = sorted({item.order_id for item in self.items if item.order_id not in order_ids})
        if orphan_items:
            raise ShopeeMyAnalyticsError(
                "Shopee MY Live Analysis found Invoice_Items without matching Invoice_Orders."
            )

    def available_date_bounds(self, basis: DateBasis) -> tuple[date, date] | None:
        values = self.available_dates(basis, PayoutStatus.ALL)
        return (min(values), max(values)) if values else None

    def available_dates(
        self,
        basis: DateBasis,
        payout_status: PayoutStatus = PayoutStatus.ALL,
    ) -> tuple[date, ...]:
        """Return distinct committed dates eligible for the selected UI scope."""

        effective_status = (
            PayoutStatus.RELEASED
            if basis is DateBasis.PAYOUT
            else payout_status
        )
        return tuple(
            sorted(
                {
                    selected_date
                    for order in self.orders
                    if (selected_date := _selected_date(order, basis)) is not None
                    and (
                        effective_status is PayoutStatus.ALL
                        or (
                            effective_status is PayoutStatus.RELEASED
                            and order.payout_completed_date is not None
                        )
                        or (
                            effective_status is PayoutStatus.PENDING
                            and order.payout_completed_date is None
                        )
                    )
                }
            )
        )


@dataclass(frozen=True)
class AnalysisFilters:
    date_basis: DateBasis
    from_date: date
    to_date: date
    payout_status: PayoutStatus = PayoutStatus.ALL
    group_by: GroupBy = GroupBy.DAY
    top_n: int = 10

    def __post_init__(self) -> None:
        if self.from_date > self.to_date:
            raise DateRangeError("From Date must be on or before To Date.")
        if self.top_n not in {5, 10, 20}:
            raise ValueError("Top N must be 5, 10, or 20.")

    @property
    def effective_payout_status(self) -> PayoutStatus:
        return (
            PayoutStatus.RELEASED
            if self.date_basis is DateBasis.PAYOUT
            else self.payout_status
        )


@dataclass(frozen=True)
class Period:
    from_date: date
    to_date: date

    @property
    def day_count(self) -> int:
        return (self.to_date - self.from_date).days + 1


@dataclass(frozen=True)
class KpiValues:
    total_sales: Decimal
    orders: int
    units_sold: int
    average_order_value: Decimal
    refund_amount: Decimal


@dataclass(frozen=True)
class KpiComparison:
    current: Decimal
    previous: Decimal
    display: str
    direction: str
    delta_color: str


@dataclass(frozen=True)
class TimeSeriesPoint:
    bucket_start: date
    bucket_end: date
    sales: Decimal
    orders: int
    position: int


@dataclass(frozen=True)
class ProductAnalyticsRow:
    nav: str
    seller_sku: str
    unit_price: Decimal
    description: str
    display_label: str
    quantity: int
    product_sales: Decimal


@dataclass(frozen=True)
class ContributionRow:
    label: str
    description: str
    product_sales: Decimal


@dataclass(frozen=True)
class PayoutCoverage:
    released_orders: int
    pending_orders: int

    @property
    def total_orders(self) -> int:
        return self.released_orders + self.pending_orders


@dataclass(frozen=True)
class ShopeeMyLiveAnalysis:
    filters: AnalysisFilters
    current_period: Period
    previous_period: Period
    current_kpis: KpiValues
    previous_kpis: KpiValues
    comparisons: dict[str, KpiComparison]
    current_trend: tuple[TimeSeriesPoint, ...]
    previous_trend: tuple[TimeSeriesPoint, ...]
    top_products_by_sales: tuple[ProductAnalyticsRow, ...]
    top_products_by_quantity: tuple[ProductAnalyticsRow, ...]
    sales_contribution: tuple[ContributionRow, ...]
    payout_coverage: PayoutCoverage
    unknown_layout_count: int


def load_shopee_my_analytics_snapshot(
    repository: HistoricalInvoiceAnalyticsReader,
) -> ShopeeMyAnalyticsSnapshot:
    """Read both canonical tabs through the existing repository snapshot path."""

    orders = repository.list_orders(platform=SHOPEE_MY_PERSISTED_PLATFORM)
    order_ids = tuple(order.order_id for order in orders)
    items_by_order = repository.get_items_by_order_ids(
        SHOPEE_MY_PERSISTED_PLATFORM, order_ids
    )
    items = tuple(
        item
        for order_id in order_ids
        for item in items_by_order.get(order_id, ())
    )
    return ShopeeMyAnalyticsSnapshot(orders=orders, items=items)


def load_shopee_my_analytics_snapshot_from_reporting_snapshot(
    reporting_snapshot: CrossPlatformReportingSnapshot,
) -> ShopeeMyAnalyticsSnapshot:
    """Use legacy financial facts with the active Full Order/Statement date authority.

    Financial layout and amounts remain untouched in this MY-01 phase.  The
    report's two date facts are deliberately overwritten from the active
    reporting projection so no Invoice/PDF date can leak into filtering.
    """

    if reporting_snapshot.date_authority is None:
        raise ShopeeMyAnalyticsError(
            "Shopee MY Live Analysis requires Full Order Current and committed Statement date authority."
        )
    orders = tuple(
        replace(
            order,
            order_created_date=reporting_snapshot.date_authority.dates_for(
                order.platform, order.order_id
            )[0],
            payout_completed_date=reporting_snapshot.date_authority.dates_for(
                order.platform, order.order_id
            )[1],
        )
        for order in reporting_snapshot.canonical_orders
        if order.platform == SHOPEE_MY_PERSISTED_PLATFORM
    )
    order_ids = {order.order_id for order in orders}
    items = tuple(
        item
        for item in reporting_snapshot.canonical_items
        if item.platform == SHOPEE_MY_PERSISTED_PLATFORM and item.order_id in order_ids
    )
    return ShopeeMyAnalyticsSnapshot(orders=orders, items=items)


def build_shopee_my_live_analysis(
    snapshot: ShopeeMyAnalyticsSnapshot,
    filters: AnalysisFilters,
) -> ShopeeMyLiveAnalysis:
    current_period = Period(filters.from_date, filters.to_date)
    previous_period = _previous_period(current_period)

    current_orders = _filter_orders(snapshot.orders, current_period, filters)
    previous_orders = _filter_orders(snapshot.orders, previous_period, filters)
    items_by_order = _items_by_order(snapshot.items)

    current_normal = tuple(
        order for order in current_orders if _layout(order) == NORMAL_ORDER
    )
    previous_normal = tuple(
        order for order in previous_orders if _layout(order) == NORMAL_ORDER
    )
    current_refunds = tuple(
        order for order in current_orders if _layout(order) == RETURN_REFUND
    )
    previous_refunds = tuple(
        order for order in previous_orders if _layout(order) == RETURN_REFUND
    )

    current_items = _items_for_orders(items_by_order, current_normal)
    previous_items = _items_for_orders(items_by_order, previous_normal)
    current_kpis = _kpis(current_normal, current_refunds, current_items)
    previous_kpis = _kpis(previous_normal, previous_refunds, previous_items)
    product_rows = _aggregate_products(current_items)

    return ShopeeMyLiveAnalysis(
        filters=filters,
        current_period=current_period,
        previous_period=previous_period,
        current_kpis=current_kpis,
        previous_kpis=previous_kpis,
        comparisons=_comparisons(current_kpis, previous_kpis),
        current_trend=_trend(current_normal, current_period, filters),
        previous_trend=_trend(previous_normal, previous_period, filters),
        top_products_by_sales=tuple(
            sorted(
                product_rows,
                key=lambda row: (-row.product_sales, row.seller_sku, row.nav),
            )[: filters.top_n]
        ),
        top_products_by_quantity=tuple(
            sorted(
                product_rows,
                key=lambda row: (-row.quantity, row.seller_sku, row.nav),
            )[: filters.top_n]
        ),
        sales_contribution=_contribution(product_rows),
        payout_coverage=_payout_coverage(current_normal),
        unknown_layout_count=sum(
            _layout(order) not in {NORMAL_ORDER, "CANCELLED_ORDER", RETURN_REFUND}
            for order in current_orders
        ),
    )


def _previous_period(current: Period) -> Period:
    previous_to = current.from_date - timedelta(days=1)
    return Period(
        previous_to - timedelta(days=current.day_count - 1),
        previous_to,
    )


def _filter_orders(
    orders: tuple[CanonicalInvoiceOrder, ...],
    period: Period,
    filters: AnalysisFilters,
) -> tuple[CanonicalInvoiceOrder, ...]:
    result = []
    payout_status = filters.effective_payout_status
    for order in orders:
        selected_date = _selected_date(order, filters.date_basis)
        if selected_date is None or not period.from_date <= selected_date <= period.to_date:
            continue
        has_payout = order.payout_completed_date is not None
        if payout_status is PayoutStatus.RELEASED and not has_payout:
            continue
        if payout_status is PayoutStatus.PENDING and has_payout:
            continue
        result.append(order)
    return tuple(result)


def _selected_date(order: CanonicalInvoiceOrder, basis: DateBasis) -> date | None:
    return (
        order.order_created_date
        if basis is DateBasis.ORDER_CREATED
        else order.payout_completed_date
    )


def _layout(order: CanonicalInvoiceOrder) -> str:
    return str(order.invoice_financial_layout or "").strip().upper()


def _items_by_order(
    items: tuple[CanonicalInvoiceItem, ...],
) -> dict[str, tuple[CanonicalInvoiceItem, ...]]:
    grouped: dict[str, list[CanonicalInvoiceItem]] = defaultdict(list)
    for item in items:
        grouped[item.order_id].append(item)
    return {order_id: tuple(values) for order_id, values in grouped.items()}


def _items_for_orders(
    items_by_order: dict[str, tuple[CanonicalInvoiceItem, ...]],
    orders: tuple[CanonicalInvoiceOrder, ...],
) -> tuple[CanonicalInvoiceItem, ...]:
    return tuple(
        item for order in orders for item in items_by_order.get(order.order_id, ())
    )


def _kpis(
    normal_orders: tuple[CanonicalInvoiceOrder, ...],
    refund_orders: tuple[CanonicalInvoiceOrder, ...],
    normal_items: tuple[CanonicalInvoiceItem, ...],
) -> KpiValues:
    total_sales = sum(
        (order.final_amount for order in normal_orders if order.final_amount is not None),
        ZERO_MONEY,
    )
    order_count = len({order.order_id for order in normal_orders})
    units_sold = sum(item.quantity or 0 for item in normal_items)
    raw_refund = sum(
        (order.refund_amount for order in refund_orders if order.refund_amount is not None),
        ZERO_MONEY,
    )
    return KpiValues(
        total_sales=total_sales,
        orders=order_count,
        units_sold=units_sold,
        average_order_value=(
            total_sales / Decimal(order_count) if order_count else ZERO_MONEY
        ),
        refund_amount=abs(raw_refund),
    )


def _comparisons(
    current: KpiValues, previous: KpiValues
) -> dict[str, KpiComparison]:
    values = {
        "total_sales": (current.total_sales, previous.total_sales, False),
        "orders": (Decimal(current.orders), Decimal(previous.orders), False),
        "units_sold": (Decimal(current.units_sold), Decimal(previous.units_sold), False),
        "average_order_value": (
            current.average_order_value,
            previous.average_order_value,
            False,
        ),
        "refund_amount": (current.refund_amount, previous.refund_amount, True),
    }
    return {
        key: compare_kpi(current_value, previous_value, inverse=inverse)
        for key, (current_value, previous_value, inverse) in values.items()
    }


def compare_kpi(
    current: Decimal, previous: Decimal, *, inverse: bool = False
) -> KpiComparison:
    if previous == 0:
        if current == 0:
            return KpiComparison(current, previous, "—", "neutral", "off")
        return KpiComparison(
            current,
            previous,
            "New",
            "negative" if inverse else "positive",
            "inverse" if inverse else "normal",
        )
    percent = (current - previous) / previous * Decimal("100")
    if percent == 0:
        return KpiComparison(current, previous, "0.0%", "neutral", "off")
    improving = percent < 0 if inverse else percent > 0
    return KpiComparison(
        current=current,
        previous=previous,
        display=f"{percent:+.1f}%",
        direction="positive" if improving else "negative",
        delta_color="inverse" if inverse else "normal",
    )


def _trend(
    orders: tuple[CanonicalInvoiceOrder, ...],
    period: Period,
    filters: AnalysisFilters,
) -> tuple[TimeSeriesPoint, ...]:
    sales_by_bucket: dict[date, Decimal] = defaultdict(lambda: ZERO_MONEY)
    orders_by_bucket: dict[date, set[str]] = defaultdict(set)
    for order in orders:
        selected_date = _selected_date(order, filters.date_basis)
        assert selected_date is not None
        bucket = _bucket_start_for_period(selected_date, filters.group_by, period)
        if order.final_amount is not None:
            sales_by_bucket[bucket] += order.final_amount
        orders_by_bucket[bucket].add(order.order_id)

    buckets = _period_buckets(period, filters.group_by)
    return tuple(
        TimeSeriesPoint(
            bucket_start=max(bucket, period.from_date),
            bucket_end=min(
                _bucket_end(bucket, filters.group_by), period.to_date
            ),
            sales=sales_by_bucket[bucket],
            orders=len(orders_by_bucket[bucket]),
            position=position,
        )
        for position, bucket in enumerate(buckets, start=1)
    )


def _period_buckets(period: Period, group_by: GroupBy) -> tuple[date, ...]:
    values: list[date] = []
    cursor = period.from_date
    while cursor <= period.to_date:
        bucket = _bucket_start_for_period(cursor, group_by, period)
        if not values or values[-1] != bucket:
            values.append(bucket)
        cursor += timedelta(days=1)
    return tuple(values)


def _bucket_start_for_period(value: date, group_by: GroupBy, period: Period) -> date:
    if group_by is GroupBy.WEEK:
        offset = (value - period.from_date).days
        return period.from_date + timedelta(days=(offset // 7) * 7)
    return _bucket_start(value, group_by)


def _bucket_start(value: date, group_by: GroupBy) -> date:
    if group_by is GroupBy.DAY:
        return value
    if group_by is GroupBy.WEEK:
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _bucket_end(value: date, group_by: GroupBy) -> date:
    if group_by is GroupBy.DAY:
        return value
    if group_by is GroupBy.WEEK:
        return value + timedelta(days=6)
    next_month = (
        value.replace(year=value.year + 1, month=1)
        if value.month == 12
        else value.replace(month=value.month + 1)
    )
    return next_month - timedelta(days=1)


def _aggregate_products(
    items: tuple[CanonicalInvoiceItem, ...],
) -> tuple[ProductAnalyticsRow, ...]:
    groups: dict[tuple[str, str, Decimal], list[CanonicalInvoiceItem]] = defaultdict(list)
    descriptions: dict[tuple[str, str, Decimal], list[str]] = defaultdict(list)
    for item in items:
        sku = str(item.resolved_seller_sku or item.seller_sku or "").strip()
        nav = str(item.nav or "").strip()
        name = str(item.product_name or "").strip()
        if not sku or not nav or not name or item.unit_price is None:
            raise ShopeeMyAnalyticsError(
                f"{item.order_id}/{item.item_index}: committed product identity is incomplete."
            )
        identity = resolve_product_summary_identity(
            nav=nav,
            seller_sku=sku,
            product_name=name,
            variation=item.variation,
            historical_pm_unit_price=item.unit_price,
        )
        key = product_summary_group_key(identity=identity, resolved_sku=sku)
        groups[key].append(item)
        if identity.display_description not in descriptions[key]:
            descriptions[key].append(identity.display_description)

    rows = []
    for key, members in groups.items():
        description = " | ".join(descriptions[key])
        rows.append(
            ProductAnalyticsRow(
                nav=key[0],
                seller_sku=key[1],
                unit_price=key[2],
                description=description,
                display_label=_product_label(description, key[1]),
                quantity=sum(item.quantity or 0 for item in members),
                product_sales=sum(
                    (item.line_subtotal for item in members if item.line_subtotal is not None),
                    ZERO_MONEY,
                ),
            )
        )
    return tuple(rows)


def _product_label(description: str, sku: str, *, limit: int = 84) -> str:
    readable = f"[{sku}] {description}"
    return readable if len(readable) <= limit else readable[: limit - 1].rstrip() + "…"


def _contribution(
    rows: tuple[ProductAnalyticsRow, ...],
) -> tuple[ContributionRow, ...]:
    ranked = tuple(
        sorted(
            (row for row in rows if row.product_sales > 0),
            key=lambda row: (-row.product_sales, row.seller_sku, row.nav),
        )
    )
    top = ranked[:5]
    result = [
        ContributionRow(row.display_label, row.description, row.product_sales)
        for row in top
    ]
    others = sum((row.product_sales for row in ranked[5:]), ZERO_MONEY)
    if others:
        result.append(ContributionRow("Others", "All remaining products", others))
    return tuple(result)


def _payout_coverage(
    normal_orders: tuple[CanonicalInvoiceOrder, ...],
) -> PayoutCoverage:
    released = {
        order.order_id
        for order in normal_orders
        if order.payout_completed_date is not None
    }
    pending = {
        order.order_id
        for order in normal_orders
        if order.payout_completed_date is None
    }
    return PayoutCoverage(
        released_orders=len(released),
        pending_orders=len(pending),
    )
