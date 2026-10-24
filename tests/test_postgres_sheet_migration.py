from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from sqlalchemy import Numeric, Text

from scripts import sync_sheets_to_postgres as sync
from src.invoice_app.pg_orm.pg_models import FullOrderCurrentItem, InvoiceOrder


def test_postgres_credentials_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVOICEGATHER_PG_USER", raising=False)
    monkeypatch.delenv("INVOICEGATHER_PG_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="credentials are never stored"):
        sync.PostgresConfig.from_environment()


def test_postgres_target_rejects_remote_or_wrong_port() -> None:
    with pytest.raises(RuntimeError, match="non-local"):
        sync.PostgresConfig("db.example.com", 5433, "invoicegather", "user", "secret", "container").assert_authorized_target()
    with pytest.raises(RuntimeError, match="unapproved"):
        sync.PostgresConfig("127.0.0.1", 5432, "invoicegather", "user", "secret", "container").assert_authorized_target()


def test_sync_has_no_truncate_or_hard_coded_engine() -> None:
    source = inspect.getsource(sync)

    assert "TRUNCATE" not in source.upper()
    assert "create_engine(\"postgresql" not in source


def test_text_cells_preserve_nonblank_source_whitespace() -> None:
    assert sync._text_cell("  SKU  ") == "  SKU  "
    assert sync._text_cell("   ") is None


def test_money_annotations_and_resolve_sku_schema_are_exact() -> None:
    money_column = InvoiceOrder.__table__.c.merchandise_subtotal
    resolve_sku_column = FullOrderCurrentItem.__table__.c.resolve_sku

    assert isinstance(money_column.type, Numeric)
    assert money_column.type.python_type is Decimal
    assert isinstance(resolve_sku_column.type, Text)
