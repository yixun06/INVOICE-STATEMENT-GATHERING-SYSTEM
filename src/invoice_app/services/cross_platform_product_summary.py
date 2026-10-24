"""Read-only Cross Platform Product Summary over committed reporting facts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    is_cancelled_invoice_order,
)
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
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    deserialize_full_order_current_snapshot,
)
from src.invoice_app.services.product_summary_identity import (
    product_summary_group_key,
    resolve_product_summary_identity,
)
from src.invoice_app.services.market_context import (
    CROSS_PLATFORM_MARKET_CONTEXTS,
    CROSS_PLATFORM_OPTIONS,
    SHOPEE_MY,
    resolve_market_context,
)
from src.invoice_app.utils.normalize import (
    normalize_sku_text,
)


CENT = Decimal("0.01")
PLATFORM_OPTIONS = CROSS_PLATFORM_OPTIONS
CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS = frozenset(
    context.persisted_platform for context in CROSS_PLATFORM_MARKET_CONTEXTS
)
SHOPEE_MY_ORDER_CREATED_EXCLUDED_STATUSES = frozenset(
    {"TO_SHIP", "CANCELLED"}
)


class CrossPlatformDateType(str, Enum):
    """The committed order date fact used for Cross Platform filtering."""

    ORDER_CREATED = "Order Created Date"
    PAYOUT = "Payout Date"

    @property
    def calendar_label(self) -> str:
        return "order created date" if self is self.ORDER_CREATED else "payout date"


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
    payout_completed_date: date | None
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
    order_created_date: date | None = None
    amount: Decimal | None = None
    resolve_sku: str | None = None


@dataclass(frozen=True)
class ReportingDateAuthority:
    """Date facts keyed by the only approved cross-source order identity."""

    order_created_dates: Mapping[tuple[str, str], date]
    payout_completed_dates: Mapping[tuple[str, str], date]

    def dates_for(
        self, persisted_platform: str, order_id: str
    ) -> tuple[date | None, date | None]:
        identity = _reporting_order_identity_for_persisted_platform(
            persisted_platform, order_id
        )
        return (
            self.order_created_dates.get(identity),
            self.payout_completed_dates.get(identity),
        )


@dataclass(frozen=True)
class CrossPlatformReportingSnapshot:
    """One in-memory committed snapshot shared by filters, UI, and export."""

    eligible_items: tuple[CommittedReportingItem, ...]
    order_created_items: tuple[CommittedReportingItem, ...] | None = None
    date_authority: ReportingDateAuthority | None = None
    canonical_orders: tuple[CanonicalInvoiceOrder, ...] = ()
    canonical_items: tuple[CanonicalInvoiceItem, ...] = ()

    def items_for(self, date_type: CrossPlatformDateType) -> tuple[CommittedReportingItem, ...]:
        """Select the approved authority without canonical fallback."""

        if date_type is CrossPlatformDateType.PAYOUT:
            return self.eligible_items
        # Directly-built snapshots are legacy in-memory fixtures. The live reader
        # always supplies this field, including an explicit empty Current source.
        return self.eligible_items if self.order_created_items is None else self.order_created_items

    def available_dates(
        self,
        platform: str,
        date_type: CrossPlatformDateType,
    ) -> tuple[date, ...]:
        _validate_platform(platform)
        return tuple(
            sorted(
                {
                    selected_date
                    for item in self.items_for(date_type)
                    if (selected_date := _date_for_type(item, date_type)) is not None
                    if item.platform == _persisted_platform_for_filter(platform)
                }
            )
        )

    def available_payout_dates(self, platform: str) -> tuple[date, ...]:
        """Compatibility helper for existing payout-only callers."""

        return self.available_dates(platform, CrossPlatformDateType.PAYOUT)


@dataclass(frozen=True)
class CrossPlatformProductSummary:
    platform: str
    date_type: CrossPlatformDateType
    from_date: date | None
    to_date: date | None
    source_items: tuple[CommittedReportingItem, ...]
    product_rows: tuple[ProductSummaryRow, ...]

    @property
    def currency_code(self) -> str:
        return resolve_market_context(self.platform).currency_code

    @property
    def total_quantity(self) -> int:
        return sum(row.quantity for row in self.product_rows)


class GoogleSheetsCrossPlatformProductSummaryReader:
    """Fetch one committed reporting snapshot exactly once per live render."""

    def __init__(
        self, *, spreadsheet_id: str, gateway: CrossPlatformReportingGateway
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway

    def load_snapshot(self) -> CrossPlatformReportingSnapshot:
        tabs = (
            INVOICE_ORDERS_TAB,
            INVOICE_ITEMS_TAB,
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            STATEMENT_DATA_TAB,
        )
        try:
            values = self._gateway.read_tabs(self._spreadsheet_id, tabs)
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "Consolidated Product Summary source snapshot read failed."
            ) from error
        return build_cross_platform_reporting_snapshot(values)


def build_cross_platform_reporting_snapshot(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> CrossPlatformReportingSnapshot:
    """Normalize committed facts without creating or caching reporting data."""

    order_rows = _tab_rows(tabs, INVOICE_ORDERS_TAB, INVOICE_ORDERS_HEADERS)
    item_rows = _tab_rows(tabs, INVOICE_ITEMS_TAB, INVOICE_ITEMS_HEADERS)

    orders: dict[tuple[str, str], CanonicalInvoiceOrder] = {}
    for row_number, row in order_rows:
        order = _deserialize_order(row, row_number)
        identity = (order.platform, order.order_id)
        if identity in orders:
            raise CrossPlatformProductSummaryError(
                f"Invoice_Orders duplicates committed order {order.platform}/{order.order_id}."
            )
        orders[identity] = order

    date_authority = (
        _reporting_date_authority(tabs)
        if {
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            STATEMENT_DATA_TAB,
        } <= tabs.keys()
        else None
    )
    eligible: list[CommittedReportingItem] = []
    canonical_items: list[CanonicalInvoiceItem] = []
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
        if item.platform not in CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS:
            continue
        canonical_items.append(item)
        order_identity = (item.platform, item.order_id)
        if order_identity not in orders:
            raise CrossPlatformProductSummaryError(
                f"Shopee Invoice item {item.order_id}/{item.item_index} has no committed order."
            )
        order = orders[order_identity]
        if is_cancelled_invoice_order(order):
            continue
        reporting_item = _reporting_item(item, order)
        if date_authority is not None:
            order_created_date, payout_completed_date = date_authority.dates_for(
                item.platform, item.order_id
            )
            reporting_item = replace(
                reporting_item,
                order_created_date=order_created_date,
                payout_completed_date=payout_completed_date,
            )
        eligible.append(reporting_item)
    return CrossPlatformReportingSnapshot(
        eligible_items=tuple(eligible),
        order_created_items=(
            _full_order_current_reporting_items(tabs)
            if {
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            } <= tabs.keys()
            else ()
        ),
        date_authority=date_authority,
        canonical_orders=tuple(
            order
            for order in orders.values()
            if order.platform in CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS
        ),
        canonical_items=tuple(canonical_items),
    )


def build_cross_platform_product_summary(
    snapshot: CrossPlatformReportingSnapshot,
    *,
    platform: str = SHOPEE_MY.display_name,
    date_type: CrossPlatformDateType = CrossPlatformDateType.PAYOUT,
    from_date: date | None = None,
    to_date: date | None = None,
) -> CrossPlatformProductSummary:
    """Filter one snapshot before aggregation and retain the exact final rowset."""

    _validate_platform(platform)
    if not isinstance(date_type, CrossPlatformDateType):
        raise ValueError(f"Unsupported Consolidated Product Summary date type: {date_type}.")
    available_dates = set(snapshot.available_dates(platform, date_type))
    if (from_date is None) != (to_date is None):
        raise CrossPlatformProductSummaryError(
            "From Date and To Date must both be selected from committed "
            f"{date_type.calendar_label}s."
        )
    if from_date is not None and from_date not in available_dates:
        raise CrossPlatformProductSummaryError(
            "From Date must be a committed "
            f"{date_type.calendar_label} in the selected platform scope."
        )
    if to_date is not None and to_date not in available_dates:
        raise CrossPlatformProductSummaryError(
            "To Date must be a committed "
            f"{date_type.calendar_label} in the selected platform scope."
        )
    if from_date is not None and to_date is not None and from_date > to_date:
        raise CrossPlatformProductSummaryError(
            "From Date must be on or before To Date."
        )
    filtered = tuple(
        item
        for item in snapshot.items_for(date_type)
        if (
            item.platform == _persisted_platform_for_filter(platform)
        )
        and (selected_date := _date_for_type(item, date_type)) is not None
        and (from_date is None or selected_date >= from_date)
        and (to_date is None or selected_date <= to_date)
    )
    source_items = (
        _direct_full_order_amounts(filtered)
        if date_type is CrossPlatformDateType.ORDER_CREATED
        else _allocate_shopee_amounts(filtered)
    )
    return CrossPlatformProductSummary(
        platform=platform,
        date_type=date_type,
        from_date=from_date,
        to_date=to_date,
        source_items=source_items,
        product_rows=_aggregate_product_rows(source_items),
    )


def _reporting_item(
    item: CanonicalInvoiceItem, order: CanonicalInvoiceOrder
) -> CommittedReportingItem:
    seller_sku = normalize_sku_text(
        _text(item.resolved_seller_sku) or _text(item.seller_sku)
    )
    if not seller_sku:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Seller SKU."
        )
    description = _text(item.product_name)
    nav = _text(item.nav)
    if not description:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Product Name."
        )
    if not nav:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted NAV."
        )
    if item.quantity is None or item.quantity <= 0:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Qty."
        )
    if item.unit_price is None:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Unit Price."
        )
    context = _context_for_persisted_platform(item.platform)
    return CommittedReportingItem(
        platform=context.persisted_platform,
        currency=context.currency_code,
        order_id=item.order_id,
        item_index=item.item_index,
        payout_completed_date=order.payout_completed_date,
        seller_sku=seller_sku,
        nav=nav,
        description=description,
        variation=_text(item.variation),
        quantity=item.quantity,
        unit_price=_cent_money(item.unit_price, item, "Unit Price"),
        line_subtotal=(
            None if item.line_subtotal is None else _cent_money(item.line_subtotal, item, "line_subtotal")
        ),
        promotion_group_id=_text(item.promotion_group_id) or None,
        promotion_label=_text(item.promotion_label) or None,
        source_group_total=(
            None if item.source_group_total is None else _cent_money(item.source_group_total, item, "source_group_total")
        ),
        order_created_date=order.order_created_date,
    )


def _reporting_date_authority(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> ReportingDateAuthority:
    """Project committed Full Order and Statement dates without financial fallback.

    Full Order and Statement rows have different granularities.  This projection
    deliberately accepts only one committed ``ORDER`` Statement row date per
    normalized market/order identity; SKU and component rows can therefore
    never multiply a reporting order or its amounts.
    """

    current = deserialize_full_order_current_snapshot(
        {
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: tabs[
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB
            ],
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: tabs[
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB
            ],
        }
    )
    order_created_dates: dict[tuple[str, str], date] = {}
    for order in current.orders:
        identity = _reporting_order_identity(order.market, order.order_id)
        _set_consistent_date(
            order_created_dates,
            identity,
            order.order_created_date,
            source="Shopee_FullOrder_Current_Orders",
        )

    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    payout_completed_dates: dict[tuple[str, str], date] = {}
    for row_number, row in _tab_rows(tabs, STATEMENT_DATA_TAB, STATEMENT_DATA_HEADERS):
        if _text(row[positions["validation_status"]]) != "PASSED":
            continue
        if _text(row[positions["commit_status"]]) != "COMMITTED":
            continue
        if _text(row[positions["record_type"]]) != "ORDER":
            continue
        platform = _text(row[positions["platform"]])
        order_id = _text(row[positions["order_id"]])
        if not platform or not order_id:
            raise CrossPlatformProductSummaryError(
                f"{STATEMENT_DATA_TAB} row {row_number} has no committed ORDER identity."
            )
        try:
            identity = _reporting_order_identity_for_persisted_platform(platform, order_id)
        except CrossPlatformProductSummaryError as error:
            raise CrossPlatformProductSummaryError(
                f"{STATEMENT_DATA_TAB} row {row_number} has unsupported market identity."
            ) from error
        payout_date = _optional_iso_date(
            row[positions["payout_completed_date"]],
            tab=STATEMENT_DATA_TAB,
            row_number=row_number,
            field="payout_completed_date",
        )
        if payout_date is not None:
            _set_consistent_date(
                payout_completed_dates,
                identity,
                payout_date,
                source=STATEMENT_DATA_TAB,
            )
    return ReportingDateAuthority(
        order_created_dates=order_created_dates,
        payout_completed_dates=payout_completed_dates,
    )


def _reporting_order_identity(market: str, order_id: str) -> tuple[str, str]:
    try:
        context = resolve_market_context(market)
    except Exception as error:
        raise CrossPlatformProductSummaryError(
            f"Unsupported reporting market identity: {market!r}."
        ) from error
    normalized_order_id = _text(order_id)
    if not normalized_order_id:
        raise CrossPlatformProductSummaryError("Reporting order identity requires order_id.")
    return context.key.value, normalized_order_id


def _reporting_order_identity_for_persisted_platform(
    persisted_platform: str, order_id: str
) -> tuple[str, str]:
    return _reporting_order_identity(
        _context_for_persisted_platform(persisted_platform).key.value,
        order_id,
    )


def _set_consistent_date(
    target: dict[tuple[str, str], date],
    identity: tuple[str, str],
    value: date,
    *,
    source: str,
) -> None:
    existing = target.get(identity)
    if existing is not None and existing != value:
        raise CrossPlatformProductSummaryError(
            f"{source} has conflicting dates for {identity[0]}/{identity[1]}."
        )
    target[identity] = value


def _optional_iso_date(
    value: Any, *, tab: str, row_number: int, field: str
) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise CrossPlatformProductSummaryError(
            f"{tab} row {row_number} has invalid {field}."
        ) from error


def _full_order_current_reporting_items(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> tuple[CommittedReportingItem, ...]:
    """Map Full Order Current directly to gross order-created reporting facts."""

    current = deserialize_full_order_current_snapshot(
        {
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: tabs[
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB
            ],
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: tabs[
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB
            ],
        }
    )
    orders = {(order.market, order.order_id): order for order in current.orders}
    eligible: list[CommittedReportingItem] = []
    for item in current.items:
        order = orders[(item.market, item.order_id)]
        if order.market == SHOPEE_MY.key and _is_excluded_my_order_created_status(
            order.order_status
        ):
            continue
        if order.market != SHOPEE_MY.key and _is_explicitly_cancelled_full_order(
            order.order_status
        ):
            continue
        context = resolve_market_context(item.market)
        seller_sku = normalize_sku_text(_text(item.resolved_seller_sku))
        if not seller_sku:
            raise CrossPlatformProductSummaryError(
                f"{item.order_id}/{item.item_index}: Full Order Current has no resolved Seller SKU."
            )
        if not _text(item.nav):
            raise CrossPlatformProductSummaryError(
                f"{item.order_id}/{item.item_index}: Full Order Current has no NAV."
            )
        if not _text(item.product_name):
            raise CrossPlatformProductSummaryError(
                f"{item.order_id}/{item.item_index}: Full Order Current has no Product Name."
            )
        if item.quantity <= 0:
            raise CrossPlatformProductSummaryError(
                f"{item.order_id}/{item.item_index}: Full Order Current has invalid Qty."
            )
        unit_price = _cent_current_money(item.unit_price, item.order_id, item.item_index, "Unit Price")
        subtotal = _cent_current_money(
            item.product_subtotal, item.order_id, item.item_index, "Product Subtotal"
        )
        eligible.append(
            CommittedReportingItem(
                platform=context.persisted_platform,
                currency=context.currency_code,
                order_id=item.order_id,
                item_index=item.item_index,
                payout_completed_date=None,
                seller_sku=seller_sku,
                nav=_text(item.nav),
                description=_text(item.product_name),
                variation=_text(item.variation),
                quantity=item.quantity,
                unit_price=unit_price,
                line_subtotal=subtotal,
                promotion_group_id=None,
                promotion_label=None,
                source_group_total=None,
                order_created_date=order.order_created_date,
                amount=subtotal,
                resolve_sku=_text(item.resolve_sku) or None,
            )
        )
    return tuple(eligible)


def _is_excluded_my_order_created_status(order_status: str) -> bool:
    """Exclude only Product Owner-approved MY Order Created status classes."""

    return (
        _normalize_my_order_created_exclusion_status(order_status)
        in SHOPEE_MY_ORDER_CREATED_EXCLUDED_STATUSES
    )


def _normalize_my_order_created_exclusion_status(order_status: str) -> str:
    """Normalize only exact MY Order Created exclusion equivalents."""

    normalized = " ".join(_text(order_status).split())
    exclusion_statuses = {
        "to ship": "TO_SHIP",
        "cancelled": "CANCELLED",
    }
    return exclusion_statuses.get(normalized.casefold(), normalized)


def _is_explicitly_cancelled_full_order(order_status: str) -> bool:
    return _text(order_status).casefold() in {"cancelled", "canceled"}


def _direct_full_order_amounts(
    items: Sequence[CommittedReportingItem],
) -> tuple[CommittedReportingItem, ...]:
    """Full Order Product Subtotal is already the promotion-aware item amount."""

    return tuple(
        sorted(
            (
                replace(
                    item,
                    amount=(
                        item.line_subtotal
                        if item.line_subtotal is not None
                        else _missing_current_subtotal(item)
                    ),
                )
                for item in items
            ),
            key=_source_item_sort_key,
        )
    )


def _missing_current_subtotal(item: CommittedReportingItem) -> Decimal:
    raise CrossPlatformProductSummaryError(
        f"{item.order_id}/{item.item_index}: Full Order Current has no Product Subtotal."
    )


def _cent_current_money(value: Decimal, order_id: str, item_index: int, field: str) -> Decimal:
    quantized = value.quantize(CENT)
    if value != quantized:
        raise CrossPlatformProductSummaryError(
            f"{order_id}/{item_index}: Full Order Current {field} is not at cent precision."
        )
    return quantized
    if not seller_sku:
        raise CrossPlatformProductSummaryError(
            "BUSINESS DECISION REQUIRED — CONSOLIDATED PRODUCT SUMMARY SKU MISSING: "
            f"{item.order_id}/{item.item_index}."
        )
    description = _text(item.product_name)
    if not description:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Product Name."
        )
    nav = _text(item.nav)
    if not nav:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted NAV."
        )
    if item.quantity is None or item.quantity <= 0:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Qty."
        )
    if item.unit_price is None:
        raise CrossPlatformProductSummaryError(
            f"{item.order_id}/{item.item_index}: missing persisted Unit Price."
        )
    context = _context_for_persisted_platform(item.platform)
    return CommittedReportingItem(
        platform=context.persisted_platform,
        currency=context.currency_code,
        order_id=item.order_id,
        item_index=item.item_index,
        payout_completed_date=order.payout_completed_date,
        seller_sku=seller_sku,
        nav=nav,
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
        order_created_date=order.order_created_date,
    )


def _allocate_shopee_amounts(
    items: Sequence[CommittedReportingItem],
) -> tuple[CommittedReportingItem, ...]:
    normal: list[CommittedReportingItem] = []
    groups: dict[tuple[str, str, str], list[CommittedReportingItem]] = defaultdict(list)
    for item in items:
        if item.platform not in CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS:
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
    groups: dict[tuple[str, str, Decimal], list[CommittedReportingItem]] = defaultdict(list)
    descriptions: dict[tuple[str, str, Decimal], list[str]] = defaultdict(list)
    for item in source_items:
        identity = resolve_product_summary_identity(
            nav=item.nav,
            seller_sku=item.seller_sku,
            product_name=item.description,
            variation=item.variation,
            historical_pm_unit_price=item.unit_price,
        )
        key = product_summary_group_key(
            identity=identity,
            resolved_sku=item.seller_sku,
        )
        groups[key].append(item)
        if identity.display_description not in descriptions[key]:
            descriptions[key].append(identity.display_description)

    unsorted: list[ProductSummaryRow] = []
    for key, members in groups.items():
        amount = sum((item.amount or Decimal("0.00") for item in members), Decimal("0.00"))
        quantity = sum(item.quantity for item in members)
        resolve_sku, resolve_sku_warning = _group_resolve_sku(members)
        unsorted.append(
            ProductSummaryRow(
                number=0,
                sku_code=key[1],
                nav=key[0],
                product_name=" | ".join(descriptions[key]),
                uom="EA",
                unit_price=key[2],
                quantity=quantity,
                discount_percent=None,
                discount_amount=key[2] * quantity - amount,
                amount=amount,
                source_item_count=len(members),
                resolve_sku=resolve_sku,
                resolve_sku_warning=resolve_sku_warning,
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


def _group_resolve_sku(
    members: Sequence[CommittedReportingItem],
) -> tuple[str | None, str | None]:
    """Never attach one Listing POS mapping to a conflicting summary group."""

    values = {item.resolve_sku for item in members}
    if values == {None}:
        return None, None
    if len(values) == 1:
        return next(iter(values)), None
    return None, "Resolve SKU differs across consolidated source items."
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
        item.payout_completed_date or date.min,
        item.nav.casefold(),
        item.seller_sku.casefold(),
        item.unit_price,
        item.order_id,
        item.item_index,
    )


def _date_for_type(
    item: CommittedReportingItem,
    date_type: CrossPlatformDateType,
) -> date | None:
    return (
        item.order_created_date
        if date_type is CrossPlatformDateType.ORDER_CREATED
        else item.payout_completed_date
    )


def _validate_platform(platform: str) -> None:
    if platform not in PLATFORM_OPTIONS:
        raise ValueError(f"Unsupported Consolidated Product Summary filter: {platform}.")


def cross_platform_backend_available(platform: str) -> bool:
    """Return whether one visible filter has an implemented reporting adapter."""

    _validate_platform(platform)
    return _persisted_platform_for_filter(platform) in CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS


def _persisted_platform_for_filter(platform: str) -> str:
    return resolve_market_context(platform).persisted_platform


def _context_for_persisted_platform(persisted_platform: str):
    for context in CROSS_PLATFORM_MARKET_CONTEXTS:
        if context.persisted_platform == persisted_platform:
            return context
    raise CrossPlatformProductSummaryError(
        f"No committed reporting adapter exists for {persisted_platform}."
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
