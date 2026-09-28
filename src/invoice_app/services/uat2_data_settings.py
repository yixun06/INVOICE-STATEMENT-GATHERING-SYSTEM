"""Dedicated UAT2 historical-invoice Google Sheets configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping

from src.invoice_app.config import SECRETS_PATH
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    GoogleApiHistoricalInvoiceGateway,
    GoogleServiceAccountInfo,
    GoogleSheetsHistoricalInvoiceRepository,
    HistoricalInvoiceStorageError,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
)
from src.invoice_app.services.google_sheets_monthly_statement_writer import (
    GoogleSheetsMonthlyStatementWriter,
)
from src.invoice_app.services.weekly_billing import (
    GoogleSheetsWeeklyBillingReader,
)
from src.invoice_app.services.cross_platform_product_summary import (
    GoogleSheetsCrossPlatformProductSummaryReader,
)
from src.invoice_app.services.market_context import (
    MarketConfigurationUnavailable,
    MarketContext,
    MarketKey,
    SHOPEE_MY,
    require_capability,
    resolve_market_context,
)


DEFAULT_UAT2_DATA_SPREADSHEET_ID = "1sZHYrmL9KuxhIdlZUN-EIy5tedmlbY62fOPxP22hsF8"


@dataclass(frozen=True)
class UAT2DataSettings:
    google_spreadsheet_id: str = DEFAULT_UAT2_DATA_SPREADSHEET_ID
    google_credentials_path: Path | None = None
    google_service_account: GoogleServiceAccountInfo | None = field(default=None, repr=False)
    cache_ttl_seconds: float = 45.0
    market_context: MarketContext = SHOPEE_MY

    def create_repository(self) -> GoogleSheetsHistoricalInvoiceRepository:
        credentials = self._credentials_source()
        return GoogleSheetsHistoricalInvoiceRepository(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(credentials),
            cache_ttl_seconds=self.cache_ttl_seconds,
        )

    def create_statement_writer(self) -> GoogleSheetsStatementWriter:
        require_capability(self.market_context, "statement_commit")
        return GoogleSheetsStatementWriter(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
            persisted_platform=self.market_context.persisted_platform,
        )

    def create_monthly_statement_writer(self) -> GoogleSheetsMonthlyStatementWriter:
        require_capability(self.market_context, "statement_commit")
        return GoogleSheetsMonthlyStatementWriter(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def create_weekly_billing_reader(self) -> GoogleSheetsWeeklyBillingReader:
        require_capability(self.market_context, "weekly_billing")
        return GoogleSheetsWeeklyBillingReader(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def create_cross_platform_product_summary_reader(
        self,
    ) -> GoogleSheetsCrossPlatformProductSummaryReader:
        if not self.market_context.cross_platform_eligible:
            raise MarketConfigurationUnavailable(
                f"{self.market_context.display_name} is permanently excluded from Cross Platform Summary."
            )
        return GoogleSheetsCrossPlatformProductSummaryReader(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def _credentials_source(self) -> Path | GoogleServiceAccountInfo:
        if self.google_service_account:
            return self.google_service_account
        if self.google_credentials_path is not None:
            if not self.google_credentials_path.is_file():
                raise HistoricalInvoiceStorageError("UAT2 Google Sheets credentials file is unavailable.")
            return self.google_credentials_path
        raise HistoricalInvoiceStorageError(
            "UAT2 Google Sheets credentials are missing; configure "
            "INV_UAT2_GOOGLE_CREDENTIALS_PATH or uat2_data.google_service_account."
        )


def configured_uat2_data_settings(
    context: MarketContext | MarketKey | str | None = None,
) -> UAT2DataSettings:
    """Resolve one market's persistence settings without cross-market fallback."""

    resolved = resolve_market_context(context)
    if resolved.key is MarketKey.SHOPEE_MY:
        values = {
            "google_spreadsheet_id": os.getenv("INV_UAT2_DATA_SPREADSHEET_ID", DEFAULT_UAT2_DATA_SPREADSHEET_ID),
            "google_credentials_path": os.getenv("INV_UAT2_GOOGLE_CREDENTIALS_PATH", ""),
            "cache_ttl_seconds": os.getenv("INV_UAT2_DATA_CACHE_TTL_SECONDS", "45"),
        }
        configured = _configured_uat2_secret_mapping()
    else:
        values = {
            "google_spreadsheet_id": os.getenv("INV_SHOPEE_SG_UAT2_DATA_SPREADSHEET_ID", ""),
            "google_credentials_path": os.getenv("INV_SHOPEE_SG_UAT2_GOOGLE_CREDENTIALS_PATH", ""),
            "cache_ttl_seconds": os.getenv("INV_SHOPEE_SG_UAT2_DATA_CACHE_TTL_SECONDS", "45"),
        }
        configured = _configured_market_uat2_secret_mapping(resolved)
    for key in values:
        if configured.get(key) not in (None, ""):
            values[key] = str(configured[key])
    service_account = configured.get("google_service_account")
    if not values["google_spreadsheet_id"].strip():
        raise MarketConfigurationUnavailable(
            f"{resolved.display_name} persistence is unavailable: no market-specific spreadsheet is configured."
        )
    try:
        ttl = float(values["cache_ttl_seconds"])
    except ValueError as error:
        raise HistoricalInvoiceStorageError("UAT2 Google Sheets cache TTL must be numeric.") from error
    return UAT2DataSettings(
        google_spreadsheet_id=values["google_spreadsheet_id"].strip(),
        google_credentials_path=Path(values["google_credentials_path"]) if values["google_credentials_path"] else None,
        google_service_account=dict(service_account) if isinstance(service_account, Mapping) and service_account else None,
        cache_ttl_seconds=ttl,
        market_context=resolved,
    )


def _configured_uat2_secret_mapping() -> Mapping[str, Any]:
    if SECRETS_PATH.is_file():
        try:
            with SECRETS_PATH.open("rb") as handle:
                secrets = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise HistoricalInvoiceStorageError(f"UAT2 data configuration could not be read: {error}") from error
        configured = secrets.get("uat2_data", {})
        return configured if isinstance(configured, Mapping) else {}
    try:
        import streamlit as st
        configured = st.secrets.get("uat2_data", {})
    except Exception:
        return {}
    return configured if isinstance(configured, Mapping) else {}


def _configured_market_uat2_secret_mapping(context: MarketContext) -> Mapping[str, Any]:
    """Read only a deliberately named market block; never reuse legacy MY config."""

    try:
        if SECRETS_PATH.is_file():
            with SECRETS_PATH.open("rb") as handle:
                secrets = tomllib.load(handle)
        else:
            import streamlit as st

            secrets = st.secrets
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise HistoricalInvoiceStorageError(f"Market UAT2 data configuration could not be read: {error}") from error
    except Exception:
        return {}
    markets = secrets.get("markets", {}) if isinstance(secrets, Mapping) else {}
    market = markets.get(context.key.value, {}) if isinstance(markets, Mapping) else {}
    configured = market.get("uat2_data", {}) if isinstance(market, Mapping) else {}
    return configured if isinstance(configured, Mapping) else {}
