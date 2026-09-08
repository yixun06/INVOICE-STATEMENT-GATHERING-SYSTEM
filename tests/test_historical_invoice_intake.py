from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder, InvoiceBundle
from src.invoice_app.repositories.historical_invoice_repository import (
    HistoricalInvoiceBulkImportError,
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.services.batch_service import PdfProcessingResult
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
    InvoiceIntakeUpload,
    classify_staging,
    import_new_staging,
    process_and_classify_uploads,
    remove_staging_entry,
)


def _bundle(order_id: str = "SHP-1", *, refund=Decimal("0.00")) -> InvoiceBundle:
    order = CanonicalInvoiceOrder(
        platform="Shopee", order_id=order_id, order_created_date=None, income_type="Final",
        order_income=Decimal("10.00"), refund_amount=refund, invoice_payment_signal="Released",
        source_filename="invoice.pdf", source_hash="source", first_imported_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    item = CanonicalInvoiceItem(
        platform="Shopee", order_id=order_id, item_index=0, seller_sku="000-SKU", product_name="Tea",
        variation=None, quantity=1, source_unit_price=Decimal("10.00"), source_line_subtotal=Decimal("10.00"),
        actual_selling_value=None, pricing_status="invoice_source", source_hash="source",
    )
    return InvoiceBundle(order=order, items=(item,))


def _entry(bundle: InvoiceBundle, status=IntakeStatus.NEW) -> InvoiceIntakeEntry:
    return InvoiceIntakeEntry(
        staging_id=bundle.order.order_id, source_filename="invoice.pdf", source_hash="a" * 64,
        order_id=bundle.order.order_id, status=status, message=None, bundle=bundle,
    )


def test_preview_is_no_write_and_classifies_new_duplicate_and_conflict_independently():
    repository = InMemoryHistoricalInvoiceRepository()
    original = _bundle("EXACT")
    repository.import_invoice(original)
    preview = classify_staging((_entry(_bundle("NEW")), _entry(original)), repository)
    conflict = classify_staging(
        (_entry(replace(original, order=replace(original.order, refund_amount=Decimal("-1.00")))),), repository
    )

    assert [entry.status for entry in preview] == [IntakeStatus.NEW, IntakeStatus.ALREADY_IMPORTED]
    assert conflict[0].status is IntakeStatus.SOURCE_CONFLICT
    assert repository.get_order("Shopee", "NEW") is None


def test_remove_only_changes_staging_and_never_existing_repository_data():
    repository = InMemoryHistoricalInvoiceRepository()
    existing = _bundle("EXISTING")
    repository.import_invoice(existing)
    removed = remove_staging_entry((_entry(existing, IntakeStatus.SOURCE_CONFLICT),), "EXISTING")

    assert removed == ()
    assert repository.get_order("Shopee", "EXISTING") is not None


def test_incoming_duplicate_identity_becomes_needs_review_and_cannot_write():
    repository = InMemoryHistoricalInvoiceRepository()
    preview = classify_staging((_entry(_bundle("DUP")), _entry(_bundle("DUP"))), repository)

    assert all(entry.status is IntakeStatus.NEEDS_REVIEW for entry in preview)
    assert all(entry.bundle is None for entry in preview)
    assert repository.get_order("Shopee", "DUP") is None


def test_explicit_import_only_writes_new_candidates_in_50_bundle_chunks():
    repository = RecordingMemoryRepository()
    entries = tuple(_entry(_bundle(f"ORDER-{index:03}")) for index in range(120))

    outcome = import_new_staging(entries, repository)

    assert outcome.bulk_result.chunk_sizes == (50, 50, 20)
    assert repository.write_chunks == (50, 50, 20)
    assert all(entry.status is IntakeStatus.IMPORTED for entry in outcome.entries)


def test_parser_staging_hashes_actual_bytes_and_preserves_zero_and_missing(monkeypatch):
    def fake_process(source_pdf, _path, _batch_id):
        return PdfProcessingResult(
            orders=[{"platform": "Shopee", "order_id": "PDF-1", "source_pdf": source_pdf, "status": "Accepted", "refund_amount": Decimal("0.00")}],
            products=[{"platform": "Shopee", "order_id": "PDF-1", "source_pdf": source_pdf, "status": "Accepted", "seller_sku": "SKU", "product_name": "Tea", "quantity": 1, "source_line_subtotal": Decimal("0.00")}],
            reviews=[], unsupported_files=[], processing_errors=[],
        )

    monkeypatch.setattr("src.invoice_app.services.historical_invoice_intake.process_pdf_file_with_outcome", fake_process)
    preview = process_and_classify_uploads((InvoiceIntakeUpload("real.pdf", b"actual-pdf-bytes"),), InMemoryHistoricalInvoiceRepository())

    assert preview[0].source_hash == "040e68fe0fe0e1fba9e14f342d988209de6260012d88faf45b98f2e2e7eda118"
    assert preview[0].bundle is not None
    assert preview[0].bundle.order.refund_amount == Decimal("0.00")
    assert preview[0].bundle.items[0].actual_selling_value is None


def test_non_shopee_parser_outcome_is_staging_review_and_never_a_persistence_candidate(monkeypatch):
    def fake_process(source_pdf, _path, _batch_id):
        return PdfProcessingResult(
            orders=[{"platform": "Lazada", "order_id": "LZD-1", "source_pdf": source_pdf, "status": "Accepted"}],
            products=[{"platform": "Lazada", "order_id": "LZD-1", "source_pdf": source_pdf, "status": "Accepted", "seller_sku": "SKU", "product_name": "Tea", "quantity": 1}],
            reviews=[], unsupported_files=[], processing_errors=[],
        )

    monkeypatch.setattr("src.invoice_app.services.historical_invoice_intake.process_pdf_file_with_outcome", fake_process)
    repository = InMemoryHistoricalInvoiceRepository()
    preview = process_and_classify_uploads((InvoiceIntakeUpload("lazada.pdf", b"lazada-bytes"),), repository)

    assert preview[0].status is IntakeStatus.NEEDS_REVIEW
    assert preview[0].bundle is None
    assert repository.list_orders() == ()


class RecordingMemoryRepository(InMemoryHistoricalInvoiceRepository):
    def __init__(self):
        super().__init__()
        self.write_chunks = ()

    def import_invoices(self, bundles, *, chunk_size=50):
        values = tuple(bundles)
        self.write_chunks = tuple(len(values[index:index + chunk_size]) for index in range(0, len(values), chunk_size))
        return super().import_invoices(values, chunk_size=chunk_size)
