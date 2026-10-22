"""Direct Full Order row-to-current-snapshot mapping with Product Master proof."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable

from src.invoice_app.parsers.shopee_full_order_parser import (
    ParsedShopeeFullOrder,
    ShopeeFullOrderRow,
)
from src.invoice_app.services.product_price_master import (
    PriceLookupStatus,
    ProductPriceLookupResult,
    ProductPriceMaster,
    VerifiedErpIdentityEquivalence,
)
from src.invoice_app.services.market_context import MarketKey


PM_ENRICHMENT_REQUIRED = "PM_ENRICHMENT_REQUIRED"
MALFORMED_ORDER_SNAPSHOT = "MALFORMED_ORDER_SNAPSHOT"

FULL_ORDER_VERIFIED_ERP_IDENTITIES = (
    VerifiedErpIdentityEquivalence(
        product_id="929433653",
        sku="9555208106937",
        product_name="Simply Natural Organic Lakadong Turmeric Powder 80g India",
        allowed_variations=("", "09/2026", "09/2027"),
        nav_code="5000064",
        usoft_product_description="SN Org Lakadong Turmeric Powder EA 80g",
        uom="EA",
        unit_selling_price=Decimal("11.50"),
    ),
)

_MATCHED_STATUSES = {
    PriceLookupStatus.MATCHED,
    PriceLookupStatus.MATCHED_BY_ALIAS,
    PriceLookupStatus.MATCHED_BY_SKU,
    PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
    PriceLookupStatus.MATCHED_BY_PARENT_SKU,
    PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION,
    PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
    PriceLookupStatus.MATCHED_BY_FULL_ORDER_MATCH_ONLY,
    PriceLookupStatus.VERIFIED_ERP_IDENTITY_EQUIVALENCE,
}


@dataclass(frozen=True)
class FullOrderCurrentOrder:
    market: str
    order_id: str
    order_status: str
    order_created_date: date
    source_file: str
    snapshot_hash: str
    last_synced_at: datetime


@dataclass(frozen=True)
class FullOrderCurrentItem:
    market: str
    order_id: str
    item_index: int
    source_parent_sku: str
    source_seller_sku: str
    resolved_seller_sku: str
    nav: str
    product_name: str
    variation: str
    quantity: int
    unit_price: Decimal
    product_subtotal: Decimal
    return_refund_status: str

    @property
    def original_product_sales(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(Decimal("0.01"))

    @property
    def discount(self) -> Decimal:
        return (self.original_product_sales - self.product_subtotal).quantize(
            Decimal("0.01")
        )


@dataclass(frozen=True)
class FullOrderValidationIssue:
    code: str
    order_id: str
    source_row_number: int | None
    message: str


@dataclass(frozen=True)
class FullOrderCurrentBatch:
    source_filename: str
    source_hash: str
    orders: tuple[FullOrderCurrentOrder, ...]
    items: tuple[FullOrderCurrentItem, ...]
    issues: tuple[FullOrderValidationIssue, ...]

    @property
    def syncable(self) -> bool:
        return bool(self.orders) and not self.issues


def adapt_shopee_full_order(
    parsed: ParsedShopeeFullOrder,
    *,
    product_master: ProductPriceMaster,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FullOrderCurrentBatch:
    """Build one fail-closed current snapshot without guessing product identity."""

    synced_at = now()
    rows_by_order: dict[str, list[ShopeeFullOrderRow]] = defaultdict(list)
    for row in parsed.rows:
        rows_by_order[row.order_id].append(row)

    orders: list[FullOrderCurrentOrder] = []
    items: list[FullOrderCurrentItem] = []
    issues: list[FullOrderValidationIssue] = []
    for order_id, source_rows in rows_by_order.items():
        if not _order_facts_are_consistent(source_rows):
            issues.append(
                FullOrderValidationIssue(
                    MALFORMED_ORDER_SNAPSHOT,
                    order_id,
                    source_rows[0].source_row_number,
                    "Repeated Full Order rows disagree on Order Status or Order Creation Date.",
                )
            )
            continue
        mapped_items: list[FullOrderCurrentItem] = []
        for item_index, row in enumerate(source_rows):
            lookup, reason = _resolve_product(
                row,
                product_master,
                market=parsed.market.key,
            )
            if lookup is None:
                issues.append(
                    FullOrderValidationIssue(
                        PM_ENRICHMENT_REQUIRED,
                        order_id,
                        row.source_row_number,
                        reason,
                    )
                )
                continue
            resolved_sku = lookup.matched_sku or lookup.matched_parent_sku or ""
            unit_price = _full_order_unit_price(parsed.market.key, lookup)
            if not resolved_sku or not lookup.nav_code or unit_price is None:
                issues.append(
                    FullOrderValidationIssue(
                        PM_ENRICHMENT_REQUIRED,
                        order_id,
                        row.source_row_number,
                        _missing_full_order_price_reason(parsed.market.key),
                    )
                )
                continue
            mapped_items.append(
                FullOrderCurrentItem(
                    market=parsed.market.key.value,
                    order_id=order_id,
                    item_index=item_index,
                    source_parent_sku=row.parent_sku,
                    source_seller_sku=row.seller_sku,
                    resolved_seller_sku=resolved_sku,
                    nav=lookup.nav_code,
                    product_name=row.product_name,
                    variation=row.variation,
                    quantity=row.quantity,
                    unit_price=unit_price,
                    product_subtotal=row.product_subtotal,
                    return_refund_status=row.return_refund_status,
                )
            )
        if len(mapped_items) != len(source_rows):
            continue
        first = source_rows[0]
        snapshot_hash = full_order_snapshot_hash(
            market=parsed.market.key.value,
            order_id=order_id,
            order_status=first.order_status,
            order_created_date=first.order_created_at.date(),
            items=mapped_items,
        )
        orders.append(
            FullOrderCurrentOrder(
                market=parsed.market.key.value,
                order_id=order_id,
                order_status=first.order_status,
                order_created_date=first.order_created_at.date(),
                source_file=parsed.source_filename,
                snapshot_hash=snapshot_hash,
                last_synced_at=synced_at,
            )
        )
        items.extend(mapped_items)
    return FullOrderCurrentBatch(
        source_filename=parsed.source_filename,
        source_hash=parsed.source_hash,
        orders=tuple(orders),
        items=tuple(items),
        issues=tuple(issues),
    )


def _resolve_product(
    row: ShopeeFullOrderRow,
    product_master: ProductPriceMaster,
    *,
    market: MarketKey,
) -> tuple[ProductPriceLookupResult | None, str]:
    result = product_master.lookup_full_order(
        seller_sku=row.seller_sku,
        parent_sku=row.parent_sku,
        product_name=row.product_name,
        variation_name=row.variation,
        verified_erp_identities=FULL_ORDER_VERIFIED_ERP_IDENTITIES,
        parent_family_variation_disambiguation=(market is MarketKey.SHOPEE_MY),
    )
    if result.status in _MATCHED_STATUSES:
        return result, ""
    return None, result.reason or "Product Master identity is unresolved."


def _full_order_unit_price(
    market: MarketKey, lookup: ProductPriceLookupResult
) -> Decimal | None:
    """Return the approved market-specific PM price after identity resolution."""

    if market is MarketKey.SHOPEE_MY:
        return lookup.unit_price_usolf
    return lookup.unit_selling_price


def _missing_full_order_price_reason(market: MarketKey) -> str:
    if market is MarketKey.SHOPEE_MY:
        return (
            "Resolved Product Master row lacks Seller SKU, NAV, or valid "
            "UNIT PRICE USOLF."
        )
    return "Resolved Product Master row lacks Seller SKU, NAV, or ERP Unit Price."


def _order_facts_are_consistent(rows: list[ShopeeFullOrderRow]) -> bool:
    return len({(row.order_status, row.order_created_at) for row in rows}) == 1


def full_order_snapshot_hash(
    *,
    market: str,
    order_id: str,
    order_status: str,
    order_created_date: date,
    items: list[FullOrderCurrentItem],
) -> str:
    """Hash only durable Order and Item facts; provenance never affects identity."""

    payload = {
        "market": market,
        "order_id": order_id,
        "order_status": order_status,
        "order_created_date": order_created_date.isoformat(),
        "items": [
            {
                "item_index": item.item_index,
                "source_parent_sku": item.source_parent_sku,
                "source_seller_sku": item.source_seller_sku,
                "resolved_seller_sku": item.resolved_seller_sku,
                "nav": item.nav,
                "product_name": item.product_name,
                "variation": item.variation,
                "quantity": item.quantity,
                "unit_price": format(item.unit_price.quantize(Decimal("0.01")), ".2f"),
                "product_subtotal": format(item.product_subtotal.quantize(Decimal("0.01")), ".2f"),
                "return_refund_status": item.return_refund_status,
            }
            for item in sorted(items, key=lambda item: item.item_index)
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(serialized.encode("utf-8")).hexdigest()
