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


def is_authorized_fact_server(url) -> bool:
    """Explicit Fact opt-in plus exact configured and generally allowed target."""
    if os.environ.get('INVOICEGATHER_FACT_SERVER_ENABLED') != '1' or os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') == '1':
        return False
    configured = (os.environ.get('INVOICEGATHER_PG_HOST'), os.environ.get('INVOICEGATHER_PG_PORT'),
                  os.environ.get('INVOICEGATHER_PG_DATABASE'))
    actual = (url.host, str(url.port), url.database)
    # This is the existing explicit server-side UAT2 PostgreSQL allowlist entry.
    return configured == actual == ('127.0.0.1', '55434', 'invoicegather_uat2_import')


def assert_fact_server_target(connection):
    from sqlalchemy import text
    if not is_authorized_fact_server(connection.engine.url):
        raise PostgresRuntimeUnavailable('Fact server target is not explicitly authorized')
    if connection.execute(text('SELECT current_database(), current_schema()')).one() != ('invoicegather_uat2_import', 'public'):
        raise PostgresRuntimeUnavailable('Fact server database or schema identity mismatch')


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
        if os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') == '1':
            if (self.host, self.port, self.database) != ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test'):
                raise PostgresRuntimeUnavailable('Isolated upload test mode requires its exact test target.')
            return
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise PostgresRuntimeUnavailable(
                f"Refusing non-local PostgreSQL {consumer} target: {self.host!r}."
            )
        if not (
            (self.port == 5433 and self.database == "invoicegather")
            or (self.host == "127.0.0.1" and self.port == 5433 and self.database == "invoicegather_perf_uat")
            or (self.host == "127.0.0.1" and self.port == 5433 and self.database == "invoicegather_perf_scale")
            or (self.host == "127.0.0.1" and self.port == 55434 and self.database == "invoicegather_uat2_import")
        ):
            raise PostgresRuntimeUnavailable(
                f"Refusing unapproved PostgreSQL {consumer} target; expected "
                "localhost:5433/invoicegather, 127.0.0.1:5433/invoicegather_perf_uat or invoicegather_perf_scale, "
                "or 127.0.0.1:55434/invoicegather_uat2_import."
            )

    def engine(self) -> Engine:
        isolated = os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') == '1'
        if isolated:
            self.assert_authorized_target()
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
            connect_args={'options': '-c search_path=ig_real_upload_uat'} if isolated else {},
        )
