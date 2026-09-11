"""Fail-closed, one-time schema migration support for UAT2 Statement persistence.

This module is deliberately not wired to a UI action.  A future explicitly
authorized administrative operation may call ``migrate`` once, while the
single-instance application commit lock is held.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.uat2_persistence_schema import (
    PRE_FINANCIAL_INVOICE_ITEMS_HEADERS as INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_DIFFERENCE_INDEX,
    PRE_FINANCIAL_INVOICE_ORDERS_HEADERS as INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    LEGACY_INVOICE_ORDERS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
)


class UAT2StatementSchemaMigrationError(RuntimeError):
    """The current spreadsheet schema cannot be safely migrated or verified."""


class UAT2StatementSchemaState(Enum):
    ELIGIBLE_FOR_MIGRATION = "ELIGIBLE_FOR_MIGRATION"
    ELIGIBLE_FOR_COMPONENT_LEDGER_MIGRATION = "ELIGIBLE_FOR_COMPONENT_LEDGER_MIGRATION"
    ALREADY_MIGRATED = "ALREADY_MIGRATED"


class UAT2StatementSchemaMigrationStatus(Enum):
    MIGRATED = "MIGRATED"
    ALREADY_MIGRATED = "ALREADY_MIGRATED"
    MIGRATED_AFTER_UNCERTAIN_RESULT = "MIGRATED_AFTER_UNCERTAIN_RESULT"


@dataclass(frozen=True)
class SheetSchema:
    sheet_id: int
    headers: tuple[str, ...]


@dataclass(frozen=True)
class SpreadsheetSchemaSnapshot:
    tabs: Mapping[str, SheetSchema]


@dataclass(frozen=True)
class UAT2StatementSchemaMigrationResult:
    status: UAT2StatementSchemaMigrationStatus


class UAT2StatementSchemaMigrationGateway(Protocol):
    def read_schema(self, spreadsheet_id: str) -> SpreadsheetSchemaSnapshot: ...

    def apply_schema_migration(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None: ...


def classify_uat2_statement_schema(
    snapshot: SpreadsheetSchemaSnapshot,
) -> UAT2StatementSchemaState:
    """Classify only exact old/new canonical states; every other state blocks."""
    orders = snapshot.tabs.get(INVOICE_ORDERS_TAB)
    items = snapshot.tabs.get(INVOICE_ITEMS_TAB)
    statement_data = snapshot.tabs.get(STATEMENT_DATA_TAB)
    components = snapshot.tabs.get(STATEMENT_FINANCIAL_COMPONENTS_TAB)
    if orders is None or items is None:
        missing = [
            tab
            for tab, schema in ((INVOICE_ORDERS_TAB, orders), (INVOICE_ITEMS_TAB, items))
            if schema is None
        ]
        raise UAT2StatementSchemaMigrationError(
            "UAT2 Statement schema migration requires canonical tabs: " + ", ".join(missing)
        )
    if items.headers != INVOICE_ITEMS_HEADERS:
        raise UAT2StatementSchemaMigrationError(
            "UAT2 Statement schema migration rejected: Invoice_Items header is not the exact canonical schema."
        )
    if (
        orders.headers == LEGACY_INVOICE_ORDERS_HEADERS
        and statement_data is None
        and components is None
    ):
        return UAT2StatementSchemaState.ELIGIBLE_FOR_MIGRATION
    if (
        orders.headers == INVOICE_ORDERS_HEADERS
        and statement_data is not None
        and statement_data.headers == STATEMENT_DATA_HEADERS
        and components is None
    ):
        return UAT2StatementSchemaState.ELIGIBLE_FOR_COMPONENT_LEDGER_MIGRATION
    if (
        orders.headers == INVOICE_ORDERS_HEADERS
        and statement_data is not None
        and statement_data.headers == STATEMENT_DATA_HEADERS
        and components is not None
        and components.headers == STATEMENT_FINANCIAL_COMPONENT_HEADERS
    ):
        return UAT2StatementSchemaState.ALREADY_MIGRATED
    raise UAT2StatementSchemaMigrationError(
        "UAT2 Statement schema migration rejected an unexpected or mixed canonical schema; zero write was performed."
    )


def build_uat2_statement_schema_migration_requests(
    snapshot: SpreadsheetSchemaSnapshot,
) -> tuple[Mapping[str, Any], ...]:
    """Build the sole permitted schema batch after exact old-state preflight."""
    state = classify_uat2_statement_schema(snapshot)
    component_sheet_id = _unused_sheet_id(snapshot)
    component_requests = (
        {
            "addSheet": {
                "properties": {
                    "sheetId": component_sheet_id,
                    "title": STATEMENT_FINANCIAL_COMPONENTS_TAB,
                }
            }
        },
        _header_update_request(
            component_sheet_id, 0, STATEMENT_FINANCIAL_COMPONENT_HEADERS
        ),
    )
    if state is UAT2StatementSchemaState.ELIGIBLE_FOR_COMPONENT_LEDGER_MIGRATION:
        return component_requests
    if state is not UAT2StatementSchemaState.ELIGIBLE_FOR_MIGRATION:
        raise UAT2StatementSchemaMigrationError("UAT2 Statement schema is already migrated; no batch was built.")
    invoice_orders_sheet_id = snapshot.tabs[INVOICE_ORDERS_TAB].sheet_id
    statement_data_sheet_id = component_sheet_id + 1
    return (
        {"addSheet": {"properties": {"sheetId": statement_data_sheet_id, "title": STATEMENT_DATA_TAB}}},
        _header_update_request(statement_data_sheet_id, 0, STATEMENT_DATA_HEADERS),
        *component_requests,
        {
            "insertDimension": {
                "range": {
                    "sheetId": invoice_orders_sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": INVOICE_ORDERS_DIFFERENCE_INDEX,
                    "endIndex": INVOICE_ORDERS_DIFFERENCE_INDEX + 1,
                }
            }
        },
        _header_update_request(
            invoice_orders_sheet_id,
            INVOICE_ORDERS_DIFFERENCE_INDEX,
            ("difference",),
        ),
    )


class UAT2StatementSchemaMigrator:
    """Runs exact schema preflight, one batch write, and deterministic verification."""

    def __init__(
        self,
        *,
        spreadsheet_id: str,
        gateway: UAT2StatementSchemaMigrationGateway,
        commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway
        self._commit_lock = commit_lock

    def migrate(self) -> UAT2StatementSchemaMigrationResult:
        """Migrate once, or safely report a confirmed already-migrated state."""
        with self._commit_lock.acquire():
            before_snapshot = self._gateway.read_schema(self._spreadsheet_id)
            before = classify_uat2_statement_schema(before_snapshot)
            if before is UAT2StatementSchemaState.ALREADY_MIGRATED:
                return UAT2StatementSchemaMigrationResult(
                    UAT2StatementSchemaMigrationStatus.ALREADY_MIGRATED
                )
            requests = build_uat2_statement_schema_migration_requests(
                before_snapshot
            )
            try:
                self._gateway.apply_schema_migration(self._spreadsheet_id, requests)
            except Exception as write_error:
                if self._read_and_classify_after_uncertain_write() is UAT2StatementSchemaState.ALREADY_MIGRATED:
                    return UAT2StatementSchemaMigrationResult(
                        UAT2StatementSchemaMigrationStatus.MIGRATED_AFTER_UNCERTAIN_RESULT
                    )
                raise UAT2StatementSchemaMigrationError(
                    "UAT2 Statement schema migration write failed and fresh verification did not confirm completion; do not blindly retry."
                ) from write_error
            if self._read_and_classify() is not UAT2StatementSchemaState.ALREADY_MIGRATED:
                raise UAT2StatementSchemaMigrationError(
                    "UAT2 Statement schema migration result did not verify the exact target schema; do not retry blindly."
                )
            return UAT2StatementSchemaMigrationResult(
                UAT2StatementSchemaMigrationStatus.MIGRATED
            )

    def _read_and_classify(self) -> UAT2StatementSchemaState:
        return classify_uat2_statement_schema(self._gateway.read_schema(self._spreadsheet_id))

    def _read_and_classify_after_uncertain_write(self) -> UAT2StatementSchemaState | None:
        try:
            return self._read_and_classify()
        except Exception:
            return None


def _unused_sheet_id(snapshot: SpreadsheetSchemaSnapshot) -> int:
    sheet_ids = [tab.sheet_id for tab in snapshot.tabs.values()]
    if any(sheet_id < 0 for sheet_id in sheet_ids):
        raise UAT2StatementSchemaMigrationError("UAT2 spreadsheet contains an invalid negative sheet ID.")
    return max(sheet_ids, default=-1) + 1


def _header_update_request(
    sheet_id: int, start_column_index: int, headers: Sequence[str]
) -> Mapping[str, Any]:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": start_column_index,
                "endColumnIndex": start_column_index + len(headers),
            },
            "rows": [
                {
                    "values": [
                        {"userEnteredValue": {"stringValue": header}}
                        for header in headers
                    ]
                }
            ],
            "fields": "userEnteredValue",
        }
    }
