"""Safe Phase A analysis projection for committed Shopee SG evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Protocol

from src.invoice_app.domain.order_adjustment import CanonicalOrderAdjustment
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.services.shopee_order_adjustment_read import (
    read_committed_order_adjustments,
)
from src.invoice_app.services.shopee_order_financial_projection import (
    OrderFinancialProjection,
    OrderFinancialProjectionState,
    OrderFinancialProjectionStatementReader,
    read_order_financial_projections,
)
ZERO_MONEY = Decimal("0.00")


class SgAnalysisStatementReader(OrderFinancialProjectionStatementReader, Protocol):
    """Read-only Statement facets consumed through the financial projection."""


class SgAnalysisDateBasis(str, Enum):
    ORDER_CREATED = "Order Created Date"
    PAYOUT = "Payout Date"


@dataclass(frozen=True)
class SgAnalysisFilters:
    date_basis: SgAnalysisDateBasis
    from_date: date
    to_date: date

    def __post_init__(self) -> None:
        if self.from_date > self.to_date:
            raise ValueError("From Date must be on or before To Date.")


@dataclass(frozen=True)
class SgAnalysisOrderRow:
    order_id: str
    order_status: str
    order_created_date: date | None
    payout_completed_date: date | None
    quantity: int
    product_subtotal: Decimal
    settlement_state: OrderFinancialProjectionState
    released_amount: Decimal | None


@dataclass(frozen=True)
class SgComponentTotal:
    component_name: str
    amount: Decimal


@dataclass(frozen=True)
class ShopeeSgAnalysisSnapshot:
    current: FullOrderCurrentSnapshot
    projections: tuple[OrderFinancialProjection, ...]
    adjustments: tuple[CanonicalOrderAdjustment, ...]

    def available_dates(self, basis: SgAnalysisDateBasis) -> tuple[date, ...]:
        if basis is SgAnalysisDateBasis.ORDER_CREATED:
            return tuple(sorted({
                order.order_created_date for order in self.current.orders
                if order.market == SHOPEE_SG.key.value
            }))
        return tuple(sorted({
            projection.payout_completed_date for projection in self.projections
            if projection.settlement_state is OrderFinancialProjectionState.AVAILABLE
            and projection.payout_completed_date is not None
        }))


@dataclass(frozen=True)
class ShopeeSgAnalysis:
    filters: SgAnalysisFilters
    rows: tuple[SgAnalysisOrderRow, ...]
    adjustments: tuple[CanonicalOrderAdjustment, ...]
    financial_records: tuple[OrderFinancialProjection, ...] = ()

    @property
    def order_count(self) -> int:
        return len(self.rows)

    @property
    def item_quantity(self) -> int:
        return sum(row.quantity for row in self.rows)

    @property
    def product_subtotal(self) -> Decimal:
        return sum((row.product_subtotal for row in self.rows), ZERO_MONEY)

    @property
    def settled_order_count(self) -> int:
        return sum(row.settlement_state is OrderFinancialProjectionState.AVAILABLE for row in self.rows)

    @property
    def released_amount(self) -> Decimal:
        return sum((row.released_amount or ZERO_MONEY for row in self.rows), ZERO_MONEY)

    def financial_record(self, order_id: str) -> OrderFinancialProjection | None:
        normalized = _normalized_order_id(order_id)
        return next(
            (record for record in self.financial_records if record.order_id == normalized),
            None,
        )

    def components_for_order(self, order_id: str):
        record = self.financial_record(order_id)
        return () if record is None else record.financial_components

    def component_totals_for_order(self, order_id: str) -> tuple[SgComponentTotal, ...]:
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO_MONEY)
        for component in self.components_for_order(order_id):
            totals[component.component_name] += component.component_amount
        return tuple(
            SgComponentTotal(name, amount)
            for name, amount in sorted(totals.items(), key=lambda value: value[0].casefold())
        )

    def adjustments_for_order(self, order_id: str) -> tuple[CanonicalOrderAdjustment, ...]:
        normalized = _normalized_order_id(order_id)
        return tuple(event for event in self.adjustments if _normalized_order_id(event.linked_order_id) == normalized)

def load_shopee_sg_analysis_snapshot(
    *,
    full_order_repository: ShopeeFullOrderCurrentRepository,
    statement_reader: SgAnalysisStatementReader,
) -> ShopeeSgAnalysisSnapshot:
    """Load the SG authorities through their existing market-locked readers."""

    current = full_order_repository.read_snapshot()
    projections = read_order_financial_projections(
        market_context=SHOPEE_SG,
        full_order_repository=full_order_repository,
        statement_reader=statement_reader,
    )
    adjustments = read_committed_order_adjustments(
        market_context=SHOPEE_SG,
        statement_reader=statement_reader,
    )
    return ShopeeSgAnalysisSnapshot(current, projections, adjustments.events)


def build_shopee_sg_analysis(
    snapshot: ShopeeSgAnalysisSnapshot,
    filters: SgAnalysisFilters,
) -> ShopeeSgAnalysis:
    """Build a read-only order-level report without Invoice/PDF or PM facts."""

    orders = {
        _normalized_order_id(order.order_id): order
        for order in snapshot.current.orders
        if order.market == SHOPEE_SG.key.value
    }
    quantities: dict[str, int] = defaultdict(int)
    subtotals: dict[str, Decimal] = defaultdict(lambda: ZERO_MONEY)
    for item in snapshot.current.items:
        if item.market != SHOPEE_SG.key.value:
            continue
        order_id = _normalized_order_id(item.order_id)
        quantities[order_id] += item.quantity
        subtotals[order_id] += item.product_subtotal

    financial = {
        _normalized_order_id(projection.order_id): projection
        for projection in snapshot.projections
    }
    if filters.date_basis is SgAnalysisDateBasis.ORDER_CREATED:
        candidates = (
            order_id for order_id, order in orders.items()
            if filters.from_date <= order.order_created_date <= filters.to_date
        )
    else:
        candidates = (
            order_id for order_id, projection in financial.items()
            if projection.settlement_state is OrderFinancialProjectionState.AVAILABLE
            and projection.payout_completed_date is not None
            and filters.from_date <= projection.payout_completed_date <= filters.to_date
        )

    rows = []
    for order_id in sorted(candidates):
        order = orders[order_id]
        projection = financial.get(order_id)
        rows.append(SgAnalysisOrderRow(
            order_id=order_id,
            order_status=order.order_status,
            order_created_date=order.order_created_date,
            payout_completed_date=None if projection is None else projection.payout_completed_date,
            quantity=quantities[order_id],
            product_subtotal=subtotals[order_id],
            settlement_state=(
                OrderFinancialProjectionState.MISSING_STATEMENT
                if projection is None
                else projection.settlement_state
            ),
            released_amount=None if projection is None else projection.released_amount,
        ))
    return ShopeeSgAnalysis(
        filters,
        tuple(rows),
        snapshot.adjustments,
        tuple(financial.values()),
    )


def _normalized_order_id(value: object) -> str:
    return str(value or "").strip()
