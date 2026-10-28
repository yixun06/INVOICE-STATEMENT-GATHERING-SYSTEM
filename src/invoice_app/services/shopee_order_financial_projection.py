"""Canonical read-only order-level financial projection for Shopee MY and SG.

This composes the existing market-locked Full Order and committed Statement
readers.  It deliberately keeps native component and adjustment evidence at
order grain: no Invoice/PDF source, Product Master lookup, item allocation, or
released-plus-adjustment "net income" calculation is part of this boundary.
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
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.market_context import MarketContext, resolve_market_context
from src.invoice_app.services.shopee_order_adjustment_read import (
    CommittedOrderAdjustmentReader,
    read_committed_order_adjustments,
)
from src.invoice_app.services.shopee_statement_financial_read import (
    CommittedStatementFinancialReader,
    read_committed_statement_financials,
)
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentOrder


class OrderFinancialProjectionError(RuntimeError):
    """Canonical Full Order coverage is required but not present."""


class OrderFinancialProjectionState(str, Enum):
    """The cross-authority composition state owned by this projection."""

    AVAILABLE = "AVAILABLE"
    MISSING_STATEMENT = "MISSING_STATEMENT"
    DUPLICATE_STATEMENT = "DUPLICATE_STATEMENT"
    CONFLICTING_STATEMENT = "CONFLICTING_STATEMENT"


class OrderFinancialProjectionStatementReader(
    CommittedStatementFinancialReader,
    CommittedOrderAdjustmentReader,
    Protocol,
):
    """The existing committed Statement read facets needed by this projection."""


@dataclass(frozen=True)
class OrderFinancialComponentTotal:
    """One exact persisted native component name and its order-level total."""

    component_name: str
    amount: Decimal


@dataclass(frozen=True)
class OrderFinancialProjection:
    """One market/order financial view with auditable native facts.

    ``released_amount`` and ``adjustment_total`` intentionally remain separate;
    this read model does not define a derived net-income formula.
    """

    market: str
    order_id: str
    order_created_date: date
    order_status: str
    settlement_state: OrderFinancialProjectionState
    payout_completed_date: date | None
    released_amount: Decimal | None
    statement_product_price: Decimal | None
    statement_order_facts: tuple[CommittedStatementOrderFact, ...]
    financial_components: tuple[CommittedStatementFinancialComponentFact, ...]
    component_totals: tuple[OrderFinancialComponentTotal, ...]
    refund_components: tuple[CommittedStatementFinancialComponentFact, ...]
    adjustment_total: Decimal
    adjustment_events: tuple[CanonicalOrderAdjustment, ...]


def read_order_financial_projections(
    *,
    market_context: MarketContext,
    full_order_repository: ShopeeFullOrderCurrentRepository,
    statement_reader: OrderFinancialProjectionStatementReader,
    order_ids: Iterable[str] = (),
) -> tuple[OrderFinancialProjection, ...]:
    """Read canonical financial views for one market and optional exact order IDs.

    A committed Statement ORDER without the corresponding market-bound Full
    Order is a fail-closed error.  A Full Order without committed Statement
    evidence remains a valid projection with ``MISSING_STATEMENT`` and empty
    Statement facts, allowing a future caller to select unpayout orders without
    falling back to Invoice/PDF data.
    """

    context = resolve_market_context(market_context)
    financial = read_committed_statement_financials(
        market_context=context,
        statement_reader=statement_reader,
        order_ids=order_ids,
    )
    orders = _orders_for_market(full_order_repository.read_snapshot(), context.key.value)
    statement_by_order = {record.order_id: record for record in financial.records}
    requested = {_normalized_order_id(value) for value in order_ids if _normalized_order_id(value)}
    identities = requested or (set(orders) | set(statement_by_order))
    missing_full_order = tuple(
        order_id for order_id in sorted(identities)
        if order_id in statement_by_order and order_id not in orders
    )
    if missing_full_order:
        raise OrderFinancialProjectionError(
            "Committed Statement ORDER evidence has no matching Full Order Current "
            f"record in {context.key.value}: {', '.join(missing_full_order[:5])}."
        )

    adjustments = read_committed_order_adjustments(
        market_context=context,
        statement_reader=statement_reader,
        order_ids=identities,
    )
    events_by_order: dict[str, list[CanonicalOrderAdjustment]] = defaultdict(list)
    for event in adjustments.events:
        events_by_order[_normalized_order_id(event.linked_order_id)].append(event)

    projections = []
    for order_id in sorted(identities):
        order = orders.get(order_id)
        if order is None:
            continue
        record = statement_by_order.get(order_id)
        events = tuple(events_by_order[order_id])
        projections.append(
            OrderFinancialProjection(
                market=context.key.value,
                order_id=order_id,
                order_created_date=order.order_created_date,
                order_status=order.order_status,
                settlement_state=(
                    OrderFinancialProjectionState.MISSING_STATEMENT
                    if record is None
                    else OrderFinancialProjectionState(record.state.value)
                ),
                payout_completed_date=None if record is None else record.payout_completed_date,
                released_amount=None if record is None else record.released_amount,
                statement_product_price=None if record is None else record.statement_product_price,
                statement_order_facts=() if record is None else record.order_facts,
                financial_components=() if record is None else record.financial_components,
                component_totals=_component_totals(() if record is None else record.financial_components),
                refund_components=() if record is None else record.refund_components,
                adjustment_total=sum((event.adjustment_amount for event in events), Decimal("0.00")),
                adjustment_events=events,
            )
        )
    return tuple(projections)


def _component_totals(
    components: Iterable[CommittedStatementFinancialComponentFact],
) -> tuple[OrderFinancialComponentTotal, ...]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for component in components:
        totals[component.component_name] += component.component_amount
    return tuple(
        OrderFinancialComponentTotal(component_name, amount)
        for component_name, amount in sorted(totals.items(), key=lambda entry: entry[0].casefold())
    )


def _normalized_order_id(value: object) -> str:
    return str(value or "").strip()


def _orders_for_market(
    snapshot: FullOrderCurrentSnapshot, market: str
) -> dict[str, FullOrderCurrentOrder]:
    """Read Full Order identity/status facts at the projection boundary only."""

    orders: dict[str, FullOrderCurrentOrder] = {}
    for order in snapshot.orders:
        if order.market != market:
            continue
        order_id = _normalized_order_id(order.order_id)
        if order_id in orders:
            raise OrderFinancialProjectionError(
                f"Full Order Current duplicates the market/order identity {market}/{order_id}."
            )
        orders[order_id] = order
    return orders
