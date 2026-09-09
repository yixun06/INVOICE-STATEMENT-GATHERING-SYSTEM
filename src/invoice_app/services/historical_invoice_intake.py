"""Staging orchestration for the UAT2 Shopee historical-invoice intake."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from typing import Iterable

from src.invoice_app.domain.historical_invoice import InvoiceBundle, map_accepted_shopee_invoice
from src.invoice_app.repositories.historical_invoice_repository import (
    BulkImportResult,
    HistoricalInvoiceRepository,
    ImportResult,
    ImportStatus,
)
from src.invoice_app.services.batch_service import resolve_archived_pdf_path
from src.invoice_app.services.product_price_master import PriceLookupStatus, ProductPriceMaster


DEFAULT_BULK_CHUNK_SIZE = 50


class IntakeStatus(str, Enum):
    NEW = "NEW"
    ALREADY_IMPORTED = "ALREADY_IMPORTED"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IMPORTED = "IMPORTED"


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


def build_current_batch_staging(
    *, batch_id: str | None, orders: Iterable[dict], products: Iterable[dict], reviews: Iterable[dict],
    price_master: ProductPriceMaster | None = None,
) -> tuple[InvoiceIntakeEntry, ...]:
    """Map existing Data Import staging without parsing a source document again."""
    all_products = tuple(products)
    all_reviews = tuple(reviews)
    entries: list[InvoiceIntakeEntry] = []
    for order in orders:
        if str(order.get("platform", "")).strip() != "Shopee" or str(order.get("status", "")).strip() != "Accepted":
            continue
        source_pdf = str(order.get("source_pdf", "")).strip()
        order_id = str(order.get("order_id", "")).strip() or None
        source_path = resolve_archived_pdf_path(batch_id, source_pdf)
        if source_path is None:
            entries.append(_review_entry(source_pdf, source_pdf, "", order_id, "Archived source is unavailable for historical source hashing."))
            continue
        try:
            source_hash = sha256(source_path.read_bytes()).hexdigest()
        except OSError as error:
            entries.append(_review_entry(source_pdf, source_pdf, "", order_id, f"Archived source cannot be read: {error}"))
            continue
        staging_id = f"{source_pdf}:{source_hash}:{order_id or 'unknown'}"
        related_review = any(
            str(review.get("source_pdf", "")).strip() == source_pdf
            and str(review.get("order_id", "")).strip() == (order_id or "")
            for review in all_reviews
        )
        if related_review:
            entries.append(_review_entry(staging_id, source_pdf, source_hash, order_id, "Source has a related Manual Review record."))
            continue
        if price_master is None:
            entries.append(_review_entry(staging_id, source_pdf, source_hash, order_id, "Product Master is unavailable for required Unit Price and NAV CODE enrichment."))
            continue
        candidate_products = [
            product for product in all_products
            if str(product.get("platform", "")).strip() == "Shopee"
            and str(product.get("order_id", "")).strip() == (order_id or "")
            and str(product.get("source_pdf", "")).strip() == source_pdf
        ]
        enriched_items = []
        enrichment_error = None
        for product in candidate_products:
            lookup = price_master.lookup(
                seller_sku=product.get("seller_sku"), product_name=product.get("product_name"),
                variation_name=product.get("variation") or product.get("variation_name"),
            )
            if lookup.status in {PriceLookupStatus.PRICE_NOT_FOUND, PriceLookupStatus.PRICING_CONFLICT, PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS} or lookup.unit_selling_price is None:
                enrichment_error = lookup.reason or f"Product Master {lookup.status.value.replace('_', ' ')}."
                break
            if not lookup.nav_code:
                enrichment_error = "Resolved Product Master row has blank NAV CODE."
                break
            enriched_items.append({"unit_price": lookup.unit_selling_price, "nav": lookup.nav_code})
        if enrichment_error:
            entries.append(_review_entry(staging_id, source_pdf, source_hash, order_id, enrichment_error))
            continue
        try:
            bundle = map_accepted_shopee_invoice(order, candidate_products, source_hash=source_hash, enriched_items=enriched_items)
        except (TypeError, ValueError) as error:
            entries.append(_review_entry(staging_id, source_pdf, source_hash, order_id, str(error)))
            continue
        entries.append(InvoiceIntakeEntry(staging_id, source_pdf, source_hash, bundle.order.order_id, IntakeStatus.NEW, None, bundle))
    accepted_sources = {entry.source_filename for entry in entries}
    for review in all_reviews:
        if str(review.get("platform", "")).strip() != "Shopee":
            continue
        source_pdf = str(review.get("source_pdf", "")).strip()
        if source_pdf in accepted_sources:
            continue
        order_id = str(review.get("order_id", "")).strip() or None
        staging_id = f"review:{source_pdf}:{order_id or 'unknown'}"
        entries.append(_review_entry(staging_id, source_pdf, "", order_id, "Manual Review source remains in the current batch."))
    return tuple(entries)


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
    blocking = tuple(entry for entry in staged if entry.status is not IntakeStatus.NEW)
    if blocking:
        return InvoiceIntakeImportOutcome(entries=staged, bulk_result=BulkImportResult(results=(), chunk_sizes=()))
    candidates = tuple(entry for entry in staged if entry.bundle is not None)
    result = repository.import_invoices((entry.bundle for entry in candidates), chunk_size=DEFAULT_BULK_CHUNK_SIZE)
    result_by_id = {(item.platform, item.order_id): item for item in result.results}
    mixed_fresh_classification = (
        not result.chunk_sizes
        and any(item.status is not ImportStatus.NEW for item in result.results)
    )
    return InvoiceIntakeImportOutcome(
        entries=tuple(
            _with_repository_result(entry, result_by_id)
            if mixed_fresh_classification
            else _with_import_result(entry, result_by_id)
            for entry in staged
        ),
        bulk_result=result,
    )


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


def _with_import_result(
    entry: InvoiceIntakeEntry, results: dict[tuple[str, str], ImportResult]
) -> InvoiceIntakeEntry:
    if entry.bundle is None:
        return entry
    result = results.get((entry.bundle.order.platform, entry.bundle.order.order_id))
    if result is None:
        return entry
    if result.status is ImportStatus.NEW:
        return replace(entry, status=IntakeStatus.IMPORTED, message="Imported to historical invoice storage.")
    return _with_repository_result(entry, results)


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
