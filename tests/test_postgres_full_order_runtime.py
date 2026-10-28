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
from src.invoice_app.pg_orm.runtime import PostgresRuntimeUnavailable
from src.invoice_app.services.market_context import (
    MarketConfigurationUnavailable,
    SHOPEE_MY,
    SHOPEE_SG,
)
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentBatch,
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
    full_order_snapshot_hash,
)
from src.invoice_app.services.uat2_data_settings import (
    UAT2DataSettings,
    configured_uat2_data_settings,
)


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


def test_postgres_full_order_runtime_accepts_sg_and_keeps_the_market_isolated() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    PgOrder.__table__.create(engine)
    PgItem.__table__.create(engine)
    repository = PostgresFullOrderCurrentRepository(engine=engine, market_context=SHOPEE_SG)

    sg_batch = _batch()
    sg_batch = FullOrderCurrentBatch(
        source_filename=sg_batch.source_filename,
        source_hash=sg_batch.source_hash,
        orders=tuple(
            FullOrderCurrentOrder(
                market="shopee_sg",
                order_id=row.order_id,
                order_status=row.order_status,
                order_created_date=row.order_created_date,
                source_file=row.source_file,
                snapshot_hash=row.snapshot_hash,
                last_synced_at=row.last_synced_at,
            )
            for row in sg_batch.orders
        ),
        items=tuple(
            FullOrderCurrentItem(
                market="shopee_sg",
                order_id=row.order_id,
                item_index=row.item_index,
                source_parent_sku=row.source_parent_sku,
                source_seller_sku=row.source_seller_sku,
                resolved_seller_sku=row.resolved_seller_sku,
                nav=row.nav,
                product_name=row.product_name,
                variation=row.variation,
                quantity=row.quantity,
                unit_price=row.unit_price,
                product_subtotal=row.product_subtotal,
                return_refund_status=row.return_refund_status,
                resolve_sku=row.resolve_sku,
            )
            for row in sg_batch.items
        ),
        issues=(),
    )
    repository.sync(sg_batch)

    assert repository.has_order("MY-PG-1") is True
    assert repository.read_snapshot().orders[0].market == "shopee_sg"
    with pytest.raises(HistoricalInvoiceStorageError, match="configured market boundary"):
        repository.classify(_batch())


@pytest.mark.parametrize("context", (SHOPEE_MY, SHOPEE_SG))
def test_default_postgres_backend_never_constructs_a_sheets_repository(monkeypatch, context) -> None:
    from src.invoice_app.services import uat2_data_settings

    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(
        uat2_data_settings,
        "ShopeeFullOrderCurrentRepository",
        lambda **_kwargs: pytest.fail(
            "Google Sheets Full Order repository was constructed under default PostgreSQL mode"
        ),
    )
    monkeypatch.setattr(
        uat2_data_settings.PostgresRuntimeConfig,
        "from_environment",
        lambda **_kwargs: type("Runtime", (), {"engine": lambda self: engine})(),
    )
    settings = UAT2DataSettings(
        google_spreadsheet_id="market-sheet",
        google_service_account={"type": "service_account"},
        market_context=context,
    )

    assert isinstance(
        settings.create_full_order_current_repository(),
        PostgresFullOrderCurrentRepository,
    )


@pytest.mark.parametrize("context", (SHOPEE_MY, SHOPEE_SG))
def test_explicit_postgres_backend_never_constructs_a_sheets_repository(
    monkeypatch, context
) -> None:
    from src.invoice_app.services import uat2_data_settings

    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(
        uat2_data_settings.PostgresRuntimeConfig,
        "from_environment",
        lambda **_kwargs: type("Runtime", (), {"engine": lambda self: engine})(),
    )
    settings = UAT2DataSettings(
        google_spreadsheet_id="market-sheet",
        google_service_account={"type": "service_account"},
        market_context=context,
        full_order_backend="postgresql",
    )

    assert isinstance(
        settings.create_full_order_current_repository(),
        PostgresFullOrderCurrentRepository,
    )


@pytest.mark.parametrize("selector", (None, "postgresql"))
@pytest.mark.parametrize("context", (SHOPEE_MY, SHOPEE_SG))
def test_default_or_explicit_postgres_backend_missing_user_fails_before_any_repository(
    monkeypatch: pytest.MonkeyPatch, context, selector: str | None
) -> None:
    from src.invoice_app.services import uat2_data_settings

    monkeypatch.delenv("INVOICEGATHER_PG_USER", raising=False)
    monkeypatch.delenv("INVOICEGATHER_PG_PASSWORD", raising=False)
    monkeypatch.setattr(
        uat2_data_settings,
        "ShopeeFullOrderCurrentRepository",
        lambda **_kwargs: pytest.fail("Google Sheets Full Order repository was constructed"),
    )
    monkeypatch.setattr(
        uat2_data_settings,
        "PostgresFullOrderCurrentRepository",
        lambda **_kwargs: pytest.fail("PostgreSQL repository was constructed before config validation"),
    )

    kwargs: dict[str, object] = {
        "google_spreadsheet_id": "market-sheet",
        "google_service_account": {"type": "service_account"},
        "market_context": context,
    }
    if selector is not None:
        kwargs["full_order_backend"] = selector
    settings = UAT2DataSettings(**kwargs)

    with pytest.raises(PostgresRuntimeUnavailable, match="INVOICEGATHER_PG_USER"):
        settings.create_full_order_current_repository()


def test_explicit_postgres_backend_invalid_port_fails_before_any_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.invoice_app.services import uat2_data_settings

    monkeypatch.setenv("INVOICEGATHER_PG_USER", "test-user")
    monkeypatch.setenv("INVOICEGATHER_PG_PASSWORD", "test-password")
    monkeypatch.setenv("INVOICEGATHER_PG_PORT", "not-a-port")
    monkeypatch.setattr(
        uat2_data_settings,
        "ShopeeFullOrderCurrentRepository",
        lambda **_kwargs: pytest.fail("Google Sheets Full Order repository was constructed"),
    )
    monkeypatch.setattr(
        uat2_data_settings,
        "PostgresFullOrderCurrentRepository",
        lambda **_kwargs: pytest.fail("PostgreSQL repository was constructed before config validation"),
    )

    with pytest.raises(PostgresRuntimeUnavailable, match="INVOICEGATHER_PG_PORT"):
        UAT2DataSettings(full_order_backend="postgresql").create_full_order_current_repository()


@pytest.mark.parametrize("context", (SHOPEE_MY, SHOPEE_SG))
def test_explicit_google_sheets_backend_does_not_require_postgres_credentials(
    monkeypatch: pytest.MonkeyPatch, context
) -> None:
    from src.invoice_app.services import uat2_data_settings

    monkeypatch.delenv("INVOICEGATHER_PG_USER", raising=False)
    monkeypatch.delenv("INVOICEGATHER_PG_PASSWORD", raising=False)
    marker = object()
    monkeypatch.setattr(
        uat2_data_settings,
        "ShopeeFullOrderCurrentRepository",
        lambda **_kwargs: marker,
    )
    monkeypatch.setattr(
        uat2_data_settings.PostgresRuntimeConfig,
        "from_environment",
        lambda **_kwargs: pytest.fail("PostgreSQL config was consulted for explicit Google Sheets mode"),
    )

    repository = UAT2DataSettings(
        google_spreadsheet_id="market-sheet",
        google_service_account={"type": "service_account"},
        market_context=context,
        full_order_backend="google_sheets",
    ).create_full_order_current_repository()

    assert repository is marker


@pytest.mark.parametrize(
    ("context", "environment_name"),
    (
        (SHOPEE_MY, "INV_SHOPEE_MY_FULL_ORDER_BACKEND"),
        (SHOPEE_SG, "INV_SHOPEE_SG_FULL_ORDER_BACKEND"),
    ),
)
def test_configured_settings_default_to_postgres_when_selector_is_absent(
    monkeypatch: pytest.MonkeyPatch, context, environment_name: str
) -> None:
    from src.invoice_app.services import uat2_data_settings

    monkeypatch.delenv(environment_name, raising=False)
    if context is SHOPEE_SG:
        monkeypatch.setenv("INV_SHOPEE_SG_UAT2_DATA_SPREADSHEET_ID", "sg-sheet")
    monkeypatch.setattr(uat2_data_settings, "_configured_uat2_secret_mapping", lambda: {})
    monkeypatch.setattr(uat2_data_settings, "_configured_market_uat2_secret_mapping", lambda _context: {})

    settings = configured_uat2_data_settings(context, require_explicit_context=True)

    assert settings.full_order_backend == "postgresql"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr(
        uat2_data_settings.PostgresRuntimeConfig,
        "from_environment",
        lambda **_kwargs: type("Runtime", (), {"engine": lambda self: engine})(),
    )
    assert isinstance(
        settings.create_full_order_current_repository(),
        PostgresFullOrderCurrentRepository,
    )


@pytest.mark.parametrize("context", (SHOPEE_MY, SHOPEE_SG))
@pytest.mark.parametrize("selector", ("", "unsupported_backend"))
def test_invalid_full_order_backend_selector_fails_closed(context, selector: str) -> None:
    with pytest.raises(
        MarketConfigurationUnavailable,
        match="Full Order backend is (not configured|unsupported)",
    ):
        UAT2DataSettings(
            market_context=context, full_order_backend=selector
        ).create_full_order_current_repository()
