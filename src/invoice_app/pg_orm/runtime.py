"""Fail-closed local PostgreSQL runtime configuration for approved adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)


class PostgresRuntimeUnavailable(HistoricalInvoiceStorageError):
    """The approved local PostgreSQL runtime cannot be used safely."""


@dataclass(frozen=True)
class PostgresRuntimeConfig:
    """External-only connection settings for the local approved target."""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_environment(
        cls, *, consumer: str = "Statement"
    ) -> "PostgresRuntimeConfig":
        user = os.environ.get("INVOICEGATHER_PG_USER", "")
        password = os.environ.get("INVOICEGATHER_PG_PASSWORD", "")
        if not user or not password:
            raise PostgresRuntimeUnavailable(
                "INVOICEGATHER_PG_USER and INVOICEGATHER_PG_PASSWORD are required; "
                f"PostgreSQL {consumer} access never falls back to Google Sheets."
            )
        try:
            port = int(os.environ.get("INVOICEGATHER_PG_PORT", "5433"))
        except ValueError as error:
            raise PostgresRuntimeUnavailable(
                "INVOICEGATHER_PG_PORT must be the approved local PostgreSQL port."
            ) from error
        config = cls(
            host=os.environ.get("INVOICEGATHER_PG_HOST", "127.0.0.1"),
            port=port,
            database=os.environ.get("INVOICEGATHER_PG_DATABASE", "invoicegather"),
            user=user,
            password=password,
        )
        config.assert_authorized_target(consumer=consumer)
        return config

    def assert_authorized_target(self, *, consumer: str = "Statement") -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise PostgresRuntimeUnavailable(
                f"Refusing non-local PostgreSQL {consumer} target: {self.host!r}."
            )
        if self.port != 5433 or self.database != "invoicegather":
            raise PostgresRuntimeUnavailable(
                f"Refusing unapproved PostgreSQL {consumer} target; expected "
                "localhost:5433/invoicegather."
            )

    def engine(self) -> Engine:
        return create_engine(
            URL.create(
                "postgresql+psycopg2",
                username=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                database=self.database,
            ),
            pool_pre_ping=True,
        )
