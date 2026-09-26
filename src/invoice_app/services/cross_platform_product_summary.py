"""Read-only Cross Platform Product Summary over committed UAT2 Invoice facts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem
from src.invoice_app.domain.weekly_billing import ProductSummaryRow
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
    _deserialize_item,
    _deserialize_order,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
)
from src.invoice_app.utils.normalize import (
    normalize_match_text,
    normalize_sku_text,
    normalize_whitespace,
)


CENT = Decimal("0.01")
PLATFORM_OPTIONS = ("All", "Shopee", "Lazada", "ZENXIN")


class CrossPlatformProductSummaryError(RuntimeError):
    """The committed reporting snapshot cannot prove a safe summary."""


class CrossPlatformReportingGateway(Protocol):
    def read_tabs(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, Sequence[Sequence[Any]]]: ...


@dataclass(frozen=True)
class CommittedReportingItem:
    platform: str
    currency: str
    order_id: str
    item_index: int
    payout_completed_date: date
    seller_sku: str
    nav: str
    description: str
    variation: str
    quantity: int
    unit_price: Decimal
    line_subtotal: Decimal | None
    promotion_group_id: str | None
    promotion_label: str | None
    source_group_total: Decimal | None
    amount: Decimal | None = None


@dataclass(frozen=True)
class CrossPlatformReportingSnapshot:
    """One in-memory committed snapshot shared by filters, UI, and export."""

    eligible_items: tuple[CommittedReportingItem, ...]

    def available_payout_dates(self, platform: str) -> tuple[date, ...]:
        _validate_platform(platform)
        return tuple(
            sorted(
                {
                    item.payout_completed_date
                    for item in self.eligible_items
                    if platform == "All" or item.platform == platform
                }
            )
        )


@dataclass(frozen=True)
class CrossPlatformProductSummary:
    platform: str
    from_date: date | None
    to_date: date | None
    source_items: tuple[CommittedReportingItem, ...]
    product_rows: tuple[ProductSummaryRow, ...]

    @property
    def total_quantity(self) -> int:
        return sum(row.quantity for row in self.product_rows)


class GoogleSheetsCrossPlatformProductSummaryReader:
    """Fetch the two committed Invoice tabs exactly once per live snapshot."""

    def __init__(
        self, *, spreadsheet_id: str, gateway: CrossPlatformReportingGateway
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway

    def load_snapshot(self) -> CrossPlatformReportingSnapshot:
        tabs = (INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB)
        try:
            values = self._gateway.read_tabs(self._spreadsheet_id, tabs)
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "Cross Platform Product Summary source snapshot read failed."
            ) from error
        return build_cross_platform_reporting_snapshot(values)


def build_cross_platform_reporting_snapshot(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> CrossPlatformReportingSnapshot:
    """Normalize committed Invoice facts without creating or caching reporting data."""

    order_rows = _tab_rows(tabs, INVOICE_ORDERS_TAB, INVOICE_ORDERS_HEADERS)
    item_rows = _tab_rows(tabs, INVOICE_ITEMS_TAB, INVOICE_ITEMS_HEADERS)

    orders: dict[tuple[str, str], date | None] = {}
    for row_number, row in order_rows:
        order = _deserialize_order(row, row_number)
        identity = (order.platform, order.order_id)
        if identity in orders:
            raise CrossPlatformProductSummaryError(
                f"Invoice_Orders duplicates committed order {order.platform}/{order.order_id}."
            )
        orders[identity] = order.payout_completed_date

    eligible: list[CommittedReportingItem] = []
    item_identities: set[tuple[str, str, int]] = set()
    for row_number, row in item_rows:
        item = _deserialize_item(row, row_number)
        identity = (item.platform, item.order_id, item.item_index)
        if identity in item_identities:
            raise CrossPlatformProductSummaryError(
                "Invoice_Items duplicates committed item "
                f"{item.platform}/{item.order_id}/{item.item_index}."
            )
        item_identities.add(identity)
        if item.platform != "Shopee":
            continue
        order_identity = (item.platform, item.order_id)
        if order_identity not in orders:
            raise CrossPlatformProductSummaryError(
                f"Shopee Invoice item {item.order_id}/{item.item_index} has no committed order."
            )
        payout_date = orders[order_identity]
        if payout_date is None:
            continue
        eligible.append(_reporting_item(item, payout_date))
    return CrossPlatformReportingSnapshot(eligible_items=tuple(eligible))


def build_cross_platform_product_summary(
    snapshot: CrossPlatformReportingSnapshot,
    *,
    platform: str = "All",
    from_date: date | None = None,
    to_date: date | None = None,
) -> CrossPlatformProductSummary:
    """Filter one snapshot before aggregation and retain the exact final rowset."""

    _validate_platform(platform)
    if from_date is not None and to_date is not None and from_date > to_date:
        raise CrossPlatformProductSummaryError(
            "From Date must be on or before To Date."
        )
    filtered = tuple(
        item
        for item in snapshot.eligible_items
        if (platform == "All" or item.platform == platform)
        and (from_date is None or item.payout_completed_date >= from_date)
        and (to_date is None or item.payout_completed_date <= to_date)
    )
    source_items = _allocate_shopee_amounts(filtered)
    return CrossPlatformProductSummary(
        platform=platform,
        from_date=from_date,
        to_date=to_date,
        source_items=source_items,
        product_rows=_aggregate_product_rows(source_items),
    )


def _reporting_item(
    item: CanonicalInvoiceItem, payout_date: date
) -> CommittedReportingItem:
    seller_sku = normalize_sku_text(item.resolved_seller_sku or item.seller_sku)
    if not seller_sku:
        raise CrossPlatformProductSummaryError(
            "BUSINESS DECISION REQUIRED — CROSS PLATFORM SKU MISSING: "
            f"{item.order_id}/{item.item_index}."
        )
    description = _text(item.product_name)
    if not description:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Product Name."
        )
    if item.quantity is None or item.quantity <= 0:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Qty."
        )
    if item.unit_price is None:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Unit Price."
        )
    return CommittedReportingItem(
        platform="Shopee",
        currency="RM",
        order_id=item.order_id,
        item_index=item.item_index,
        payout_completed_date=payout_date,
        seller_sku=seller_sku,
        nav=_text(item.nav) or "N/A",
        description=description,
        variation=_text(item.variation),
        quantity=item.quantity,
        unit_price=_cent_money(item.unit_price, item, "Unit Price"),
        line_subtotal=(
            None
            if item.line_subtotal is None
            else _cent_money(item.line_subtotal, item, "line_subtotal")
        ),
        promotion_group_id=_text(item.promotion_group_id) or None,
        promotion_label=_text(item.promotion_label) or None,
        source_group_total=(
            None
            if item.source_group_total is None
            else _cent_money(item.source_group_total, item, "source_group_total")
        ),
    )


def _allocate_shopee_amounts(
    items: Sequence[CommittedReportingItem],
) -> tuple[CommittedReportingItem, ...]:
    normal: list[CommittedReportingItem] = []
    groups: dict[tuple[str, str, str], list[CommittedReportingItem]] = defaultdict(list)
    for item in items:
        if item.platform != "Shopee":
            raise CrossPlatformProductSummaryError(
                f"No committed reporting adapter exists for {item.platform}."
            )
        if item.promotion_group_id:
            groups[(item.platform, item.order_id, item.promotion_group_id)].append(item)
        elif item.promotion_label or item.source_group_total is not None:
            raise CrossPlatformProductSummaryError(
                f"{item.order_id}/{item.item_index}: promotion evidence has no group identity."
            )
        else:
            if item.line_subtotal is None:
                raise CrossPlatformProductSummaryError(
                    f"{item.order_id}/{item.item_index}: missing persisted line_subtotal."
                )
            normal.append(replace(item, amount=item.line_subtotal))

    result = list(normal)
    for (_platform, _order_id, group_id), members in sorted(groups.items()):
        totals = {item.source_group_total for item in members}
        if None in totals or len(totals) != 1:
            first = members[0]
            raise CrossPlatformProductSummaryError(
                f"{first.order_id}/{group_id}: persisted promotion total is missing or conflicting."
            )
        total = next(iter(totals))
        assert total is not None
        prices = {item.unit_price for item in members}
        weights = (
            tuple(item.unit_price * item.quantity for item in members)
            if len(prices) > 1
            else tuple(Decimal(item.quantity) for item in members)
        )
        tie_breaks = tuple(
            (item.nav, item.seller_sku, item.order_id, item.item_index)
            for item in members
        )
        allocations = _largest_remainder_allocations(total, weights, tie_breaks)
        result.extend(
            replace(item, amount=amount) for item, amount in zip(members, allocations)
        )
    return tuple(sorted(result, key=_source_item_sort_key))


def _aggregate_product_rows(
    source_items: Sequence[CommittedReportingItem],
) -> tuple[ProductSummaryRow, ...]:
    groups: dict[tuple[str, str, str, Decimal], list[CommittedReportingItem]] = defaultdict(list)
    for item in source_items:
        identity = (
            item.seller_sku,
            normalize_match_text(item.description),
            normalize_whitespace(item.variation).casefold(),
            item.unit_price,
        )
        groups[identity].append(item)

    unsorted: list[ProductSummaryRow] = []
    for members in groups.values():
        nav_values = {item.nav for item in members}
        if len(nav_values) != 1:
            first = members[0]
            raise CrossPlatformProductSummaryError(
                "BUSINESS DECISION REQUIRED — CROSS PLATFORM NAV CONFLICT: "
                f"{first.seller_sku}/{first.description}."
            )
        first = members[0]
        amount = sum((item.amount or Decimal("0.00") for item in members), Decimal("0.00"))
        quantity = sum(item.quantity for item in members)
        unit_price = first.unit_price
        unsorted.append(
            ProductSummaryRow(
                number=0,
                sku_code=first.seller_sku,
                nav=first.nav,
                product_name=_description_label(first.description, first.variation),
                uom="EA",
                unit_price=unit_price,
                quantity=quantity,
                discount_percent=None,
                discount_amount=unit_price * quantity - amount,
                amount=amount,
                source_item_count=len(members),
            )
        )
    return tuple(
        replace(row, number=number)
        for number, row in enumerate(
            sorted(
                unsorted,
                key=lambda row: (
                    row.sku_code.casefold(),
                    row.product_name.casefold(),
                    row.unit_price,
                ),
            ),
            start=1,
        )
    )


def _description_label(description: str, variation: str) -> str:
    return description if not variation else f"{description} — {variation}"


def _tab_rows(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
    tab: str,
    headers: Sequence[str],
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    if tab not in tabs:
        raise CrossPlatformProductSummaryError(f"Required reporting source tab {tab} is missing.")
    rows = tuple(tuple(row) for row in tabs[tab])
    if not rows or tuple(_text(value) for value in rows[0]) != tuple(headers):
        raise CrossPlatformProductSummaryError(
            f"{tab} header does not match the locked schema."
        )
    result = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) > len(headers) and any(_text(value) for value in row[len(headers):]):
            raise CrossPlatformProductSummaryError(
                f"{tab} row {row_number} exceeds the locked schema."
            )
        padded = row + (None,) * max(0, len(headers) - len(row))
        if any(_text(value) for value in padded):
            result.append((row_number, padded[: len(headers)]))
    return tuple(result)


def _largest_remainder_allocations(
    total: Decimal,
    weights: Sequence[Decimal],
    tie_breaks: Sequence[tuple[str, str, str, int]],
) -> tuple[Decimal, ...]:
    if not weights or len(weights) != len(tie_breaks):
        raise CrossPlatformProductSummaryError(
            "Promotion allocation requires aligned persisted members."
        )
    if any(weight < 0 for weight in weights) or sum(weights) <= 0:
        raise CrossPlatformProductSummaryError(
            "Promotion allocation requires positive persisted weights."
        )
    sign = Decimal("-1") if total < 0 else Decimal("1")
    absolute_cents = int((abs(total) / CENT).to_integral_exact())
    total_weight = sum(weights, Decimal("0"))
    exact = tuple(Decimal(absolute_cents) * weight / total_weight for weight in weights)
    floors = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    remaining = absolute_cents - sum(floors)
    ranking = sorted(
        range(len(weights)),
        key=lambda index: (-(exact[index] - floors[index]), tie_breaks[index]),
    )
    for index in ranking[:remaining]:
        floors[index] += 1
    return tuple(sign * Decimal(value) * CENT for value in floors)


def _cent_money(
    value: Decimal, item: CanonicalInvoiceItem, field: str
) -> Decimal:
    quantized = value.quantize(CENT)
    if value != quantized:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: {field} is not at cent precision."
        )
    return quantized


def _source_item_sort_key(item: CommittedReportingItem) -> tuple[Any, ...]:
    return (
        item.payout_completed_date,
        item.nav.casefold(),
        item.seller_sku.casefold(),
        item.unit_price,
        item.order_id,
        item.item_index,
    )


def _validate_platform(platform: str) -> None:
    if platform not in PLATFORM_OPTIONS:
        raise ValueError(f"Unsupported Cross Platform filter: {platform}.")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
