from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
)
from src.invoice_app.services.market_context import (
    MarketConfigurationUnavailable,
    MarketContext,
    MarketKey,
    MarketStateIsolationError,
    SHOPEE_MY,
    SHOPEE_SG,
    active_import_market,
    bind_active_import_market,
    market_state_key,
    resolve_market_context,
)
from src.invoice_app.services.product_master_source import (
    ProductMasterSourceSettings,
)
from src.invoice_app.services.uat2_data_settings import (
    UAT2DataSettings,
    configured_uat2_data_settings,
)


def test_market_registry_preserves_my_identity_and_reserves_sg_identity():
    assert SHOPEE_MY.display_name == "Shopee MY"
    assert SHOPEE_MY.persisted_platform == "Shopee"
    assert SHOPEE_MY.currency_code == "MYR"
    assert SHOPEE_MY.currency_display == "RM"
    assert SHOPEE_SG.display_name == "Shopee SG"
    assert SHOPEE_SG.persisted_platform == "Shopee SG"
    assert SHOPEE_SG.currency_code == "SGD"
    assert SHOPEE_SG.currency_display == "SGD"
    assert SHOPEE_MY.cross_platform_eligible is True
    assert SHOPEE_SG.cross_platform_eligible is False


def test_market_registry_rejects_ad_hoc_context_that_weakens_sg_capabilities():
    unsafe = MarketContext(
        key=MarketKey.SHOPEE_SG,
        display_name="Shopee SG",
        persisted_platform="Shopee",
        currency_code="MYR",
        currency_display="RM",
        cross_platform_eligible=True,
        statement_commit_available=True,
        weekly_billing_available=True,
    )

    with pytest.raises(MarketConfigurationUnavailable, match="Unrecognized market context"):
        resolve_market_context(unsafe)


def test_unconfigured_sg_persistence_never_falls_back_to_my(monkeypatch, tmp_path):
    from src.invoice_app.services import uat2_data_settings

    monkeypatch.setattr(uat2_data_settings, "SECRETS_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("INV_UAT2_DATA_SPREADSHEET_ID", "my-only-sheet")
    for name in (
        "INV_SHOPEE_SG_UAT2_DATA_SPREADSHEET_ID",
        "INV_SHOPEE_SG_UAT2_GOOGLE_CREDENTIALS_PATH",
        "INV_SHOPEE_SG_UAT2_DATA_CACHE_TTL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(MarketConfigurationUnavailable, match="Shopee SG persistence is unavailable"):
        configured_uat2_data_settings(SHOPEE_SG)


def test_sg_market_capabilities_fail_closed_even_with_manually_constructed_settings():
    settings = UAT2DataSettings(
        google_spreadsheet_id="sg-only-sheet",
        market_context=SHOPEE_SG,
    )

    with pytest.raises(MarketConfigurationUnavailable, match="statement commit is unavailable"):
        settings.create_statement_writer()
    with pytest.raises(MarketConfigurationUnavailable, match="statement commit is unavailable"):
        settings.create_monthly_statement_writer()
    with pytest.raises(MarketConfigurationUnavailable, match="weekly billing is unavailable"):
        settings.create_weekly_billing_reader()
    with pytest.raises(MarketConfigurationUnavailable, match="permanently excluded"):
        settings.create_cross_platform_product_summary_reader()


def test_unconfigured_sg_product_master_never_falls_back_to_my(monkeypatch, tmp_path):
    from src.invoice_app.services import product_master_source

    monkeypatch.setattr(product_master_source, "SECRETS_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("INV_PRODUCT_MASTER_SOURCE", "local_excel")
    for name in (
        "INV_SHOPEE_SG_PRODUCT_MASTER_SOURCE",
        "INV_SHOPEE_SG_PRODUCT_MASTER_PATH",
        "INV_SHOPEE_SG_GOOGLE_CREDENTIALS_PATH",
        "INV_SHOPEE_SG_GOOGLE_SPREADSHEET_ID",
        "INV_SHOPEE_SG_GOOGLE_WORKSHEET_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(MarketConfigurationUnavailable, match="Shopee SG Product Master is unavailable"):
        product_master_source.configured_product_master_source_settings(SHOPEE_SG)


def test_sg_product_master_rejects_a_non_tab_provider(monkeypatch, tmp_path):
    from src.invoice_app.services import product_master_source

    monkeypatch.setattr(product_master_source, "SECRETS_PATH", tmp_path / "missing.toml")
    monkeypatch.setenv("INV_SHOPEE_SG_PRODUCT_MASTER_SOURCE", "local_excel")

    with pytest.raises(MarketConfigurationUnavailable, match="Google Sheets tab configuration"):
        product_master_source.configured_product_master_source_settings(SHOPEE_SG)


def test_product_master_cache_identity_includes_market_context(monkeypatch, tmp_path):
    from src.invoice_app.services import product_master_source

    settings = ProductMasterSourceSettings(
        source="local_excel",
        local_excel_path=tmp_path / "same-source.xlsx",
    )
    calls: list[Path] = []

    class FakeSource:
        def load(self):
            calls.append(settings.local_excel_path)
            return SimpleNamespace(
                to_price_master=lambda: object(),
                source_label="Synthetic",
            )

    monkeypatch.setattr(ProductMasterSourceSettings, "create_source", lambda _self: FakeSource())
    product_master_source.clear_product_master_source_cache()

    my_master, my_label = product_master_source._load_configured_product_price_master(
        SHOPEE_MY, settings
    )
    sg_master, sg_label = product_master_source._load_configured_product_price_master(
        SHOPEE_SG, settings
    )

    assert my_label == sg_label == "Synthetic"
    assert my_master is not sg_master
    assert calls == [settings.local_excel_path, settings.local_excel_path]
    product_master_source.clear_product_master_source_cache()


def test_market_scoped_state_keys_and_active_batch_binding_are_isolated():
    assert market_state_key(SHOPEE_MY, "data_import", "batch_id") == "data_import.shopee_my.batch_id"
    assert market_state_key(SHOPEE_SG, "data_import", "batch_id") == "data_import.shopee_sg.batch_id"

    state: dict[str, object] = {}
    assert bind_active_import_market(state, SHOPEE_MY) is SHOPEE_MY
    assert active_import_market(state) is SHOPEE_MY
    with pytest.raises(MarketStateIsolationError, match="another market"):
        bind_active_import_market(state, SHOPEE_SG)


def test_statement_writer_rejects_plan_for_another_persisted_platform_before_io():
    class NoIoGateway:
        def read_tabs(self, *_args):
            raise AssertionError("market mismatch must reject before any read")

        def read_sheet_ids(self, *_args):
            raise AssertionError("market mismatch must reject before any read")

    row = [""] * 40
    row[3] = "Shopee"
    plan = SimpleNamespace(
        rows=(tuple(row),),
        financial_component_rows=(),
        summary_rows=(),
        order_adjustments=(),
    )
    writer = GoogleSheetsStatementWriter(
        spreadsheet_id="sg-only-sheet",
        gateway=NoIoGateway(),
        persisted_platform=SHOPEE_SG.persisted_platform,
    )

    with pytest.raises(Exception, match="does not match the locked writer market"):
        writer.write_statement_batch(plan)
