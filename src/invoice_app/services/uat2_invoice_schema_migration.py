"""Fail-closed migration plan for the Invoice financial-layout schema revision.

The migrator is deliberately not exposed in the application UI. Live execution
requires a separately authorized administrative operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    PRE_FINANCIAL_INVOICE_ITEMS_HEADERS,
    PRE_FINANCIAL_INVOICE_ORDERS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
)
from src.invoice_app.services.uat2_statement_schema_migration import (
    SpreadsheetSchemaSnapshot,
    UAT2StatementSchemaMigrationGateway,
)


class UAT2InvoiceSchemaMigrationError(RuntimeError):
    """The Invoice schema cannot be safely migrated or verified."""


class UAT2InvoiceSchemaState(Enum):
    ELIGIBLE_FOR_MIGRATION = "ELIGIBLE_FOR_MIGRATION"
    ALREADY_MIGRATED = "ALREADY_MIGRATED"


class UAT2InvoiceSchemaMigrationStatus(Enum):
    MIGRATED = "MIGRATED"
    ALREADY_MIGRATED = "ALREADY_MIGRATED"
    MIGRATED_AFTER_UNCERTAIN_RESULT = "MIGRATED_AFTER_UNCERTAIN_RESULT"


@dataclass(frozen=True)
class UAT2InvoiceSchemaMigrationResult:
    status: UAT2InvoiceSchemaMigrationStatus


def classify_uat2_invoice_schema(
    snapshot: SpreadsheetSchemaSnapshot,
) -> UAT2InvoiceSchemaState:
    required = {
        INVOICE_ORDERS_TAB: snapshot.tabs.get(INVOICE_ORDERS_TAB),
        INVOICE_ITEMS_TAB: snapshot.tabs.get(INVOICE_ITEMS_TAB),
        STATEMENT_DATA_TAB: snapshot.tabs.get(STATEMENT_DATA_TAB),
        STATEMENT_FINANCIAL_COMPONENTS_TAB: snapshot.tabs.get(
            STATEMENT_FINANCIAL_COMPONENTS_TAB
        ),
    }
    if any(schema is None for schema in required.values()):
        missing = [tab for tab, schema in required.items() if schema is None]
        raise UAT2InvoiceSchemaMigrationError(
            "UAT2 Invoice schema migration requires canonical tabs: " + ", ".join(missing)
        )
    orders = required[INVOICE_ORDERS_TAB]
    items = required[INVOICE_ITEMS_TAB]
    statement = required[STATEMENT_DATA_TAB]
    components = required[STATEMENT_FINANCIAL_COMPONENTS_TAB]
    assert orders is not None and items is not None and statement is not None and components is not None
    statement_is_locked = (
        statement.headers == STATEMENT_DATA_HEADERS
        and components.headers == STATEMENT_FINANCIAL_COMPONENT_HEADERS
    )
    if not statement_is_locked:
        raise UAT2InvoiceSchemaMigrationError(
            "UAT2 Invoice schema migration rejected non-canonical locked Statement headers; zero write was performed."
        )
    if (
        orders.headers == PRE_FINANCIAL_INVOICE_ORDERS_HEADERS
        and items.headers == PRE_FINANCIAL_INVOICE_ITEMS_HEADERS
    ):
        return UAT2InvoiceSchemaState.ELIGIBLE_FOR_MIGRATION
    if orders.headers == INVOICE_ORDERS_HEADERS and items.headers == INVOICE_ITEMS_HEADERS:
        return UAT2InvoiceSchemaState.ALREADY_MIGRATED
    raise UAT2InvoiceSchemaMigrationError(
        "UAT2 Invoice schema migration rejected unexpected or mixed Invoice headers; zero write was performed."
    )


def build_uat2_invoice_schema_migration_requests(
    snapshot: SpreadsheetSchemaSnapshot,
) -> tuple[Mapping[str, Any], ...]:
    if classify_uat2_invoice_schema(snapshot) is not UAT2InvoiceSchemaState.ELIGIBLE_FOR_MIGRATION:
        raise UAT2InvoiceSchemaMigrationError(
            "UAT2 Invoice schema is already migrated; no batch was built."
        )
    orders_id = snapshot.tabs[INVOICE_ORDERS_TAB].sheet_id
    items_id = snapshot.tabs[INVOICE_ITEMS_TAB].sheet_id
    return (
        _insert_columns_request(orders_id, 7, 1),
        _insert_columns_request(orders_id, 15, 2),
        _insert_columns_request(orders_id, 25, 1),
        _header_update_request(orders_id, INVOICE_ORDERS_HEADERS),
        _insert_columns_request(items_id, 4, 1),
        _insert_columns_request(items_id, 14, 2),
        _header_update_request(items_id, INVOICE_ITEMS_HEADERS),
    )


class UAT2InvoiceSchemaMigrator:
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

    def migrate(self) -> UAT2InvoiceSchemaMigrationResult:
        with self._commit_lock.acquire():
            before = self._gateway.read_schema(self._spreadsheet_id)
            state = classify_uat2_invoice_schema(before)
            if state is UAT2InvoiceSchemaState.ALREADY_MIGRATED:
                return UAT2InvoiceSchemaMigrationResult(
                    UAT2InvoiceSchemaMigrationStatus.ALREADY_MIGRATED
                )
            requests = build_uat2_invoice_schema_migration_requests(before)
            try:
                self._gateway.apply_schema_migration(self._spreadsheet_id, requests)
            except Exception as write_error:
                if self._read_state() is UAT2InvoiceSchemaState.ALREADY_MIGRATED:
                    return UAT2InvoiceSchemaMigrationResult(
                        UAT2InvoiceSchemaMigrationStatus.MIGRATED_AFTER_UNCERTAIN_RESULT
                    )
                raise UAT2InvoiceSchemaMigrationError(
                    "UAT2 Invoice schema migration write failed and fresh verification did not confirm completion; do not blindly retry."
                ) from write_error
            if self._read_state() is not UAT2InvoiceSchemaState.ALREADY_MIGRATED:
                raise UAT2InvoiceSchemaMigrationError(
                    "UAT2 Invoice schema migration result did not verify the exact target schema; do not retry blindly."
                )
            return UAT2InvoiceSchemaMigrationResult(UAT2InvoiceSchemaMigrationStatus.MIGRATED)

    def _read_state(self) -> UAT2InvoiceSchemaState | None:
        try:
            return classify_uat2_invoice_schema(
                self._gateway.read_schema(self._spreadsheet_id)
            )
        except Exception:
            return None


def _insert_columns_request(
    sheet_id: int, start_column_index: int, count: int
) -> Mapping[str, Any]:
    return {
        "insertDimension": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": start_column_index,
                "endIndex": start_column_index + count,
            }
        }
    }


def _header_update_request(
    sheet_id: int, headers: Sequence[str]
) -> Mapping[str, Any]:
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": len(headers),
            },
            "rows": [{"values": [{"userEnteredValue": {"stringValue": value}} for value in headers]}],
            "fields": "userEnteredValue",
        }
    }
