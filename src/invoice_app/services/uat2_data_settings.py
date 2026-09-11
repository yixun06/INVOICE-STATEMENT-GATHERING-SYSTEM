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


DEFAULT_UAT2_DATA_SPREADSHEET_ID = "1sZHYrmL9KuxhIdlZUN-EIy5tedmlbY62fOPxP22hsF8"


@dataclass(frozen=True)
class UAT2DataSettings:
    google_spreadsheet_id: str = DEFAULT_UAT2_DATA_SPREADSHEET_ID
    google_credentials_path: Path | None = None
    google_service_account: GoogleServiceAccountInfo | None = field(default=None, repr=False)
    cache_ttl_seconds: float = 45.0

    def create_repository(self) -> GoogleSheetsHistoricalInvoiceRepository:
        credentials = self._credentials_source()
        return GoogleSheetsHistoricalInvoiceRepository(
            spreadsheet_id=self.google_spreadsheet_id,
            gateway=GoogleApiHistoricalInvoiceGateway(credentials),
            cache_ttl_seconds=self.cache_ttl_seconds,
        )

    def create_statement_writer(self) -> GoogleSheetsStatementWriter:
        return GoogleSheetsStatementWriter(
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


def configured_uat2_data_settings() -> UAT2DataSettings:
    values = {
        "google_spreadsheet_id": os.getenv("INV_UAT2_DATA_SPREADSHEET_ID", DEFAULT_UAT2_DATA_SPREADSHEET_ID),
        "google_credentials_path": os.getenv("INV_UAT2_GOOGLE_CREDENTIALS_PATH", ""),
        "cache_ttl_seconds": os.getenv("INV_UAT2_DATA_CACHE_TTL_SECONDS", "45"),
    }
    configured = _configured_uat2_secret_mapping()
    for key in values:
        if configured.get(key) not in (None, ""):
            values[key] = str(configured[key])
    service_account = configured.get("google_service_account")
    try:
        ttl = float(values["cache_ttl_seconds"])
    except ValueError as error:
        raise HistoricalInvoiceStorageError("UAT2 Google Sheets cache TTL must be numeric.") from error
    return UAT2DataSettings(
        google_spreadsheet_id=values["google_spreadsheet_id"].strip(),
        google_credentials_path=Path(values["google_credentials_path"]) if values["google_credentials_path"] else None,
        google_service_account=dict(service_account) if isinstance(service_account, Mapping) and service_account else None,
        cache_ttl_seconds=ttl,
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
