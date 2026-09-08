"""Canonical, persistence-neutral records for accepted historical invoices."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CanonicalInvoiceOrder:
    """Accepted Invoice_Orders source facts, independent of a storage adapter."""

    platform: str
    order_id: str
    order_created_date: date | None
    income_type: str | None
    order_income: Decimal | None
    refund_amount: Decimal | None
    invoice_payment_signal: str | None
    source_filename: str | None
    source_hash: str | None
    source_fingerprint: str | None = None
    first_imported_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class CanonicalInvoiceItem:
    """Accepted Invoice_Items source facts for one invoice line."""

    platform: str
    order_id: str
    item_index: int
    seller_sku: str | None
    product_name: str | None
    variation: str | None
    quantity: int | None
    source_unit_price: Decimal | None
    source_line_subtotal: Decimal | None
    actual_selling_value: Decimal | None
    pricing_status: str | None
    source_hash: str | None
    promotion_group_id: str | None = None
    promotion_label: str | None = None
    source_group_total: Decimal | None = None
    allocation_method: str | None = None
    allocation_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvoiceBundle:
    """One accepted invoice and every item that belongs to it."""

    order: CanonicalInvoiceOrder
    items: tuple[CanonicalInvoiceItem, ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("InvoiceBundle must contain at least one item.")
        identity = (self.order.platform, self.order.order_id)
        if not all((item.platform, item.order_id) == identity for item in self.items):
            raise ValueError("Every InvoiceBundle item must match the order platform and order ID.")
        indexes = tuple(item.item_index for item in self.items)
        if len(indexes) != len(set(indexes)):
            raise ValueError("InvoiceBundle item_index values must be unique within an order.")

    def with_source_fingerprint(self, fingerprint: str) -> "InvoiceBundle":
        return replace(self, order=replace(self.order, source_fingerprint=fingerprint))


def map_accepted_shopee_invoice(
    order: Mapping[str, Any],
    products: Iterable[Mapping[str, Any]],
    *,
    source_hash: str | None,
    first_imported_at: datetime | None = None,
) -> InvoiceBundle:
    """Map existing accepted Shopee order/product mappings without changing parsers.

    The caller supplies a file hash because current parsed rows retain a source
    filename but do not expose a canonical content hash.
    """
    if _text(order.get("platform")) != "Shopee":
        raise ValueError("Only accepted Shopee data is in the current UAT2 mapping scope.")
    if _text(order.get("status")) != "Accepted":
        raise ValueError("Historical invoice persistence accepts only Accepted source data.")

    canonical_order = CanonicalInvoiceOrder(
        platform="Shopee",
        order_id=_required_text(order.get("order_id"), "order_id"),
        order_created_date=_canonical_date(order.get("order_created_date")),
        income_type=_optional_text(order.get("income_type")),
        order_income=_money(order.get("order_income")),
        refund_amount=_money(order.get("refund_amount")),
        invoice_payment_signal=_optional_text(order.get("payment_status")),
        source_filename=_optional_text(order.get("source_pdf")),
        source_hash=source_hash,
        first_imported_at=first_imported_at or utc_now(),
    )
    canonical_items = tuple(
        _map_accepted_shopee_item(canonical_order, index, product, source_hash)
        for index, product in enumerate(products)
    )
    return InvoiceBundle(order=canonical_order, items=canonical_items)


def _map_accepted_shopee_item(
    order: CanonicalInvoiceOrder,
    index: int,
    product: Mapping[str, Any],
    source_hash: str | None,
) -> CanonicalInvoiceItem:
    if _text(product.get("platform")) != order.platform:
        raise ValueError("Accepted product platform does not match its accepted order.")
    if _text(product.get("order_id")) != order.order_id:
        raise ValueError("Accepted product order ID does not match its accepted order.")
    if _text(product.get("status")) != "Accepted":
        raise ValueError("Historical invoice persistence accepts only Accepted product data.")

    source_line_subtotal = _first_present_money(
        product.get("source_line_subtotal"),
        product.get("line_subtotal"),
        product.get("line_total"),
    )
    actual_selling_value = _first_present_money(
        product.get("actual_selling_value"),
        product.get("reporting_actual_selling_value"),
    )

    evidence = product.get("allocation_evidence") or product.get("reporting_allocation_evidence") or ()
    if isinstance(evidence, str):
        evidence = (evidence,)
    return CanonicalInvoiceItem(
        platform=order.platform,
        order_id=order.order_id,
        item_index=index,
        seller_sku=_optional_text(product.get("seller_sku")),
        product_name=_optional_text(product.get("product_name")),
        variation=_first_present_text(product.get("variation"), product.get("variation_name")),
        quantity=_quantity(product.get("quantity")),
        source_unit_price=_money(product.get("unit_price")),
        source_line_subtotal=source_line_subtotal,
        actual_selling_value=actual_selling_value,
        pricing_status=_first_present_text(product.get("pricing_status"), product.get("reporting_pricing_status"))
        or "invoice_source",
        source_hash=source_hash,
        promotion_group_id=_optional_text(product.get("promotion_group_id")),
        promotion_label=_optional_text(product.get("promotion_label")),
        source_group_total=_first_present_money(
            product.get("source_group_total"), product.get("promotion_group_total")
        ),
        allocation_method=_first_present_text(
            product.get("allocation_method"), product.get("reporting_allocation_method")
        ),
        allocation_evidence=tuple(str(value) for value in evidence),
    )


def _canonical_date(value: Any) -> date | None:
    if value is None or _text(value) in {"", "N/A"}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), pattern).date()
            except ValueError:
                continue
    raise ValueError("order_created_date is not in a supported canonical date format.")


def _money(value: Any) -> Decimal | None:
    if value is None or _text(value) in {"", "N/A"}:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace("RM", "").replace(",", "").strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Invalid money value: {value!r}") from error


def _first_present_money(*values: Any) -> Decimal | None:
    for value in values:
        money = _money(value)
        if money is not None:
            return money
    return None


def _quantity(value: Any) -> int | None:
    if value is None or _text(value) in {"", "N/A"}:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid quantity value: {value!r}") from error


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return None if text in {"", "N/A"} else text


def _first_present_text(*values: Any) -> str | None:
    for value in values:
        text = _optional_text(value)
        if text is not None:
            return text
    return None


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required for a canonical invoice record.")
    return text
