"""Canonical, statement-driven post-payment Order Adjustment evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable


RETURN_REFUND_DESCRIPTION = "Return Refund Adjustment After Order Completed"
RETURN_REFUND_AFTER_ORDER_COMPLETED = "RETURN_REFUND_AFTER_ORDER_COMPLETED"


class AdjustmentImportStatus(str, Enum):
    NEW = "NEW"
    ALREADY_IMPORTED = "ALREADY_IMPORTED"
    PDF_CONFIRMED = "PDF_CONFIRMED"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    UNMATCHED = "UNMATCHED"


class EvidenceStatus(str, Enum):
    STATEMENT_ONLY = "STATEMENT_ONLY"
    PDF_CONFIRMED = "PDF_CONFIRMED"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"


@dataclass(frozen=True)
class CanonicalOrderAdjustment:
    """One immutable Statement event plus optional corroborating PDF evidence."""

    platform: str
    linked_order_id: str
    adjustment_type: str
    adjustment_description: str
    adjustment_reason: str | None
    adjustment_complete_date: date
    adjustment_amount: Decimal
    payout_completed_date: date | None
    statement_batch_id: str
    statement_sequence_no: str
    statement_source_filename: str
    statement_file_hash: str
    adjustment_event_fingerprint: str
    evidence_status: EvidenceStatus
    invoice_evidence_date: date | None = None
    invoice_evidence_amount: Decimal | None = None
    invoice_evidence_final_amount: Decimal | None = None
    invoice_evidence_reason: str | None = None
    invoice_source_pdf: str | None = None
    invoice_source_hash: str | None = None
    first_observed_at: datetime | None = None

    def with_pdf_evidence(
        self,
        *,
        evidence_date: date,
        evidence_amount: Decimal,
        evidence_final_amount: Decimal | None,
        evidence_reason: str | None,
        source_pdf: str,
        source_hash: str,
        conflict: bool = False,
    ) -> "CanonicalOrderAdjustment":
        """Only optional PDF evidence is mutable after Statement insertion."""
        return replace(
            self,
            evidence_status=(EvidenceStatus.EVIDENCE_CONFLICT if conflict else EvidenceStatus.PDF_CONFIRMED),
            invoice_evidence_date=evidence_date,
            invoice_evidence_amount=_money(evidence_amount),
            invoice_evidence_final_amount=(None if evidence_final_amount is None else _money(evidence_final_amount)),
            invoice_evidence_reason=_optional_text(evidence_reason),
            invoice_source_pdf=_required_text(source_pdf, "invoice_source_pdf"),
            invoice_source_hash=_required_text(source_hash, "invoice_source_hash"),
        )


@dataclass(frozen=True)
class OrderAdjustmentImportResult:
    status: AdjustmentImportStatus
    event: CanonicalOrderAdjustment


def supported_adjustment_type(description: str) -> str | None:
    """Map only the exact V1 Statement description; future values stay unsupported."""
    return (
        RETURN_REFUND_AFTER_ORDER_COMPLETED
        if _normalize_text(description) == _normalize_text(RETURN_REFUND_DESCRIPTION)
        else None
    )


def adjustment_event_fingerprint(
    *,
    platform: str,
    linked_order_id: str,
    adjustment_type: str,
    adjustment_description: str,
    adjustment_reason: str | None,
    adjustment_complete_date: date,
    adjustment_amount: Decimal,
) -> str:
    """Stable identity that excludes source provenance and mutable PDF evidence."""
    payload = {
        "platform": _normalize_text(platform),
        "linked_order_id": _normalize_text(linked_order_id),
        "adjustment_type": _normalize_text(adjustment_type),
        "adjustment_description": _normalize_text(adjustment_description),
        "adjustment_reason": _normalize_text(adjustment_reason or ""),
        "adjustment_complete_date": adjustment_complete_date.isoformat(),
        "adjustment_amount": _money(adjustment_amount).to_eng_string(),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def make_statement_adjustment(
    *,
    linked_order_id: str,
    adjustment_description: str,
    adjustment_reason: str | None,
    adjustment_complete_date: date,
    adjustment_amount: Decimal,
    payout_completed_date: date | None,
    statement_batch_id: str,
    statement_sequence_no: str,
    statement_source_filename: str,
    statement_file_hash: str,
    first_observed_at: datetime,
) -> CanonicalOrderAdjustment | None:
    """Build one persistable V1 event or retain an unsupported Statement type."""
    adjustment_type = supported_adjustment_type(adjustment_description)
    if adjustment_type is None:
        return None
    platform = "Shopee"
    description = _required_text(adjustment_description, "adjustment_description")
    order_id = _required_text(linked_order_id, "linked_order_id")
    return CanonicalOrderAdjustment(
        platform=platform,
        linked_order_id=order_id,
        adjustment_type=adjustment_type,
        adjustment_description=description,
        adjustment_reason=_optional_text(adjustment_reason),
        adjustment_complete_date=adjustment_complete_date,
        adjustment_amount=_money(adjustment_amount),
        payout_completed_date=payout_completed_date,
        statement_batch_id=_required_text(statement_batch_id, "statement_batch_id"),
        statement_sequence_no=_required_text(statement_sequence_no, "statement_sequence_no"),
        statement_source_filename=_required_text(statement_source_filename, "statement_source_filename"),
        statement_file_hash=_required_text(statement_file_hash, "statement_file_hash"),
        adjustment_event_fingerprint=adjustment_event_fingerprint(
            platform=platform,
            linked_order_id=order_id,
            adjustment_type=adjustment_type,
            adjustment_description=description,
            adjustment_reason=adjustment_reason,
            adjustment_complete_date=adjustment_complete_date,
            adjustment_amount=adjustment_amount,
        ),
        evidence_status=EvidenceStatus.STATEMENT_ONLY,
        first_observed_at=first_observed_at,
    )


def classify_statement_events(
    incoming: Iterable[CanonicalOrderAdjustment],
    existing: Iterable[CanonicalOrderAdjustment],
) -> tuple[OrderAdjustmentImportResult, ...]:
    """Classify by event fingerprint, allowing multiple events on one order."""
    existing_by_fingerprint = {
        event.adjustment_event_fingerprint: event for event in existing
    }
    results: list[OrderAdjustmentImportResult] = []
    seen: set[str] = set()
    for event in incoming:
        prior = existing_by_fingerprint.get(event.adjustment_event_fingerprint)
        if prior is not None or event.adjustment_event_fingerprint in seen:
            results.append(OrderAdjustmentImportResult(AdjustmentImportStatus.ALREADY_IMPORTED, prior or event))
        else:
            results.append(OrderAdjustmentImportResult(AdjustmentImportStatus.NEW, event))
            seen.add(event.adjustment_event_fingerprint)
    return tuple(results)


def _money(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _optional_text(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text or None


def _required_text(value: str, field: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{field} is required.")
    return text
