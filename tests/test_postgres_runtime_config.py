from __future__ import annotations

import pytest

from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig, PostgresRuntimeUnavailable


@pytest.mark.parametrize(
    ("host", "port", "database"),
    [
        ("127.0.0.1", "5433", "invoicegather"),
        ("localhost", "5433", "invoicegather"),
        ("::1", "5433", "invoicegather"),
        ("127.0.0.1", "55434", "invoicegather_uat2_import"),
    ],
)
def test_allowed_targets(monkeypatch, host, port, database):
    for key, value in {"HOST": host, "PORT": port, "DATABASE": database,
                       "USER": "test-user", "PASSWORD": "test-password"}.items():
        monkeypatch.setenv(f"INVOICEGATHER_PG_{key}", value)
    config = PostgresRuntimeConfig.from_environment()
    assert (config.host, config.port, config.database) == (host, int(port), database)


@pytest.mark.parametrize(
    ("host", "port", "database"),
    [
        ("127.0.0.1", "55434", "invoicegather"),
        ("127.0.0.1", "55435", "invoicegather_uat2_import"),
        ("127.0.0.1", "5433", "arbitrary"),
        ("remote.example", "55434", "invoicegather_uat2_import"),
        ("localhost", "55434", "invoicegather_uat2_import"),
        ("::1", "55434", "invoicegather_uat2_import"),
    ],
)
def test_unapproved_targets_fail_closed(monkeypatch, host, port, database):
    for key, value in {"HOST": host, "PORT": port, "DATABASE": database,
                       "USER": "test-user", "PASSWORD": "test-password"}.items():
        monkeypatch.setenv(f"INVOICEGATHER_PG_{key}", value)
    with pytest.raises(PostgresRuntimeUnavailable):
        PostgresRuntimeConfig.from_environment()


@pytest.mark.parametrize("missing", ["USER", "PASSWORD"])
def test_missing_credentials_fail_closed(monkeypatch, missing):
    monkeypatch.setenv("INVOICEGATHER_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("INVOICEGATHER_PG_PORT", "55434")
    monkeypatch.setenv("INVOICEGATHER_PG_DATABASE", "invoicegather_uat2_import")
    monkeypatch.setenv("INVOICEGATHER_PG_USER", "test-user")
    monkeypatch.setenv("INVOICEGATHER_PG_PASSWORD", "test-password")
    monkeypatch.delenv(f"INVOICEGATHER_PG_{missing}")
    with pytest.raises(PostgresRuntimeUnavailable):
        PostgresRuntimeConfig.from_environment()
