"""Shared canonical analysis view model for one Shopee market.

Providers load market-locked Full Order and Statement facts, while consumers
receive only this normalized read model.  It deliberately has no Invoice/PDF
dependency and never allocates order-level settlement facts to product rows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable, Protocol

from src.invoice_app.domain.order_adjustment import CanonicalOrderAdjustment
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.market_context import MarketContext, resolve_market_context
from src.invoice_app.services.product_summary_identity import (
    product_summary_group_key,
    resolve_product_summary_identity,
)
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentItem
from src.invoice_app.services.shopee_order_financial_projection import (
    OrderFinancialProjection,
    OrderFinancialProjectionState,
    OrderFinancialProjectionStatementReader,
    read_order_financial_projections,
)
from src.invoice_app.utils.monetary import currency_symbol


ZERO_MONEY = Decimal("0.00")
MONEY_TOLERANCE = Decimal("0.02")


class ShopeeAnalysisError(RuntimeError):
    """Canonical market facts cannot produce a trustworthy analysis view."""


class AnalysisDateBasis(str, Enum):
    ORDER_CREATED = "Order Created Date"
    PAYOUT = "Payout Date"


class ReconciliationStatus(str, Enum):
    RECONCILED = "RECONCILED"
    MISMATCH = "MISMATCH"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ShopeeAnalysisStatementReader(OrderFinancialProjectionStatementReader, Protocol):
    """Read-only Statement facets needed by a market-specific provider."""


@dataclass(frozen=True)
class ShopeeAnalysisPresentation:
    """Market-neutral display metadata supplied by the provider, not the UI."""

    market_label: str
    currency_code: str
    currency_display: str


@dataclass(frozen=True)
class ShopeeAnalysisSnapshot:
    """One market-locked canonical input snapshot for the shared dashboard."""

    presentation: ShopeeAnalysisPresentation
    market: str
    current: FullOrderCurrentSnapshot
    projections: tuple[OrderFinancialProjection, ...]

    def __post_init__(self) -> None:
        foreign_orders = {order.market for order in self.current.orders if order.market != self.market}
        foreign_items = {item.market for item in self.current.items if item.market != self.market}
        foreign_projections = {row.market for row in self.projections if row.market != self.market}
        if foreign_orders or foreign_items or foreign_projections:
            raise ShopeeAnalysisError(
                "Shopee Analysis received facts outside its explicit market boundary."
            )

    def available_dates(self, basis: AnalysisDateBasis) -> tuple[date, ...]:
        if basis is AnalysisDateBasis.ORDER_CREATED:
            return tuple(sorted({row.order_created_date for row in self.projections}))
        return tuple(sorted({
            row.payout_completed_date
            for row in self.projections
            if row.settlement_state is OrderFinancialProjectionState.AVAILABLE
            and row.payout_completed_date is not None
        }))


@dataclass(frozen=True)
class AnalysisProductOption:
    key: str
    label: str


@dataclass(frozen=True)
class ShopeeAnalysisFilters:
    date_basis: AnalysisDateBasis
    from_date: date
    to_date: date
    product_keys: frozenset[str] = frozenset()
    order_statuses: frozenset[str] = frozenset()
    order_id_query: str = ""
    settlement_states: frozenset[OrderFinancialProjectionState] = frozenset()
    has_refund: bool | None = None
    has_adjustment: bool | None = None
    fee_types: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.from_date > self.to_date:
            raise ValueError("From Date must be on or before To Date.")


@dataclass(frozen=True)
class ShopeeAnalysisProductRow:
    key: str
    description: str
    variation: str
    nav: str
    seller_sku: str
    resolve_sku: str
    unit_price: Decimal
    quantity: int
    product_sales: Decimal


@dataclass(frozen=True)
class ShopeeAnalysisComponentTotal:
    component_name: str
    amount: Decimal


@dataclass(frozen=True)
class ShopeeAnalysisOrderRow:
    order_id: str
    order_status: str
    order_created_date: date
    payout_completed_date: date | None
    settlement_state: OrderFinancialProjectionState
    product_sales: Decimal
    quantity: int
    released_amount: Decimal | None
    charges: Decimal
    credits: Decimal
    refund_amount: Decimal
    adjustment_total: Decimal
    expected_released_amount: Decimal | None
    reconciliation_difference: Decimal | None
    reconciliation_status: ReconciliationStatus
    reconciliation_reason: str
    component_totals: tuple[ShopeeAnalysisComponentTotal, ...]
    items: tuple[FullOrderCurrentItem, ...]
    financial_components: tuple[object, ...]
    adjustment_events: tuple[CanonicalOrderAdjustment, ...]

    @property
    def settlement_difference(self) -> Decimal | None:
        return (
            None
            if self.released_amount is None
            else self.product_sales - self.released_amount
        )


@dataclass(frozen=True)
class ShopeeAnalysisReport:
    presentation: ShopeeAnalysisPresentation
    filters: ShopeeAnalysisFilters
    rows: tuple[ShopeeAnalysisOrderRow, ...]
    products: tuple[ShopeeAnalysisProductRow, ...]

    @property
    def product_sales(self) -> Decimal:
        return sum((row.product_sales for row in self.products), ZERO_MONEY)

    @property
    def total_orders(self) -> int:
        return len(self.rows)

    @property
    def units_sold(self) -> int:
        return sum(row.quantity for row in self.products)

    @property
    def average_order_value(self) -> Decimal:
        return self.product_sales / self.total_orders if self.total_orders else ZERO_MONEY

    @property
    def released_amount(self) -> Decimal | None:
        values = [row.released_amount for row in self.settled_rows]
        return sum(values, ZERO_MONEY) if values else None

    @property
    def settlement_difference(self) -> Decimal | None:
        if self.filters.product_keys:
            return None
        released = self.released_amount
        return None if released is None else self.settled_product_sales - released

    @property
    def settled_rows(self) -> tuple[ShopeeAnalysisOrderRow, ...]:
        """Rows with the exact committed Statement ORDER evidence cohort."""

        return tuple(
            row
            for row in self.rows
            if row.settlement_state is OrderFinancialProjectionState.AVAILABLE
            and row.released_amount is not None
        )

    @property
    def settled_orders(self) -> int:
        return len(self.settled_rows)

    @property
    def missing_statement_orders(self) -> int:
        return sum(
            row.settlement_state is OrderFinancialProjectionState.MISSING_STATEMENT
            for row in self.rows
        )

    @property
    def settlement_coverage(self) -> Decimal:
        return (
            Decimal(self.settled_orders) / Decimal(self.total_orders)
            if self.total_orders
            else ZERO_MONEY
        )

    @property
    def settled_product_sales(self) -> Decimal:
        return sum((row.product_sales for row in self.settled_rows), ZERO_MONEY)

    @property
    def refund_amount(self) -> Decimal:
        return abs(sum((row.refund_amount for row in self.settled_rows), ZERO_MONEY))

    @property
    def signed_refund_amount(self) -> Decimal:
        return sum((row.refund_amount for row in self.settled_rows), ZERO_MONEY)

    @property
    def adjustment_total(self) -> Decimal:
        return sum((row.adjustment_total for row in self.rows), ZERO_MONEY)

    @property
    def statement_charges(self) -> Decimal:
        return sum((row.charges for row in self.settled_rows), ZERO_MONEY)

    @property
    def statement_credits(self) -> Decimal:
        return sum((row.credits for row in self.settled_rows), ZERO_MONEY)

    @property
    def component_totals(self) -> tuple[ShopeeAnalysisComponentTotal, ...]:
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO_MONEY)
        for row in self.settled_rows:
            for component in row.component_totals:
                totals[component.component_name] += component.amount
        return tuple(
            ShopeeAnalysisComponentTotal(name, amount)
            for name, amount in sorted(totals.items(), key=lambda value: value[0].casefold())
        )

    @property
    def reconciliation_counts(self) -> dict[ReconciliationStatus, int]:
        return {
            status: sum(row.reconciliation_status is status for row in self.rows)
            for status in ReconciliationStatus
        }

    @property
    def expected_released_amount(self) -> Decimal | None:
        if self.filters.product_keys or not self.settled_rows:
            return None
        expected = [row.expected_released_amount for row in self.settled_rows]
        return None if any(value is None for value in expected) else sum(expected, ZERO_MONEY)

    @property
    def reconciliation_difference(self) -> Decimal | None:
        if self.expected_released_amount is None or self.released_amount is None:
            return None
        return self.released_amount - self.expected_released_amount

    def order(self, order_id: str) -> ShopeeAnalysisOrderRow | None:
        normalized = _text(order_id)
        return next((row for row in self.rows if row.order_id == normalized), None)


def load_shopee_analysis_snapshot(
    *,
    market_context: MarketContext,
    full_order_repository: ShopeeFullOrderCurrentRepository,
    statement_reader: ShopeeAnalysisStatementReader,
) -> ShopeeAnalysisSnapshot:
    """Load one market's canonical Full Order and financial projections only."""

    context = resolve_market_context(market_context)
    current = full_order_repository.read_snapshot()
    projections = read_order_financial_projections(
        market_context=context,
        full_order_repository=full_order_repository,
        statement_reader=statement_reader,
    )
    return ShopeeAnalysisSnapshot(
        presentation=ShopeeAnalysisPresentation(
            market_label=context.display_name,
            currency_code=context.currency_code,
            currency_display=currency_symbol(context.currency_code),
        ),
        market=context.key.value,
        current=current,
        projections=projections,
    )


def product_options(snapshot: ShopeeAnalysisSnapshot) -> tuple[AnalysisProductOption, ...]:
    values: dict[str, AnalysisProductOption] = {}
    for item in snapshot.current.items:
        key, description, variation, nav, seller_sku, unit_price = _item_identity(item)
        label = " · ".join(part for part in (description, variation, f"SKU {seller_sku}", f"NAV {nav}") if part)
        values[key] = AnalysisProductOption(key, label)
    return tuple(sorted(values.values(), key=lambda value: value.label.casefold()))


def available_statuses(snapshot: ShopeeAnalysisSnapshot) -> tuple[str, ...]:
    return tuple(sorted({
        row.order_status
        for row in snapshot.projections
        if row.order_status and _is_eligible_status(row.order_status)
    }))


def available_fee_types(snapshot: ShopeeAnalysisSnapshot) -> tuple[str, ...]:
    return tuple(sorted({
        component.component_name
        for projection in snapshot.projections
        for component in _order_level_components(projection.financial_components)
    }, key=str.casefold))


def build_shopee_analysis(
    snapshot: ShopeeAnalysisSnapshot,
    filters: ShopeeAnalysisFilters,
) -> ShopeeAnalysisReport:
    """Build a filtered shared dashboard model from one coherent input snapshot."""

    items_by_order: dict[str, list[FullOrderCurrentItem]] = defaultdict(list)
    for item in snapshot.current.items:
        items_by_order[_text(item.order_id)].append(item)
    rows: list[ShopeeAnalysisOrderRow] = []
    selected_items: list[FullOrderCurrentItem] = []
    for projection in sorted(snapshot.projections, key=lambda value: value.order_id):
        order_id = _text(projection.order_id)
        if not _is_eligible_status(projection.order_status):
            continue
        if not _matches_date(projection, filters):
            continue
        if filters.order_statuses and projection.order_status not in filters.order_statuses:
            continue
        if filters.order_id_query and filters.order_id_query.casefold().strip() not in order_id.casefold():
            continue
        if filters.settlement_states and projection.settlement_state not in filters.settlement_states:
            continue
        order_items = tuple(items_by_order.get(order_id, ()))
        matching_items = tuple(
            item for item in order_items
            if not filters.product_keys or _item_identity(item)[0] in filters.product_keys
        )
        if filters.product_keys and not matching_items:
            continue
        components = _order_level_components(projection.financial_components)
        component_names = {component.component_name for component in components}
        if filters.fee_types and not component_names.intersection(filters.fee_types):
            continue
        refund = _refund_total(components)
        if filters.has_refund is True and refund == ZERO_MONEY:
            continue
        if filters.has_refund is False and refund != ZERO_MONEY:
            continue
        events = projection.adjustment_events
        if filters.has_adjustment is True and not events:
            continue
        if filters.has_adjustment is False and events:
            continue
        product_sales = sum((item.product_subtotal for item in matching_items), ZERO_MONEY)
        component_totals = _component_totals(components)
        expected, difference, status, reason = _reconciliation(
            product_sales=product_sales,
            released_amount=projection.released_amount,
            settlement_state=projection.settlement_state,
            components=components,
            product_scope_is_partial=bool(filters.product_keys),
        )
        rows.append(ShopeeAnalysisOrderRow(
            order_id=order_id,
            order_status=projection.order_status,
            order_created_date=projection.order_created_date,
            payout_completed_date=projection.payout_completed_date,
            settlement_state=projection.settlement_state,
            product_sales=product_sales,
            quantity=sum(item.quantity for item in matching_items),
            released_amount=projection.released_amount,
            charges=sum((component.component_amount for component in components if _is_charge(component)), ZERO_MONEY),
            credits=sum((component.component_amount for component in components if _is_credit(component)), ZERO_MONEY),
            refund_amount=refund,
            adjustment_total=projection.adjustment_total,
            expected_released_amount=expected,
            reconciliation_difference=difference,
            reconciliation_status=status,
            reconciliation_reason=reason,
            component_totals=component_totals,
            items=matching_items,
            financial_components=projection.financial_components,
            adjustment_events=events,
        ))
        selected_items.extend(matching_items)
    return ShopeeAnalysisReport(
        presentation=snapshot.presentation,
        filters=filters,
        rows=tuple(rows),
        products=_aggregate_products(selected_items),
    )


def _matches_date(projection: OrderFinancialProjection, filters: ShopeeAnalysisFilters) -> bool:
    if filters.date_basis is AnalysisDateBasis.ORDER_CREATED:
        return filters.from_date <= projection.order_created_date <= filters.to_date
    return (
        projection.settlement_state is OrderFinancialProjectionState.AVAILABLE
        and projection.payout_completed_date is not None
        and filters.from_date <= projection.payout_completed_date <= filters.to_date
    )


def _is_eligible_status(status: str) -> bool:
    """Reuse the approved Full Order reporting exclusions without Invoice layouts."""

    return " ".join(_text(status).split()).casefold() not in {"to ship", "cancelled"}


def _item_identity(item: FullOrderCurrentItem) -> tuple[str, str, str, str, str, Decimal]:
    seller_sku = _text(item.resolved_seller_sku)
    nav = _text(item.nav)
    description = _text(item.product_name)
    variation = _text(item.variation)
    if not seller_sku or not nav or not description:
        raise ShopeeAnalysisError(
            f"{item.order_id}/{item.item_index}: Full Order product identity is incomplete."
        )
    identity = resolve_product_summary_identity(
        nav=nav,
        seller_sku=seller_sku,
        product_name=description,
        variation=variation,
        historical_pm_unit_price=item.unit_price,
    )
    grouped = product_summary_group_key(identity=identity, resolved_sku=seller_sku)
    key = "|".join((grouped[0], grouped[1], format(grouped[2], ".2f")))
    return key, identity.display_description, variation, nav, seller_sku, grouped[2]


def _aggregate_products(items: Iterable[FullOrderCurrentItem]) -> tuple[ShopeeAnalysisProductRow, ...]:
    grouped: dict[str, list[FullOrderCurrentItem]] = defaultdict(list)
    identity: dict[str, tuple[str, str, str, str, str, Decimal]] = {}
    for item in items:
        item_identity = _item_identity(item)
        grouped[item_identity[0]].append(item)
        identity[item_identity[0]] = item_identity
    rows = []
    for key, members in grouped.items():
        _, description, variation, nav, seller_sku, unit_price = identity[key]
        resolve_values = tuple(dict.fromkeys(_text(member.resolve_sku) for member in members if _text(member.resolve_sku)))
        rows.append(ShopeeAnalysisProductRow(
            key=key,
            description=description,
            variation=variation,
            nav=nav,
            seller_sku=seller_sku,
            resolve_sku=" | ".join(resolve_values),
            unit_price=unit_price,
            quantity=sum(member.quantity for member in members),
            product_sales=sum((member.product_subtotal for member in members), ZERO_MONEY),
        ))
    return tuple(sorted(rows, key=lambda row: (-row.product_sales, row.seller_sku, row.nav)))


def _order_level_components(components: Iterable[object]) -> tuple[object, ...]:
    """Only ORDER component rows can explain an order settlement once.

    SKU and supporting-detail records remain available at drill-down grain but
    must never multiply an order-level amount or be treated as a fee allocation.
    """

    return tuple(component for component in components if _text(getattr(component, "record_type", "")) == "ORDER")


def _component_totals(components: Iterable[object]) -> tuple[ShopeeAnalysisComponentTotal, ...]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO_MONEY)
    for component in components:
        totals[component.component_name] += component.component_amount
    return tuple(
        ShopeeAnalysisComponentTotal(name, amount)
        for name, amount in sorted(totals.items(), key=lambda value: value[0].casefold())
    )


def _refund_total(components: Iterable[object]) -> Decimal:
    return sum((
        component.component_amount
        for component in components
        if _text(component.component_name).casefold() == "refund amount"
    ), ZERO_MONEY)


def _is_charge(component: object) -> bool:
    return (
        _text(component.component_name).casefold() not in {"product price", "refund amount"}
        and component.component_amount < ZERO_MONEY
    )


def _is_credit(component: object) -> bool:
    return (
        _text(component.component_name).casefold() not in {"product price", "refund amount"}
        and component.component_amount > ZERO_MONEY
    )


def _reconciliation(
    *,
    product_sales: Decimal,
    released_amount: Decimal | None,
    settlement_state: OrderFinancialProjectionState,
    components: tuple[object, ...],
    product_scope_is_partial: bool,
) -> tuple[Decimal | None, Decimal | None, ReconciliationStatus, str]:
    if product_scope_is_partial:
        return (
            None,
            None,
            ReconciliationStatus.INSUFFICIENT_EVIDENCE,
            "Product-filtered views do not allocate order-level settlement facts to items.",
        )
    if settlement_state is not OrderFinancialProjectionState.AVAILABLE or released_amount is None:
        return None, None, ReconciliationStatus.INSUFFICIENT_EVIDENCE, "No committed Statement ORDER evidence is available."
    if not components:
        return None, None, ReconciliationStatus.INSUFFICIENT_EVIDENCE, "No committed ORDER-level financial components are available."
    # Product Price is the Statement's base-sales component.  Starting with Full
    # Order Product Sales prevents it from being counted twice.
    expected = product_sales + sum((
        component.component_amount
        for component in components
        if _text(component.component_name).casefold() != "product price"
    ), ZERO_MONEY)
    difference = released_amount - expected
    if abs(difference) <= MONEY_TOLERANCE:
        return expected, difference, ReconciliationStatus.RECONCILED, "Full Order Product Sales plus native ORDER components matches Statement released amount."
    return expected, difference, ReconciliationStatus.MISMATCH, "Full Order Product Sales plus native ORDER components does not match Statement released amount."


def _text(value: object) -> str:
    return str(value or "").strip()
