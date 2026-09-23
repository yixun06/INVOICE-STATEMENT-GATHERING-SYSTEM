"""Atomic Google Sheets writer for isolated Shopee Monthly Statements."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from ..repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from .application_commit_lock import APPLICATION_COMMIT_LOCK, ApplicationCommitLock
from .shopee_monthly_statement_persistence import MonthlyStatementCommitPlan
from .shopee_monthly_statement_service import MonthlyStatementReference
from .shopee_statement_persistence import (
    StatementCommitBlocked,
    StatementWriteIntegrityError,
    StatementWriteNotApplied,
)
from .uat2_persistence_schema import (
    MONTHLY_STATEMENT_DATA_HEADERS,
    MONTHLY_STATEMENT_DATA_TAB,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
    MONTHLY_STATEMENT_SUMMARY_HEADERS,
    MONTHLY_STATEMENT_SUMMARY_TAB,
)
from .uat2_statement_schema_migration import SheetSchema, SpreadsheetSchemaSnapshot


class MonthlyWriteVerification(Enum):
    ALL_APPLIED = "ALL_APPLIED"
    NONE_APPLIED = "NONE_APPLIED"
    MIXED = "MIXED"


class MonthlySchemaStatus(Enum):
    CREATED = "CREATED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    CREATED_AFTER_UNCERTAIN_RESULT = "CREATED_AFTER_UNCERTAIN_RESULT"


class GoogleMonthlyStatementGateway(Protocol):
    def read_schema(self, spreadsheet_id: str) -> SpreadsheetSchemaSnapshot: ...

    def apply_schema_migration(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None: ...

    def read_tabs(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, Sequence[Sequence[Any]]]: ...

    def batch_update_values(
        self, spreadsheet_id: str, data: Sequence[Mapping[str, Any]]
    ) -> None: ...


@dataclass(frozen=True)
class MonthlyStatementWriteResult:
    statement_batch_id: str
    data_rows: int
    financial_component_rows: int
    summary_rows: int


class GoogleSheetsMonthlyStatementWriter:
    def __init__(
        self,
        *,
        spreadsheet_id: str,
        gateway: GoogleMonthlyStatementGateway,
        commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway
        self._commit_lock = commit_lock

    def ensure_schema(self) -> MonthlySchemaStatus:
        """Create only missing Monthly tabs, then verify all exact headers."""

        with self._commit_lock.acquire():
            before = self._gateway.read_schema(self._spreadsheet_id)
            missing = _validate_monthly_schema(before, allow_missing=True)
            if not missing:
                return MonthlySchemaStatus.ALREADY_PRESENT
            requests = _schema_creation_requests(before, missing)
            try:
                self._gateway.apply_schema_migration(self._spreadsheet_id, requests)
            except Exception as write_error:
                try:
                    after = self._gateway.read_schema(self._spreadsheet_id)
                    if not _validate_monthly_schema(after, allow_missing=False):
                        return MonthlySchemaStatus.CREATED_AFTER_UNCERTAIN_RESULT
                except Exception:
                    pass
                raise StatementWriteIntegrityError(
                    "Monthly schema creation failed and exact readback did not confirm all three tabs; do not blindly retry."
                ) from write_error
            after = self._gateway.read_schema(self._spreadsheet_id)
            _validate_monthly_schema(after, allow_missing=False)
            return MonthlySchemaStatus.CREATED

    def committed_references(self) -> tuple[MonthlyStatementReference, ...]:
        snapshot = self._read_snapshot()
        return _committed_references(snapshot[MONTHLY_STATEMENT_DATA_TAB])

    def write_monthly_batch(
        self, plan: MonthlyStatementCommitPlan
    ) -> MonthlyStatementWriteResult:
        """Fresh-preflight and append all three Monthly datasets in one call."""

        with self._commit_lock.acquire():
            before = self._read_snapshot()
            _validate_duplicate_scope(plan, before[MONTHLY_STATEMENT_DATA_TAB])
            data = (
                _value_range(
                    MONTHLY_STATEMENT_DATA_TAB,
                    _append_row_index(before[MONTHLY_STATEMENT_DATA_TAB]),
                    plan.rows,
                ),
                _value_range(
                    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
                    _append_row_index(
                        before[MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB]
                    ),
                    plan.financial_component_rows,
                ),
                _value_range(
                    MONTHLY_STATEMENT_SUMMARY_TAB,
                    _append_row_index(before[MONTHLY_STATEMENT_SUMMARY_TAB]),
                    plan.summary_rows,
                ),
            )
            try:
                self._gateway.batch_update_values(self._spreadsheet_id, data)
            except Exception as write_error:
                verification = self._verify_after_write(plan, before)
                if verification is MonthlyWriteVerification.ALL_APPLIED:
                    return _write_result(plan)
                if verification is MonthlyWriteVerification.NONE_APPLIED:
                    raise StatementWriteNotApplied(
                        "The Monthly Statement write was not applied; a fresh commit attempt is required."
                    ) from write_error
                raise StatementWriteIntegrityError(
                    "The Monthly Statement write outcome is mixed or unverifiable; manual recovery is required."
                ) from write_error
            if self._verify_after_write(plan, before) is not MonthlyWriteVerification.ALL_APPLIED:
                raise StatementWriteIntegrityError(
                    "Google Sheets acknowledged the Monthly write but complete three-tab readback did not verify."
                )
            return _write_result(plan)

    def _read_snapshot(self) -> Mapping[str, tuple[tuple[str, ...], ...]]:
        schema = self._gateway.read_schema(self._spreadsheet_id)
        _validate_monthly_schema(schema, allow_missing=False)
        tabs = (
            MONTHLY_STATEMENT_DATA_TAB,
            MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
            MONTHLY_STATEMENT_SUMMARY_TAB,
        )
        try:
            raw = self._gateway.read_tabs(self._spreadsheet_id, tabs)
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "Monthly Statement snapshot read failed."
            ) from error
        return {
            tab: _normalized_rows(tab, raw.get(tab, ()))
            for tab in tabs
        }

    def _verify_after_write(
        self,
        plan: MonthlyStatementCommitPlan,
        before: Mapping[str, tuple[tuple[str, ...], ...]],
    ) -> MonthlyWriteVerification:
        try:
            after = self._read_snapshot()
        except Exception:
            return MonthlyWriteVerification.MIXED
        expected = {
            MONTHLY_STATEMENT_DATA_TAB: plan.rows,
            MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB: plan.financial_component_rows,
            MONTHLY_STATEMENT_SUMMARY_TAB: plan.summary_rows,
        }
        applied: list[bool] = []
        unchanged: list[bool] = []
        for tab, rows in expected.items():
            start = _append_row_index(before[tab])
            actual = after[tab][start : start + len(rows)]
            applied.append(actual == rows and len(after[tab]) == len(before[tab]) + len(rows))
            unchanged.append(after[tab] == before[tab])
        if all(applied):
            return MonthlyWriteVerification.ALL_APPLIED
        if all(unchanged):
            return MonthlyWriteVerification.NONE_APPLIED
        return MonthlyWriteVerification.MIXED


_MONTHLY_SCHEMAS = {
    MONTHLY_STATEMENT_DATA_TAB: MONTHLY_STATEMENT_DATA_HEADERS,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB: MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    MONTHLY_STATEMENT_SUMMARY_TAB: MONTHLY_STATEMENT_SUMMARY_HEADERS,
}


def _validate_monthly_schema(
    snapshot: SpreadsheetSchemaSnapshot, *, allow_missing: bool
) -> tuple[str, ...]:
    missing: list[str] = []
    for tab, expected in _MONTHLY_SCHEMAS.items():
        current = snapshot.tabs.get(tab)
        if current is None:
            missing.append(tab)
            continue
        if current.headers != expected:
            raise StatementCommitBlocked(
                f"{tab} header is not the exact approved schema; zero write was performed."
            )
    if missing and not allow_missing:
        raise StatementCommitBlocked(
            "Monthly Statement required tab is missing: " + ", ".join(missing)
        )
    return tuple(missing)


def _schema_creation_requests(
    snapshot: SpreadsheetSchemaSnapshot, missing: Sequence[str]
) -> tuple[Mapping[str, Any], ...]:
    next_id = max((tab.sheet_id for tab in snapshot.tabs.values()), default=-1) + 1
    requests: list[Mapping[str, Any]] = []
    for offset, tab in enumerate(missing):
        sheet_id = next_id + offset
        requests.extend(
            (
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": tab,
                            "gridProperties": {
                                "rowCount": 1000,
                                "columnCount": max(26, len(_MONTHLY_SCHEMAS[tab])),
                            },
                        }
                    }
                },
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(_MONTHLY_SCHEMAS[tab]),
                        },
                        "rows": [
                            {
                                "values": [
                                    {"userEnteredValue": {"stringValue": header}}
                                    for header in _MONTHLY_SCHEMAS[tab]
                                ]
                            }
                        ],
                        "fields": "userEnteredValue",
                    }
                },
            )
        )
    return tuple(requests)


def _committed_references(
    rows: Sequence[Sequence[str]],
) -> tuple[MonthlyStatementReference, ...]:
    if not rows:
        return ()
    headers = tuple(_cell(value) for value in rows[0])
    if headers != MONTHLY_STATEMENT_DATA_HEADERS:
        raise StatementCommitBlocked(
            "Monthly_Statement_Data header is not the exact approved schema."
        )
    positions = {header: index for index, header in enumerate(headers)}
    references: dict[str, MonthlyStatementReference] = {}
    from datetime import date

    for row in rows[1:]:
        values = tuple(row) + ("",) * (len(headers) - len(row))
        if values[positions["commit_status"]] != "COMMITTED":
            continue
        file_hash = values[positions["statement_file_hash"]]
        if not file_hash:
            continue
        reference = MonthlyStatementReference(
            file_hash=file_hash,
            statement_period_from=date.fromisoformat(
                values[positions["statement_period_from"]]
            ),
            statement_period_to=date.fromisoformat(
                values[positions["statement_period_to"]]
            ),
        )
        prior = references.get(file_hash)
        if prior is not None and prior != reference:
            raise StatementCommitBlocked(
                "Monthly_Statement_Data contains contradictory committed batch references."
            )
        references[file_hash] = reference
    return tuple(references.values())


def _validate_duplicate_scope(
    plan: MonthlyStatementCommitPlan, rows: Sequence[Sequence[str]]
) -> None:
    references = _committed_references(rows)
    if any(reference.file_hash == plan.statement_file_hash for reference in references):
        raise StatementCommitBlocked("Monthly Statement already imported.")
    if any(
        reference.statement_period_from.isoformat() == plan.statement_period_from
        and reference.statement_period_to.isoformat() == plan.statement_period_to
        for reference in references
    ):
        raise StatementCommitBlocked(
            "A different Monthly Statement is already committed for this calendar month."
        )


def _value_range(
    tab: str, start_row_index: int, rows: Sequence[Sequence[str]]
) -> Mapping[str, Any]:
    width = len(_MONTHLY_SCHEMAS[tab])
    end_column = _column_name(width)
    start_row = start_row_index + 1
    end_row = start_row_index + len(rows)
    return {
        "range": f"'{tab}'!A{start_row}:{end_column}{end_row}",
        "majorDimension": "ROWS",
        "values": [list(row) for row in rows],
    }


def _append_row_index(rows: Sequence[Sequence[Any]]) -> int:
    last = 0
    for index, row in enumerate(rows):
        if any(_cell(value) for value in row):
            last = index + 1
    return last


def _normalized_rows(
    tab: str, rows: Sequence[Sequence[Any]]
) -> tuple[tuple[str, ...], ...]:
    """Pad Sheets API rows whose trailing blank cells are omitted on readback."""

    width = len(_MONTHLY_SCHEMAS[tab])
    normalized: list[tuple[str, ...]] = []
    for row in rows:
        values = tuple(_cell(value) for value in row)
        if len(values) > width and any(values[width:]):
            raise StatementCommitBlocked(
                f"{tab} contains populated cells beyond the approved schema."
            )
        normalized.append((values[:width] + ("",) * width)[:width])
    return tuple(normalized)


def _column_name(one_based_index: int) -> str:
    value = one_based_index
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _write_result(plan: MonthlyStatementCommitPlan) -> MonthlyStatementWriteResult:
    return MonthlyStatementWriteResult(
        statement_batch_id=plan.statement_batch_id,
        data_rows=len(plan.rows),
        financial_component_rows=len(plan.financial_component_rows),
        summary_rows=len(plan.summary_rows),
    )
