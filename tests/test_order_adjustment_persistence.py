from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.domain.order_adjustment import (
    AdjustmentImportStatus,
    EvidenceStatus,
    canonical_adjustment_type,
    make_statement_adjustment,
)
from src.invoice_app.repositories.order_adjustment_repository import (
    InMemoryOrderAdjustmentRepository,
)
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
    PostOrderAdjustmentEvidence,
)
from src.invoice_app.services.order_adjustment_pdf_confirmation import (
    AdjustmentEvidenceNeedsReview,
    confirm_pdf_adjustment_evidence,
)
from src.invoice_app.services.shopee_statement_persistence import (
    serialize_order_adjustment,
)
from src.invoice_app.services.uat2_persistence_schema import ORDER_ADJUSTMENTS_HEADERS


NOW = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)


def _event(*, amount="-43.61", complete_date=date(2026, 8, 6), sequence="1", file_hash="statement-a"):
    event = make_statement_adjustment(
        linked_order_id="2607302A9FMUBM",
        adjustment_description="Return Refund Adjustment After Order Completed",
        adjustment_reason="",
        adjustment_complete_date=complete_date,
        adjustment_amount=Decimal(amount),
        payout_completed_date=date(2026, 8, 3),
        statement_batch_id="batch-a",
        statement_sequence_no=sequence,
        statement_source_filename="statement-a.xlsx",
        statement_file_hash=file_hash,
        first_observed_at=NOW,
    )
    assert event is not None
    return event


def _generic_event(
    *,
    description="Return Refund Adjustment/Compensation",
    reason="Separate source reason",
    amount="137.24",
    complete_date=date(2026, 9, 2),
    sequence="8",
    file_hash="statement-generic",
):
    event = make_statement_adjustment(
        linked_order_id="26082480BKAV7A",
        adjustment_description=description,
        adjustment_reason=reason,
        adjustment_complete_date=complete_date,
        adjustment_amount=Decimal(amount),
        payout_completed_date=date(2026, 9, 3),
        statement_batch_id="batch-generic",
        statement_sequence_no=sequence,
        statement_source_filename="statement-generic.xlsx",
        statement_file_hash=file_hash,
        first_observed_at=NOW,
    )
    assert event is not None
    return event


@pytest.mark.parametrize(
    ("description", "expected"),
    (
        ("Return Refund Adjustment After Order Completed", "RETURN_REFUND_AFTER_ORDER_COMPLETED"),
        ("Return Refund Adjustment/Compensation", "RETURN_REFUND_ADJUSTMENT_COMPENSATION"),
        ("Lost-parcel compensation", "LOST_PARCEL_COMPENSATION"),
        ("  Voucher / Promotion Adjustment  ", "VOUCHER_PROMOTION_ADJUSTMENT"),
        ("***Voucher___Promotion///Adjustment***", "VOUCHER_PROMOTION_ADJUSTMENT"),
        ("lost-PARCEL compensation", "LOST_PARCEL_COMPENSATION"),
        ("\t \n", None),
        ("---", None),
    ),
)
def test_canonical_adjustment_type_mechanically_normalizes_source_description(description, expected):
    assert canonical_adjustment_type(description) == expected


def test_generic_adjustment_preserves_raw_source_text_and_ignores_amount_sign_for_type():
    positive = _generic_event(amount="137.24")
    negative = _generic_event(amount="-137.24")
    zero = _generic_event(amount="0.00")

    assert {event.adjustment_type for event in (positive, negative, zero)} == {
        "RETURN_REFUND_ADJUSTMENT_COMPENSATION"
    }
    assert positive.adjustment_description == "Return Refund Adjustment/Compensation"
    assert positive.adjustment_reason == "Separate source reason"
    assert positive.adjustment_amount == Decimal("137.24")


def test_generic_adjustment_fingerprint_is_stable_and_separates_real_event_facts():
    generic = _generic_event()
    repeat = _generic_event(sequence="99", file_hash="statement-reimport")
    different_description = _generic_event(description="Lost-parcel compensation")
    different_amount = _generic_event(amount="137.25")
    different_date = _generic_event(complete_date=date(2026, 9, 3))
    historical = _event()
    repository = InMemoryOrderAdjustmentRepository()

    result = repository.insert_new((generic, repeat, different_description, different_amount, different_date))

    assert historical.adjustment_type == "RETURN_REFUND_AFTER_ORDER_COMPLETED"
    assert historical.adjustment_event_fingerprint == "721c45211ddc46d3a0e32c6e741a5eec9e1163726fe8d0cb015205ec33a682da"
    assert generic.adjustment_event_fingerprint == repeat.adjustment_event_fingerprint
    assert len({
        generic.adjustment_event_fingerprint,
        different_description.adjustment_event_fingerprint,
        different_amount.adjustment_event_fingerprint,
        different_date.adjustment_event_fingerprint,
    }) == 4
    assert [entry.status for entry in result] == [
        AdjustmentImportStatus.NEW,
        AdjustmentImportStatus.ALREADY_IMPORTED,
        AdjustmentImportStatus.NEW,
        AdjustmentImportStatus.NEW,
        AdjustmentImportStatus.NEW,
    ]
    assert len(repository.read_by_linked_order_id("26082480BKAV7A")) == 4


def test_generic_adjustment_keeps_linked_order_id_required():
    with pytest.raises(ValueError, match="linked_order_id is required"):
        make_statement_adjustment(
            linked_order_id="",
            adjustment_description="Return Refund Adjustment/Compensation",
            adjustment_reason="Separate source reason",
            adjustment_complete_date=date(2026, 9, 2),
            adjustment_amount=Decimal("137.24"),
            payout_completed_date=date(2026, 9, 3),
            statement_batch_id="batch-generic",
            statement_sequence_no="8",
            statement_source_filename="statement-generic.xlsx",
            statement_file_hash="statement-generic",
            first_observed_at=NOW,
        )


def _pdf_entry(*, amount="-43.61", complete_date="06/08/2026"):
    order = CanonicalInvoiceOrder(
        platform="Shopee", order_id="2607302A9FMUBM", order_income=Decimal("46.58"),
        final_amount=Decimal("2.97"), refund_amount=None, payment_status="RELEASED",
    )
    bundle = InvoiceBundle(
        order=order,
        items=(CanonicalInvoiceItem(platform="Shopee", order_id=order.order_id, item_index=1),),
    )
    return InvoiceIntakeEntry(
        staging_id="pdf:hash:2607302A9FMUBM",
        source_filename="2607302A9FMUBM.pdf",
        source_hash="p" * 64,
        order_id=order.order_id,
        status=IntakeStatus.ALREADY_IMPORTED,
        message="Already imported Invoice is closed.",
        bundle=bundle,
        post_order_adjustment=PostOrderAdjustmentEvidence(
            adjustment_type="RETURN_REFUND_AFTER_ORDER_COMPLETED",
            adjustment_reason="Return Refund Adjustment After Order Completed",
            adjustment_complete_date=complete_date,
            released_amount=Decimal(amount),
            final_amount_consistent=True,
        ),
    )


def test_exact_21_column_event_round_trip_and_blank_statement_reason():
    event = _event()
    repository = InMemoryOrderAdjustmentRepository()

    result = repository.insert_new((event,))
    row = serialize_order_adjustment(repository.read_by_event_fingerprint(event.adjustment_event_fingerprint))

    assert result[0].status is AdjustmentImportStatus.NEW
    assert len(ORDER_ADJUSTMENTS_HEADERS) == len(row) == 21
    assert row[ORDER_ADJUSTMENTS_HEADERS.index("adjustment_reason")] == ""
    assert row[ORDER_ADJUSTMENTS_HEADERS.index("evidence_status")] == "STATEMENT_ONLY"
    assert row[ORDER_ADJUSTMENTS_HEADERS.index("invoice_source_hash")] == ""


def test_fingerprint_is_statement_provenance_independent_and_allows_multiple_events_per_order():
    original = _event(sequence="1", file_hash="statement-a")
    repeated = _event(sequence="99", file_hash="statement-b")
    later = _event(amount="-5.00", complete_date=date(2026, 8, 15), sequence="2")
    repository = InMemoryOrderAdjustmentRepository()

    first = repository.insert_new((original,))
    repeat = repository.insert_new((repeated,))
    second = repository.insert_new((later,))

    assert original.adjustment_event_fingerprint == repeated.adjustment_event_fingerprint
    assert first[0].status is AdjustmentImportStatus.NEW
    assert repeat[0].status is AdjustmentImportStatus.ALREADY_IMPORTED
    assert second[0].status is AdjustmentImportStatus.NEW
    assert len(repository.read_by_linked_order_id("2607302A9FMUBM")) == 2


def test_pdf_confirmation_updates_only_evidence_and_works_for_closed_invoice():
    event = _event()
    repository = InMemoryOrderAdjustmentRepository((event,))
    entry = _pdf_entry()

    confirmation = confirm_pdf_adjustment_evidence(
        entry, repository.read_by_linked_order_id(entry.order_id or "")
    )
    assert confirmation is not None
    assert confirmation.status is AdjustmentImportStatus.PDF_CONFIRMED
    confirmed = repository.enrich_pdf_evidence(confirmation.event)

    assert confirmed.evidence_status is EvidenceStatus.PDF_CONFIRMED
    assert confirmed.invoice_evidence_amount == Decimal("-43.61")
    assert confirmed.invoice_evidence_final_amount == Decimal("2.97")
    assert confirmed.invoice_evidence_reason == "Return Refund Adjustment After Order Completed"
    assert confirmed.adjustment_amount == Decimal("-43.61")
    assert confirmed.statement_file_hash == "statement-a"
    assert entry.bundle is not None
    assert entry.bundle.order.order_income == Decimal("46.58")
    assert entry.bundle.order.refund_amount is None


def test_pdf_before_statement_creates_no_persistent_event_and_single_disagreement_is_conflict():
    entry = _pdf_entry()
    assert confirm_pdf_adjustment_evidence(entry, ()) is None

    event = _event()
    disagreement = _pdf_entry(amount="-40.00")
    conflict = confirm_pdf_adjustment_evidence(disagreement, (event,))

    assert conflict is not None
    assert conflict.status is AdjustmentImportStatus.EVIDENCE_CONFLICT
    assert conflict.event.evidence_status is EvidenceStatus.EVIDENCE_CONFLICT
    assert conflict.event.adjustment_amount == Decimal("-43.61")


def test_ambiguous_pdf_disagreement_fails_closed():
    entry = _pdf_entry(amount="-40.00")
    with pytest.raises(AdjustmentEvidenceNeedsReview):
        confirm_pdf_adjustment_evidence(
            entry,
            (_event(amount="-43.61"), _event(amount="-5.00", complete_date=date(2026, 8, 15))),
        )
