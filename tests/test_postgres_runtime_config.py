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
        ("127.0.0.1", "5433", "invoicegather_perf_uat"),
        ("127.0.0.1", "5433", "invoicegather_perf_scale"),
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
        ("localhost", "5433", "invoicegather_perf_uat"),
        ("127.0.0.1", "5434", "invoicegather_perf_uat"),
        ("remote.example", "5433", "invoicegather_perf_uat"),
        ("localhost", "5433", "invoicegather_perf_scale"),
        ("127.0.0.1", "5434", "invoicegather_perf_scale"),
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
def test_isolated_upload_mode_requires_exact_database(monkeypatch):
    import pytest
    from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig, PostgresRuntimeUnavailable
    monkeypatch.setenv('INVOICEGATHER_ATOMICITY_TEST_MODE', '1')
    config = PostgresRuntimeConfig('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test', 'user', 'test-password')
    config.assert_authorized_target()
    for host, port, database in [('localhost', 5433, 'invoicegather_fact_atomicity_test'),
                                 ('127.0.0.1', 5433, 'invoicegather'),
                                 ('127.0.0.1', 5433, 'invoicegather_perf_scale')]:
        with pytest.raises(PostgresRuntimeUnavailable):
            PostgresRuntimeConfig(host, port, database, 'user', 'test-password').assert_authorized_target()


@pytest.mark.parametrize('market,backend', [('my', 'full_order_backend'), ('sg', 'full_order_backend'), ('sg', 'statement_backend')])
def test_isolated_upload_mode_rejects_sheets_backend_override(monkeypatch, market, backend):
    from src.invoice_app.services import uat2_data_settings as settings
    from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
    from src.invoice_app.repositories.google_sheets_historical_invoice_repository import HistoricalInvoiceStorageError
    monkeypatch.setenv('INVOICEGATHER_ATOMICITY_TEST_MODE', '1')
    monkeypatch.setattr(settings, '_configured_uat2_secret_mapping', lambda: {backend: 'google_sheets'})
    monkeypatch.setattr(settings, '_configured_market_uat2_secret_mapping', lambda _context: {backend: 'google_sheets'})
    with pytest.raises(HistoricalInvoiceStorageError, match='requires PostgreSQL'):
        settings.configured_uat2_data_settings(SHOPEE_MY if market == 'my' else SHOPEE_SG)


def test_fact_server_policy_requires_explicit_matching_target(monkeypatch):
    from sqlalchemy.engine import URL
    from src.invoice_app.pg_orm.runtime import is_authorized_fact_server
    server = URL.create('postgresql', host='127.0.0.1', port=55434, database='invoicegather_uat2_import')
    monkeypatch.delenv('INVOICEGATHER_ATOMICITY_TEST_MODE', raising=False)
    monkeypatch.delenv('INVOICEGATHER_FACT_SERVER_ENABLED', raising=False)
    for key, value in {'HOST': '127.0.0.1', 'PORT': '55434', 'DATABASE': 'invoicegather_uat2_import'}.items():
        monkeypatch.setenv('INVOICEGATHER_PG_' + key, value)
    assert not is_authorized_fact_server(server)
    monkeypatch.setenv('INVOICEGATHER_FACT_SERVER_ENABLED', '1')
    assert is_authorized_fact_server(server)
    assert not is_authorized_fact_server(server.set(database='other'))
    monkeypatch.setenv('INVOICEGATHER_PG_PORT', '5433')
    assert not is_authorized_fact_server(server)
    monkeypatch.setenv('INVOICEGATHER_ATOMICITY_TEST_MODE', '1')
    assert not is_authorized_fact_server(server)
