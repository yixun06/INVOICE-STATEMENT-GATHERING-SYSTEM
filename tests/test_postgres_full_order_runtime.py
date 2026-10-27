from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from src.invoice_app.pg_orm.pg_models import FullOrderCurrentItem as PgItem
from src.invoice_app.pg_orm.pg_models import FullOrderCurrentOrder as PgOrder
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
)
from src.invoice_app.repositories.postgres_full_order_current_repository import (
    PostgresFullOrderCurrentRepository,
)
from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentBatch,
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
    full_order_snapshot_hash,
)
from src.invoice_app.services.uat2_data_settings import UAT2DataSettings


def _repository() -> PostgresFullOrderCurrentRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PgOrder.__table__.create(engine)
    PgItem.__table__.create(engine)
    return PostgresFullOrderCurrentRepository(engine=engine)


def _batch(*, subtotal: str = "12.00", items: int = 2, resolve_sku: str = "") -> FullOrderCurrentBatch:
    current_items = tuple(
        FullOrderCurrentItem(
            market="shopee_my",
            order_id="MY-PG-1",
            item_index=index,
            source_parent_sku=f"PARENT-{index}",
            source_seller_sku=f"SKU-{index}",
            resolved_seller_sku=f"SKU-{index}",
            nav=f"NAV-{index}",
            product_name=f"Product {index}",
            variation="",
            quantity=1,
            unit_price=Decimal("6.00"),
            product_subtotal=Decimal(subtotal),
            return_refund_status="",
            resolve_sku=resolve_sku,
        )
        for index in range(items)
    )
    return FullOrderCurrentBatch(
        source_filename="real-my-full-order.xlsx",
        source_hash="source-hash",
        orders=(
            FullOrderCurrentOrder(
                market="shopee_my",
                order_id="MY-PG-1",
                order_status="Completed",
                order_created_date=date(2026, 10, 1),
                source_file="real-my-full-order.xlsx",
                snapshot_hash=full_order_snapshot_hash(
                    market="shopee_my",
                    order_id="MY-PG-1",
                    order_status="Completed",
                    order_created_date=date(2026, 10, 1),
                    items=list(current_items),
                ),
                last_synced_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
            ),
        ),
        items=current_items,
        issues=(),
    )


def test_postgres_full_order_runtime_inserts_updates_and_reconstructs_deterministically() -> None:
    repository = _repository()
    first = repository.sync(_batch(resolve_sku="stored-pos"))

    assert first.new_count == 1
    assert repository.has_order("MY-PG-1") is True
    snapshot = repository.read_snapshot()
    assert snapshot.orders[0].market == "shopee_my"
    assert snapshot.items[0].unit_price == Decimal("6.00")
    assert snapshot.items[0].resolve_sku == "stored-pos"

    replay = repository.sync(_batch(resolve_sku="changed-by-replay"))
    assert replay.unchanged_count == 1

    changed = repository.sync(_batch(subtotal="11.00", resolve_sku="new-pm-value"))
    assert changed.updated_count == 1
    after = repository.read_snapshot()
    assert after.items[0].product_subtotal == Decimal("11.00")
    # MY replay retains the separately managed operational resolve SKU.
    assert after.items[0].resolve_sku == "stored-pos"


def test_postgres_full_order_runtime_fails_closed_before_removing_an_existing_item() -> None:
    repository = _repository()
    repository.sync(_batch(items=2))

    with pytest.raises(HistoricalInvoiceStorageError, match="would delete"):
        repository.sync(_batch(subtotal="11.00", items=1))

    assert len(repository.read_snapshot().items) == 2


def test_postgres_full_order_runtime_is_my_only() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(HistoricalInvoiceStorageError, match="MY only"):
        PostgresFullOrderCurrentRepository(engine=engine, market_context=SHOPEE_SG)


def test_explicit_my_postgres_backend_never_constructs_a_sheets_repository(monkeypatch) -> None:
    from src.invoice_app.services import uat2_data_settings

    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(
        uat2_data_settings.PostgresRuntimeConfig,
        "from_environment",
        lambda: type("Runtime", (), {"engine": lambda self: engine})(),
    )
    settings = UAT2DataSettings(full_order_backend="postgresql")

    assert isinstance(
        settings.create_full_order_current_repository(),
        PostgresFullOrderCurrentRepository,
    )
