from decimal import Decimal
import json
import logging
import sys
from types import ModuleType

import pytest
from openpyxl import Workbook

from src.invoice_app.services.product_master_source import (
    GoogleSheetsProductMasterSource,
    LocalExcelProductMasterSource,
    ProductMasterSourceError,
    ProductMasterSourceSettings,
    clear_product_master_source_cache,
    load_configured_product_price_master,
)
from src.invoice_app.services import product_master_source
from src.invoice_app.services.product_master_source import GOOGLE_SHEETS_READONLY_SCOPE
from src.invoice_app.services.product_price_master import PriceLookupStatus


def _sheet_rows(*, include_price=True):
    headers = ["Product Name", "Variation Name", "Parent SKU", "SKU"]
    if include_price:
        headers.append("Price")
    return [
        ["Shopee Product Listing"],
        headers,
        ["Fresh Oil", "500ml", "000PARENT", "00123", "RM 12.50"],
        ["Bad price", "", "PARENT-2", "SKU-2", "not a price"],
    ]


def _google_source(tmp_path, rows):
    credentials_path = tmp_path / "service-account.json"
    credentials_path.write_text("unused by fake fetcher", encoding="utf-8")
    return GoogleSheetsProductMasterSource(
        credentials_path=credentials_path,
        spreadsheet_id="synthetic-sheet-id",
        worksheet_name="shopee",
        values_fetcher=lambda *_: rows,
    )


def test_google_sheet_rows_become_canonical_records_with_text_skus(tmp_path):
    loaded = _google_source(tmp_path, _sheet_rows()).load()

    assert loaded.source_label == "Google Sheets"
    assert loaded.metadata.sheet_name == "shopee"
    assert loaded.metadata.candidate_row_count == 2
    assert loaded.metadata.invalid_price_row_count == 1
    assert len(loaded.records) == 1
    record = loaded.records[0]
    assert record.seller_sku == "00123"
    assert record.parent_sku == "000PARENT"
    assert isinstance(record.seller_sku, str)
    assert isinstance(record.parent_sku, str)
    assert record.unit_selling_price == Decimal("12.50")


def test_google_and_excel_sources_produce_equivalent_canonical_records(tmp_path):
    rows = _sheet_rows()
    workbook_path = tmp_path / "product_listing.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    workbook.save(workbook_path)

    google_records = _google_source(tmp_path, rows).load().records
    excel_records = LocalExcelProductMasterSource(workbook_path).load().records

    assert google_records == excel_records


def test_google_source_missing_required_column_fails_safely(tmp_path):
    with pytest.raises(ProductMasterSourceError, match="required headers"):
        _google_source(tmp_path, _sheet_rows(include_price=False)).load()


def test_google_source_network_or_auth_failure_fails_safely(tmp_path):
    credentials_path = tmp_path / "service-account.json"
    credentials_path.write_text("unused by fake fetcher", encoding="utf-8")
    source = GoogleSheetsProductMasterSource(
        credentials_path=credentials_path,
        spreadsheet_id="synthetic-sheet-id",
        worksheet_name="shopee",
        values_fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError("network unavailable")),
    )

    with pytest.raises(ProductMasterSourceError, match="Google Sheets Product Master load failed"):
        source.load()


def test_google_sheet_file_path_credentials_still_work(monkeypatch, tmp_path):
    calls = _install_google_client_fakes(monkeypatch)
    credentials_path = tmp_path / "service-account.json"

    product_master_source._fetch_google_sheet_values(
        credentials_path,
        "synthetic-sheet-id",
        "shopee",
    )

    assert calls["file"] == [(str(credentials_path), [GOOGLE_SHEETS_READONLY_SCOPE])]
    assert calls["info"] == []


def test_google_sheet_service_account_info_credentials_work(monkeypatch):
    calls = _install_google_client_fakes(monkeypatch)
    service_account = {
        "type": "service_account",
        "client_email": "placeholder@example.invalid",
        "private_key": "placeholder-private-key",
    }

    product_master_source._fetch_google_sheet_values(
        service_account,
        "synthetic-sheet-id",
        "shopee",
    )

    assert calls["file"] == []
    assert calls["info"] == [(service_account, [GOOGLE_SHEETS_READONLY_SCOPE])]


def test_service_account_info_takes_precedence_over_file_path(tmp_path):
    credentials_path = tmp_path / "unused-service-account.json"
    service_account = {"type": "service_account", "private_key": "placeholder-private-key"}
    seen_credentials_sources = []
    settings = ProductMasterSourceSettings(
        source="google_sheets",
        local_excel_path=tmp_path / "unused.xlsx",
        google_credentials_path=credentials_path,
        google_service_account=service_account,
        google_spreadsheet_id="synthetic-sheet-id",
        google_worksheet_name="shopee",
    )
    source = settings.create_source()

    def fetcher(credentials_source, *_):
        seen_credentials_sources.append(credentials_source)
        return _sheet_rows()

    source = GoogleSheetsProductMasterSource(
        credentials_path=source.credentials_path,
        google_service_account=source.google_service_account,
        spreadsheet_id=source.spreadsheet_id,
        worksheet_name=source.worksheet_name,
        values_fetcher=fetcher,
    )

    source.load()

    assert seen_credentials_sources == [service_account]


def test_google_settings_missing_credentials_fails_without_secret_leak(tmp_path):
    settings = ProductMasterSourceSettings(
        source="google_sheets",
        local_excel_path=tmp_path / "unused.xlsx",
        google_spreadsheet_id="synthetic-sheet-id",
        google_worksheet_name="shopee",
    )

    with pytest.raises(ProductMasterSourceError) as raised:
        settings.create_source()

    assert "google_service_account or google_credentials_path" in str(raised.value)
    assert "private_key" not in str(raised.value)


def test_google_auth_error_does_not_expose_service_account_info(tmp_path):
    secret_value = "do-not-expose-this-private-key"
    source = GoogleSheetsProductMasterSource(
        credentials_path=None,
        google_service_account={"private_key": secret_value},
        spreadsheet_id="synthetic-sheet-id",
        worksheet_name="shopee",
        values_fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError(secret_value)),
    )

    with pytest.raises(ProductMasterSourceError) as raised:
        source.load()

    assert secret_value not in str(raised.value)


def test_google_load_diagnostic_logs_safe_api_fields_without_service_account_secrets(caplog):
    private_key = "diagnostic-private-key-must-not-appear"
    private_key_id = "diagnostic-private-key-id-must-not-appear"

    class FakeGoogleApiError(RuntimeError):
        def __init__(self):
            self.resp = type("Response", (), {"status": 403})()
            self.content = json.dumps(
                {
                    "error": {
                        "status": "PERMISSION_DENIED",
                        "message": "The caller does not have permission",
                        "private_key": private_key,
                        "private_key_id": private_key_id,
                    }
                }
            ).encode("utf-8")

    source = GoogleSheetsProductMasterSource(
        credentials_path=None,
        google_service_account={
            "private_key": private_key,
            "private_key_id": private_key_id,
        },
        spreadsheet_id="synthetic-sheet-id",
        worksheet_name="shopee",
        values_fetcher=lambda *_: (_ for _ in ()).throw(FakeGoogleApiError()),
    )

    caplog.set_level(logging.ERROR, logger=product_master_source.__name__)
    with pytest.raises(ProductMasterSourceError, match="Google Sheets Product Master load failed"):
        source.load()

    diagnostic = caplog.text
    assert "exception_type=FakeGoogleApiError" in diagnostic
    assert "http_status=403" in diagnostic
    assert "google_reason=PERMISSION_DENIED" in diagnostic
    assert "google_message=The caller does not have permission" in diagnostic
    assert "worksheet_name=shopee" in diagnostic
    assert "credential_source_type=service_account_info" in diagnostic
    assert private_key not in diagnostic
    assert private_key_id not in diagnostic
    assert "{'private_key'" not in diagnostic


def test_google_load_diagnostic_never_logs_generic_exception_text(caplog, tmp_path):
    secret_value = "generic-secret-must-not-appear"
    credentials_path = tmp_path / "service-account.json"
    credentials_path.write_text("unused by fake fetcher", encoding="utf-8")
    source = GoogleSheetsProductMasterSource(
        credentials_path=credentials_path,
        spreadsheet_id="synthetic-sheet-id",
        worksheet_name="shopee",
        values_fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError(secret_value)),
    )

    caplog.set_level(logging.ERROR, logger=product_master_source.__name__)
    with pytest.raises(ProductMasterSourceError, match="Google Sheets Product Master load failed"):
        source.load()

    assert "exception_type=RuntimeError" in caplog.text
    assert "credential_source_type=service_account_file" in caplog.text
    assert secret_value not in caplog.text


def test_streamlit_cloud_secrets_provide_service_account_info(monkeypatch, tmp_path):
    service_account = {
        "type": "service_account",
        "project_id": "placeholder-project",
        "private_key": "placeholder-private-key",
        "client_email": "placeholder@example.invalid",
    }
    streamlit_module = ModuleType("streamlit")
    streamlit_module.secrets = {
        "product_master": {
            "source": "google_sheets",
            "google_spreadsheet_id": "synthetic-sheet-id",
            "google_worksheet_name": "shopee",
            "google_service_account": service_account,
        }
    }
    monkeypatch.setattr(product_master_source, "SECRETS_PATH", tmp_path / "missing.toml")
    monkeypatch.setitem(sys.modules, "streamlit", streamlit_module)

    settings = product_master_source.configured_product_master_source_settings()

    assert settings.source == "google_sheets"
    assert settings.google_credentials_path is None
    assert settings.google_service_account == service_account


def _install_google_client_fakes(monkeypatch):
    calls = {"file": [], "info": [], "credentials": []}

    class FakeCredentials:
        @classmethod
        def from_service_account_file(cls, path, *, scopes):
            calls["file"].append((path, scopes))
            credentials = object()
            calls["credentials"].append(credentials)
            return credentials

        @classmethod
        def from_service_account_info(cls, info, *, scopes):
            calls["info"].append((info, scopes))
            credentials = object()
            calls["credentials"].append(credentials)
            return credentials

    class FakeRequest:
        def execute(self):
            return {"values": [["header"]]}

    class FakeValues:
        def get(self, **_kwargs):
            return FakeRequest()

    class FakeSpreadsheets:
        def values(self):
            return FakeValues()

    class FakeService:
        def spreadsheets(self):
            return FakeSpreadsheets()

    service_account_module = ModuleType("google.oauth2.service_account")
    service_account_module.Credentials = FakeCredentials
    discovery_module = ModuleType("googleapiclient.discovery")
    discovery_module.build = lambda *_args, **_kwargs: FakeService()
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", service_account_module)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery_module)
    return calls


def test_google_source_preserves_existing_price_master_lookup_contract(tmp_path):
    master = _google_source(tmp_path, _sheet_rows()).load().to_price_master()

    result = master.lookup(seller_sku="00123", product_name="Fresh Oil", variation_name="500ml")

    assert result.status is PriceLookupStatus.MATCHED
    assert result.unit_selling_price == Decimal("12.50")


def test_clearing_product_master_source_cache_forces_a_fresh_source_load(
    tmp_path, monkeypatch
):
    settings = ProductMasterSourceSettings(
        source="google_sheets",
        local_excel_path=tmp_path / "unused.xlsx",
        google_credentials_path=tmp_path / "service-account.json",
        google_spreadsheet_id="synthetic-sheet-id",
        google_worksheet_name="shopee",
    )
    source = _google_source(tmp_path, _sheet_rows())
    load_calls = 0

    def create_source(_settings):
        nonlocal load_calls
        load_calls += 1
        return source

    monkeypatch.setattr(
        product_master_source,
        "configured_product_master_source_settings",
        lambda: settings,
    )
    monkeypatch.setattr(ProductMasterSourceSettings, "create_source", create_source)
    clear_product_master_source_cache()

    load_configured_product_price_master()
    load_configured_product_price_master()
    assert load_calls == 1

    clear_product_master_source_cache()
    load_configured_product_price_master()
    assert load_calls == 2
    clear_product_master_source_cache()
