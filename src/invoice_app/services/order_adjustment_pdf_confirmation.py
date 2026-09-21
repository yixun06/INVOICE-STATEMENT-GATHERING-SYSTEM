"""Optional PDF corroboration for existing Statement-created Order Adjustments."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from src.invoice_app.domain.order_adjustment import (
    AdjustmentImportStatus,
    CanonicalOrderAdjustment,
    OrderAdjustmentImportResult,
)
from src.invoice_app.services.historical_invoice_intake import InvoiceIntakeEntry


class AdjustmentEvidenceNeedsReview(RuntimeError):
    """More than one persisted event could plausibly own the PDF evidence."""


def confirm_pdf_adjustment_evidence(
    entry: InvoiceIntakeEntry,
    existing_events: Iterable[CanonicalOrderAdjustment],
) -> OrderAdjustmentImportResult | None:
    """Return a controlled evidence update; never create an event from a PDF.

    The caller persists the returned event through its adjustment repository.
    This remains independent of Invoice NEW/ALREADY_IMPORTED/CLOSED status.
    """
    evidence = entry.post_order_adjustment
    if evidence is None or not entry.order_id:
        return None
    try:
        evidence_date = datetime.strptime(
            evidence.adjustment_complete_date, "%d/%m/%Y"
        ).date()
    except ValueError:
        return None
    events = tuple(existing_events)
    same_order_and_type = tuple(
        event for event in events
        if event.linked_order_id == entry.order_id
        and event.adjustment_type == evidence.adjustment_type
    )
    exact = tuple(
        event for event in same_order_and_type
        if event.adjustment_complete_date == evidence_date
        and event.adjustment_amount == evidence.released_amount
    )
    if len(exact) == 1:
        return OrderAdjustmentImportResult(
            AdjustmentImportStatus.PDF_CONFIRMED,
            _with_pdf_evidence(exact[0], entry, conflict=False),
        )
    if len(exact) > 1 or len(same_order_and_type) > 1:
        raise AdjustmentEvidenceNeedsReview(
            "PDF adjustment evidence matches more than one Statement event."
        )
    if len(same_order_and_type) == 1:
        return OrderAdjustmentImportResult(
            AdjustmentImportStatus.EVIDENCE_CONFLICT,
            _with_pdf_evidence(same_order_and_type[0], entry, conflict=True),
        )
    return None


def _with_pdf_evidence(
    event: CanonicalOrderAdjustment,
    entry: InvoiceIntakeEntry,
    *,
    conflict: bool,
) -> CanonicalOrderAdjustment:
    assert entry.post_order_adjustment is not None
    evidence = entry.post_order_adjustment
    evidence_date = datetime.strptime(
        evidence.adjustment_complete_date, "%d/%m/%Y"
    ).date()
    final_amount = entry.bundle.order.final_amount if entry.bundle is not None else None
    return event.with_pdf_evidence(
        evidence_date=evidence_date,
        evidence_amount=evidence.released_amount,
        evidence_final_amount=final_amount,
        evidence_reason=evidence.adjustment_reason,
        source_pdf=entry.source_filename,
        source_hash=entry.source_hash,
        conflict=conflict,
    )
