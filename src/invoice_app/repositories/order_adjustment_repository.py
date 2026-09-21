"""Persistence-neutral repository for independent Order Adjustment events."""

from __future__ import annotations

from typing import Iterable, Protocol

from src.invoice_app.domain.order_adjustment import (
    AdjustmentImportStatus,
    CanonicalOrderAdjustment,
    OrderAdjustmentImportResult,
    classify_statement_events,
)


class OrderAdjustmentRepository(Protocol):
    def read_by_event_fingerprint(self, fingerprint: str) -> CanonicalOrderAdjustment | None: ...
    def read_by_linked_order_id(self, linked_order_id: str) -> tuple[CanonicalOrderAdjustment, ...]: ...
    def classify_events(self, events: Iterable[CanonicalOrderAdjustment]) -> tuple[OrderAdjustmentImportResult, ...]: ...
    def insert_new(self, events: Iterable[CanonicalOrderAdjustment]) -> tuple[OrderAdjustmentImportResult, ...]: ...
    def enrich_pdf_evidence(self, event: CanonicalOrderAdjustment) -> CanonicalOrderAdjustment: ...


class InMemoryOrderAdjustmentRepository:
    """Test/reference repository; never mutates Statement-owned event fields."""

    def __init__(self, events: Iterable[CanonicalOrderAdjustment] = ()) -> None:
        self._events = {event.adjustment_event_fingerprint: event for event in events}

    def read_by_event_fingerprint(self, fingerprint: str) -> CanonicalOrderAdjustment | None:
        return self._events.get(fingerprint)

    def read_by_linked_order_id(self, linked_order_id: str) -> tuple[CanonicalOrderAdjustment, ...]:
        return tuple(event for event in self._events.values() if event.linked_order_id == linked_order_id)

    def classify_events(self, events: Iterable[CanonicalOrderAdjustment]) -> tuple[OrderAdjustmentImportResult, ...]:
        return classify_statement_events(events, self._events.values())

    def insert_new(self, events: Iterable[CanonicalOrderAdjustment]) -> tuple[OrderAdjustmentImportResult, ...]:
        results = self.classify_events(events)
        for result in results:
            if result.status is AdjustmentImportStatus.NEW:
                self._events[result.event.adjustment_event_fingerprint] = result.event
        return results

    def enrich_pdf_evidence(self, event: CanonicalOrderAdjustment) -> CanonicalOrderAdjustment:
        existing = self._events.get(event.adjustment_event_fingerprint)
        if existing is None:
            raise KeyError("Order Adjustment event is not persisted.")
        statement_fields = (
            "platform", "linked_order_id", "adjustment_type", "adjustment_description",
            "adjustment_reason", "adjustment_complete_date", "adjustment_amount",
            "payout_completed_date", "statement_batch_id", "statement_sequence_no",
            "statement_source_filename", "statement_file_hash", "adjustment_event_fingerprint",
            "first_observed_at",
        )
        if any(getattr(existing, field) != getattr(event, field) for field in statement_fields):
            raise ValueError("PDF enrichment attempted to change immutable Statement-owned facts.")
        self._events[event.adjustment_event_fingerprint] = event
        return event
