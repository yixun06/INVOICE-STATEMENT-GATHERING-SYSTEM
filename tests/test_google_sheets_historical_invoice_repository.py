from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    GOOGLE_SHEETS_WRITE_SCOPE,
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    GoogleApiHistoricalInvoiceGateway,
    GoogleSheetsHistoricalInvoiceRepository,
    HistoricalInvoiceStorageError,
    _serialize_item,
    _serialize_order,
)
from src.invoice_app.repositories.historical_invoice_repository import HistoricalInvoiceBulkImportError, ImportStatus
from src.invoice_app.repositories.historical_invoice_repository import source_fact_fingerprint
from src.invoice_app.services.product_master_source import GOOGLE_SHEETS_READONLY_SCOPE
from src.invoice_app.services.uat2_data_settings import (
    DEFAULT_UAT2_DATA_SPREADSHEET_ID,
    UAT2DataSettings,
)


class FakeGateway:
    def __init__(self) -> None:
        self.tabs = {
            INVOICE_ORDERS_TAB: [list(INVOICE_ORDERS_HEADERS)],
            INVOICE_ITEMS_TAB: [list(INVOICE_ITEMS_HEADERS)],
        }
        self.read_calls = 0
        self.append_calls = 0

    def read_tabs(self, _spreadsheet_id, tabs):
        self.read_calls += 1
        return {tab: [list(row) for row in self.tabs[tab]] for tab in tabs if tab in self.tabs}

    def append_bundle(self, _spreadsheet_id, order_values, item_values):
        self.append_calls += 1
        self.tabs[INVOICE_ORDERS_TAB].append(list(order_values))
        self.tabs[INVOICE_ITEMS_TAB].extend(list(row) for row in item_values)

    def append_bundles(self, _spreadsheet_id, order_values, item_values):
        self.append_calls += 1
        self.tabs[INVOICE_ORDERS_TAB].extend(list(row) for row in order_values)
        self.tabs[INVOICE_ITEMS_TAB].extend(list(row) for row in item_values)


def _bundle(*, order_id="000123456789", imported_at=None, items=1):
    order = CanonicalInvoiceOrder(
        platform="Shopee", order_id=order_id, order_created_date=date(2026, 8, 7),
        income_type="Final", order_income=Decimal("12.50"), refund_amount=Decimal("0.00"),
        invoice_payment_signal="Released", source_filename="source.pdf", source_hash="source-hash",
        first_imported_at=imported_at or datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc),
    )
    return InvoiceBundle(order=order, items=tuple(
        CanonicalInvoiceItem(
            platform="Shopee", order_id=order_id, item_index=index, seller_sku=f"000SKU-{index}",
            product_name="Tea", variation=None, quantity=index + 1, source_unit_price=Decimal("12.50"),
            source_line_subtotal=Decimal("12.50"), actual_selling_value=Decimal("0.00") if index == 0 else None,
            pricing_status="invoice_source", source_hash="source-hash", allocation_evidence=("evidence", str(index)),
        ) for index in range(items)
    ))


def _repository(gateway, *, ttl=45):
    return GoogleSheetsHistoricalInvoiceRepository(
        spreadsheet_id="synthetic-sheet", gateway=gateway, cache_ttl_seconds=ttl
    )


def test_exact_headers_and_full_bundle_round_trip_preserve_text_decimal_and_timezone():
    gateway = FakeGateway()
    repository = _repository(gateway)
    bundle = _bundle(items=2)

    assert repository.import_invoice(bundle).status is ImportStatus.NEW
    restored = repository.get_order("Shopee", "000123456789")
    restored_items = repository.get_items_by_order_ids("Shopee", ["000123456789"])["000123456789"]

    assert restored.order_id == "000123456789"
    assert restored.order_income == Decimal("12.50")
    assert restored.refund_amount == Decimal("0.00")
    assert restored.first_imported_at == bundle.order.first_imported_at
    assert restored_items[0].seller_sku == "000SKU-0"
    assert restored_items[0].actual_selling_value == Decimal("0.00")
    assert restored_items[1].actual_selling_value is None
    assert restored_items[0].allocation_evidence == ("evidence", "0")
    assert len(gateway.tabs[INVOICE_ORDERS_TAB]) == 2
    assert len(gateway.tabs[INVOICE_ITEMS_TAB]) == 3


@pytest.mark.parametrize("tab", [INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB])
def test_missing_canonical_tab_fails_closed(tab):
    gateway = FakeGateway()
    del gateway.tabs[tab]
    with pytest.raises(HistoricalInvoiceStorageError, match="required tab"):
        _repository(gateway).list_orders()


@pytest.mark.parametrize("tab", [INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB])
def test_wrong_or_duplicate_schema_header_fails_closed(tab):
    gateway = FakeGateway()
    gateway.tabs[tab][0][0] = "wrong"
    with pytest.raises(HistoricalInvoiceStorageError, match="header does not exactly"):
        _repository(gateway).list_orders()


def test_repeated_identical_import_does_not_append_again_and_conflict_keeps_original():
    gateway = FakeGateway()
    repository = _repository(gateway)
    original = _bundle()
    changed = replace(original, order=replace(original.order, refund_amount=Decimal("-1.00")))

    assert repository.import_invoice(original).status is ImportStatus.NEW
    assert repository.import_invoice(original).status is ImportStatus.ALREADY_IMPORTED
    assert repository.import_invoice(changed).status is ImportStatus.SOURCE_CONFLICT
    assert gateway.append_calls == 1
    assert repository.get_order("Shopee", original.order.order_id).refund_amount == Decimal("0.00")


def test_many_requested_ids_use_one_snapshot_not_per_id():
    gateway = FakeGateway()
    repository = _repository(gateway)
    for number in range(10):
        repository.import_invoice(_bundle(order_id=f"0000000000{number}"))
    repository.refresh()
    gateway.read_calls = 0

    orders = repository.get_orders_by_ids("Shopee", [f"0000000000{number}" for number in range(10)] + ["missing"])
    items = repository.get_items_by_order_ids("Shopee", list(orders))

    assert len(orders) == len(items) == 10
    assert gateway.read_calls == 1


def test_bulk_import_uses_50_invoice_chunks_with_one_order_and_item_append_per_chunk():
    gateway = FakeGateway()
    repository = _repository(gateway)
    bundles = tuple(_bundle(order_id=f"BULK-{index:03}", items=2) for index in range(120))

    result = repository.import_invoices(bundles)

    assert result.chunk_sizes == (50, 50, 20)
    assert gateway.read_calls == 1
    assert gateway.append_calls == 3
    assert len(gateway.tabs[INVOICE_ORDERS_TAB]) == 121
    assert len(gateway.tabs[INVOICE_ITEMS_TAB]) == 241


def test_bulk_preflight_rejects_invalid_bundle_before_the_first_write():
    gateway = FakeGateway()
    repository = _repository(gateway)
    malformed = _bundle(order_id="BAD", imported_at=datetime(2026, 8, 8, 12, 30))

    with pytest.raises(HistoricalInvoiceStorageError, match="timezone"):
        repository.import_invoices((_bundle(order_id="GOOD"), malformed))

    assert gateway.append_calls == 0


def test_bulk_chunk_failure_stops_later_chunks_and_invalidates_cached_snapshot():
    class FailingGateway(FakeGateway):
        def append_bundles(self, *args):
            if self.append_calls == 1:
                self.append_calls += 1
                raise HistoricalInvoiceStorageError("synthetic chunk failure")
            return super().append_bundles(*args)

    gateway = FailingGateway()
    repository = _repository(gateway)
    bundles = tuple(_bundle(order_id=f"FAIL-{index:03}") for index in range(120))

    with pytest.raises(HistoricalInvoiceBulkImportError) as caught:
        repository.import_invoices(bundles)

    assert len(caught.value.confirmed_results) == 50
    assert len(caught.value.pending_identities) == 70
    assert gateway.append_calls == 2
    reads_before = gateway.read_calls
    repository.list_orders()
    assert gateway.read_calls == reads_before + 1


def test_successful_write_invalidates_cache_and_refresh_observes_external_rows():
    gateway = FakeGateway()
    repository = _repository(gateway)
    assert repository.list_orders() == ()
    assert gateway.read_calls == 1
    repository.import_invoice(_bundle())
    assert repository.get_order("Shopee", "000123456789") is not None
    assert gateway.read_calls == 2

    external = _bundle(order_id="EXTERNAL-000")
    stored = external.with_source_fingerprint(source_fact_fingerprint(external))
    gateway.tabs[INVOICE_ORDERS_TAB].append(list(_serialize_order(stored.order)))
    gateway.tabs[INVOICE_ITEMS_TAB].extend(list(_serialize_item(item)) for item in stored.items)
    assert repository.get_order("Shopee", "EXTERNAL-000") is None
    repository.refresh()
    assert repository.get_order("Shopee", "EXTERNAL-000") is not None


def test_malformed_persisted_money_or_item_identity_fails_visibly():
    gateway = FakeGateway()
    repository = _repository(gateway)
    repository.import_invoice(_bundle())
    gateway.tabs[INVOICE_ORDERS_TAB][1][4] = "not-money"
    repository.refresh()
    with pytest.raises(HistoricalInvoiceStorageError, match="malformed Decimal"):
        repository.list_orders()

    gateway = FakeGateway()
    repository = _repository(gateway)
    repository.import_invoice(_bundle())
    gateway.tabs[INVOICE_ITEMS_TAB][1][1] = "OTHER"
    repository.refresh()
    with pytest.raises(HistoricalInvoiceStorageError, match="no matching"):
        repository.list_orders()


def test_product_master_readonly_scope_remains_separate_from_uat2_write_scope():
    assert GOOGLE_SHEETS_READONLY_SCOPE == "https://www.googleapis.com/auth/spreadsheets.readonly"
    assert GOOGLE_SHEETS_WRITE_SCOPE == "https://www.googleapis.com/auth/spreadsheets"


def test_google_api_gateway_uses_the_uat2_write_scope(monkeypatch):
    import sys
    from types import ModuleType

    calls = []

    class Credentials:
        @classmethod
        def from_service_account_info(cls, info, *, scopes):
            calls.append((dict(info), scopes))
            return object()

    service_account_module = ModuleType("google.oauth2.service_account")
    service_account_module.Credentials = Credentials
    discovery_module = ModuleType("googleapiclient.discovery")
    discovery_module.build = lambda *_args, **_kwargs: object()
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", service_account_module)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery_module)

    GoogleApiHistoricalInvoiceGateway({"private_key": "placeholder"})._service_client()
    assert calls == [({"private_key": "placeholder"}, [GOOGLE_SHEETS_WRITE_SCOPE])]


def test_google_gateway_bulk_chunk_uses_one_batch_update_with_two_append_requests():
    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class SheetsApi:
        def __init__(self):
            self.batch_bodies = []

        def get(self, **_kwargs):
            return Request({"sheets": [
                {"properties": {"sheetId": 11, "title": INVOICE_ORDERS_TAB}},
                {"properties": {"sheetId": 12, "title": INVOICE_ITEMS_TAB}},
            ]})

        def batchUpdate(self, **kwargs):
            self.batch_bodies.append(kwargs["body"])
            return Request({})

    class Service:
        def __init__(self, sheets):
            self.sheets_api = sheets

        def spreadsheets(self):
            return self.sheets_api

    sheets = SheetsApi()
    gateway = GoogleApiHistoricalInvoiceGateway({"private_key": "placeholder"})
    gateway._service = Service(sheets)

    gateway.append_bundles("synthetic-sheet", (("order-a",), ("order-b",)), (("item-a",), ("item-b",)))

    assert len(sheets.batch_bodies) == 1
    requests = sheets.batch_bodies[0]["requests"]
    assert len(requests) == 2
    assert [len(request["appendCells"]["rows"]) for request in requests] == [2, 2]
    assert all("updateCells" not in request and "delete" not in request for request in requests)


def test_adapter_has_no_streamlit_ui_dependency():
    from pathlib import Path

    adapter_path = Path(__file__).parents[1] / "src" / "invoice_app" / "repositories" / "google_sheets_historical_invoice_repository.py"
    assert "import streamlit" not in adapter_path.read_text(encoding="utf-8").casefold()


def test_dedicated_uat2_settings_do_not_reuse_product_master_keys(tmp_path):
    settings = UAT2DataSettings(
        google_spreadsheet_id=DEFAULT_UAT2_DATA_SPREADSHEET_ID,
        google_credentials_path=tmp_path / "missing.json",
    )
    assert settings.google_spreadsheet_id == "1sZHYrmL9KuxhIdlZUN-EIy5tedmlbY62fOPxP22hsF8"
    with pytest.raises(HistoricalInvoiceStorageError, match="credentials file"):
        settings.create_repository()
