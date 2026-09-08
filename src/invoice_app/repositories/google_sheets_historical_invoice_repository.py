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
    ImportResult,
    ImportStatus,
    source_fact_fingerprint,
)


GOOGLE_SHEETS_WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
INVOICE_ORDERS_TAB = "Invoice_Orders"
INVOICE_ITEMS_TAB = "Invoice_Items"
INVOICE_ORDERS_HEADERS = (
    "platform", "order_id", "order_created_date", "income_type", "order_income",
    "refund_amount", "invoice_payment_signal", "source_filename", "source_hash",
    "source_fingerprint", "first_imported_at",
)
INVOICE_ITEMS_HEADERS = (
    "platform", "order_id", "item_index", "seller_sku", "product_name", "variation",
    "quantity", "source_unit_price", "source_line_subtotal", "actual_selling_value",
    "pricing_status", "source_hash", "promotion_group_id", "promotion_label",
    "source_group_total", "allocation_method", "allocation_evidence",
)
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class HistoricalInvoiceStorageError(RuntimeError):
    """The configured UAT2 repository cannot safely read or write source facts."""


class GoogleSheetsHistoricalInvoiceGateway(Protocol):
    def read_tabs(self, spreadsheet_id: str, tabs: Sequence[str]) -> Mapping[str, Sequence[Sequence[Any]]]: ...

    def append_bundle(
        self, spreadsheet_id: str, order_values: Sequence[str], item_values: Sequence[Sequence[str]]
    ) -> None: ...


GoogleServiceAccountInfo = Mapping[str, Any]
GoogleCredentialsSource = Path | GoogleServiceAccountInfo


class GoogleApiHistoricalInvoiceGateway:
    """Small Google API boundary; callers never receive worksheet objects."""

    def __init__(self, credentials_source: GoogleCredentialsSource) -> None:
        self._credentials_source = credentials_source
        self._service: Any | None = None

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
        try:
            sheet_ids = self._sheet_ids(spreadsheet_id)
            requests = [
                _append_cells_request(sheet_ids[INVOICE_ORDERS_TAB], (order_values,)),
                _append_cells_request(sheet_ids[INVOICE_ITEMS_TAB], item_values),
            ]
            self._service_client().spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": requests}
            ).execute()
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets bundle write failed; no overwrite was attempted.") from error

    def _sheet_ids(self, spreadsheet_id: str) -> Mapping[str, int]:
        response = self._service_client().spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))"
        ).execute()
        sheets = response.get("sheets")
        ids = {
            properties.get("title"): properties.get("sheetId")
            for sheet in sheets or ()
            if isinstance(sheet, Mapping)
            and isinstance((properties := sheet.get("properties")), Mapping)
        }
        missing = [tab for tab in (INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB) if not isinstance(ids.get(tab), int)]
        if missing:
            raise HistoricalInvoiceStorageError("UAT2 spreadsheet required tab is missing: " + ", ".join(missing))
        return ids  # type: ignore[return-value]

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
    ) -> None:
        if not spreadsheet_id.strip():
            raise HistoricalInvoiceStorageError("UAT2 Google Sheets spreadsheet ID is missing.")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must not be negative.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway
        self._cache_ttl_seconds = cache_ttl_seconds
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
    return tuple(_serialize(value) for value in (
        order.platform, order.order_id, order.order_created_date, order.income_type,
        order.order_income, order.refund_amount, order.invoice_payment_signal,
        order.source_filename, order.source_hash, order.source_fingerprint, order.first_imported_at,
    ))


def _serialize_item(item: CanonicalInvoiceItem) -> tuple[str, ...]:
    return tuple(_serialize(value) for value in (
        item.platform, item.order_id, item.item_index, item.seller_sku, item.product_name,
        item.variation, item.quantity, item.source_unit_price, item.source_line_subtotal,
        item.actual_selling_value, item.pricing_status, item.source_hash, item.promotion_group_id,
        item.promotion_label, item.source_group_total, item.allocation_method,
        json.dumps(list(item.allocation_evidence), ensure_ascii=True, separators=(",", ":")),
    ))


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
    return CanonicalInvoiceOrder(
        platform=_required_text(values["platform"], "platform", INVOICE_ORDERS_TAB, row_number),
        order_id=_required_text(values["order_id"], "order_id", INVOICE_ORDERS_TAB, row_number),
        order_created_date=_date(values["order_created_date"], INVOICE_ORDERS_TAB, row_number),
        income_type=_optional(values["income_type"]), order_income=_decimal(values["order_income"], INVOICE_ORDERS_TAB, row_number),
        refund_amount=_decimal(values["refund_amount"], INVOICE_ORDERS_TAB, row_number),
        invoice_payment_signal=_optional(values["invoice_payment_signal"]), source_filename=_optional(values["source_filename"]),
        source_hash=_optional(values["source_hash"]), source_fingerprint=_optional(values["source_fingerprint"]),
        first_imported_at=_datetime(values["first_imported_at"], INVOICE_ORDERS_TAB, row_number),
    )


def _deserialize_item(row: Sequence[Any], row_number: int) -> CanonicalInvoiceItem:
    values = dict(zip(INVOICE_ITEMS_HEADERS, row))
    evidence = _optional(values["allocation_evidence"])
    try:
        decoded_evidence = json.loads(evidence) if evidence else []
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise HistoricalInvoiceStorageError(f"Invoice_Items row {row_number} has malformed allocation_evidence.") from error
    if not isinstance(decoded_evidence, list):
        raise HistoricalInvoiceStorageError(f"Invoice_Items row {row_number} allocation_evidence must be a JSON list.")
    allocation_evidence = tuple(str(value) for value in decoded_evidence)
    return CanonicalInvoiceItem(
        platform=_required_text(values["platform"], "platform", INVOICE_ITEMS_TAB, row_number),
        order_id=_required_text(values["order_id"], "order_id", INVOICE_ITEMS_TAB, row_number),
        item_index=_integer(values["item_index"], "item_index", INVOICE_ITEMS_TAB, row_number, required=True),
        seller_sku=_optional(values["seller_sku"]), product_name=_optional(values["product_name"]), variation=_optional(values["variation"]),
        quantity=_integer(values["quantity"], "quantity", INVOICE_ITEMS_TAB, row_number, required=False),
        source_unit_price=_decimal(values["source_unit_price"], INVOICE_ITEMS_TAB, row_number),
        source_line_subtotal=_decimal(values["source_line_subtotal"], INVOICE_ITEMS_TAB, row_number),
        actual_selling_value=_decimal(values["actual_selling_value"], INVOICE_ITEMS_TAB, row_number),
        pricing_status=_optional(values["pricing_status"]), source_hash=_optional(values["source_hash"]),
        promotion_group_id=_optional(values["promotion_group_id"]), promotion_label=_optional(values["promotion_label"]),
        source_group_total=_decimal(values["source_group_total"], INVOICE_ITEMS_TAB, row_number),
        allocation_method=_optional(values["allocation_method"]), allocation_evidence=allocation_evidence,
    )


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
