"""Repository contract and in-memory reference implementation for UAT2 invoices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Iterable, Protocol

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)


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


class HistoricalInvoiceRepository(Protocol):
    def get_order(self, platform: str, order_id: str) -> CanonicalInvoiceOrder | None: ...

    def get_orders_by_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, CanonicalInvoiceOrder]: ...

    def get_items_by_order_ids(
        self, platform: str, order_ids: Iterable[str]
    ) -> dict[str, tuple[CanonicalInvoiceItem, ...]]: ...

    def import_invoice(self, bundle: InvoiceBundle) -> ImportResult: ...

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


class InMemoryHistoricalInvoiceRepository:
    """Test-only repository that enforces the UAT2 duplicate/conflict contract."""

    def __init__(self) -> None:
        self._bundles: dict[tuple[str, str], InvoiceBundle] = {}

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
        fingerprint = source_fact_fingerprint(bundle)
        stored = bundle.with_source_fingerprint(fingerprint)
        identity = _identity(bundle.order.platform, bundle.order.order_id)
        existing = self._bundles.get(identity)
        if existing is None:
            self._bundles[identity] = stored
            status = ImportStatus.NEW
        elif source_fact_fingerprint(existing) == fingerprint:
            status = ImportStatus.ALREADY_IMPORTED
        else:
            status = ImportStatus.SOURCE_CONFLICT
        return ImportResult(status, identity[0], identity[1], fingerprint)

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
    return {
        "platform": _text(order.platform),
        "order_id": _text(order.order_id),
        "order_created_date": _value(order.order_created_date),
        "income_type": _value(order.income_type),
        "order_income": _value(order.order_income),
        "refund_amount": _value(order.refund_amount),
        "invoice_payment_signal": _value(order.invoice_payment_signal),
    }


def _item_facts(item: CanonicalInvoiceItem) -> dict[str, object]:
    return {
        "platform": _text(item.platform),
        "order_id": _text(item.order_id),
        "item_index": item.item_index,
        "seller_sku": _value(item.seller_sku),
        "product_name": _value(item.product_name),
        "variation": _value(item.variation),
        "quantity": item.quantity,
        "source_unit_price": _value(item.source_unit_price),
        "source_line_subtotal": _value(item.source_line_subtotal),
        "actual_selling_value": _value(item.actual_selling_value),
        "pricing_status": _value(item.pricing_status),
        "promotion_group_id": _value(item.promotion_group_id),
        "promotion_label": _value(item.promotion_label),
        "source_group_total": _value(item.source_group_total),
        "allocation_method": _value(item.allocation_method),
        "allocation_evidence": list(item.allocation_evidence),
    }


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
