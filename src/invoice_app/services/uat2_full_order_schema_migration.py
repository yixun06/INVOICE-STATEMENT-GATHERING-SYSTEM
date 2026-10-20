"""Explicit one-time schema plan for Full Order current tabs.

The application does not invoke this migrator automatically. Live schema writes
remain an administrative operation with exact readback verification.
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
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
)
from src.invoice_app.services.uat2_statement_schema_migration import (
    SpreadsheetSchemaSnapshot,
    UAT2StatementSchemaMigrationGateway,
)


class FullOrderSchemaState(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    ALREADY_MIGRATED = "ALREADY_MIGRATED"


@dataclass(frozen=True)
class FullOrderSchemaMigrationResult:
    migrated: bool


def classify_full_order_schema(snapshot: SpreadsheetSchemaSnapshot) -> FullOrderSchemaState:
    expected = {
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    }
    present = {name: snapshot.tabs.get(name) for name in expected}
    if all(schema is None for schema in present.values()):
        return FullOrderSchemaState.ELIGIBLE
    if all(
        schema is not None and schema.headers == expected[name]
        for name, schema in present.items()
    ):
        return FullOrderSchemaState.ALREADY_MIGRATED
    raise ValueError(
        "Full Order current schema is partial or has unexpected headers; zero write is allowed."
    )


def build_full_order_schema_requests(
    snapshot: SpreadsheetSchemaSnapshot,
) -> tuple[Mapping[str, Any], ...]:
    if classify_full_order_schema(snapshot) is not FullOrderSchemaState.ELIGIBLE:
        raise ValueError("Full Order current schema already exists.")
    used_ids = {schema.sheet_id for schema in snapshot.tabs.values()}
    first_id = max(used_ids, default=0) + 1
    while first_id in used_ids or first_id + 1 in used_ids:
        first_id += 2
    return (
        _add_sheet_request(
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
            len(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS),
            first_id,
        ),
        _add_sheet_request(
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            len(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS),
            first_id + 1,
        ),
        _header_request(
            first_id,
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
        ),
        _header_request(
            first_id + 1,
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
        ),
    )


class UAT2FullOrderSchemaMigrator:
    def __init__(
        self,
        *,
        spreadsheet_id: str,
        gateway: UAT2StatementSchemaMigrationGateway,
        commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway
        self._commit_lock = commit_lock
        if not self._spreadsheet_id:
            raise ValueError("spreadsheet_id must not be blank.")

    def migrate(self) -> FullOrderSchemaMigrationResult:
        with self._commit_lock.acquire():
            before = self._gateway.read_schema(self._spreadsheet_id)
            if classify_full_order_schema(before) is FullOrderSchemaState.ALREADY_MIGRATED:
                return FullOrderSchemaMigrationResult(False)
            requests = build_full_order_schema_requests(before)
            try:
                self._gateway.apply_schema_migration(self._spreadsheet_id, requests)
            except Exception as error:
                if self._is_migrated():
                    return FullOrderSchemaMigrationResult(True)
                raise RuntimeError(
                    "Full Order current schema write failed and did not verify; do not retry blindly."
                ) from error
            if not self._is_migrated():
                raise RuntimeError("Full Order current schema did not verify after write.")
            return FullOrderSchemaMigrationResult(True)

    def _is_migrated(self) -> bool:
        try:
            return (
                classify_full_order_schema(self._gateway.read_schema(self._spreadsheet_id))
                is FullOrderSchemaState.ALREADY_MIGRATED
            )
        except Exception:
            return False


def _add_sheet_request(
    title: str, column_count: int, sheet_id: int
) -> Mapping[str, Any]:
    return {
        "addSheet": {
            "properties": {
                "title": title,
                "sheetId": sheet_id,
                "gridProperties": {"rowCount": 1000, "columnCount": column_count},
            }
        }
    }


def _header_request(sheet_id: int, headers: Sequence[str]) -> Mapping[str, Any]:
    return {
        "appendCells": {
            "sheetId": sheet_id,
            "rows": [
                {
                    "values": [
                        {"userEnteredValue": {"stringValue": value}}
                        for value in headers
                    ]
                }
            ],
            "fields": "userEnteredValue",
        }
    }
