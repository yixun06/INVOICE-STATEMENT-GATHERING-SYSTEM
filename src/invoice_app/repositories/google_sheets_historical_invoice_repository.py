"""Google Sheets adapter for the UAT2 historical-invoice repository contract.

This module deliberately contains no Streamlit dependency.  It reads both
canonical tabs as one snapshot and writes an invoice bundle in one Sheets API
batchUpdate request; Google Sheets does not provide relational transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from time import monotonic
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    BulkImportResult,
    HistoricalInvoiceBulkImportError,
    ImportResult,
    ImportStatus,
    _chunks,
    _unique_bundles,
    _validate_chunk_size,
    source_fact_fingerprint,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
)
from src.invoice_app.services.uat2_statement_schema_migration import (
    SheetSchema,
    SpreadsheetSchemaSnapshot,
)


GOOGLE_SHEETS_WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class HistoricalInvoiceStorageError(RuntimeError):
    """The configured UAT2 repository cannot safely read or write source facts."""


class GoogleSheetsHistoricalInvoiceGateway(Protocol):
    def read_tabs(self, spreadsheet_id: str, tabs: Sequence[str]) -> Mapping[str, Sequence[Sequence[Any]]]: ...

    def append_bundle(
        self, spreadsheet_id: str, order_values: Sequence[str], item_values: Sequence[Sequence[str]]
    ) -> None: ...

    def append_bundles(
        self,
        spreadsheet_id: str,
        order_values: Sequence[Sequence[str]],
        item_values: Sequence[Sequence[str]],
    ) -> None: ...

    def read_schema(self, spreadsheet_id: str) -> SpreadsheetSchemaSnapshot: ...

    def apply_schema_migration(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None: ...

    def batch_update(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None: ...

    def read_sheet_ids(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, int]: ...


GoogleServiceAccountInfo = Mapping[str, Any]
GoogleCredentialsSource = Path | GoogleServiceAccountInfo


class GoogleApiHistoricalInvoiceGateway:
    """Small Google API boundary; callers never receive worksheet objects."""

    def __init__(self, credentials_source: GoogleCredentialsSource) -> None:
        self._credentials_source = credentials_source
        self._service: Any | None = None
        self._sheet_ids_by_spreadsheet: dict[str, Mapping[str, int]] = {}

    def read_tabs(self, spreadsheet_id: str, tabs: Sequence[str]) -> Mapping[str, Sequence[Sequence[Any]]]:
        try:
            response = self._service_client().spreadsheets().values().batchGet(
                spreadsheetId=spreadsheet_id, ranges=list(tabs), valueRenderOption="FORMATTED_VALUE"
            ).execute()
            value_ranges = response.get("valueRanges")
            if not isinstance(value_ranges, list) or len(value_ranges) != len(tabs):
                raise HistoricalInvoiceStorageError("UAT2 Google Sheets response is missing requested canonical tabs.")
            result: dict[str, Sequence[Sequence[Any]]] = {}
            for tab, value_range in zip(tabs, value_ranges):
                values = value_range.get("values") if isinstance(value_range, Mapping) else None
                result[tab] = values if isinstance(values, list) else ()
            return result
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets read failed; check service account access and spreadsheet configuration.") from error

    def append_bundle(self, spreadsheet_id: str, order_values: Sequence[str], item_values: Sequence[Sequence[str]]) -> None:
        self.append_bundles(spreadsheet_id, (order_values,), item_values)

    def append_bundles(
        self,
        spreadsheet_id: str,
        order_values: Sequence[Sequence[str]],
        item_values: Sequence[Sequence[str]],
    ) -> None:
        try:
            sheet_ids = self._sheet_ids(spreadsheet_id)
            requests = [
                _append_cells_request(sheet_ids[INVOICE_ORDERS_TAB], order_values),
                _append_cells_request(sheet_ids[INVOICE_ITEMS_TAB], item_values),
            ]
            self._service_client().spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": requests}
            ).execute()
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets bundle write failed; no overwrite was attempted.") from error

    def read_schema(self, spreadsheet_id: str) -> SpreadsheetSchemaSnapshot:
        """Freshly read sheet IDs and only row-one headers needed by migration."""
        try:
            response = self._service_client().spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(sheetId,title))",
            ).execute()
            properties = tuple(
                sheet.get("properties")
                for sheet in response.get("sheets") or ()
                if isinstance(sheet, Mapping) and isinstance(sheet.get("properties"), Mapping)
            )
            titles = tuple(
                value.get("title")
                for value in properties
                if isinstance(value.get("title"), str) and isinstance(value.get("sheetId"), int)
            )
            header_rows = self._read_header_rows(spreadsheet_id, titles)
            return SpreadsheetSchemaSnapshot(
                tabs={
                    value["title"]: SheetSchema(
                        sheet_id=value["sheetId"],
                        headers=tuple(_text(cell) for cell in header_rows.get(value["title"], ())),
                    )
                    for value in properties
                    if isinstance(value.get("title"), str) and isinstance(value.get("sheetId"), int)
                }
            )
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets schema read failed; no migration was attempted.") from error

    def apply_schema_migration(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None:
        """Submit the caller's prebuilt one-time schema migration batch exactly once."""
        try:
            self._service_client().spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": list(requests)}
            ).execute()
            self._sheet_ids_by_spreadsheet.pop(spreadsheet_id, None)
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "UAT2 Google Sheets schema migration request failed; verify the final schema before any retry."
            ) from error

    def batch_update(
        self, spreadsheet_id: str, requests: Sequence[Mapping[str, Any]]
    ) -> None:
        """Submit one preflighted authoritative business-write batch."""
        try:
            self._service_client().spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": list(requests)}
            ).execute()
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "UAT2 Google Sheets business write failed; the outcome must be verified before any retry."
            ) from error

    def read_sheet_ids(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, int]:
        """Freshly resolve every requested tab to its immutable numeric sheet ID."""
        try:
            response = self._service_client().spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(sheetId,title))",
            ).execute()
            ids = {
                properties.get("title"): properties.get("sheetId")
                for sheet in response.get("sheets") or ()
                if isinstance(sheet, Mapping)
                and isinstance((properties := sheet.get("properties")), Mapping)
            }
            missing = [tab for tab in tabs if not isinstance(ids.get(tab), int)]
            if missing:
                raise HistoricalInvoiceStorageError(
                    "UAT2 spreadsheet required tab is missing: " + ", ".join(missing)
                )
            return {tab: ids[tab] for tab in tabs}  # type: ignore[misc]
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "UAT2 Google Sheets sheet-ID read failed; no write was attempted."
            ) from error

    def _sheet_ids(self, spreadsheet_id: str) -> Mapping[str, int]:
        cached = self._sheet_ids_by_spreadsheet.get(spreadsheet_id)
        if cached is not None:
            return cached
        resolved = self.read_sheet_ids(
            spreadsheet_id, (INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB)
        )
        self._sheet_ids_by_spreadsheet[spreadsheet_id] = resolved
        return resolved

    def _read_header_rows(
        self, spreadsheet_id: str, titles: Sequence[str]
    ) -> Mapping[str, Sequence[Any]]:
        if not titles:
            return {}
        response = self._service_client().spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=[f"{title}!1:1" for title in titles],
            valueRenderOption="FORMATTED_VALUE",
        ).execute()
        value_ranges = response.get("valueRanges")
        if not isinstance(value_ranges, list) or len(value_ranges) != len(titles):
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets schema response is missing header rows.")
        headers: dict[str, Sequence[Any]] = {}
        for title, value_range in zip(titles, value_ranges):
            values = value_range.get("values") if isinstance(value_range, Mapping) else None
            first_row = values[0] if isinstance(values, list) and values else ()
            headers[title] = first_row if isinstance(first_row, list) else ()
        return headers

    def _service_client(self) -> Any:
        if self._service is not None:
            return self._service
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            if isinstance(self._credentials_source, Path):
                credentials = Credentials.from_service_account_file(
                    str(self._credentials_source), scopes=[GOOGLE_SHEETS_WRITE_SCOPE]
                )
            else:
                credentials = Credentials.from_service_account_info(
                    dict(self._credentials_source), scopes=[GOOGLE_SHEETS_WRITE_SCOPE]
                )
            self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
            return self._service
        except ImportError as error:
            raise HistoricalInvoiceStorageError("Google Sheets dependencies are unavailable; install google-api-python-client.") from error
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets credentials could not be initialized.") from error


@dataclass(frozen=True)
class _Snapshot:
    bundles: Mapping[tuple[str, str], InvoiceBundle]
    loaded_at: float


class GoogleSheetsHistoricalInvoiceRepository:
    """UAT2 canonical-tab repository backed by a dedicated Google spreadsheet."""

    def __init__(
        self,
        *,
        spreadsheet_id: str,
        gateway: GoogleSheetsHistoricalInvoiceGateway,
        cache_ttl_seconds: float = 45.0,
        commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    ) -> None:
        if not spreadsheet_id.strip():
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets spreadsheet ID is missing.")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must not be negative.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway
        self._cache_ttl_seconds = cache_ttl_seconds
        self._commit_lock = commit_lock
        self._snapshot_cache: _Snapshot | None = None

    def refresh(self) -> None:
        """Invalidate the short read cache; the next operation loads current rows."""
        self._snapshot_cache = None

    def get_order(self, platform: str, order_id: str) -> CanonicalInvoiceOrder | None:
        bundle = self._snapshot().bundles.get(_identity(platform, order_id))
        return bundle.order if bundle else None

    def get_orders_by_ids(self, platform: str, order_ids: Iterable[str]) -> dict[str, CanonicalInvoiceOrder]:
        bundles = self._snapshot().bundles
        return {
            order_id: bundles[identity].order
            for order_id in _distinct_ids(order_ids)
            if (identity := _identity(platform, order_id)) in bundles
        }

    def get_items_by_order_ids(self, platform: str, order_ids: Iterable[str]) -> dict[str, tuple[CanonicalInvoiceItem, ...]]:
        bundles = self._snapshot().bundles
        return {
            order_id: bundles[identity].items
            for order_id in _distinct_ids(order_ids)
            if (identity := _identity(platform, order_id)) in bundles
        }

    def list_orders(self, *, platform: str | None = None, order_created_from: date | None = None, order_created_to: date | None = None) -> tuple[CanonicalInvoiceOrder, ...]:
        wanted_platform = _text(platform) if platform is not None else None
        orders = []
        for bundle in self._snapshot().bundles.values():
            order = bundle.order
            if wanted_platform is not None and order.platform != wanted_platform:
                continue
            if order_created_from is not None and (order.order_created_date is None or order.order_created_date < order_created_from):
                continue
            if order_created_to is not None and (order.order_created_date is None or order.order_created_date > order_created_to):
                continue
            orders.append(order)
        return tuple(sorted(orders, key=lambda value: (value.platform, value.order_id)))

    def import_invoice(self, bundle: InvoiceBundle) -> ImportResult:
        with self._commit_lock.acquire():
            return self._import_invoice_under_commit_lock(bundle)

    def _import_invoice_under_commit_lock(self, bundle: InvoiceBundle) -> ImportResult:
        self.refresh()
        fingerprint = source_fact_fingerprint(bundle)
        identity = _identity(bundle.order.platform, bundle.order.order_id)
        existing = self._snapshot().bundles.get(identity)
        if existing is not None:
            status = ImportStatus.ALREADY_IMPORTED if source_fact_fingerprint(existing) == fingerprint else ImportStatus.SOURCE_CONFLICT
            return ImportResult(status, identity[0], identity[1], fingerprint)
        stored = bundle.with_source_fingerprint(fingerprint)
        try:
            self._gateway.append_bundle(
                self._spreadsheet_id,
                _serialize_order(stored.order),
                tuple(_serialize_item(item) for item in stored.items),
            )
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 historical invoice bundle write failed.") from error
        self.refresh()
        return ImportResult(ImportStatus.NEW, identity[0], identity[1], fingerprint)

    def classify_invoices(self, bundles: Iterable[InvoiceBundle]) -> tuple[ImportResult, ...]:
        candidates = _unique_bundles(bundles)
        snapshot = self._snapshot().bundles
        results = []
        for bundle in candidates:
            fingerprint = source_fact_fingerprint(bundle)
            identity = _identity(bundle.order.platform, bundle.order.order_id)
            existing = snapshot.get(identity)
            status = (
                ImportStatus.NEW
                if existing is None
                else ImportStatus.ALREADY_IMPORTED
                if source_fact_fingerprint(existing) == fingerprint
                else ImportStatus.SOURCE_CONFLICT
            )
            results.append(ImportResult(status, identity[0], identity[1], fingerprint))
        return tuple(results)

    def import_invoices(
        self, bundles: Iterable[InvoiceBundle], *, chunk_size: int = 50
    ) -> BulkImportResult:
        with self._commit_lock.acquire():
            return self._import_invoices_under_commit_lock(bundles, chunk_size=chunk_size)

    def _import_invoices_under_commit_lock(
        self, bundles: Iterable[InvoiceBundle], *, chunk_size: int
    ) -> BulkImportResult:
        candidates = _unique_bundles(bundles)
        _validate_chunk_size(chunk_size)
        self.refresh()
        classification = self.classify_invoices(candidates)
        if any(result.status is not ImportStatus.NEW for result in classification):
            return BulkImportResult(results=classification, chunk_sizes=())

        # Preflight the entire all-NEW batch before the first network write.
        serialized = tuple(
            (
                bundle.with_source_fingerprint(result.source_fingerprint),
                result,
                _serialize_order(bundle.with_source_fingerprint(result.source_fingerprint).order),
                tuple(_serialize_item(item) for item in bundle.with_source_fingerprint(result.source_fingerprint).items),
            )
            for bundle, result in zip(candidates, classification)
        )
        confirmed: list[ImportResult] = []
        chunks = _chunks(serialized, chunk_size)
        for chunk_index, chunk in enumerate(chunks, start=1):
            order_rows = tuple(values[2] for values in chunk)
            item_rows = tuple(item for values in chunk for item in values[3])
            try:
                self._gateway.append_bundles(self._spreadsheet_id, order_rows, item_rows)
            except Exception as error:
                self.refresh()
                pending = tuple(
                    _identity(values[0].order.platform, values[0].order.order_id)
                    for remaining in chunks[chunk_index - 1 :]
                    for values in remaining
                )
                raise HistoricalInvoiceBulkImportError(
                    "UAT2 historical invoice bulk write stopped; refresh and reclassify before retrying.",
                    confirmed_results=confirmed,
                    pending_identities=pending,
                ) from error
            confirmed.extend(values[1] for values in chunk)
        self.refresh()
        return BulkImportResult(
            results=classification,
            chunk_sizes=tuple(len(chunk) for chunk in chunks),
        )

    def _snapshot(self) -> _Snapshot:
        now = monotonic()
        if self._snapshot_cache is not None and now - self._snapshot_cache.loaded_at <= self._cache_ttl_seconds:
            return self._snapshot_cache
        try:
            tabs = self._gateway.read_tabs(self._spreadsheet_id, (INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB))
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 historical invoice spreadsheet read failed.") from error
        snapshot = _deserialize_snapshot(tabs, now)
        self._snapshot_cache = snapshot
        return snapshot


def _deserialize_snapshot(tabs: Mapping[str, Sequence[Sequence[Any]]], loaded_at: float) -> _Snapshot:
    order_rows = _validate_tab(tabs, INVOICE_ORDERS_TAB, INVOICE_ORDERS_HEADERS)
    item_rows = _validate_tab(tabs, INVOICE_ITEMS_TAB, INVOICE_ITEMS_HEADERS)
    orders: dict[tuple[str, str], CanonicalInvoiceOrder] = {}
    for row_number, row in enumerate(order_rows, start=2):
        order = _deserialize_order(row, row_number)
        identity = _identity(order.platform, order.order_id)
        if identity in orders:
            raise HistoricalInvoiceStorageError(f"Invoice_Orders row {row_number} duplicates canonical identity {identity!r}.")
        orders[identity] = order
    items: dict[tuple[str, str], list[CanonicalInvoiceItem]] = {}
    for row_number, row in enumerate(item_rows, start=2):
        item = _deserialize_item(row, row_number)
        identity = _identity(item.platform, item.order_id)
        if identity not in orders:
            raise HistoricalInvoiceStorageError(f"Invoice_Items row {row_number} has no matching Invoice_Orders row.")
        items.setdefault(identity, []).append(item)
    bundles: dict[tuple[str, str], InvoiceBundle] = {}
    for identity, order in orders.items():
        try:
            bundle = InvoiceBundle(order=order, items=tuple(sorted(items.get(identity, ()), key=lambda item: item.item_index)))
        except ValueError as error:
            raise HistoricalInvoiceStorageError(f"Persisted bundle {identity!r} is malformed: {error}") from error
        if not order.source_fingerprint or not _FINGERPRINT.fullmatch(order.source_fingerprint):
            raise HistoricalInvoiceStorageError(f"Invoice_Orders identity {identity!r} has a malformed source_fingerprint.")
        if source_fact_fingerprint(bundle) != order.source_fingerprint:
            raise HistoricalInvoiceStorageError(f"Invoice_Orders identity {identity!r} source_fingerprint does not match stored facts.")
        bundles[identity] = bundle
    return _Snapshot(bundles=bundles, loaded_at=loaded_at)


def _validate_tab(tabs: Mapping[str, Sequence[Sequence[Any]]], tab: str, headers: Sequence[str]) -> tuple[tuple[Any, ...], ...]:
    if tab not in tabs:
        raise HistoricalInvoiceStorageError(f"UAT2 spreadsheet required tab '{tab}' is missing.")
    rows = tuple(tuple(row) for row in tabs[tab])
    if not rows:
        raise HistoricalInvoiceStorageError(f"UAT2 spreadsheet tab '{tab}' is missing its schema header.")
    actual = tuple(_text(value) for value in rows[0])
    if actual != tuple(headers):
        raise HistoricalInvoiceStorageError(f"UAT2 spreadsheet tab '{tab}' header does not exactly match the required schema.")
    normalized_rows = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) > len(headers) and any(_text(value) for value in row[len(headers):]):
            raise HistoricalInvoiceStorageError(f"UAT2 spreadsheet tab '{tab}' row {row_number} has unexpected data beyond the schema.")
        padded = tuple(row) + (None,) * (len(headers) - len(row))
        if any(_text(value) for value in padded):
            normalized_rows.append(padded[: len(headers)])
    return tuple(normalized_rows)


def _serialize_order(order: CanonicalInvoiceOrder) -> tuple[str, ...]:
    return tuple(_serialize(getattr(order, header)) for header in INVOICE_ORDERS_HEADERS)


def _serialize_item(item: CanonicalInvoiceItem) -> tuple[str, ...]:
    return tuple(_serialize(getattr(item, header)) for header in INVOICE_ITEMS_HEADERS)


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise HistoricalInvoiceStorageError("Canonical datetime must include timezone information.")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _deserialize_order(row: Sequence[Any], row_number: int) -> CanonicalInvoiceOrder:
    values = dict(zip(INVOICE_ORDERS_HEADERS, row))
    required = {"platform", "order_id"}
    dates = {"order_created_date", "delivered_date", "completed_date", "fund_transfer_date", "payout_completed_date"}
    money = {"merchandise_subtotal", "product_price", "shipping_subtotal", "shipping_fee_paid_by_buyer", "shipping_fee_charged_by_logistic_provider", "shipping_fee_rebate_from_shopee", "seller_paid_shipping_fee_sst", "reverse_shipping_fee", "reverse_shipping_fee_sst", "vouchers_rebates_total", "voucher_amount", "commission_fee", "service_fee", "transaction_fee", "ams_commission_fee", "ads_escrow_top_up_fee", "fees_charges_total", "order_income", "final_amount", "refund_amount", "buyer_merchandise_subtotal", "buyer_shipping_fee", "shopee_voucher", "seller_voucher", "total_buyer_payment", "difference"}
    parsed = {field: _required_text(values[field], field, INVOICE_ORDERS_TAB, row_number) if field in required else _date(values[field], INVOICE_ORDERS_TAB, row_number) if field in dates else _decimal(values[field], INVOICE_ORDERS_TAB, row_number) if field in money else _optional(values[field]) for field in INVOICE_ORDERS_HEADERS if field != "first_imported_at"}
    return CanonicalInvoiceOrder(**parsed, first_imported_at=_datetime(values["first_imported_at"], INVOICE_ORDERS_TAB, row_number))


def _deserialize_item(row: Sequence[Any], row_number: int) -> CanonicalInvoiceItem:
    values = dict(zip(INVOICE_ITEMS_HEADERS, row))
    money = {"unit_price", "actual_selling_unit_price", "line_subtotal", "promotion_advertised_amount", "promotion_discount_percent", "source_group_total", "statement_product_price", "statement_refund_amount", "statement_net_selling_amount"}
    parsed = {field: _required_text(values[field], field, INVOICE_ITEMS_TAB, row_number) if field in {"platform", "order_id"} else _integer(values[field], field, INVOICE_ITEMS_TAB, row_number, required=field == "item_index") if field in {"item_index", "quantity"} else _decimal(values[field], INVOICE_ITEMS_TAB, row_number) if field in money else _boolean(values[field], INVOICE_ITEMS_TAB, row_number) if field == "sku_missing_in_source" else _optional(values[field]) for field in INVOICE_ITEMS_HEADERS}
    return CanonicalInvoiceItem(**parsed)


def _boolean(value: Any, tab: str, row_number: int) -> bool | None:
    text = _optional(value)
    if text is None:
        return None
    normalized = text.casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HistoricalInvoiceStorageError(f"{tab} row {row_number} has malformed Boolean data.")


def _decimal(value: Any, tab: str, row_number: int) -> Decimal | None:
    text = _optional(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError) as error:
        raise HistoricalInvoiceStorageError(f"{tab} row {row_number} has malformed Decimal data.") from error


def _date(value: Any, tab: str, row_number: int) -> date | None:
    text = _optional(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise HistoricalInvoiceStorageError(f"{tab} row {row_number} has malformed ISO date data.") from error


def _datetime(value: Any, tab: str, row_number: int) -> datetime:
    text = _required_text(value, "first_imported_at", tab, row_number)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise HistoricalInvoiceStorageError(f"{tab} row {row_number} has malformed ISO datetime data.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoricalInvoiceStorageError(f"{tab} row {row_number} datetime must include timezone information.")
    return parsed


def _integer(value: Any, field: str, tab: str, row_number: int, *, required: bool) -> int | None:
    text = _optional(value)
    if text is None:
        if required:
            raise HistoricalInvoiceStorageError(f"{tab} row {row_number} requires {field}.")
        return None
    if not re.fullmatch(r"-?\d+", text):
        raise HistoricalInvoiceStorageError(f"{tab} row {row_number} has malformed {field}.")
    return int(text)


def _required_text(value: Any, field: str, tab: str, row_number: int) -> str:
    text = _optional(value)
    if text is None:
        raise HistoricalInvoiceStorageError(f"{tab} row {row_number} requires {field}.")
    return text


def _optional(value: Any) -> str | None:
    text = _text(value)
    return text if text else None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _identity(platform: str, order_id: str) -> tuple[str, str]:
    return (_text(platform), _text(order_id))


def _distinct_ids(order_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_text(value) for value in order_ids if _text(value)))


def _append_cells_request(sheet_id: int, rows: Sequence[Sequence[str]]) -> dict[str, object]:
    """Write strings only, preserving numeric-looking IDs and Decimal text exactly."""
    return {
        "appendCells": {
            "sheetId": sheet_id,
            "fields": "userEnteredValue",
            "rows": [
                {"values": [{"userEnteredValue": {"stringValue": value}} for value in row]}
                for row in rows
            ],
        }
    }
