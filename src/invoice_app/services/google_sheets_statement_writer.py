"""Concrete one-batch Google Sheets writer for committed Shopee Statements."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
import json
from typing import Any, Callable, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
    _deserialize_item,
    _deserialize_order,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.shopee_statement_persistence import (
    CommittedStatementReference,
    InvoiceItemStatementUpdate,
    InvoiceOrderStatementUpdate,
    ProtectedInvoiceItemStatementFields,
    StatementCommitAttempt,
    StatementCommitBlocked,
    StatementCommitPlan,
    StatementCommitState,
    StatementWriteIntegrityError,
    StatementWriteNotApplied,
    validate_current_statement_state,
    write_statement_plan_if_current,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
    STATEMENT_SUMMARY_HEADERS,
    STATEMENT_SUMMARY_TAB,
)


class StatementWriteVerification(Enum):
    ALL_APPLIED = "ALL_APPLIED"
    NONE_APPLIED = "NONE_APPLIED"
    MIXED = "MIXED"


class GoogleStatementGateway(Protocol):
    def read_tabs(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, Sequence[Sequence[Any]]]: ...

    def batch_update(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None: ...

    def batch_update_values(
        self, spreadsheet_id: str, data: Sequence[Mapping[str, Any]]
    ) -> None: ...

    def read_sheet_ids(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, int]: ...


@dataclass(frozen=True)
class _OrderTarget:
    row_index: int
    order: CanonicalInvoiceOrder
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _ItemTarget:
    row_index: int
    item: CanonicalInvoiceItem
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _StatementTarget:
    row_index: int
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _FinancialComponentTarget:
    row_index: int
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _StatementSummaryTarget:
    row_index: int
    values: tuple[Any, ...]


@dataclass(frozen=True)
class _WriterSnapshot:
    sheet_ids: Mapping[str, int]
    orders: Mapping[tuple[str, str], _OrderTarget]
    items: Mapping[tuple[str, str, int], _ItemTarget]
    statement_rows: tuple[_StatementTarget, ...]
    statement_append_row_index: int
    financial_component_rows: tuple[_FinancialComponentTarget, ...]
    financial_component_append_row_index: int
    summary_rows: tuple[_StatementSummaryTarget, ...]
    summary_append_row_index: int
    commit_state: StatementCommitState


class GoogleSheetsStatementWriter:
    """Resolve exact persisted rows and submit all Statement mutations together."""

    def __init__(self, *, spreadsheet_id: str, gateway: GoogleStatementGateway) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway

    def reload_commit_state(self) -> StatementCommitState:
        """Freshly read all four authoritative tabs for guarded preflight."""
        return self._read_snapshot().commit_state

    def write_statement_batch(self, plan: StatementCommitPlan) -> None:
        """Build one complete batch and verify the deterministic final state."""
        before = self._read_snapshot()
        reasons = validate_current_statement_state(
            plan, before.commit_state, sku_matching_is_current=True
        )
        if reasons:
            raise StatementCommitBlocked(
                "Fresh Google Sheets writer preflight failed: " + ", ".join(reasons)
            )
        data = self._build_value_ranges(plan, before)
        try:
            self._gateway.batch_update_values(self._spreadsheet_id, data)
        except Exception as write_error:
            verification = self._verify_after_write(plan, before)
            if verification is StatementWriteVerification.ALL_APPLIED:
                return
            if verification is StatementWriteVerification.NONE_APPLIED:
                raise StatementWriteNotApplied(
                    "The Google Sheets write was not applied; a fresh commit attempt is required."
                ) from write_error
            raise StatementWriteIntegrityError(
                "The Google Sheets write outcome is mixed or unverifiable; manual recovery is required and automatic retry is forbidden."
            ) from write_error
        verification = self._verify_after_write(plan, before)
        if verification is not StatementWriteVerification.ALL_APPLIED:
            raise StatementWriteIntegrityError(
                "Google Sheets acknowledged the write but exact Statement and Invoice enrichment readback did not verify; manual recovery is required."
            )

    def _read_snapshot(self) -> _WriterSnapshot:
        try:
            required_tabs = (
                INVOICE_ORDERS_TAB,
                INVOICE_ITEMS_TAB,
                STATEMENT_DATA_TAB,
                STATEMENT_FINANCIAL_COMPONENTS_TAB,
                STATEMENT_SUMMARY_TAB,
            )
            sheet_ids = self._gateway.read_sheet_ids(
                self._spreadsheet_id, required_tabs
            )
            tabs = self._gateway.read_tabs(
                self._spreadsheet_id,
                required_tabs,
            )
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "UAT2 Statement authoritative snapshot read failed."
            ) from error
        order_rows = _rows_with_positions(tabs, INVOICE_ORDERS_TAB, INVOICE_ORDERS_HEADERS)
        item_rows = _rows_with_positions(tabs, INVOICE_ITEMS_TAB, INVOICE_ITEMS_HEADERS)
        statement_rows = _rows_with_positions(tabs, STATEMENT_DATA_TAB, STATEMENT_DATA_HEADERS)
        financial_component_rows = _rows_with_positions(
            tabs,
            STATEMENT_FINANCIAL_COMPONENTS_TAB,
            STATEMENT_FINANCIAL_COMPONENT_HEADERS,
        )
        summary_rows = _rows_with_positions(
            tabs, STATEMENT_SUMMARY_TAB, STATEMENT_SUMMARY_HEADERS
        )

        orders: dict[tuple[str, str], _OrderTarget] = {}
        for row_index, values in order_rows:
            order = _deserialize_order(values, row_index + 1)
            key = (_text(order.platform), _text(order.order_id))
            if key in orders:
                raise StatementCommitBlocked(
                    f"Invoice_Orders contains duplicate authoritative key {key!r}."
                )
            orders[key] = _OrderTarget(row_index, order, values)

        items: dict[tuple[str, str, int], _ItemTarget] = {}
        for row_index, values in item_rows:
            item = _deserialize_item(values, row_index + 1)
            key = (_text(item.platform), _text(item.order_id), item.item_index)
            if key in items:
                raise StatementCommitBlocked(
                    f"Invoice_Items contains duplicate authoritative key {key!r}."
                )
            items[key] = _ItemTarget(row_index, item, values)

        persisted_statement_rows = tuple(
            _StatementTarget(row_index, values) for row_index, values in statement_rows
        )
        commit_state = StatementCommitState(
            orders={
                order_id: target.order
                for (platform, order_id), target in orders.items()
                if platform == "Shopee"
            },
            committed_statements=_committed_statement_references(persisted_statement_rows),
            items=tuple(
                target.item
                for (platform, _, _), target in items.items()
                if platform == "Shopee"
            ),
        )
        return _WriterSnapshot(
            sheet_ids=sheet_ids,
            orders=orders,
            items=items,
            statement_rows=persisted_statement_rows,
            statement_append_row_index=_append_row_index(tabs[STATEMENT_DATA_TAB]),
            financial_component_rows=tuple(
                _FinancialComponentTarget(row_index, values)
                for row_index, values in financial_component_rows
            ),
            financial_component_append_row_index=_append_row_index(
                tabs[STATEMENT_FINANCIAL_COMPONENTS_TAB]
            ),
            summary_rows=tuple(
                _StatementSummaryTarget(row_index, values)
                for row_index, values in summary_rows
            ),
            summary_append_row_index=_append_row_index(tabs[STATEMENT_SUMMARY_TAB]),
            commit_state=commit_state,
        )

    def _build_value_ranges(
        self, plan: StatementCommitPlan, snapshot: _WriterSnapshot
    ) -> tuple[Mapping[str, Any], ...]:
        _validate_incoming_statement_identities(plan, snapshot)
        _validate_incoming_financial_component_identities(plan, snapshot)
        _validate_incoming_summary_identities(plan, snapshot)
        data: list[Mapping[str, Any]] = [
            _value_range(
                STATEMENT_DATA_TAB,
                snapshot.statement_append_row_index,
                0,
                plan.rows,
            ),
            _value_range(
                STATEMENT_FINANCIAL_COMPONENTS_TAB,
                snapshot.financial_component_append_row_index,
                0,
                plan.financial_component_rows,
            ),
            _value_range(
                STATEMENT_SUMMARY_TAB,
                snapshot.summary_append_row_index,
                0,
                plan.summary_rows,
            ),
        ]
        seen_orders: set[tuple[str, str]] = set()
        for update in plan.invoice_order_updates:
            key = ("Shopee", _text(update.order_id))
            if key in seen_orders:
                raise StatementCommitBlocked(f"Statement plan duplicates Invoice_Orders target {key!r}.")
            seen_orders.add(key)
            target = snapshot.orders.get(key)
            if target is None:
                raise StatementCommitBlocked(f"Invoice_Orders target is missing: {key!r}.")
            data.append(
                _order_update_value_range(INVOICE_ORDERS_TAB, target, update)
            )

        seen_items: set[tuple[str, str, int]] = set()
        for update in plan.invoice_item_updates:
            key = ("Shopee", _text(update.order_id), update.item_index)
            if key in seen_items:
                raise StatementCommitBlocked(f"Statement plan duplicates Invoice_Items target {key!r}.")
            seen_items.add(key)
            target = snapshot.items.get(key)
            if target is None:
                raise StatementCommitBlocked(f"Invoice_Items target is missing: {key!r}.")
            data.append(
                _item_update_value_range(INVOICE_ITEMS_TAB, target, update)
            )
        _validate_group_protection(plan, snapshot, seen_items)
        return tuple(data)

    def _verify_after_write(
        self, plan: StatementCommitPlan, before: _WriterSnapshot
    ) -> StatementWriteVerification:
        try:
            after = self._read_snapshot()
        except Exception:
            return StatementWriteVerification.MIXED
        return _classify_write_result(plan, before, after)


def write_google_statement_plan_if_current(
    plan: StatementCommitPlan,
    *,
    writer: GoogleSheetsStatementWriter,
    sku_matching_is_current: Callable[[], bool],
    commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
) -> StatementCommitAttempt:
    """Use the established shared-lock boundary with the concrete writer."""
    return write_statement_plan_if_current(
        plan,
        reload_state=writer.reload_commit_state,
        sku_matching_is_current=sku_matching_is_current,
        writer=writer,
        commit_lock=commit_lock,
    )


def _rows_with_positions(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
    tab: str,
    headers: Sequence[str],
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    if tab not in tabs:
        raise StatementCommitBlocked(f"UAT2 spreadsheet required tab '{tab}' is missing.")
    raw_rows = tuple(tuple(row) for row in tabs[tab])
    if not raw_rows:
        raise StatementCommitBlocked(f"UAT2 spreadsheet tab '{tab}' is missing its schema header.")
    if tuple(_text(value) for value in raw_rows[0]) != tuple(headers):
        raise StatementCommitBlocked(
            f"UAT2 spreadsheet tab '{tab}' header does not exactly match the required schema."
        )
    result = []
    for row_index, row in enumerate(raw_rows[1:], start=1):
        if len(row) > len(headers) and any(_text(value) for value in row[len(headers) :]):
            raise StatementCommitBlocked(
                f"UAT2 spreadsheet tab '{tab}' row {row_index + 1} has data beyond the schema."
            )
        padded = row + (None,) * max(0, len(headers) - len(row))
        if any(_text(value) for value in padded):
            result.append((row_index, padded[: len(headers)]))
    return tuple(result)


def _committed_statement_references(
    rows: Sequence[_StatementTarget],
) -> tuple[CommittedStatementReference, ...]:
    positions = {header: index for index, header in enumerate(STATEMENT_DATA_HEADERS)}
    batches: dict[str, tuple[str, date, date]] = {}
    identities: set[tuple[str, str, str]] = set()
    for target in rows:
        values = target.values
        if _text(values[positions["commit_status"]]) != "COMMITTED":
            continue
        identity = (
            _text(values[positions["statement_batch_id"]]),
            _text(values[positions["record_type"]]),
            _text(values[positions["sequence_no"]]),
        )
        if not all(identity):
            raise StatementCommitBlocked("Committed Statement_Data row has incomplete stable identity.")
        if identity in identities:
            raise StatementCommitBlocked(f"Statement_Data duplicates stable identity {identity!r}.")
        identities.add(identity)
        batch_id = identity[0]
        file_hash = _required_text(values[positions["statement_file_hash"]], "statement_file_hash")
        period_from = _date(values[positions["statement_period_from"]], "statement_period_from")
        period_to = _date(values[positions["statement_period_to"]], "statement_period_to")
        facts = (file_hash, period_from, period_to)
        if batch_id in batches and batches[batch_id] != facts:
            raise StatementCommitBlocked(
                f"Committed Statement_Data batch {batch_id!r} has conflicting audit facts."
            )
        batches[batch_id] = facts
    return tuple(
        CommittedStatementReference(file_hash, period_from, period_to)
        for file_hash, period_from, period_to in batches.values()
    )


def _append_row_index(rows: Sequence[Sequence[Any]]) -> int:
    populated = [index for index, row in enumerate(rows) if any(_text(value) for value in row)]
    if not populated:
        raise StatementCommitBlocked("Statement persistence tab header is missing.")
    return max(populated) + 1


def _validate_incoming_statement_identities(
    plan: StatementCommitPlan, snapshot: _WriterSnapshot
) -> None:
    positions = {header: index for index, header in enumerate(STATEMENT_DATA_HEADERS)}
    existing = {
        (
            _text(target.values[positions["statement_batch_id"]]),
            _text(target.values[positions["record_type"]]),
            _text(target.values[positions["sequence_no"]]),
        )
        for target in snapshot.statement_rows
    }
    incoming = [
        (
            _text(row[positions["statement_batch_id"]]),
            _text(row[positions["record_type"]]),
            _text(row[positions["sequence_no"]]),
        )
        for row in plan.rows
    ]
    if len(set(incoming)) != len(incoming):
        raise StatementCommitBlocked("Statement plan contains duplicate stable row identity.")
    collisions = sorted(set(incoming) & existing)
    if collisions:
        raise StatementCommitBlocked(
            "Statement_Data already contains planned stable row identity: "
            + ", ".join("/".join(identity) for identity in collisions[:5])
        )


def _validate_incoming_financial_component_identities(
    plan: StatementCommitPlan, snapshot: _WriterSnapshot
) -> None:
    positions = {
        header: index
        for index, header in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    identity_fields = (
        "statement_batch_id",
        "statement_source_sheet",
        "statement_source_row_number",
        "component_name",
    )
    existing = {
        tuple(_text(target.values[positions[field]]) for field in identity_fields)
        for target in snapshot.financial_component_rows
    }
    incoming = [
        tuple(_text(row[positions[field]]) for field in identity_fields)
        for row in plan.financial_component_rows
    ]
    if not incoming:
        raise StatementCommitBlocked("Statement plan has no financial component ledger rows.")
    if any(not all(identity) for identity in incoming):
        raise StatementCommitBlocked("Financial component plan has incomplete stable identity.")
    if len(set(incoming)) != len(incoming):
        raise StatementCommitBlocked("Financial component plan contains duplicate stable identity.")
    collisions = sorted(set(incoming) & existing)
    if collisions:
        raise StatementCommitBlocked(
            "Statement_Financial_Components already contains planned stable identity: "
            + ", ".join("/".join(identity) for identity in collisions[:5])
        )


def _validate_incoming_summary_identities(
    plan: StatementCommitPlan, snapshot: _WriterSnapshot
) -> None:
    positions = {
        header: index for index, header in enumerate(STATEMENT_SUMMARY_HEADERS)
    }
    identity_fields = (
        "statement_batch_id", "statement_source_sheet", "statement_source_row_number",
    )
    existing = {
        tuple(_text(target.values[positions[field]]) for field in identity_fields)
        for target in snapshot.summary_rows
    }
    incoming = [
        tuple(_text(row[positions[field]]) for field in identity_fields)
        for row in plan.summary_rows
    ]
    if not incoming:
        raise StatementCommitBlocked("Statement plan has no native Summary rows.")
    if any(not all(identity) for identity in incoming):
        raise StatementCommitBlocked("Statement Summary plan has incomplete stable identity.")
    if len(set(incoming)) != len(incoming):
        raise StatementCommitBlocked("Statement Summary plan contains duplicate stable identity.")
    collisions = sorted(set(incoming) & existing)
    if collisions:
        raise StatementCommitBlocked(
            "Statement_Summary already contains planned stable identity: "
            + ", ".join("/".join(identity) for identity in collisions[:5])
        )


def _order_update_value_range(
    tab: str,
    target: _OrderTarget,
    update: InvoiceOrderStatementUpdate,
) -> Mapping[str, Any]:
    start = INVOICE_ORDERS_HEADERS.index("payment_status")
    expected = (
        "payment_status",
        "payout_completed_date",
        "difference",
    )
    if tuple(INVOICE_ORDERS_HEADERS[start : start + 3]) != expected:
        raise StatementCommitBlocked(
            "Invoice_Orders Statement enrichment columns are no longer contiguous."
        )
    return _value_range(
        tab,
        target.row_index,
        start,
        ((
            _serialize(update.payment_status),
            _serialize(update.payout_completed_date),
            _serialize(update.difference),
        ),),
    )


def _item_update_value_range(
    tab: str,
    target: _ItemTarget,
    update: InvoiceItemStatementUpdate,
) -> Mapping[str, Any]:
    start = INVOICE_ITEMS_HEADERS.index("statement_product_price")
    expected = (
        "statement_product_price",
        "statement_refund_amount",
        "statement_net_selling_amount",
    )
    if tuple(INVOICE_ITEMS_HEADERS[start : start + 3]) != expected:
        raise StatementCommitBlocked(
            "Invoice_Items Statement enrichment columns are no longer contiguous."
        )
    return _value_range(
        tab,
        target.row_index,
        start,
        ((
            _serialize(update.statement_product_price),
            _serialize(update.statement_refund_amount),
            _serialize(update.statement_net_selling_amount),
        ),),
    )


def _value_range(
    tab: str,
    start_row_index: int,
    start_column_index: int,
    rows: Sequence[Sequence[str]],
) -> Mapping[str, Any]:
    if not rows:
        raise StatementCommitBlocked(
            "No rows were supplied for Statement update construction."
        )
    width = len(rows[0])
    if width <= 0 or any(len(row) != width for row in rows):
        raise StatementCommitBlocked(
            "Statement update rows do not have one deterministic width."
        )
    return {
        "range": _a1_range(
            tab,
            start_row_index,
            start_column_index,
            len(rows),
            width,
        ),
        "majorDimension": "ROWS",
        "values": [list(row) for row in rows],
    }


def _a1_range(
    tab: str,
    start_row_index: int,
    start_column_index: int,
    height: int,
    width: int,
) -> str:
    if min(start_row_index, start_column_index) < 0 or min(height, width) <= 0:
        raise StatementCommitBlocked("Invalid Statement write range.")
    quoted_tab = "'" + tab.replace("'", "''") + "'"
    start = f"{_column_name(start_column_index)}{start_row_index + 1}"
    end = (
        f"{_column_name(start_column_index + width - 1)}"
        f"{start_row_index + height}"
    )
    return f"{quoted_tab}!{start}:{end}"


def _column_name(column_index: int) -> str:
    result = ""
    value = column_index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def values_batch_update_body(
    data: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Return the exact compact, single-request Google values payload."""

    return {"valueInputOption": "RAW", "data": list(data)}


def values_batch_update_payload_size(
    data: Sequence[Mapping[str, Any]],
) -> int:
    """Measure the UTF-8 JSON body submitted by the production gateway."""

    return len(
        json.dumps(
            values_batch_update_body(data),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _validate_group_protection(
    plan: StatementCommitPlan,
    snapshot: _WriterSnapshot,
    item_update_keys: set[tuple[str, str, int]],
) -> None:
    protected_keys: set[tuple[str, str, int]] = set()
    for protected in plan.protected_invoice_items:
        key = ("Shopee", _text(protected.order_id), protected.item_index)
        if key in protected_keys:
            raise StatementCommitBlocked(
                f"Statement plan duplicates GROUP protected target {key!r}."
            )
        if key in item_update_keys:
            raise StatementCommitBlocked(
                f"Statement plan overlaps ITEM and GROUP target {key!r}."
            )
        protected_keys.add(key)
        target = snapshot.items.get(key)
        if target is None:
            raise StatementCommitBlocked(
                f"GROUP protected Invoice_Items target is missing: {key!r}."
            )
        if _protected_values(target) != _expected_protected_values(protected):
            raise StatementCommitBlocked(
                f"GROUP protected Invoice_Items state changed: {key!r}."
            )


def _protected_values(target: _ItemTarget) -> tuple[str, str, str]:
    return tuple(
        _text(target.values[INVOICE_ITEMS_HEADERS.index(field)])
        for field in (
            "statement_product_price",
            "statement_refund_amount",
            "statement_net_selling_amount",
        )
    )


def _expected_protected_values(
    protected: ProtectedInvoiceItemStatementFields,
) -> tuple[str, str, str]:
    return (
        _serialize(protected.statement_product_price),
        _serialize(protected.statement_refund_amount),
        _serialize(protected.statement_net_selling_amount),
    )


def _order_update_requests(
    sheet_id: int, target: _OrderTarget, update: InvoiceOrderStatementUpdate
) -> tuple[Mapping[str, Any], ...]:
    positions = {header: index for index, header in enumerate(INVOICE_ORDERS_HEADERS)}
    requests: list[Mapping[str, Any]] = [
        _update_rows_request(
            sheet_id,
            target.row_index,
            positions["payment_status"],
            ((
                _serialize(update.payment_status),
                _serialize(update.payout_completed_date),
                _serialize(update.difference),
            ),),
        )
    ]
    return tuple(requests)


def _item_update_request(
    sheet_id: int, target: _ItemTarget, update: InvoiceItemStatementUpdate
) -> Mapping[str, Any]:
    start = INVOICE_ITEMS_HEADERS.index("statement_product_price")
    return _update_rows_request(
        sheet_id,
        target.row_index,
        start,
        ((
            _serialize(update.statement_product_price),
            _serialize(update.statement_refund_amount),
            _serialize(update.statement_net_selling_amount),
        ),),
    )


def _update_rows_request(
    sheet_id: int,
    start_row_index: int,
    start_column_index: int,
    rows: Sequence[Sequence[str]],
) -> Mapping[str, Any]:
    if not rows:
        raise StatementCommitBlocked("No rows were supplied for Statement update construction.")
    width = len(rows[0])
    if width <= 0 or any(len(row) != width for row in rows):
        raise StatementCommitBlocked("Statement update rows do not have one deterministic width.")
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row_index,
                "endRowIndex": start_row_index + len(rows),
                "startColumnIndex": start_column_index,
                "endColumnIndex": start_column_index + width,
            },
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


def _classify_write_result(
    plan: StatementCommitPlan,
    before: _WriterSnapshot,
    after: _WriterSnapshot,
) -> StatementWriteVerification:
    expected_rows = Counter(plan.rows)
    after_rows = Counter(
        tuple(_cell_string(value) for value in target.values)
        for target in after.statement_rows
    )
    all_statements = bool(expected_rows) and all(
        after_rows[row] == count for row, count in expected_rows.items()
    )
    no_statements = all(after_rows[row] == 0 for row in expected_rows)

    expected_components = Counter(plan.financial_component_rows)
    after_components = Counter(
        tuple(_cell_string(value) for value in target.values)
        for target in after.financial_component_rows
    )
    all_components = bool(expected_components) and all(
        after_components[row] == count
        for row, count in expected_components.items()
    )
    no_components = all(after_components[row] == 0 for row in expected_components)

    expected_summary = Counter(plan.summary_rows)
    after_summary = Counter(
        tuple(_cell_string(value) for value in target.values)
        for target in after.summary_rows
    )
    all_summary = bool(expected_summary) and all(
        after_summary[row] == count for row, count in expected_summary.items()
    )
    no_summary = all(after_summary[row] == 0 for row in expected_summary)

    expected_targets = _expected_target_values(plan)
    all_enrichments = True
    no_enrichments = True
    for key, field_values in expected_targets.items():
        before_values = _target_values(before, key, field_values)
        after_values = _target_values(after, key, field_values)
        expected_values = tuple(value for _, value in field_values)
        all_enrichments = all_enrichments and after_values == expected_values
        no_enrichments = no_enrichments and after_values == before_values
    protected_unchanged = _protected_items_unchanged(plan, before, after)
    if (
        all_statements
        and all_components
        and all_summary
        and all_enrichments
        and protected_unchanged
    ):
        return StatementWriteVerification.ALL_APPLIED
    if (
        no_statements
        and no_components
        and no_summary
        and no_enrichments
        and protected_unchanged
    ):
        return StatementWriteVerification.NONE_APPLIED
    return StatementWriteVerification.MIXED


def _protected_items_unchanged(
    plan: StatementCommitPlan,
    before: _WriterSnapshot,
    after: _WriterSnapshot,
) -> bool:
    for protected in plan.protected_invoice_items:
        key = ("Shopee", _text(protected.order_id), protected.item_index)
        before_target = before.items.get(key)
        after_target = after.items.get(key)
        if before_target is None or after_target is None:
            return False
        expected = _expected_protected_values(protected)
        if (
            _protected_values(before_target) != expected
            or _protected_values(after_target) != expected
        ):
            return False
    return True


def _expected_target_values(
    plan: StatementCommitPlan,
) -> Mapping[tuple[str, str, str, int | None], tuple[tuple[str, str], ...]]:
    expected: dict[tuple[str, str, str, int | None], tuple[tuple[str, str], ...]] = {}
    for update in plan.invoice_order_updates:
        expected[(INVOICE_ORDERS_TAB, "Shopee", update.order_id, None)] = (
            ("payment_status", _serialize(update.payment_status)),
            ("payout_completed_date", _serialize(update.payout_completed_date)),
            ("difference", _serialize(update.difference)),
        )
    for update in plan.invoice_item_updates:
        expected[(INVOICE_ITEMS_TAB, "Shopee", update.order_id, update.item_index)] = (
            ("statement_product_price", _serialize(update.statement_product_price)),
            ("statement_refund_amount", _serialize(update.statement_refund_amount)),
            ("statement_net_selling_amount", _serialize(update.statement_net_selling_amount)),
        )
    return expected


def _target_values(
    snapshot: _WriterSnapshot,
    key: tuple[str, str, str, int | None],
    field_values: Sequence[tuple[str, str]],
) -> tuple[str, ...] | None:
    tab, platform, order_id, item_index = key
    if tab == INVOICE_ORDERS_TAB:
        target = snapshot.orders.get((platform, order_id))
        headers = INVOICE_ORDERS_HEADERS
    else:
        target = snapshot.items.get((platform, order_id, item_index))
        headers = INVOICE_ITEMS_HEADERS
    if target is None:
        return None
    return tuple(_text(target.values[headers.index(field)]) for field, _ in field_values)


def _serialize(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _date(value: Any, field: str) -> date:
    text = _required_text(value, field)
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise StatementCommitBlocked(f"Statement_Data has invalid {field}.") from error


def _required_text(value: Any, field: str) -> str:
    text = _text(value)
    if not text:
        raise StatementCommitBlocked(f"Statement_Data has blank {field}.")
    return text


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _cell_string(value: Any) -> str:
    return "" if value is None else str(value)
