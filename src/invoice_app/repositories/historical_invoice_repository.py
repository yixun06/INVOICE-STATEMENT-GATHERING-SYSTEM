"""Repository contract and in-memory reference implementation for UAT2 invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Iterable, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.domain.transaction_closure import (
    TransactionClosureStatus,
    decide_transaction_closure,
)


CLOSED_TRANSACTION_SOURCE_CHANGE = "CLOSED_TRANSACTION_SOURCE_CHANGE"
SOURCE_FINGERPRINT_V2_PREFIX = "v2:"


class ImportStatus(str, Enum):
    NEW = "NEW"
    ALREADY_IMPORTED = "ALREADY_IMPORTED"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


@dataclass(frozen=True)
class ImportResult:
    status: ImportStatus
    platform: str
    order_id: str
    source_fingerprint: str
    reason_code: str | None = None


@dataclass(frozen=True)
class BulkImportResult:
    """Results for an explicitly requested bulk import operation."""

    results: tuple[ImportResult, ...]
    chunk_sizes: tuple[int, ...]


class HistoricalInvoiceBulkImportError(RuntimeError):
    """A chunk failed after earlier chunks may already have been persisted."""

    def __init__(
        self,
        message: str,
        *,
        confirmed_results: Sequence[ImportResult] = (),
        pending_identities: Sequence[tuple[str, str]] = (),
        chunk_size: int | None = None,
        failed_chunk_index: int | None = None,
        failed_chunk_size: int | None = None,
        total_chunks: int | None = None,
        underlying_error_type: str | None = None,
        underlying_error_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.confirmed_results = tuple(confirmed_results)
        self.pending_identities = tuple(pending_identities)
        self.chunk_size = chunk_size
        self.failed_chunk_index = failed_chunk_index
        self.failed_chunk_size = failed_chunk_size
        self.total_chunks = total_chunks
        self.underlying_error_type = underlying_error_type
        self.underlying_error_message = underlying_error_message

    @property
    def confirmed_count(self) -> int:
        return len(self.confirmed_results)

    @property
    def pending_count(self) -> int:
        return len(self.pending_identities)

    @property
    def completed_chunk_count(self) -> int:
        return max(0, (self.failed_chunk_index or 1) - 1)


class HistoricalInvoiceRepository(Protocol):
    def refresh(self) -> None: ...

    def get_order(self, platform: str, order_id: str) -> CanonicalInvoiceOrder | None: ...

    def get_orders_by_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, CanonicalInvoiceOrder]: ...

    def get_items_by_order_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, tuple[CanonicalInvoiceItem, ...]]: ...

    def import_invoice(self, bundle: InvoiceBundle) -> ImportResult: ...

    def classify_invoices(self, bundles: Iterable[InvoiceBundle]) -> tuple[ImportResult, ...]: ...

    def import_invoices(
        self, bundles: Iterable[InvoiceBundle], *, chunk_size: int = 50
    ) -> BulkImportResult: ...

    def list_orders(
        self,
        *,
        platform: str | None = None,
        order_created_from: date | None = None,
        order_created_to: date | None = None,
    ) -> tuple[CanonicalInvoiceOrder, ...]: ...


def source_fact_fingerprint(bundle: InvoiceBundle) -> str:
    """Return a stable SHA-256 digest over material invoice source facts only."""
    payload = {
        "order": _order_facts(bundle.order),
        "items": [_item_facts(item) for item in sorted(bundle.items, key=lambda value: value.item_index)],
    }
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


def source_fingerprint_v2(bundle: InvoiceBundle) -> str:
    """Return the V2 source-version identity for newly persisted invoices.

    V2 deliberately identifies the exact uploaded source version only.  It is
    not an immutability hash for mutable canonical/enrichment fields.
    """

    return source_fingerprint_v2_from_values(
        bundle.order.platform,
        bundle.order.order_id,
        bundle.order.source_hash,
    )


def source_fingerprint_v2_from_values(
    platform: object,
    order_id: object,
    source_hash: object,
) -> str:
    """Return the V2 fingerprint from the three source-version inputs only."""

    platform, order_id = _identity(platform, order_id)
    source_hash = _text(source_hash)
    if not source_hash:
        raise ValueError("source_hash is required to create a V2 source_fingerprint.")
    payload = {
        "version": 2,
        "platform": platform,
        "order_id": order_id,
        "source_hash": source_hash,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return SOURCE_FINGERPRINT_V2_PREFIX + sha256(serialized.encode("utf-8")).hexdigest()


def is_source_fingerprint_v2(value: object) -> bool:
    """Return whether a persisted fingerprint explicitly opts into V2."""

    return _text(value).startswith(SOURCE_FINGERPRINT_V2_PREFIX)


class InMemoryHistoricalInvoiceRepository:
    """Test-only repository that enforces the UAT2 duplicate/conflict contract."""

    def __init__(self) -> None:
        self._bundles: dict[tuple[str, str], InvoiceBundle] = {}

    def refresh(self) -> None:
        """Keep the storage-neutral refresh lifecycle harmless in memory."""
        return None

    def get_order(self, platform: str, order_id: str) -> CanonicalInvoiceOrder | None:
        bundle = self._bundles.get(_identity(platform, order_id))
        return bundle.order if bundle else None

    def get_orders_by_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, CanonicalInvoiceOrder]:
        return {
            order_id: bundle.order
            for order_id in _distinct_order_ids(order_ids)
            if (bundle := self._bundles.get(_identity(platform, order_id))) is not None
        }

    def get_items_by_order_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, tuple[CanonicalInvoiceItem, ...]]:
        return {
            order_id: bundle.items
            for order_id in _distinct_order_ids(order_ids)
            if (bundle := self._bundles.get(_identity(platform, order_id))) is not None
        }

    def import_invoice(self, bundle: InvoiceBundle) -> ImportResult:
        identity = _identity(bundle.order.platform, bundle.order.order_id)
        existing = self._bundles.get(identity)
        result = classify_invoice_against_existing(bundle, existing)
        if result.status is ImportStatus.NEW:
            self._bundles[identity] = bundle.with_source_fingerprint(
                result.source_fingerprint
            )
        return result

    def classify_invoices(self, bundles: Iterable[InvoiceBundle]) -> tuple[ImportResult, ...]:
        candidates = _unique_bundles(bundles)
        results = []
        for bundle in candidates:
            identity = _identity(bundle.order.platform, bundle.order.order_id)
            existing = self._bundles.get(identity)
            results.append(classify_invoice_against_existing(bundle, existing))
        return tuple(results)

    def import_invoices(
        self, bundles: Iterable[InvoiceBundle], *, chunk_size: int = 50
    ) -> BulkImportResult:
        candidates = _unique_bundles(bundles)
        _validate_chunk_size(chunk_size)
        results = self.classify_invoices(candidates)
        if any(result.status is not ImportStatus.NEW for result in results):
            return BulkImportResult(results=results, chunk_sizes=())

        for bundle, result in zip(candidates, results):
            self._bundles[_identity(bundle.order.platform, bundle.order.order_id)] = bundle.with_source_fingerprint(
                result.source_fingerprint
            )
        return BulkImportResult(
            results=results,
            chunk_sizes=tuple(len(chunk) for chunk in _chunks(candidates, chunk_size)),
        )

    def list_orders(
        self,
        *,
        platform: str | None = None,
        order_created_from: date | None = None,
        order_created_to: date | None = None,
    ) -> tuple[CanonicalInvoiceOrder, ...]:
        orders = []
        for bundle in self._bundles.values():
            order = bundle.order
            if platform is not None and _identity(platform, "")[0] != order.platform:
                continue
            if order_created_from is not None and (order.order_created_date is None or order.order_created_date < order_created_from):
                continue
            if order_created_to is not None and (order.order_created_date is None or order.order_created_date > order_created_to):
                continue
            orders.append(order)
        return tuple(sorted(orders, key=lambda value: (value.platform, value.order_id)))


def _order_facts(order: CanonicalInvoiceOrder) -> dict[str, object]:
    fields = (
        "platform", "order_id", "order_status", "order_created_date", "delivered_date", "completed_date", "fund_transfer_date",
        "merchandise_subtotal", "product_price", "shipping_subtotal", "shipping_fee_paid_by_buyer", "shipping_fee_charged_by_logistic_provider", "shipping_fee_rebate_from_shopee", "seller_paid_shipping_fee_sst", "reverse_shipping_fee", "reverse_shipping_fee_sst",
        "vouchers_rebates_total", "voucher_type", "voucher_code", "voucher_funded_by", "voucher_amount", "commission_fee", "service_fee", "transaction_fee", "ams_commission_fee", "ads_escrow_top_up_fee", "fees_charges_total",
        "order_income", "income_type", "final_amount", "refund_amount", "buyer_merchandise_subtotal", "buyer_shipping_fee", "shopee_voucher", "seller_voucher", "total_buyer_payment",
    )
    return {field: _value(getattr(order, field)) for field in fields}


def classify_invoice_against_existing(
    candidate: InvoiceBundle,
    existing: InvoiceBundle | None,
) -> ImportResult:
    """Apply the existing source identity contract plus the closed-order guard."""

    platform, order_id = _identity(
        candidate.order.platform, candidate.order.order_id
    )
    if existing is None:
        fingerprint = source_fingerprint_v2(candidate)
        return ImportResult(ImportStatus.NEW, platform, order_id, fingerprint)
    if is_source_fingerprint_v2(existing.order.source_fingerprint):
        fingerprint = source_fingerprint_v2(candidate)
        if existing.order.source_fingerprint == source_fingerprint_v2(existing) == fingerprint:
            return ImportResult(
                ImportStatus.ALREADY_IMPORTED, platform, order_id, fingerprint
            )
    else:
        fingerprint = source_fact_fingerprint(candidate)
        if source_fact_fingerprint(existing) == fingerprint:
            return ImportResult(
                ImportStatus.ALREADY_IMPORTED, platform, order_id, fingerprint
            )
    closure = decide_transaction_closure(existing.order)
    reason_code = (
        CLOSED_TRANSACTION_SOURCE_CHANGE
        if closure.status is TransactionClosureStatus.CLOSED
        else None
    )
    return ImportResult(
        ImportStatus.SOURCE_CONFLICT,
        platform,
        order_id,
        fingerprint,
        reason_code,
    )


def _item_facts(item: CanonicalInvoiceItem) -> dict[str, object]:
    fields = ("platform", "order_id", "item_index", "seller_sku", "sku_missing_in_source", "product_name", "variation", "quantity", "actual_selling_unit_price", "line_subtotal", "promotion_group_id", "promotion_label", "promotion_advertised_amount", "promotion_discount_percent", "source_group_total")
    return {field: _value(getattr(item, field)) for field in fields}


def _value(value: object) -> object:
    if value is None:
        return {"kind": "none"}
    if isinstance(value, Decimal):
        return {"kind": "decimal", "value": format(value.normalize(), "f")}
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"kind": "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"kind": "text", "value": _text(value)}
    return value


def _identity(platform: str, order_id: str) -> tuple[str, str]:
    return (_text(platform), _text(order_id))


def _distinct_order_ids(order_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_text(order_id) for order_id in order_ids if _text(order_id)))


def _text(value: object) -> str:
    return str(value).strip()


def _unique_bundles(bundles: Iterable[InvoiceBundle]) -> tuple[InvoiceBundle, ...]:
    candidates = tuple(bundles)
    identities = [_identity(bundle.order.platform, bundle.order.order_id) for bundle in candidates]
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise ValueError(f"Incoming historical invoice bundles duplicate canonical identities: {duplicates!r}.")
    return candidates


def _validate_chunk_size(chunk_size: int) -> None:
    if chunk_size <= 0:
        raise ValueError("Historical invoice bulk chunk size must be positive.")


def _chunks(values: Sequence[InvoiceBundle], size: int) -> tuple[tuple[InvoiceBundle, ...], ...]:
    return tuple(tuple(values[index:index + size]) for index in range(0, len(values), size))
