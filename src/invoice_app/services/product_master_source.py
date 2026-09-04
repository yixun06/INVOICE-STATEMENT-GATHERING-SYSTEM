from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path
import tomllib
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from ..config import SECRETS_PATH, SHOPEE_PRODUCT_MASTER_PATH
from ..utils.normalize import normalize_sku_text, normalize_whitespace
from .product_price_master import (
    ProductPriceMaster,
    ProductPriceMasterMetadata,
    ProductPriceMasterRecord,
    _REQUIRED_HEADERS,
    _display_text,
    _strict_decimal,
)


GOOGLE_SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


class ProductMasterSourceError(RuntimeError):
    """A configured Product Master source could not provide trustworthy records."""


@dataclass(frozen=True)
class ProductMasterSourceLoad:
    """Validated canonical records produced by one Product Master source."""

    records: tuple[ProductPriceMasterRecord, ...]
    metadata: ProductPriceMasterMetadata
    source_label: str

    def to_price_master(self) -> ProductPriceMaster:
        return ProductPriceMaster(self.records, self.metadata)


class ProductMasterSource(Protocol):
    def load(self) -> ProductMasterSourceLoad: ...


@dataclass(frozen=True)
class LocalExcelProductMasterSource:
    """Existing Excel Product Listing source, retained for local development and tests."""

    source_path: Path

    def load(self) -> ProductMasterSourceLoad:
        try:
            master = ProductPriceMaster.from_xlsx(self.source_path)
        except (OSError, ValueError) as error:
            raise ProductMasterSourceError(
                f"Local Excel Product Master could not be loaded: {error}"
            ) from error
        return ProductMasterSourceLoad(
            records=master.records,
            metadata=master.metadata,
            source_label="Local Excel",
        )


GoogleServiceAccountInfo = Mapping[str, Any]
GoogleCredentialsSource = Path | GoogleServiceAccountInfo
GoogleSheetValuesFetcher = Callable[[GoogleCredentialsSource, str, str], Sequence[Sequence[Any]]]


@dataclass(frozen=True)
class GoogleSheetsProductMasterSource:
    """Read the complete Product Master tab once through a read-only Service Account."""

    credentials_path: Path | None
    spreadsheet_id: str
    worksheet_name: str
    google_service_account: GoogleServiceAccountInfo | None = field(default=None, repr=False)
    values_fetcher: GoogleSheetValuesFetcher | None = None

    def load(self) -> ProductMasterSourceLoad:
        rows = tuple(tuple(row) for row in self._fetch_values())
        header_row, columns = _find_tabular_listing_headers(rows)
        records, candidate_rows, invalid_prices = _canonical_records_from_rows(
            rows,
            header_row=header_row,
            columns=columns,
        )
        return ProductMasterSourceLoad(
            records=records,
            metadata=ProductPriceMasterMetadata(
                source_filename="Google Sheets Product Master",
                sheet_name=self.worksheet_name,
                header_row=header_row,
                workbook_row_count=len(rows),
                candidate_row_count=candidate_rows,
                loaded_row_count=len(records),
                invalid_price_row_count=invalid_prices,
            ),
            source_label="Google Sheets",
        )

    def _fetch_values(self) -> Sequence[Sequence[Any]]:
        credentials_source = self._credentials_source()
        if isinstance(credentials_source, Path) and not credentials_source.is_file():
            raise ProductMasterSourceError(
                "Google Sheets Product Master credentials file is unavailable."
            )
        try:
            fetcher = self.values_fetcher or _fetch_google_sheet_values
            rows = fetcher(
                credentials_source,
                self.spreadsheet_id,
                self.worksheet_name,
            )
        except ProductMasterSourceError:
            raise
        except Exception as error:
            raise ProductMasterSourceError(
                "Google Sheets Product Master load failed. Check the configured "
                "service account and Google Sheet access."
            ) from error
        if not rows:
            raise ProductMasterSourceError("Google Sheets Product Master is empty.")
        return rows

    def _credentials_source(self) -> GoogleCredentialsSource:
        if self.google_service_account:
            return self.google_service_account
        if self.credentials_path is not None:
            return self.credentials_path
        raise ProductMasterSourceError(
            "Google Sheets Product Master credentials are missing; configure "
            "google_service_account or google_credentials_path."
        )


@dataclass(frozen=True)
class ProductMasterSourceSettings:
    source: str
    local_excel_path: Path
    google_credentials_path: Path | None = None
    google_service_account: GoogleServiceAccountInfo | None = field(default=None, repr=False)
    google_spreadsheet_id: str | None = None
    google_worksheet_name: str | None = None

    def __hash__(self) -> int:
        """Keep source caching compatible with a non-hashable secret mapping."""
        return hash(
            (
                self.source,
                self.local_excel_path,
                self.google_credentials_path,
                bool(self.google_service_account),
                self.google_spreadsheet_id,
                self.google_worksheet_name,
            )
        )

    @property
    def source_label(self) -> str:
        return "Google Sheets" if self.source == "google_sheets" else "Local Excel"

    def create_source(self) -> ProductMasterSource:
        if self.source == "local_excel":
            return LocalExcelProductMasterSource(self.local_excel_path)
        if self.source != "google_sheets":
            raise ProductMasterSourceError(
                f"Unsupported Product Master source '{self.source}'."
            )
        missing = [
            name
            for name, value in (
                ("google_spreadsheet_id", self.google_spreadsheet_id),
                ("google_worksheet_name", self.google_worksheet_name),
            )
            if not value
        ]
        if not self.google_service_account and self.google_credentials_path is None:
            missing.insert(0, "google_service_account or google_credentials_path")
        if missing:
            raise ProductMasterSourceError(
                "Google Sheets Product Master configuration is incomplete: "
                + ", ".join(missing)
                + "."
            )
        return GoogleSheetsProductMasterSource(
            credentials_path=self.google_credentials_path,
            spreadsheet_id=self.google_spreadsheet_id,
            worksheet_name=self.google_worksheet_name,
            google_service_account=self.google_service_account,
        )


def configured_product_master_source_settings() -> ProductMasterSourceSettings:
    """Read source settings from environment, then Streamlit secrets when available."""
    values: dict[str, str] = {
        "source": os.getenv("INV_PRODUCT_MASTER_SOURCE", "local_excel"),
        "local_excel_path": os.getenv(
            "INV_SHOPEE_PRODUCT_MASTER_PATH", str(SHOPEE_PRODUCT_MASTER_PATH)
        ),
        "google_credentials_path": os.getenv("INV_GOOGLE_CREDENTIALS_PATH", ""),
        "google_spreadsheet_id": os.getenv("INV_GOOGLE_SPREADSHEET_ID", ""),
        "google_worksheet_name": os.getenv("INV_GOOGLE_WORKSHEET_NAME", ""),
    }
    google_service_account: GoogleServiceAccountInfo | None = None
    source_config = _configured_product_master_secret_mapping()
    for key in values:
        if source_config.get(key):
            values[key] = str(source_config[key])
    configured_service_account = source_config.get("google_service_account")
    if isinstance(configured_service_account, Mapping) and configured_service_account:
        google_service_account = dict(configured_service_account)
    return ProductMasterSourceSettings(
        source=values["source"].strip().casefold(),
        local_excel_path=Path(values["local_excel_path"]),
        google_credentials_path=(
            Path(values["google_credentials_path"])
            if values["google_credentials_path"]
            else None
        ),
        google_service_account=google_service_account,
        google_spreadsheet_id=values["google_spreadsheet_id"].strip() or None,
        google_worksheet_name=values["google_worksheet_name"].strip() or None,
    )


def _configured_product_master_secret_mapping() -> Mapping[str, Any]:
    if SECRETS_PATH.is_file():
        try:
            with SECRETS_PATH.open("rb") as handle:
                secrets = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ProductMasterSourceError(
                f"Product Master configuration could not be read: {error}"
            ) from error
        source_config = secrets.get("product_master", {})
        return source_config if isinstance(source_config, Mapping) else {}

    try:
        import streamlit as st

        source_config = st.secrets.get("product_master", {})
    except Exception:
        # In non-Streamlit contexts, or before Cloud Secrets are configured,
        # environment/default settings remain the safe baseline.
        return {}
    return source_config if isinstance(source_config, Mapping) else {}


def load_configured_product_price_master() -> tuple[ProductPriceMaster, str]:
    settings = configured_product_master_source_settings()
    return _load_configured_product_price_master(settings)


@lru_cache(maxsize=4)
def _load_configured_product_price_master(
    settings: ProductMasterSourceSettings,
) -> tuple[ProductPriceMaster, str]:
    loaded = settings.create_source().load()
    return loaded.to_price_master(), loaded.source_label


def clear_product_master_source_cache() -> None:
    _load_configured_product_price_master.cache_clear()


def _fetch_google_sheet_values(
    credentials_source: GoogleCredentialsSource,
    spreadsheet_id: str,
    worksheet_name: str,
) -> Sequence[Sequence[Any]]:
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as error:
        raise ProductMasterSourceError(
            "Google Sheets dependencies are unavailable; install google-api-python-client."
        ) from error

    if isinstance(credentials_source, Path):
        credentials = Credentials.from_service_account_file(
            str(credentials_source), scopes=[GOOGLE_SHEETS_READONLY_SCOPE]
        )
    else:
        credentials = Credentials.from_service_account_info(
            dict(credentials_source), scopes=[GOOGLE_SHEETS_READONLY_SCOPE]
        )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=worksheet_name,
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    values = response.get("values")
    if not isinstance(values, list):
        raise ProductMasterSourceError(
            "Google Sheets Product Master response does not contain tabular values."
        )
    return values


def _find_tabular_listing_headers(
    rows: Sequence[Sequence[Any]],
) -> tuple[int, dict[str, int]]:
    for row_number, row in enumerate(rows, start=1):
        headers = {
            _header_text(value): index
            for index, value in enumerate(row)
            if _header_text(value)
        }
        if all(expected in headers for expected in _REQUIRED_HEADERS.values()):
            return row_number, {
                field: headers[expected] for field, expected in _REQUIRED_HEADERS.items()
            }
        if row_number >= 50:
            break
    raise ProductMasterSourceError(
        "Google Sheets Product Master required headers were not found."
    )


def _canonical_records_from_rows(
    rows: Sequence[Sequence[Any]],
    *,
    header_row: int,
    columns: dict[str, int],
) -> tuple[tuple[ProductPriceMasterRecord, ...], int, int]:
    records: list[ProductPriceMasterRecord] = []
    candidate_rows = 0
    invalid_prices = 0
    for row_number, row in enumerate(rows[header_row:], start=header_row + 1):
        values = {
            field: row[index] if index < len(row) else None
            for field, index in columns.items()
        }
        seller_sku = normalize_sku_text(values["seller_sku"])
        parent_sku = normalize_sku_text(values["parent_sku"])
        if not seller_sku and not parent_sku:
            continue
        candidate_rows += 1
        price = _strict_decimal(values["unit_selling_price"])
        if price is None:
            invalid_prices += 1
            continue
        records.append(
            ProductPriceMasterRecord(
                seller_sku=seller_sku,
                parent_sku=parent_sku,
                product_name=_display_text(values["product_name"]),
                variation_name=_display_text(values["variation_name"]),
                unit_selling_price=price,
                source_row=row_number,
            )
        )
    return tuple(records), candidate_rows, invalid_prices


def _header_text(value: Any) -> str:
    return normalize_whitespace(str(value or "")).casefold()
