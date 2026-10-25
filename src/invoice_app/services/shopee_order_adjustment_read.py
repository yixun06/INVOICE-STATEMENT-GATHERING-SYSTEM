"""Read-only, market-locked access to committed post-payout adjustments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from src.invoice_app.domain.order_adjustment import CanonicalOrderAdjustment
from src.invoice_app.services.market_context import MarketContext, resolve_market_context
from src.invoice_app.services.shopee_statement_persistence import StatementCommitState


class OrderAdjustmentReadError(RuntimeError):
    """Committed adjustment evidence cannot be safely scoped to one market."""


class CommittedOrderAdjustmentReader(Protocol):
    @property
    def persisted_platform(self) -> str: ...

    def reload_active_commit_state(self) -> StatementCommitState: ...


@dataclass(frozen=True)
class CommittedOrderAdjustmentReadResult:
    """Immutable adjustment events for exactly one market context."""

    market: str
    events: tuple[CanonicalOrderAdjustment, ...]

    def for_order(self, order_id: str) -> tuple[CanonicalOrderAdjustment, ...]:
        normalized = _normalized_order_id(order_id)
        return tuple(
            event for event in self.events
            if _normalized_order_id(event.linked_order_id) == normalized
        )


def read_committed_order_adjustments(
    *,
    market_context: MarketContext,
    statement_reader: CommittedOrderAdjustmentReader,
    order_ids: Iterable[str] = (),
) -> CommittedOrderAdjustmentReadResult:
    """Return committed events by exact market/order identity, never a fallback."""

    context = resolve_market_context(market_context)
    if statement_reader.persisted_platform != context.persisted_platform:
        raise OrderAdjustmentReadError(
            "Statement reader market does not match the requested adjustment market."
        )
    requested = {_normalized_order_id(value) for value in order_ids if _normalized_order_id(value)}
    state = statement_reader.reload_active_commit_state()
    events = tuple(
        event
        for event in state.order_adjustments
        if event.platform == context.persisted_platform
        and (not requested or _normalized_order_id(event.linked_order_id) in requested)
    )
    return CommittedOrderAdjustmentReadResult(
        market=context.key.value,
        events=tuple(sorted(events, key=lambda event: (
            event.adjustment_complete_date,
            event.linked_order_id,
            event.adjustment_event_fingerprint,
        ))),
    )


def _normalized_order_id(value: object) -> str:
    return str(value or "").strip()
