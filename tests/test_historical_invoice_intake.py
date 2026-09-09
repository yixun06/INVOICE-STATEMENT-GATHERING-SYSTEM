from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder, InvoiceBundle
from src.invoice_app.repositories.historical_invoice_repository import (
    HistoricalInvoiceBulkImportError,
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
    build_current_batch_staging,
    classify_staging,
    import_new_staging,
)


def _bundle(order_id: str = "SHP-1", *, refund=Decimal("0.00")) -> InvoiceBundle:
    order = CanonicalInvoiceOrder(
        platform="Shopee", order_id=order_id, order_created_date=None, income_type="Final",
        order_income=Decimal("10.00"), refund_amount=refund, payment_status="Released",
        source_pdf="invoice.pdf", source_hash="source", first_imported_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    item = CanonicalInvoiceItem(
        platform="Shopee", order_id=order_id, item_index=0, seller_sku="000-SKU", product_name="Tea",
        nav="NAV", variation=None, quantity=1, unit_price=Decimal("10.00"), actual_selling_unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"), source_pdf="invoice.pdf", source_hash="source",
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


def test_current_batch_builds_only_accepted_shopee_from_archived_bytes(tmp_path, monkeypatch):
    archived = tmp_path / "member.pdf"
    archived.write_bytes(b"zip-member-pdf-bytes")
    monkeypatch.setattr("src.invoice_app.services.historical_invoice_intake.resolve_archived_pdf_path", lambda *_: archived)
    entries = build_current_batch_staging(
        batch_id="batch", orders=[
            {"platform": "Shopee", "order_id": "SHP-ARCHIVE", "source_pdf": "archive.zip::folder/order.pdf", "status": "Accepted", "refund_amount": Decimal("0.00")},
            {"platform": "Lazada", "order_id": "LZD-1", "source_pdf": "lazada.pdf", "status": "Accepted"},
        ], products=[
            {"platform": "Shopee", "order_id": "SHP-ARCHIVE", "source_pdf": "archive.zip::folder/order.pdf", "status": "Accepted", "seller_sku": "SKU", "product_name": "Tea", "quantity": 1, "source_line_subtotal": Decimal("0.00")},
        ], reviews=[], price_master=__import__("src.invoice_app.services.product_price_master", fromlist=["ProductPriceMaster"]).ProductPriceMaster.from_rows([{"seller_sku":"SKU","parent_sku":"","product_name":"Tea","variation_name":"","unit_selling_price":"10.00","nav_code":"NAV"}]),
    )
    assert len(entries) == 1 and entries[0].status is IntakeStatus.NEW
    assert entries[0].source_hash == "fb4ce2ff2d5cccdcee4defe77618fa4a10b25970aaf1e5de427274193d8f08a6"
    assert entries[0].bundle.items[0].actual_selling_unit_price is None


def test_current_batch_fails_closed_for_related_manual_review_or_unavailable_archive(tmp_path, monkeypatch):
    archived = tmp_path / "member.pdf"
    archived.write_bytes(b"source")
    monkeypatch.setattr("src.invoice_app.services.historical_invoice_intake.resolve_archived_pdf_path", lambda *_: archived)
    order = {"platform": "Shopee", "order_id": "SHP-REVIEW", "source_pdf": "source.pdf", "status": "Accepted"}

    review_entries = build_current_batch_staging(
        batch_id="batch", orders=[order], products=[],
        reviews=[{"platform": "Shopee", "order_id": "SHP-REVIEW", "source_pdf": "source.pdf", "status": "Manual Review"}],
    )
    monkeypatch.setattr("src.invoice_app.services.historical_invoice_intake.resolve_archived_pdf_path", lambda *_: None)
    unavailable_entries = build_current_batch_staging(batch_id="batch", orders=[order], products=[], reviews=[])

    assert review_entries[0].status is IntakeStatus.NEEDS_REVIEW and review_entries[0].bundle is None
    assert unavailable_entries[0].status is IntakeStatus.NEEDS_REVIEW and unavailable_entries[0].bundle is None


def test_import_reclassifies_a_changed_repository_result_without_silent_overwrite():
    repository = InMemoryHistoricalInvoiceRepository()
    original = _bundle("EXISTING")
    repository.import_invoice(original)

    already_imported = import_new_staging((_entry(original),), repository)
    conflicting = import_new_staging(
        (_entry(replace(original, order=replace(original.order, refund_amount=Decimal("-1.00")))),), repository
    )

    assert already_imported.entries[0].status is IntakeStatus.ALREADY_IMPORTED
    assert conflicting.entries[0].status is IntakeStatus.SOURCE_CONFLICT
    assert repository.get_order("Shopee", "EXISTING").refund_amount == Decimal("0.00")


def test_mixed_fresh_precommit_classification_maps_back_without_imported_status():
    repository = InMemoryHistoricalInvoiceRepository()
    first = _bundle("A")
    second = _bundle("B")
    stale_entries = (_entry(first), _entry(second))
    repository.import_invoice(first)

    outcome = import_new_staging(stale_entries, repository)

    assert [entry.status for entry in outcome.entries] == [
        IntakeStatus.ALREADY_IMPORTED,
        IntakeStatus.NEW,
    ]
    assert outcome.bulk_result.chunk_sizes == ()
    assert repository.get_order("Shopee", "B") is None


def test_option_a_blocks_every_mixed_batch_before_any_write():
    repository = RecordingMemoryRepository()
    outcome = import_new_staging((_entry(_bundle("NEW")), _entry(_bundle("REVIEW"), IntakeStatus.NEEDS_REVIEW)), repository)

    assert outcome.bulk_result.results == ()
    assert repository.write_chunks == ()


def test_blank_nav_or_pricing_failure_routes_whole_order_to_needs_review(tmp_path, monkeypatch):
    from src.invoice_app.services.product_price_master import ProductPriceMaster
    archived = tmp_path / "source.pdf"
    archived.write_bytes(b"source")
    monkeypatch.setattr("src.invoice_app.services.historical_invoice_intake.resolve_archived_pdf_path", lambda *_: archived)
    order = {"platform": "Shopee", "order_id": "SHP-NAV", "source_pdf": "source.pdf", "status": "Accepted"}
    product = {"platform": "Shopee", "order_id": "SHP-NAV", "source_pdf": "source.pdf", "status": "Accepted", "seller_sku": "SKU", "product_name": "Tea", "quantity": 1}
    master = ProductPriceMaster.from_rows([{"seller_sku": "SKU", "parent_sku": "", "product_name": "Tea", "variation_name": "", "unit_selling_price": "10.00", "nav_code": ""}])

    entries = build_current_batch_staging(batch_id="batch", orders=[order], products=[product], reviews=[], price_master=master)
    assert entries[0].status is IntakeStatus.NEEDS_REVIEW
    assert "NAV CODE" in entries[0].message


class RecordingMemoryRepository(InMemoryHistoricalInvoiceRepository):
    def __init__(self):
        super().__init__()
        self.write_chunks = ()

    def import_invoices(self, bundles, *, chunk_size=50):
        values = tuple(bundles)
        self.write_chunks = tuple(len(values[index:index + chunk_size]) for index in range(0, len(values), chunk_size))
        return super().import_invoices(values, chunk_size=chunk_size)
