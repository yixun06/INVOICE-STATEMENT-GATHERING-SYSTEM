"""Staging orchestration for the UAT2 Shopee historical-invoice intake."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, Protocol

from src.invoice_app.domain.historical_invoice import InvoiceBundle, map_accepted_shopee_invoice
from src.invoice_app.repositories.historical_invoice_repository import (
    BulkImportResult,
    HistoricalInvoiceRepository,
    ImportResult,
    ImportStatus,
)
from src.invoice_app.services.batch_service import apply_batch_rules, process_pdf_file_with_outcome


DEFAULT_BULK_CHUNK_SIZE = 50


class IntakeStatus(str, Enum):
    NEW = "NEW"
    ALREADY_IMPORTED = "ALREADY_IMPORTED"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IMPORTED = "IMPORTED"


class HistoricalInvoiceIntakeError(RuntimeError):
    """A source could not become a safe historical-invoice staging candidate."""


@dataclass(frozen=True)
class InvoiceIntakeUpload:
    source_filename: str
    content: bytes


@dataclass(frozen=True)
class InvoiceIntakeEntry:
    staging_id: str
    source_filename: str
    source_hash: str
    order_id: str | None
    status: IntakeStatus
    message: str | None
    bundle: InvoiceBundle | None = None


@dataclass(frozen=True)
class InvoiceIntakeImportOutcome:
    entries: tuple[InvoiceIntakeEntry, ...]
    bulk_result: BulkImportResult


def process_and_classify_uploads(
    uploads: Iterable[InvoiceIntakeUpload], repository: HistoricalInvoiceRepository
) -> tuple[InvoiceIntakeEntry, ...]:
    """Parse files into temporary staging, then perform one no-write repository snapshot lookup."""
    staged = tuple(entry for upload in uploads for entry in _process_upload(upload))
    return classify_staging(staged, repository)


def classify_staging(
    entries: Iterable[InvoiceIntakeEntry], repository: HistoricalInvoiceRepository
) -> tuple[InvoiceIntakeEntry, ...]:
    """Classify accepted Shopee candidates without performing any persistence write."""
    staged = tuple(entries)
    duplicate_ids = _duplicate_candidate_ids(staged)
    normalized = tuple(
        replace(
            entry,
            status=IntakeStatus.NEEDS_REVIEW,
            message="Duplicate canonical identity in this intake staging; remove and re-upload safely.",
            bundle=None,
        )
        if entry.bundle is not None and (entry.bundle.order.platform, entry.bundle.order.order_id) in duplicate_ids
        else entry
        for entry in staged
    )
    candidates = tuple(entry for entry in normalized if entry.bundle is not None)
    if not candidates:
        return normalized
    results = repository.classify_invoices(entry.bundle for entry in candidates)
    result_by_id = {(result.platform, result.order_id): result for result in results}
    return tuple(_with_repository_result(entry, result_by_id) for entry in normalized)


def import_new_staging(
    entries: Iterable[InvoiceIntakeEntry], repository: HistoricalInvoiceRepository
) -> InvoiceIntakeImportOutcome:
    """Persist current NEW candidates only after the caller's explicit user action."""
    staged = tuple(entries)
    candidates = tuple(entry for entry in staged if entry.status is IntakeStatus.NEW and entry.bundle is not None)
    result = repository.import_invoices((entry.bundle for entry in candidates), chunk_size=DEFAULT_BULK_CHUNK_SIZE)
    imported = {
        (item.platform, item.order_id)
        for item in result.results
        if item.status is ImportStatus.NEW
    }
    return InvoiceIntakeImportOutcome(
        entries=tuple(
            replace(entry, status=IntakeStatus.IMPORTED, message="Imported to historical invoice storage.")
            if entry.bundle is not None and (entry.bundle.order.platform, entry.bundle.order.order_id) in imported
            else entry
            for entry in staged
        ),
        bulk_result=result,
    )


def remove_staging_entry(entries: Iterable[InvoiceIntakeEntry], staging_id: str) -> tuple[InvoiceIntakeEntry, ...]:
    """Remove temporary UI staging only; this has no repository or storage side effect."""
    return tuple(entry for entry in entries if entry.staging_id != staging_id)


def _process_upload(upload: InvoiceIntakeUpload) -> tuple[InvoiceIntakeEntry, ...]:
    source_hash = sha256(upload.content).hexdigest()
    staging_id = f"{upload.source_filename}:{source_hash}"
    if not upload.content:
        return (_review_entry(staging_id, upload.source_filename, source_hash, None, "Uploaded PDF is empty."),)
    try:
        with TemporaryDirectory(prefix="invoicegather-uat2-") as temporary_directory:
            path = Path(temporary_directory) / "invoice.pdf"
            path.write_bytes(upload.content)
            outcome = process_pdf_file_with_outcome(upload.source_filename, path, "uat2-invoice-intake")
    except Exception as error:
        return (_review_entry(staging_id, upload.source_filename, source_hash, None, f"PDF processing failed: {error}"),)

    orders, products, reviews = apply_batch_rules(outcome.orders, outcome.products, outcome.reviews)
    messages = [str(row.get("message") or row.get("reason") or "Unsupported source.") for row in (*outcome.unsupported_files, *outcome.processing_errors)]
    review_order_ids = {
        str(row.get("order_id")).strip()
        for row in reviews
        if str(row.get("source_pdf", "")).strip() == upload.source_filename and str(row.get("order_id", "")).strip()
    }
    entries: list[InvoiceIntakeEntry] = []
    for order in orders:
        if str(order.get("source_pdf", "")).strip() != upload.source_filename:
            continue
        order_id = str(order.get("order_id", "")).strip() or None
        if str(order.get("platform", "")).strip() != "Shopee":
            entries.append(_review_entry(staging_id, upload.source_filename, source_hash, order_id, "Only Shopee invoices can enter UAT2 historical persistence."))
            continue
        if str(order.get("status", "")).strip() != "Accepted" or (order_id and order_id in review_order_ids):
            entries.append(_review_entry(staging_id, upload.source_filename, source_hash, order_id, "Source requires Manual Review before historical persistence."))
            continue
        order_products = [
            product for product in products
            if str(product.get("platform", "")).strip() == "Shopee"
            and str(product.get("order_id", "")).strip() == order_id
            and str(product.get("source_pdf", "")).strip() == upload.source_filename
        ]
        try:
            bundle = map_accepted_shopee_invoice(order, order_products, source_hash=source_hash)
        except (TypeError, ValueError) as error:
            entries.append(_review_entry(staging_id, upload.source_filename, source_hash, order_id, str(error)))
            continue
        entries.append(
            InvoiceIntakeEntry(
                staging_id=f"{staging_id}:{bundle.order.order_id}", source_filename=upload.source_filename,
                source_hash=source_hash, order_id=bundle.order.order_id, status=IntakeStatus.NEW,
                message=None, bundle=bundle,
            )
        )
    if entries:
        return tuple(entries)
    message = messages[0] if messages else "No accepted Shopee invoice was produced by the existing parser and validation flow."
    return (_review_entry(staging_id, upload.source_filename, source_hash, None, message),)


def _with_repository_result(
    entry: InvoiceIntakeEntry, results: dict[tuple[str, str], ImportResult]
) -> InvoiceIntakeEntry:
    if entry.bundle is None:
        return entry
    result = results[(entry.bundle.order.platform, entry.bundle.order.order_id)]
    messages = {
        ImportStatus.NEW: None,
        ImportStatus.ALREADY_IMPORTED: "Same material historical invoice is already imported.",
        ImportStatus.SOURCE_CONFLICT: "Stored historical invoice has different material source facts.",
    }
    return replace(entry, status=IntakeStatus(result.status.value), message=messages[result.status])


def _duplicate_candidate_ids(entries: Iterable[InvoiceIntakeEntry]) -> set[tuple[str, str]]:
    identities = [
        (entry.bundle.order.platform, entry.bundle.order.order_id)
        for entry in entries
        if entry.bundle is not None
    ]
    return {identity for identity in identities if identities.count(identity) > 1}


def _review_entry(
    staging_id: str, source_filename: str, source_hash: str, order_id: str | None, message: str
) -> InvoiceIntakeEntry:
    return InvoiceIntakeEntry(
        staging_id=staging_id, source_filename=source_filename, source_hash=source_hash,
        order_id=order_id, status=IntakeStatus.NEEDS_REVIEW, message=message, bundle=None,
    )
