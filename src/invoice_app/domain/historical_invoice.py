"""Canonical V2.4 persistence-neutral records for accepted Shopee invoices."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CanonicalInvoiceOrder:
    platform: str
    order_id: str
    order_status: str | None = None
    order_created_date: date | None = None
    delivered_date: date | None = None
    completed_date: date | None = None
    fund_transfer_date: date | None = None
    merchandise_subtotal: Decimal | None = None
    product_price: Decimal | None = None
    shipping_subtotal: Decimal | None = None
    shipping_fee_paid_by_buyer: Decimal | None = None
    shipping_fee_charged_by_logistic_provider: Decimal | None = None
    shipping_fee_rebate_from_shopee: Decimal | None = None
    seller_paid_shipping_fee_sst: Decimal | None = None
    vouchers_rebates_total: Decimal | None = None
    voucher_type: str | None = None
    voucher_code: str | None = None
    voucher_funded_by: str | None = None
    voucher_amount: Decimal | None = None
    commission_fee: Decimal | None = None
    service_fee: Decimal | None = None
    transaction_fee: Decimal | None = None
    ads_escrow_top_up_fee: Decimal | None = None
    fees_charges_total: Decimal | None = None
    order_income: Decimal | None = None
    income_type: str | None = None
    final_amount: Decimal | None = None
    refund_amount: Decimal | None = None
    buyer_merchandise_subtotal: Decimal | None = None
    buyer_shipping_fee: Decimal | None = None
    shopee_voucher: Decimal | None = None
    seller_voucher: Decimal | None = None
    total_buyer_payment: Decimal | None = None
    payment_status: str | None = None
    payout_completed_date: date | None = None
    difference: Decimal | None = None
    source_pdf: str | None = None
    source_hash: str | None = None
    source_fingerprint: str | None = None
    first_imported_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class CanonicalInvoiceItem:
    platform: str
    order_id: str
    item_index: int
    seller_sku: str | None = None
    nav: str | None = None
    product_name: str | None = None
    variation: str | None = None
    quantity: int | None = None
    unit_price: Decimal | None = None
    actual_selling_unit_price: Decimal | None = None
    line_subtotal: Decimal | None = None
    promotion_group_id: str | None = None
    promotion_label: str | None = None
    source_group_total: Decimal | None = None
    statement_product_price: Decimal | None = None
    statement_refund_amount: Decimal | None = None
    statement_net_selling_amount: Decimal | None = None
    source_pdf: str | None = None
    source_hash: str | None = None


@dataclass(frozen=True)
class InvoiceBundle:
    order: CanonicalInvoiceOrder
    items: tuple[CanonicalInvoiceItem, ...]

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("InvoiceBundle must contain at least one item.")
        identity = (self.order.platform, self.order.order_id)
        if not all(
            (item.platform, item.order_id) == identity for item in self.items
        ):
            raise ValueError(
                "Every InvoiceBundle item must match the order platform and order ID."
            )
        if len({item.item_index for item in self.items}) != len(self.items):
            raise ValueError(
                "InvoiceBundle item_index values must be unique within an order."
            )

    def with_source_fingerprint(self, fingerprint: str) -> "InvoiceBundle":
        return replace(
            self,
            order=replace(self.order, source_fingerprint=fingerprint),
        )


_ORDER_MONEY_FIELDS = (
    "merchandise_subtotal",
    "product_price",
    "shipping_subtotal",
    "shipping_fee_paid_by_buyer",
    "shipping_fee_charged_by_logistic_provider",
    "shipping_fee_rebate_from_shopee",
    "seller_paid_shipping_fee_sst",
    "vouchers_rebates_total",
    "voucher_amount",
    "commission_fee",
    "service_fee",
    "transaction_fee",
    "ads_escrow_top_up_fee",
    "fees_charges_total",
    "order_income",
    "final_amount",
    "refund_amount",
    "buyer_merchandise_subtotal",
    "buyer_shipping_fee",
    "shopee_voucher",
    "seller_voucher",
    "total_buyer_payment",
)


def map_accepted_shopee_invoice(
    order: Mapping[str, Any],
    products: Iterable[Mapping[str, Any]],
    *,
    source_hash: str | None,
    first_imported_at: datetime | None = None,
    enriched_items: Iterable[Mapping[str, Any]] | None = None,
) -> InvoiceBundle:
    if (
        _text(order.get("platform")) != "Shopee"
        or _text(order.get("status")) != "Accepted"
    ):
        raise ValueError(
            "Historical invoice persistence accepts only Accepted Shopee source data."
        )

    canonical_order = CanonicalInvoiceOrder(
        platform="Shopee",
        order_id=_required_text(order.get("order_id"), "order_id"),
        order_status=_optional_text(order.get("order_status")),
        order_created_date=_canonical_date(order.get("order_created_date")),
        delivered_date=_canonical_date(order.get("delivered_date")),
        completed_date=_canonical_date(order.get("completed_date")),
        fund_transfer_date=_canonical_date(order.get("fund_transfer_date")),
        **{field: _money(order.get(field)) for field in _ORDER_MONEY_FIELDS},
        voucher_type=_optional_text(order.get("voucher_type")),
        voucher_code=_optional_text(order.get("voucher_code")),
        voucher_funded_by=_optional_text(order.get("voucher_funded_by")),
        income_type=_optional_text(order.get("income_type")),
        payment_status=_optional_text(order.get("payment_status")),
        payout_completed_date=None,
        source_pdf=_optional_text(order.get("source_pdf")),
        source_hash=source_hash,
        first_imported_at=first_imported_at or utc_now(),
    )
    source_products = tuple(products)
    resolved_items = tuple(enriched_items) if enriched_items is not None else ()
    if not resolved_items:
        raise ValueError(
            "Product Master Unit Price and NAV CODE enrichment is required for persistence."
        )
    if len(source_products) != len(resolved_items):
        raise ValueError(
            "Product Master enrichment must retain every accepted invoice item."
        )
    items = tuple(
        _map_item(canonical_order, index, source, master, source_hash)
        for index, (source, master) in enumerate(
            zip(source_products, resolved_items)
        )
    )
    return InvoiceBundle(canonical_order, items)


def _map_item(
    order: CanonicalInvoiceOrder,
    index: int,
    source: Mapping[str, Any],
    master: Mapping[str, Any],
    source_hash: str | None,
) -> CanonicalInvoiceItem:
    if (
        _text(source.get("platform")) != order.platform
        or _text(source.get("order_id")) != order.order_id
        or _text(source.get("status")) != "Accepted"
    ):
        raise ValueError("Accepted product does not match its accepted Shopee order.")
    return CanonicalInvoiceItem(
        platform=order.platform,
        order_id=order.order_id,
        item_index=index,
        seller_sku=_optional_text(source.get("seller_sku")),
        nav=_optional_text(master.get("nav")),
        product_name=_optional_text(source.get("product_name")),
        variation=_first_present_text(
            source.get("variation"), source.get("variation_name")
        ),
        quantity=_quantity(source.get("quantity")),
        unit_price=_money(master.get("unit_price")),
        actual_selling_unit_price=_money(source.get("unit_price")),
        line_subtotal=_first_present_money(
            source.get("source_line_subtotal"),
            source.get("line_subtotal"),
            source.get("line_total"),
        ),
        promotion_group_id=_optional_text(source.get("promotion_group_id")),
        promotion_label=_optional_text(source.get("promotion_label")),
        source_group_total=_first_present_money(
            source.get("source_group_total"),
            source.get("promotion_group_total"),
        ),
        source_pdf=order.source_pdf,
        source_hash=source_hash,
    )


def _canonical_date(value: Any) -> date | None:
    if value is None or _text(value) in {"", "N/A"}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for pattern in ("%d/%m/%Y", "%d/%m/%Y %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), pattern).date()
            except ValueError:
                continue
    raise ValueError("Invoice date is not in a supported canonical date format.")


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
        parsed = _money(value)
        if parsed is not None:
            return parsed
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
    return _text(value) or None


def _first_present_text(*values: Any) -> str | None:
    for value in values:
        if (text := _optional_text(value)) is not None:
            return text
    return None


def _required_text(value: Any, field_name: str) -> str:
    if (text := _optional_text(value)) is None:
        raise ValueError(
            f"{field_name} is required for a canonical invoice record."
        )
    return text
