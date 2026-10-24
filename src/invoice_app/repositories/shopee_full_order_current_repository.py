"""Concrete mutable-current Google Sheets repository for Shopee Full Order."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping, Sequence

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    GoogleSheetsHistoricalInvoiceGateway,
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentBatch,
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
)
from src.invoice_app.services.uat2_persistence_schema import (
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
)


class FullOrderSyncStatus(str, Enum):
    NEW = "NEW"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    RETAINED = "RETAINED"


@dataclass(frozen=True)
class FullOrderSyncDecision:
    market: str
    order_id: str
    status: FullOrderSyncStatus


@dataclass(frozen=True)
class FullOrderSyncResult:
    decisions: tuple[FullOrderSyncDecision, ...]

    @property
    def new_count(self) -> int:
        return sum(item.status is FullOrderSyncStatus.NEW for item in self.decisions)

    @property
    def updated_count(self) -> int:
        return sum(item.status is FullOrderSyncStatus.UPDATED for item in self.decisions)

    @property
    def unchanged_count(self) -> int:
        return sum(item.status is FullOrderSyncStatus.UNCHANGED for item in self.decisions)

    @property
    def retained_count(self) -> int:
        """Orders deliberately left untouched by a historical missing-only sync."""

        return sum(item.status is FullOrderSyncStatus.RETAINED for item in self.decisions)


@dataclass(frozen=True)
class FullOrderCurrentSnapshot:
    orders: tuple[FullOrderCurrentOrder, ...]
    items: tuple[FullOrderCurrentItem, ...]

    def items_by_identity(self) -> dict[tuple[str, str], tuple[FullOrderCurrentItem, ...]]:
        grouped: dict[tuple[str, str], list[FullOrderCurrentItem]] = {}
        for item in self.items:
            grouped.setdefault((item.market, item.order_id), []).append(item)
        return {
            identity: tuple(sorted(values, key=lambda value: value.item_index))
            for identity, values in grouped.items()
        }


class ShopeeFullOrderCurrentRepository:
    """Replace uploaded Order-ID snapshots while leaving absent orders untouched."""

    def __init__(
        self,
        *,
        spreadsheet_id: str,
        gateway: GoogleSheetsHistoricalInvoiceGateway,
        commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway
        self._commit_lock = commit_lock

    def read_snapshot(self) -> FullOrderCurrentSnapshot:
        tabs = self._gateway.read_tabs(
            self._spreadsheet_id,
            (
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            ),
        )
        return deserialize_full_order_current_snapshot(tabs)

    def classify(self, batch: FullOrderCurrentBatch) -> FullOrderSyncResult:
        return _classify(batch, self.read_snapshot())

    def sync(self, batch: FullOrderCurrentBatch) -> FullOrderSyncResult:
        if not batch.syncable:
            raise HistoricalInvoiceStorageError(
                "Shopee Full Order current sync is blocked by validation or Product Master issues."
            )
        with self._commit_lock.acquire():
            before = self.read_snapshot()
            result = _classify(batch, before)
            if all(
                decision.status is FullOrderSyncStatus.UNCHANGED
                for decision in result.decisions
            ):
                return result
            expected = _merged_snapshot(before, batch, result)
            sheet_ids = self._gateway.read_sheet_ids(
                self._spreadsheet_id,
                (
                    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
                ),
            )
            row_counts = self._gateway.read_sheet_row_counts(
                self._spreadsheet_id,
                (
                    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
                ),
            )
            order_rows = max(len(before.orders), len(expected.orders), 1)
            item_rows = max(len(before.items), len(expected.items), 1)
            requests = tuple(
                request
                for request in (
                    _append_rows_request(
                        sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB],
                        current_row_count=row_counts[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB],
                        required_row_count=1 + order_rows,
                    ),
                    _append_rows_request(
                        sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB],
                        current_row_count=row_counts[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB],
                        required_row_count=1 + item_rows,
                    ),
                )
                if request is not None
            ) + (
                _replace_data_request(
                    sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB],
                    tuple(_serialize_order(order) for order in expected.orders),
                    previous_rows=order_rows,
                    width=len(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS),
                ),
                _replace_data_request(
                    sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB],
                    tuple(_serialize_item(item) for item in expected.items),
                    previous_rows=item_rows,
                    width=len(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS),
                ),
            )
            try:
                self._gateway.batch_update(self._spreadsheet_id, requests)
            except Exception as error:
                after = self.read_snapshot()
                if _same_snapshot(after, expected):
                    return result
                if _same_snapshot(after, before):
                    raise HistoricalInvoiceStorageError(
                        "Shopee Full Order current sync was not applied; retry from fresh validation."
                    ) from error
                raise HistoricalInvoiceStorageError(
                    "Shopee Full Order current sync outcome is mixed; manual recovery is required."
                ) from error
            if not _same_snapshot(self.read_snapshot(), expected):
                raise HistoricalInvoiceStorageError(
                    "Shopee Full Order current sync readback did not match the intended snapshot."
                )
            return result

    def sync_missing_only(self, batch: FullOrderCurrentBatch) -> FullOrderSyncResult:
        """Append only verified missing historical orders; never replace Current facts."""

        if not batch.syncable:
            raise HistoricalInvoiceStorageError(
                "Shopee Full Order historical backfill is blocked by validation or Product Master issues."
            )
        with self._commit_lock.acquire():
            before = self.read_snapshot()
            result = _classify_missing_only(batch, before)
            additions = tuple(
                order
                for order in batch.orders
                if (order.market, order.order_id)
                not in {(existing.market, existing.order_id) for existing in before.orders}
            )
            if not additions:
                return result
            addition_keys = {(order.market, order.order_id) for order in additions}
            addition_items = tuple(
                item for item in batch.items if (item.market, item.order_id) in addition_keys
            )
            expected = FullOrderCurrentSnapshot(
                orders=before.orders + additions,
                items=before.items + addition_items,
            )
            sheet_ids = self._gateway.read_sheet_ids(
                self._spreadsheet_id,
                (
                    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
                ),
            )
            row_counts = self._gateway.read_sheet_row_counts(
                self._spreadsheet_id,
                (
                    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
                ),
            )
            requests = tuple(
                request
                for request in (
                    _append_rows_request(
                        sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB],
                        current_row_count=row_counts[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB],
                        required_row_count=1 + len(expected.orders),
                    ),
                    _append_rows_request(
                        sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB],
                        current_row_count=row_counts[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB],
                        required_row_count=1 + len(expected.items),
                    ),
                )
                if request is not None
            ) + (
                _append_data_request(
                    sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB],
                    tuple(_serialize_order(order) for order in additions),
                ),
                _append_data_request(
                    sheet_ids[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB],
                    tuple(_serialize_item(item) for item in addition_items),
                ),
            )
            try:
                self._gateway.batch_update(self._spreadsheet_id, requests)
            except Exception as error:
                after = self.read_snapshot()
                if _same_snapshot(after, expected):
                    return result
                if _same_snapshot(after, before):
                    raise HistoricalInvoiceStorageError(
                        "Shopee Full Order historical backfill was not applied; retry from fresh validation."
                    ) from error
                raise HistoricalInvoiceStorageError(
                    "Shopee Full Order historical backfill outcome is mixed; manual recovery is required."
                ) from error
            if not _same_snapshot(self.read_snapshot(), expected):
                raise HistoricalInvoiceStorageError(
                    "Shopee Full Order historical backfill readback did not match the intended append-only snapshot."
                )
            return result


def _classify(
    batch: FullOrderCurrentBatch, snapshot: FullOrderCurrentSnapshot
) -> FullOrderSyncResult:
    if not batch.syncable:
        raise HistoricalInvoiceStorageError(
            "Shopee Full Order current sync validation is not complete."
        )
    existing_orders = {(order.market, order.order_id): order for order in snapshot.orders}
    existing_items = snapshot.items_by_identity()
    batch_items = FullOrderCurrentSnapshot(batch.orders, batch.items).items_by_identity()
    decisions = []
    for order in batch.orders:
        identity = (order.market, order.order_id)
        existing = existing_orders.get(identity)
        if existing is None:
            status = FullOrderSyncStatus.NEW
        elif existing.snapshot_hash == order.snapshot_hash:
            status = FullOrderSyncStatus.UNCHANGED
        else:
            status = FullOrderSyncStatus.UPDATED
        decisions.append(FullOrderSyncDecision(order.market, order.order_id, status))
    return FullOrderSyncResult(tuple(decisions))


def _classify_missing_only(
    batch: FullOrderCurrentBatch, snapshot: FullOrderCurrentSnapshot
) -> FullOrderSyncResult:
    existing = {(order.market, order.order_id) for order in snapshot.orders}
    return FullOrderSyncResult(
        tuple(
            FullOrderSyncDecision(
                order.market,
                order.order_id,
                FullOrderSyncStatus.RETAINED
                if (order.market, order.order_id) in existing
                else FullOrderSyncStatus.NEW,
            )
            for order in batch.orders
        )
    )


def _merged_snapshot(
    before: FullOrderCurrentSnapshot,
    batch: FullOrderCurrentBatch,
    result: FullOrderSyncResult,
) -> FullOrderCurrentSnapshot:
    incoming_orders = {(order.market, order.order_id): order for order in batch.orders}
    incoming_items = FullOrderCurrentSnapshot(batch.orders, batch.items).items_by_identity()
    status = {
        (decision.market, decision.order_id): decision.status
        for decision in result.decisions
    }
    final_orders = list(before.orders)
    order_position = {
        (order.market, order.order_id): index for index, order in enumerate(final_orders)
    }
    final_items = list(before.items)
    for identity, incoming in incoming_orders.items():
        if status[identity] in {FullOrderSyncStatus.UNCHANGED, FullOrderSyncStatus.RETAINED}:
            continue
        if identity in order_position:
            final_orders[order_position[identity]] = incoming
        else:
            order_position[identity] = len(final_orders)
            final_orders.append(incoming)
        existing_items = {
            item.item_index: item
            for item in final_items
            if (item.market, item.order_id) == identity
        }
        final_items = [
            item for item in final_items if (item.market, item.order_id) != identity
        ]
        final_items.extend(
            replace(item, resolve_sku=existing_items[item.item_index].resolve_sku)
            if item.item_index in existing_items
            else item
            for item in incoming_items.get(identity, ())
        )
    item_order = {identity: index for identity, index in order_position.items()}
    final_items.sort(
        key=lambda item: (item_order[(item.market, item.order_id)], item.item_index)
    )
    return FullOrderCurrentSnapshot(tuple(final_orders), tuple(final_items))


def _append_data_request(
    sheet_id: int,
    rows: Sequence[Sequence[str]],
) -> Mapping[str, Any]:
    """Append new Current rows without rewriting any existing order or item cells."""

    return {
        "appendCells": {
            "sheetId": sheet_id,
            "rows": [
                {
                    "values": [
                        {"userEnteredValue": {"stringValue": value}}
                        for value in row
                    ]
                }
                for row in rows
            ],
            "fields": "userEnteredValue",
        }
    }


def deserialize_full_order_current_snapshot(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> FullOrderCurrentSnapshot:
    """Read the locked Current schema for its repository and reporting consumer."""

    order_rows = _validated_rows(
        tabs,
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
    )
    item_rows = _validated_rows(
        tabs,
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    )
    orders = tuple(_deserialize_order(row, number) for number, row in order_rows)
    items = tuple(_deserialize_item(row, number) for number, row in item_rows)
    order_keys = {(order.market, order.order_id) for order in orders}
    if len(order_keys) != len(orders):
        raise HistoricalInvoiceStorageError(
            "Shopee Full Order current Orders contains duplicate identities."
        )
    item_keys = {(item.market, item.order_id, item.item_index) for item in items}
    if len(item_keys) != len(items):
        raise HistoricalInvoiceStorageError(
            "Shopee Full Order current Items contains duplicate identities."
        )
    if any((item.market, item.order_id) not in order_keys for item in items):
        raise HistoricalInvoiceStorageError(
            "Shopee Full Order current Item has no matching current Order."
        )
    return FullOrderCurrentSnapshot(orders=orders, items=items)


def _replace_data_request(
    sheet_id: int,
    rows: Sequence[Sequence[str]],
    *,
    previous_rows: int,
    width: int,
) -> Mapping[str, Any]:
    height = max(previous_rows, len(rows), 1)
    padded = tuple(rows) + tuple(("",) * width for _ in range(height - len(rows)))
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": 1 + height,
                "startColumnIndex": 0,
                "endColumnIndex": width,
            },
            "rows": [
                {
                    "values": [
                        {"userEnteredValue": {"stringValue": value}}
                        for value in row
                    ]
                }
                for row in padded
            ],
            "fields": "userEnteredValue",
        }
    }


def _append_rows_request(
    sheet_id: int,
    *,
    current_row_count: int,
    required_row_count: int,
) -> Mapping[str, Any] | None:
    """Extend only a Current tab's empty grid when its approved snapshot needs it."""
    if current_row_count >= required_row_count:
        return None
    return {
        "appendDimension": {
            "sheetId": sheet_id,
            "dimension": "ROWS",
            "length": required_row_count - current_row_count,
        }
    }


def _validated_rows(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
    tab: str,
    headers: Sequence[str],
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    values = tabs.get(tab)
    if not values or tuple(_text(value) for value in values[0]) != tuple(headers):
        raise HistoricalInvoiceStorageError(
            f"{tab} is missing or its headers do not match Full Order Sync V1."
        )
    result = []
    for row_number, row in enumerate(values[1:], start=2):
        if not any(_text(value) for value in row):
            continue
        padded = tuple(row) + ("",) * max(0, len(headers) - len(row))
        result.append((row_number, padded[: len(headers)]))
    return tuple(result)


def _deserialize_order(row: Sequence[Any], row_number: int) -> FullOrderCurrentOrder:
    values = dict(zip(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS, row))
    try:
        return FullOrderCurrentOrder(
            market=_required(values["market"]),
            order_id=_required(values["order_id"]),
            order_status=_required(values["order_status"]),
            order_created_date=date.fromisoformat(_required(values["order_created_date"])),
            source_file=_required(values["source_file"]),
            snapshot_hash=_required(values["snapshot_hash"]),
            last_synced_at=_datetime(values["last_synced_at"]),
        )
    except (ValueError, KeyError) as error:
        raise HistoricalInvoiceStorageError(
            f"{SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB} row {row_number} is malformed."
        ) from error


def _deserialize_item(row: Sequence[Any], row_number: int) -> FullOrderCurrentItem:
    values = dict(zip(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS, row))
    try:
        market = _required(values["market"])
        resolved_seller_sku = _text(values["resolved_seller_sku"])
        if market != "shopee_sg":
            resolved_seller_sku = _required(values["resolved_seller_sku"])
        return FullOrderCurrentItem(
            market=market,
            order_id=_required(values["order_id"]),
            item_index=int(_required(values["item_index"])),
            source_parent_sku=_text(values["source_parent_sku"]),
            source_seller_sku=_text(values["source_seller_sku"]),
            resolved_seller_sku=resolved_seller_sku,
            nav=_required(values["nav"]),
            product_name=_required(values["product_name"]),
            variation=_text(values["variation"]),
            quantity=int(_required(values["quantity"])),
            unit_price=_decimal(values["unit_price"]),
            product_subtotal=_decimal(values["product_subtotal"]),
            return_refund_status=_text(values["return_refund_status"]),
            resolve_sku=_raw_text(values["resolve_sku"]),
        )
    except (ValueError, KeyError) as error:
        raise HistoricalInvoiceStorageError(
            f"{SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB} row {row_number} is malformed."
        ) from error


def _serialize_order(order: FullOrderCurrentOrder) -> tuple[str, ...]:
    return tuple(_serialize(getattr(order, field)) for field in SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS)


def _serialize_item(item: FullOrderCurrentItem) -> tuple[str, ...]:
    return tuple(
        _raw_text(getattr(item, field)) if field == "resolve_sku" else _serialize(getattr(item, field))
        for field in SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS
    )


def _order_business_values(order: FullOrderCurrentOrder) -> tuple[str, ...]:
    return _serialize_order(order)[:-1]


def _item_business_values(item: FullOrderCurrentItem) -> tuple[str, ...]:
    return _serialize_item(item)[:-1]


def _same_snapshot(left: FullOrderCurrentSnapshot, right: FullOrderCurrentSnapshot) -> bool:
    return (
        tuple(_serialize_order(order) for order in left.orders)
        == tuple(_serialize_order(order) for order in right.orders)
        and tuple(_serialize_item(item) for item in left.items)
        == tuple(_serialize_item(item) for item in right.items)
    )


def _serialize(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _text(value)


def _datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_required(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(_required(value))
    except InvalidOperation as error:
        raise ValueError("Invalid decimal") from error


def _required(value: Any) -> str:
    text = _text(value)
    if not text:
        raise ValueError("Required value is blank")
    return text


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _raw_text(value: Any) -> str:
    """Preserve the approved Listing POS instruction verbatim in Current storage."""

    return "" if value is None else str(value)
