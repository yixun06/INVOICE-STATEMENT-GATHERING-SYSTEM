from decimal import Decimal

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
