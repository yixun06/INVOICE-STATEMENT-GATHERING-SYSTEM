"""Fail-closed PostgreSQL runtime repository for Shopee Full Order Current."""

from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from sqlalchemy import Engine, exists, insert, select, update
from sqlalchemy.engine import Connection

from src.invoice_app.pg_orm.pg_models import FullOrderCurrentItem as PgItem
from src.invoice_app.pg_orm.pg_models import FullOrderCurrentOrder as PgOrder
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
    FullOrderSyncResult,
    _classify,
    _classify_missing_only,
    _merged_snapshot,
    _same_snapshot,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.market_context import (
    MarketContext,
    MarketKey,
    SHOPEE_MY,
    resolve_market_context,
)
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentBatch,
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
)


class PostgresFullOrderCurrentRepository:
    """Market-bound Current persistence with no Sheets fallback or dual write.

    The Google Sheets repository remains the separately selected legacy backend.
    Each repository instance owns one explicitly approved Shopee market.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        market_context: MarketContext = SHOPEE_MY,
        commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    ) -> None:
        context = resolve_market_context(market_context)
        if context.key not in {MarketKey.SHOPEE_MY, MarketKey.SHOPEE_SG}:
            raise HistoricalInvoiceStorageError(
                "PostgreSQL Full Order runtime requires an explicit Shopee MY or Shopee SG market."
            )
        self._engine = engine
        self._market = context.key.value
        self._commit_lock = commit_lock

    def read_snapshot(self) -> FullOrderCurrentSnapshot:
        with self._engine.connect() as connection:
            return self._read_snapshot(connection)

    def read_order_ids(self, order_ids: Iterable[str], *, market: str) -> frozenset[str]:
        """Fresh exact market/ID coverage, with no item reconstruction."""
        from src.invoice_app.services.statement_performance import measure
        if market != self._market:
            raise HistoricalInvoiceStorageError("Full Order coverage market mismatch.")
        requested = tuple(dict.fromkeys(order_ids))
        found = set()
        with measure("full_order_scoped_query", full_order_items_loaded=0) as counts:
            if requested:
                with self._engine.connect() as connection:
                    for offset in range(0, len(requested), 500):
                        found.update(connection.scalars(select(PgOrder.order_id).where(
                            PgOrder.market == self._market,
                            PgOrder.order_id.in_(requested[offset:offset + 500]),
                        )))
            counts["queries"] = (len(requested) + 499) // 500
            counts["full_order_orders_loaded"] = len(found)
        return frozenset(found)

    def has_order(self, order_id: str) -> bool:
        """Market-scoped existence lookup for callers that do not need a snapshot."""

        normalized = str(order_id).strip()
        if not normalized:
            return False
        statement = select(
            exists().where(PgOrder.market == self._market, PgOrder.order_id == normalized)
        )
        with self._engine.connect() as connection:
            return bool(connection.scalar(statement))

    def classify(self, batch: FullOrderCurrentBatch) -> FullOrderSyncResult:
        self._assert_batch_market(batch)
        return _classify(batch, self.read_snapshot())

    def sync(self, batch: FullOrderCurrentBatch) -> FullOrderSyncResult:
        """Atomically insert/update one verified market source and verify readback."""

        self._assert_syncable(batch)
        with self._commit_lock.acquire(), self._engine.begin() as connection:
            before = self._read_snapshot(connection)
            result = _classify(batch, before)
            if result.new_count == 0 and result.updated_count == 0:
                return result
            expected = _canonical_snapshot(_merged_snapshot(before, batch, result))
            removed = _identity_set(before.items) - _identity_set(expected.items)
            if removed:
                raise HistoricalInvoiceStorageError(
                    "PostgreSQL Full Order sync would delete existing item identities; "
                    "manual recovery is required."
                )
            self._write_expected(connection, before, expected, result)
            after = self._read_snapshot(connection)
            if not _same_postgres_snapshot(after, expected):
                raise HistoricalInvoiceStorageError(
                    "PostgreSQL Full Order sync readback did not match the intended snapshot."
                )
            return result

    def sync_missing_only(self, batch: FullOrderCurrentBatch) -> FullOrderSyncResult:
        """Safely add verified missing market orders without replacing existing facts."""

        self._assert_syncable(batch)
        with self._commit_lock.acquire(), self._engine.begin() as connection:
            before = self._read_snapshot(connection)
            result = _classify_missing_only(batch, before)
            existing = {(row.market, row.order_id) for row in before.orders}
            additions = tuple(
                row for row in batch.orders if (row.market, row.order_id) not in existing
            )
            if not additions:
                return result
            addition_keys = {(row.market, row.order_id) for row in additions}
            addition_items = tuple(
                row for row in batch.items if (row.market, row.order_id) in addition_keys
            )
            expected = _canonical_snapshot(
                FullOrderCurrentSnapshot(
                    orders=before.orders + additions,
                    items=before.items + addition_items,
                )
            )
            self._write_expected(connection, before, expected, result)
            if not _same_postgres_snapshot(self._read_snapshot(connection), expected):
                raise HistoricalInvoiceStorageError(
                    "PostgreSQL Full Order missing-only readback did not match the intended snapshot."
                )
            return result

    def _read_snapshot(self, connection: Connection) -> FullOrderCurrentSnapshot:
        orders = tuple(
            _order_from_row(row)
            for row in connection.execute(
                select(*PgOrder.__table__.c)
                .where(PgOrder.market == self._market)
                .order_by(PgOrder.order_id)
            ).mappings()
        )
        items = tuple(
            _item_from_row(row)
            for row in connection.execute(
                select(*PgItem.__table__.c)
                .where(PgItem.market == self._market)
                .order_by(PgItem.order_id, PgItem.item_index)
            ).mappings()
        )
        order_keys = {(row.market, row.order_id) for row in orders}
        if any((row.market, row.order_id) not in order_keys for row in items):
            raise HistoricalInvoiceStorageError(
                "PostgreSQL Full Order Current contains an item without its market order."
            )
        return FullOrderCurrentSnapshot(orders=orders, items=items)

    def _write_expected(
        self,
        connection: Connection,
        before: FullOrderCurrentSnapshot,
        expected: FullOrderCurrentSnapshot,
        result: FullOrderSyncResult,
    ) -> None:
        changed = {
            (decision.market, decision.order_id)
            for decision in result.decisions
            if decision.status.value in {"NEW", "UPDATED"}
        }
        existing_orders = {(row.market, row.order_id) for row in before.orders}
        existing_items = _identity_set(before.items)
        new_orders: list[dict[str, object]] = []
        new_items: list[dict[str, object]] = []
        for order in expected.orders:
            identity = (order.market, order.order_id)
            if identity not in changed:
                continue
            values = _order_values(order)
            if identity in existing_orders:
                connection.execute(
                    update(PgOrder)
                    .where(PgOrder.market == order.market, PgOrder.order_id == order.order_id)
                    .values(**values)
                )
            else:
                new_orders.append(values)
        for item in expected.items:
            order_identity = (item.market, item.order_id)
            identity = (item.market, item.order_id, item.item_index)
            if order_identity not in changed:
                continue
            values = _item_values(item)
            if identity in existing_items:
                connection.execute(
                    update(PgItem)
                    .where(
                        PgItem.market == item.market,
                        PgItem.order_id == item.order_id,
                        PgItem.item_index == item.item_index,
                    )
                    .values(**values)
                )
            else:
                new_items.append(values)
        if new_orders:
            connection.execute(insert(PgOrder), new_orders)
        if new_items:
            connection.execute(insert(PgItem), new_items)

    def _assert_syncable(self, batch: FullOrderCurrentBatch) -> None:
        if not batch.syncable:
            raise HistoricalInvoiceStorageError(
                "PostgreSQL Full Order sync is blocked by validation or Product Master issues."
            )
        self._assert_batch_market(batch)

    def _assert_batch_market(self, batch: FullOrderCurrentBatch) -> None:
        markets = {row.market for row in batch.orders} | {row.market for row in batch.items}
        if markets != {self._market}:
            raise HistoricalInvoiceStorageError(
                "PostgreSQL Full Order runtime refuses a batch outside its configured market boundary."
            )


def _canonical_snapshot(snapshot: FullOrderCurrentSnapshot) -> FullOrderCurrentSnapshot:
    return FullOrderCurrentSnapshot(
        orders=tuple(sorted(snapshot.orders, key=lambda row: (row.market, row.order_id))),
        items=tuple(
            sorted(snapshot.items, key=lambda row: (row.market, row.order_id, row.item_index))
        ),
    )


def _identity_set(rows: tuple[FullOrderCurrentItem, ...]) -> set[tuple[str, str, int]]:
    return {(row.market, row.order_id, row.item_index) for row in rows}


def _canonical_money(value: object) -> Decimal | None:
    """Normalize decimal scale exactly, without rounding or float conversion."""
    if value is None:
        return None
    if isinstance(value, (float, bool)) or (isinstance(value, str) and not value.strip()):
        raise HistoricalInvoiceStorageError("PostgreSQL Full Order monetary value is invalid.")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise HistoricalInvoiceStorageError("PostgreSQL Full Order monetary value is invalid.") from error
    if not amount.is_finite():
        raise HistoricalInvoiceStorageError("PostgreSQL Full Order monetary value is invalid.")
    if amount.is_zero():
        return Decimal(0)
    sign, digits, exponent = amount.as_tuple()
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return Decimal((sign, tuple(digits), exponent))


def _same_postgres_snapshot(left: FullOrderCurrentSnapshot, right: FullOrderCurrentSnapshot) -> bool:
    def normalized(snapshot: FullOrderCurrentSnapshot) -> FullOrderCurrentSnapshot:
        return FullOrderCurrentSnapshot(
            orders=snapshot.orders,
            items=tuple(replace(item, unit_price=_canonical_money(item.unit_price),
                                product_subtotal=_canonical_money(item.product_subtotal))
                        for item in snapshot.items),
        )
    return _same_snapshot(normalized(left), normalized(right))


def _required_money(value: object, field: str) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HistoricalInvoiceStorageError(f"PostgreSQL Full Order Current has missing {field}.")
    amount = _canonical_money(value)
    assert amount is not None
    return value if isinstance(value, Decimal) else Decimal(value)


def _order_from_row(row: Mapping[str, object]) -> FullOrderCurrentOrder:
    return FullOrderCurrentOrder(
        market=str(row["market"]),
        order_id=str(row["order_id"]),
        order_status=str(row["order_status"] or ""),
        order_created_date=_date(row["order_created_date"]),
        source_file=str(row["source_file"] or ""),
        snapshot_hash=str(row["snapshot_hash"] or ""),
        last_synced_at=_datetime(row["last_synced_at"]),
    )


def _item_from_row(row: Mapping[str, object]) -> FullOrderCurrentItem:
    return FullOrderCurrentItem(
        market=str(row["market"]),
        order_id=str(row["order_id"]),
        item_index=int(row["item_index"]),
        source_parent_sku=str(row["source_parent_sku"] or ""),
        source_seller_sku=str(row["source_seller_sku"] or ""),
        resolved_seller_sku=str(row["resolved_seller_sku"] or ""),
        nav=str(row["nav"] or ""),
        product_name=str(row["product_name"] or ""),
        variation=str(row["variation"] or ""),
        quantity=int(row["quantity"]) if row["quantity"] is not None else _missing_quantity(),
        unit_price=_required_money(row["unit_price"], "unit_price"),
        product_subtotal=_required_money(row["product_subtotal"], "product_subtotal"),
        return_refund_status=str(row["return_refund_status"] or ""),
        resolve_sku=str(row["resolve_sku"] or ""),
    )


def _missing_quantity() -> int:
    raise HistoricalInvoiceStorageError("PostgreSQL Full Order Current has missing quantity.")


def _order_values(row: FullOrderCurrentOrder) -> dict[str, object]:
    return {
        "market": row.market,
        "order_id": row.order_id,
        "order_status": row.order_status,
        "order_created_date": row.order_created_date.isoformat(),
        "source_file": row.source_file,
        "snapshot_hash": row.snapshot_hash,
        "last_synced_at": row.last_synced_at.isoformat(),
    }


def _item_values(row: FullOrderCurrentItem) -> dict[str, object]:
    return {
        "market": row.market,
        "order_id": row.order_id,
        "item_index": row.item_index,
        "source_parent_sku": row.source_parent_sku,
        "source_seller_sku": row.source_seller_sku,
        "resolved_seller_sku": row.resolved_seller_sku,
        "nav": row.nav,
        "product_name": row.product_name,
        "variation": row.variation,
        "quantity": row.quantity,
        "unit_price": row.unit_price,
        "product_subtotal": row.product_subtotal,
        "return_refund_status": row.return_refund_status,
        "resolve_sku": row.resolve_sku,
    }


def _date(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise HistoricalInvoiceStorageError(
            "PostgreSQL Full Order Current contains an invalid order-created date."
        ) from error


def _datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise HistoricalInvoiceStorageError(
            "PostgreSQL Full Order Current contains an invalid sync timestamp."
        ) from error
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
