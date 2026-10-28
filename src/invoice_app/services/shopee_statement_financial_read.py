"""Read-only committed Statement financial facts for one market.

This reader is deliberately Statement-only.  It reads no Full Order, product,
or Invoice authority; callers that need cross-authority order composition own
that explicit join at their projection boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable, Protocol

from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.market_context import MarketContext, resolve_market_context


class StatementFinancialReadError(RuntimeError):
    """Committed Statement financial evidence cannot be safely read."""


class StatementFinancialRecordState(str, Enum):
    """Statement-evidence state; cross-authority projections add missing-order state."""

    AVAILABLE = "AVAILABLE"
    DUPLICATE_STATEMENT = "DUPLICATE_STATEMENT"
    CONFLICTING_STATEMENT = "CONFLICTING_STATEMENT"


class CommittedStatementFinancialReader(Protocol):
    @property
    def persisted_platform(self) -> str: ...

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts: ...


@dataclass(frozen=True)
class StatementFinancialRecord:
    """Committed native Statement evidence for one exact market/order identity."""

    market: str
    order_id: str
    state: StatementFinancialRecordState
    payout_completed_date: date | None
    released_amount: Decimal | None
    statement_product_price: Decimal | None
    order_facts: tuple[CommittedStatementOrderFact, ...]
    financial_components: tuple[CommittedStatementFinancialComponentFact, ...]
    refund_components: tuple[CommittedStatementFinancialComponentFact, ...]


@dataclass(frozen=True)
class StatementFinancialReadResult:
    """Market-isolated committed Statement facts plus unlinked components."""

    market: str
    records: tuple[StatementFinancialRecord, ...]
    unlinked_components: tuple[CommittedStatementFinancialComponentFact, ...]


def read_committed_statement_financials(
    *,
    market_context: MarketContext,
    statement_reader: CommittedStatementFinancialReader,
    order_ids: Iterable[str] = (),
) -> StatementFinancialReadResult:
    """Return committed native Statement facts only, without Full Order access."""

    context = resolve_market_context(market_context)
    if statement_reader.persisted_platform != context.persisted_platform:
        raise StatementFinancialReadError(
            "Statement reader market does not match the requested market context."
        )
    requested = {_normalized_order_id(value) for value in order_ids if _normalized_order_id(value)}
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

    records = []
    for order_id in sorted(requested or set(by_order)):
        order_facts = tuple(by_order.get(order_id, ()))
        if not order_facts:
            continue
        components = tuple(components_by_order.get(order_id, ()))
        state, resolved = _resolve_statement_evidence(order_facts)
        records.append(
            StatementFinancialRecord(
                market=context.key.value,
                order_id=order_id,
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
        )
    return StatementFinancialReadResult(
        market=context.key.value,
        records=tuple(records),
        unlinked_components=tuple(unlinked),
    )


def _resolve_statement_evidence(
    facts: tuple[CommittedStatementOrderFact, ...],
) -> tuple[StatementFinancialRecordState, CommittedStatementOrderFact | None]:
    if len(facts) == 1:
        return StatementFinancialRecordState.AVAILABLE, facts[0]
    stable_values = {
        (fact.payout_completed_date, fact.total_released_amount, fact.statement_product_price)
        for fact in facts
    }
    if len(stable_values) == 1:
        return StatementFinancialRecordState.DUPLICATE_STATEMENT, None
    return StatementFinancialRecordState.CONFLICTING_STATEMENT, None


def _normalized_order_id(value: object) -> str:
    return str(value or "").strip()
