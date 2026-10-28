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
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.repositories.postgres_full_order_current_repository import (
    PostgresFullOrderCurrentRepository,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
)
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.services.postgres_statement_repository import (
    PostgresStatementRepository,
)
from src.invoice_app.services.google_sheets_monthly_statement_writer import (
    GoogleSheetsMonthlyStatementWriter,
)
from src.invoice_app.services.weekly_billing import (
    GoogleSheetsWeeklyBillingReader,
    PostgresMyWeeklyBillingReader,
)
from src.invoice_app.services.monthly_billing import (
    GoogleSheetsMonthlyBillingReader,
)
from src.invoice_app.services.cross_platform_product_summary import (
    GoogleSheetsCrossPlatformProductSummaryReader,
    PostgresMyCrossPlatformProductSummaryReader,
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
POSTGRES_STATEMENT_BACKEND = "postgresql"
GOOGLE_SHEETS_STATEMENT_BACKEND = "google_sheets"
POSTGRES_FULL_ORDER_BACKEND = "postgresql"
GOOGLE_SHEETS_FULL_ORDER_BACKEND = "google_sheets"


@dataclass(frozen=True)
class UAT2DataSettings:
    google_spreadsheet_id: str = DEFAULT_UAT2_DATA_SPREADSHEET_ID
    google_credentials_path: Path | None = None
    google_service_account: GoogleServiceAccountInfo | None = field(default=None, repr=False)
    cache_ttl_seconds: float = 45.0
    market_context: MarketContext = SHOPEE_MY
    statement_backend: str = POSTGRES_STATEMENT_BACKEND
    full_order_backend: str = POSTGRES_FULL_ORDER_BACKEND

    def create_repository(self) -> GoogleSheetsHistoricalInvoiceRepository:
        context = resolve_market_context(self.market_context)
        if context.key not in {MarketKey.SHOPEE_MY, MarketKey.SHOPEE_SG}:
            raise MarketConfigurationUnavailable(
                f"{context.display_name} persistence is unavailable: "
                "no market-specific repository backend is configured."
            )
        if (
            context.key is MarketKey.SHOPEE_SG
            and self.google_spreadsheet_id == DEFAULT_UAT2_DATA_SPREADSHEET_ID
        ):
            raise MarketConfigurationUnavailable(
                "Shopee SG persistence is unavailable: an explicit SG spreadsheet "
                "destination is required."
            )
        credentials = self._credentials_source()
        return GoogleSheetsHistoricalInvoiceRepository(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(credentials),
            cache_ttl_seconds=self.cache_ttl_seconds,
        )

    def create_full_order_current_repository(
        self,
    ) -> ShopeeFullOrderCurrentRepository | PostgresFullOrderCurrentRepository:
        """Create the configured Full Order backend for one market."""
        context = resolve_market_context(self.market_context)
        backend = self._full_order_backend(context)
        if backend == POSTGRES_FULL_ORDER_BACKEND:
            return PostgresFullOrderCurrentRepository(
                engine=PostgresRuntimeConfig.from_environment(consumer="Full Order").engine(),
                market_context=context,
            )
        if context.key not in {MarketKey.SHOPEE_MY, MarketKey.SHOPEE_SG}:
            raise MarketConfigurationUnavailable(
                f"{context.display_name} Full Order current persistence is unavailable."
            )
        if (
            context.key is MarketKey.SHOPEE_SG
            and self.google_spreadsheet_id == DEFAULT_UAT2_DATA_SPREADSHEET_ID
        ):
            raise MarketConfigurationUnavailable(
                "Shopee SG Full Order persistence requires an explicit SG spreadsheet."
            )
        return ShopeeFullOrderCurrentRepository(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def _full_order_backend(self, context: MarketContext) -> str:
        backend = self.full_order_backend.strip().casefold()
        if not backend:
            variable = (
                "INV_SHOPEE_SG_FULL_ORDER_BACKEND"
                if context.key is MarketKey.SHOPEE_SG
                else "INV_SHOPEE_MY_FULL_ORDER_BACKEND"
            )
            raise MarketConfigurationUnavailable(
                f"{context.display_name} Full Order backend is not configured; "
                f"set {variable} to 'postgresql' or 'google_sheets'."
            )
        if backend not in {POSTGRES_FULL_ORDER_BACKEND, GOOGLE_SHEETS_FULL_ORDER_BACKEND}:
            raise MarketConfigurationUnavailable(
                f"{context.display_name} Full Order backend is unsupported: "
                f"{self.full_order_backend!r}."
            )
        return backend

    def create_statement_writer(self) -> GoogleSheetsStatementWriter | PostgresStatementRepository:
        context = resolve_market_context(self.market_context)
        require_capability(context, "statement_commit")
        backend = self._statement_backend(context)
        if backend == POSTGRES_STATEMENT_BACKEND:
            return PostgresStatementRepository(
                engine=PostgresRuntimeConfig.from_environment().engine(),
                persisted_platform=context.persisted_platform,
            )
        if (
            context.key is MarketKey.SHOPEE_SG
            and self.google_spreadsheet_id == DEFAULT_UAT2_DATA_SPREADSHEET_ID
        ):
            raise MarketConfigurationUnavailable(
                "Shopee SG Statement persistence requires an explicit SG spreadsheet."
            )
        return GoogleSheetsStatementWriter(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
            persisted_platform=context.persisted_platform,
        )

    def _statement_backend(self, context: MarketContext) -> str:
        backend = self.statement_backend.strip().casefold()
        if backend not in {POSTGRES_STATEMENT_BACKEND, GOOGLE_SHEETS_STATEMENT_BACKEND}:
            raise MarketConfigurationUnavailable(
                f"{context.display_name} Statement backend is unsupported: {self.statement_backend!r}."
            )
        if context.key is MarketKey.SHOPEE_MY and backend != POSTGRES_STATEMENT_BACKEND:
            raise MarketConfigurationUnavailable(
                "Shopee MY Statement runtime is PostgreSQL-only; Sheets is not an active fallback."
            )
        return backend

    def create_monthly_statement_writer(self) -> GoogleSheetsMonthlyStatementWriter:
        context = resolve_market_context(self.market_context)
        if context.key is not MarketKey.SHOPEE_MY:
            raise MarketConfigurationUnavailable(
                f"{context.display_name} Monthly Statement persistence is MY-only by design."
            )
        require_capability(context, "statement_commit")
        return GoogleSheetsMonthlyStatementWriter(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def create_weekly_billing_reader(self) -> GoogleSheetsWeeklyBillingReader | PostgresMyWeeklyBillingReader:
        require_capability(self.market_context, "weekly_billing")
        context = resolve_market_context(self.market_context)
        if context.key is MarketKey.SHOPEE_MY:
            return PostgresMyWeeklyBillingReader(
                spreadsheet_id=self.google_spreadsheet_id,
                gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
                statement_repository=PostgresStatementRepository(
                    engine=PostgresRuntimeConfig.from_environment().engine(),
                    persisted_platform=context.persisted_platform,
                ),
            )
        return GoogleSheetsWeeklyBillingReader(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def create_monthly_billing_reader(self) -> GoogleSheetsMonthlyBillingReader:
        """Bind Monthly Billing to the configured MY source without SG fallback."""
        require_capability(self.market_context, "weekly_billing")
        return GoogleSheetsMonthlyBillingReader(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
        )

    def create_cross_platform_product_summary_reader(
        self,
    ) -> GoogleSheetsCrossPlatformProductSummaryReader | PostgresMyCrossPlatformProductSummaryReader:
        if not self.market_context.cross_platform_eligible:
            raise MarketConfigurationUnavailable(
                f"{self.market_context.display_name} is permanently excluded from Cross Platform Summary."
            )
        context = resolve_market_context(self.market_context)
        if context.key in {MarketKey.SHOPEE_MY, MarketKey.SHOPEE_SG}:
            return PostgresMyCrossPlatformProductSummaryReader(
                spreadsheet_id=self.google_spreadsheet_id,
                gateway=GoogleApiHistoricalInvoiceGateway(self._credentials_source()),
                statement_repository=PostgresStatementRepository(
                    engine=PostgresRuntimeConfig.from_environment().engine(),
                    persisted_platform=context.persisted_platform,
                ),
                full_order_repository=self.create_full_order_current_repository(),
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
                raise HistoricalInvoiceStorageError(
                    f"{resolve_market_context(self.market_context).display_name} "
                    "Google Sheets credentials file is unavailable."
                )
            return self.google_credentials_path
        context = resolve_market_context(self.market_context)
        credential_name = (
            "INV_SHOPEE_SG_UAT2_GOOGLE_CREDENTIALS_PATH"
            if context.key is MarketKey.SHOPEE_SG
            else "INV_UAT2_GOOGLE_CREDENTIALS_PATH"
        )
        raise HistoricalInvoiceStorageError(
            f"{context.display_name} Google Sheets credentials are missing; configure "
            f"{credential_name} or the matching market uat2_data.google_service_account."
        )


def configured_uat2_data_settings(
    context: MarketContext | MarketKey | str | None = None,
    *,
    require_explicit_context: bool = False,
) -> UAT2DataSettings:
    """Resolve one market's persistence settings without cross-market fallback."""

    if context is None and require_explicit_context:
        raise MarketConfigurationUnavailable(
            "MISSING_MARKET_CONTEXT: persistence requires an explicit market context."
        )
    resolved = resolve_market_context(context)
    if resolved.key is MarketKey.SHOPEE_MY:
        values = {
            "google_spreadsheet_id": os.getenv("INV_UAT2_DATA_SPREADSHEET_ID", DEFAULT_UAT2_DATA_SPREADSHEET_ID),
            "google_credentials_path": os.getenv("INV_UAT2_GOOGLE_CREDENTIALS_PATH", ""),
            "cache_ttl_seconds": os.getenv("INV_UAT2_DATA_CACHE_TTL_SECONDS", "45"),
            "statement_backend": POSTGRES_STATEMENT_BACKEND,
            "full_order_backend": os.getenv(
                "INV_SHOPEE_MY_FULL_ORDER_BACKEND", POSTGRES_FULL_ORDER_BACKEND
            ),
        }
        configured = _configured_uat2_secret_mapping()
    elif resolved.key is MarketKey.SHOPEE_SG:
        values = {
            "google_spreadsheet_id": os.getenv("INV_SHOPEE_SG_UAT2_DATA_SPREADSHEET_ID", ""),
            "google_credentials_path": os.getenv("INV_SHOPEE_SG_UAT2_GOOGLE_CREDENTIALS_PATH", ""),
            "cache_ttl_seconds": os.getenv("INV_SHOPEE_SG_UAT2_DATA_CACHE_TTL_SECONDS", "45"),
            "statement_backend": os.getenv(
                "INV_SHOPEE_SG_STATEMENT_BACKEND", POSTGRES_STATEMENT_BACKEND
            ),
            "full_order_backend": os.getenv(
                "INV_SHOPEE_SG_FULL_ORDER_BACKEND", POSTGRES_FULL_ORDER_BACKEND
            ),
        }
        configured = _configured_market_uat2_secret_mapping(resolved)
    else:
        raise MarketConfigurationUnavailable(
            f"{resolved.display_name} persistence is unavailable: no billing backend is configured."
        )
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
        statement_backend=values["statement_backend"].strip(),
        full_order_backend=values["full_order_backend"].strip(),
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
