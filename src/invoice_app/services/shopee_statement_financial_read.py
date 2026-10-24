"""Read-only Full Order + committed Statement financial projection.

This is deliberately a projection, not a second persistence or reconciliation
engine.  It joins exact market/order identities after Statement rows have
already passed the native import and commit workflow.  It never consults the
legacy Invoice/PDF tables and never allocates order-level money to items.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable, Protocol

from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.market_context import MarketContext, resolve_market_context
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentOrder


class StatementFinancialReadError(RuntimeError):
    """The authoritative Full Order/Statement projection is unsafe to read."""


class StatementFinancialDateBasis(str, Enum):
    ORDER_CREATED = "ORDER_CREATED"
    PAYOUT_COMPLETED = "PAYOUT_COMPLETED"


class StatementFinancialRecordState(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING_STATEMENT = "MISSING_STATEMENT"
    UNMATCHED_FULL_ORDER = "UNMATCHED_FULL_ORDER"
    DUPLICATE_STATEMENT = "DUPLICATE_STATEMENT"
    CONFLICTING_STATEMENT = "CONFLICTING_STATEMENT"


class CommittedStatementFinancialReader(Protocol):
    @property
    def persisted_platform(self) -> str: ...

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts: ...


@dataclass(frozen=True)
class StatementFinancialRecord:
    """One exact MY/SG order identity with explicit financial-evidence state."""

    market: str
    order_id: str
    order_created_date: date | None
    state: StatementFinancialRecordState
    payout_completed_date: date | None
    released_amount: Decimal | None
    statement_product_price: Decimal | None
    order_facts: tuple[CommittedStatementOrderFact, ...]
    financial_components: tuple[CommittedStatementFinancialComponentFact, ...]
    refund_components: tuple[CommittedStatementFinancialComponentFact, ...]


@dataclass(frozen=True)
class StatementFinancialReadResult:
    """Bounded market-isolated projection plus unlinked adjustment components."""

    market: str
    records: tuple[StatementFinancialRecord, ...]
    unlinked_components: tuple[CommittedStatementFinancialComponentFact, ...]


def read_committed_statement_financials(
    *,
    market_context: MarketContext,
    full_order_repository: ShopeeFullOrderCurrentRepository,
    statement_reader: CommittedStatementFinancialReader,
    order_ids: Iterable[str] = (),
    date_basis: StatementFinancialDateBasis | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> StatementFinancialReadResult:
    """Return only committed Statement evidence joined to Full Order Current.

    ``order_ids`` remains bounded by caller intent.  A date interval is an
    optional additional filter and requires one explicit basis.  Duplicate and
    conflicting committed ORDER evidence is surfaced as state rather than
    silently merged; raw component source rows remain available for audit.
    """

    context = resolve_market_context(market_context)
    if statement_reader.persisted_platform != context.persisted_platform:
        raise StatementFinancialReadError(
            "Statement reader market does not match the requested Full Order market."
        )
    if (from_date is None) != (to_date is None):
        raise ValueError("Both from_date and to_date are required for a financial date range.")
    if from_date is not None and to_date is not None:
        if date_basis is None:
            raise ValueError("date_basis is required when filtering a financial date range.")
        if from_date > to_date:
            raise ValueError("from_date must not be after to_date.")

    requested = {_normalized_order_id(value) for value in order_ids if _normalized_order_id(value)}
    current = full_order_repository.read_snapshot()
    orders = _orders_for_market(current, context.key.value)
    facts = statement_reader.read_committed_financial_facts()
    by_order: dict[str, list[CommittedStatementOrderFact]] = {}
    for fact in facts.order_facts:
        by_order.setdefault(_normalized_order_id(fact.order_id), []).append(fact)
    components_by_order: dict[str, list[CommittedStatementFinancialComponentFact]] = {}
    unlinked: list[CommittedStatementFinancialComponentFact] = []
    for component in facts.financial_components:
        if component.order_id is None:
            unlinked.append(component)
        else:
            components_by_order.setdefault(_normalized_order_id(component.order_id), []).append(component)

    identities = requested or (set(orders) | set(by_order))
    records = []
    for order_id in sorted(identities):
        order = orders.get(order_id)
        order_facts = tuple(by_order.get(order_id, ()))
        components = tuple(components_by_order.get(order_id, ()))
        state, resolved = _resolve_order_evidence(order, order_facts)
        record = StatementFinancialRecord(
            market=context.key.value,
            order_id=order_id,
            order_created_date=None if order is None else order.order_created_date,
            state=state,
            payout_completed_date=None if resolved is None else resolved.payout_completed_date,
            released_amount=None if resolved is None else resolved.total_released_amount,
            statement_product_price=(
                None if resolved is None else resolved.statement_product_price
            ),
            order_facts=order_facts,
            financial_components=components,
            refund_components=tuple(
                component
                for component in components
                if component.component_name.strip().casefold() == "refund amount"
            ),
        )
        if _matches_date_range(record, date_basis, from_date, to_date):
            records.append(record)
    return StatementFinancialReadResult(
        market=context.key.value,
        records=tuple(records),
        unlinked_components=tuple(unlinked),
    )


def _orders_for_market(
    snapshot: FullOrderCurrentSnapshot, market: str
) -> dict[str, FullOrderCurrentOrder]:
    result: dict[str, FullOrderCurrentOrder] = {}
    for order in snapshot.orders:
        if order.market != market:
            continue
        order_id = _normalized_order_id(order.order_id)
        if order_id in result:
            raise StatementFinancialReadError(
                f"Full Order Current duplicates the market/order identity {market}/{order_id}."
            )
        result[order_id] = order
    return result


def _resolve_order_evidence(
    order: FullOrderCurrentOrder | None,
    facts: tuple[CommittedStatementOrderFact, ...],
) -> tuple[StatementFinancialRecordState, CommittedStatementOrderFact | None]:
    if order is None:
        return StatementFinancialRecordState.UNMATCHED_FULL_ORDER, None
    if not facts:
        return StatementFinancialRecordState.MISSING_STATEMENT, None
    if len(facts) == 1:
        return StatementFinancialRecordState.AVAILABLE, facts[0]
    stable_values = {
        (fact.payout_completed_date, fact.total_released_amount, fact.statement_product_price)
        for fact in facts
    }
    if len(stable_values) == 1:
        return StatementFinancialRecordState.DUPLICATE_STATEMENT, None
    return StatementFinancialRecordState.CONFLICTING_STATEMENT, None


def _matches_date_range(
    record: StatementFinancialRecord,
    date_basis: StatementFinancialDateBasis | None,
    from_date: date | None,
    to_date: date | None,
) -> bool:
    if from_date is None or to_date is None:
        return True
    value = (
        record.order_created_date
        if date_basis is StatementFinancialDateBasis.ORDER_CREATED
        else record.payout_completed_date
    )
    return value is not None and from_date <= value <= to_date


def _normalized_order_id(value: object) -> str:
    return str(value or "").strip()
