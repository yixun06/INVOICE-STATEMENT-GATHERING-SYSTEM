"""SG PostgreSQL Statement runtime boundary and transaction regressions."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import os
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    SettlementIncomeRow,
    StatementSummaryLine,
)
from src.invoice_app.pg_orm.pg_models import (
    OrderAdjustment,
    StatementData,
    StatementFinancialComponent,
    StatementSummary,
)
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.services import postgres_statement_repository
from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitBlocked,
    StatementCommitState,
    prepare_full_order_existence_statement_commit_plan,
)
from src.invoice_app.services.uat2_data_settings import UAT2DataSettings
from src.invoice_app.services.uat2_persistence_schema import ORDER_ADJUSTMENTS_HEADERS


class ForbiddenLegacySGStatementSheets:
    """A test failure for every SG Statement-tab request in PostgreSQL mode."""

    def read_tabs(self, _spreadsheet_id: str, tabs: tuple[str, ...]):
        raise AssertionError(f"legacy SG Statement Sheets access is forbidden: {tabs!r}")


def test_sg_postgres_configuration_creates_no_google_statement_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.invoice_app.services import uat2_data_settings

    forbidden = ForbiddenLegacySGStatementSheets()
    monkeypatch.setattr(
        uat2_data_settings.PostgresRuntimeConfig,
        "from_environment",
        lambda: SimpleNamespace(engine=lambda: object()),
    )
    monkeypatch.setattr(
        uat2_data_settings,
        "GoogleApiHistoricalInvoiceGateway",
        lambda _credentials: forbidden,
    )
    settings = UAT2DataSettings(
        google_spreadsheet_id="sg-full-order-current-sheet",
        google_service_account={"type": "service_account"},
        market_context=SHOPEE_SG,
        statement_backend="postgresql",
    )

    writer = settings.create_statement_writer()

    assert isinstance(writer, PostgresStatementRepository)
    assert writer.persisted_platform == "Shopee SG"


def test_explicit_sg_google_sheets_backend_is_still_available() -> None:
    settings = UAT2DataSettings(
        google_spreadsheet_id="sg-stable-statement-sheet",
        google_service_account={"type": "service_account"},
        market_context=SHOPEE_SG,
        statement_backend="google_sheets",
    )

    writer = settings.create_statement_writer()

    assert writer._spreadsheet_id == "sg-stable-statement-sheet"
    assert writer._persisted_platform == "Shopee SG"


def _local_sg_repository_or_skip() -> PostgresStatementRepository:
    if os.environ.get("INVOICEGATHER_PG_RUNTIME_INTEGRATION") != "1":
        pytest.skip("requires explicit local PostgreSQL runtime integration authorization")
    return PostgresStatementRepository(
        engine=PostgresRuntimeConfig.from_environment().engine(),
        persisted_platform="Shopee SG",
    )


def _sg_plan():
    token = uuid4().hex
    order_id = f"SG-PG-RUNTIME-{token[:12]}"
    statement = ParsedShopeeWeeklyStatement(
        source_filename="sg-postgres-runtime-control.xlsx",
        file_hash=token * 2,
        statement_period_from=date(2026, 10, 1),
        statement_period_to=date(2026, 10, 7),
        summary_total_released=Decimal("10.00"),
        adjustment_control_total=Decimal("-1.00"),
        adjustment_footer_total=None,
        income_rows=(
            SettlementIncomeRow(
                sequence_no="1", view_by="Order", order_id=order_id,
                product_id="SG-PG-PRODUCT", product_name="SG PostgreSQL control",
                order_creation_date=date(2026, 10, 1), payout_completed_date=date(2026, 10, 7),
                release_channel="Seller Wallet", order_type="Normal",
                total_released_amount=Decimal("10.00"),
                financial_components={"Product Price": Decimal("10.00"), "Refund Amount": Decimal("0")},
                source_values={}, source_row_number=2,
            ),
            SettlementIncomeRow(
                sequence_no="1", view_by="SKU", order_id=order_id,
                product_id="SG-PG-PRODUCT", product_name="SG PostgreSQL control",
                order_creation_date=date(2026, 10, 1), payout_completed_date=date(2026, 10, 7),
                release_channel="Seller Wallet", order_type="Normal",
                total_released_amount=Decimal("10.00"),
                financial_components={"Product Price": Decimal("10.00"), "Refund Amount": Decimal("0")},
                source_values={}, source_row_number=3,
            ),
        ),
        service_fee_details=(), shipping_fee_discrepancies=(),
        adjustments=(
            SettlementAdjustment(
                sequence_no="1", adjustment_complete_date=date(2026, 10, 7),
                adjustment_type="Adjustment", adjustment_reason="SG runtime control",
                adjustment_amount=Decimal("-1.00"), linked_order_id=order_id,
                payout_completed_date=date(2026, 10, 7), source_row_number=4,
            ),
        ),
        source_value_issues=(), dimension_fallback_sheets=(),
        summary_lines=(StatementSummaryLine(
            "Summary", 5, "3. Total Released Amount", "TOTAL", None, Decimal("10.00"), "S$"
        ),),
    )
    audit = StatementBatchAudit(
        statement_batch_id=f"sg-pg-runtime-{token[:16]}",
        uploaded_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
        uploaded_by="sg-postgres-runtime@example.test",
        committed_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )
    return prepare_full_order_existence_statement_commit_plan(
        statement, audit=audit, full_order_ids=(order_id,), validation_passed=True,
        persisted_platform="Shopee SG", semantic_overlap_dedupe=True,
    )


def _sg_counts(repository: PostgresStatementRepository) -> tuple[int, int, int, int]:
    with repository._engine.connect() as connection:  # controlled local integration assertion
        return tuple(
            int(connection.execute(
                select(func.count()).select_from(model.__table__).where(model.platform == "Shopee SG")
            ).scalar_one())
            for model in (StatementData, StatementFinancialComponent, StatementSummary, OrderAdjustment)
        )


def _cleanup(repository: PostgresStatementRepository, plan) -> None:
    adjustment_keys = tuple(
        postgres_statement_repository.serialize_order_adjustment(event)[
            ORDER_ADJUSTMENTS_HEADERS.index("adjustment_event_fingerprint")
        ]
        for event in plan.order_adjustments
    )
    with repository._engine.begin() as connection:
        connection.execute(delete(StatementData).where(StatementData.statement_batch_id == plan.audit.statement_batch_id))
        connection.execute(delete(StatementFinancialComponent).where(
            StatementFinancialComponent.statement_batch_id == plan.audit.statement_batch_id
        ))
        connection.execute(delete(StatementSummary).where(StatementSummary.statement_batch_id == plan.audit.statement_batch_id))
        connection.execute(delete(OrderAdjustment).where(
            OrderAdjustment.adjustment_event_fingerprint.in_(adjustment_keys)
        ))


def test_sg_postgres_commit_readback_duplicate_and_adjustment_dedupe() -> None:
    repository = _local_sg_repository_or_skip()
    before = _sg_counts(repository)
    plan = _sg_plan()
    try:
        repository.write_statement_batch(plan)
        assert _sg_counts(repository) == (
            before[0] + len(plan.rows), before[1] + len(plan.financial_component_rows),
            before[2] + len(plan.summary_rows), before[3] + len(plan.order_adjustments),
        )
        with pytest.raises(StatementCommitBlocked, match="ALREADY_IMPORTED / NO_NEW_FACTS"):
            repository.write_statement_batch(plan)
        assert _sg_counts(repository) == (
            before[0] + len(plan.rows), before[1] + len(plan.financial_component_rows),
            before[2] + len(plan.summary_rows), before[3] + len(plan.order_adjustments),
        )
    finally:
        _cleanup(repository, plan)
    assert _sg_counts(repository) == before


@pytest.mark.parametrize("fail_on_insert", (1, 2, 3, 4))
def test_sg_postgres_writer_rolls_back_each_four_domain_insert_failure(
    monkeypatch: pytest.MonkeyPatch, fail_on_insert: int
) -> None:
    repository = _local_sg_repository_or_skip()
    before = _sg_counts(repository)
    monkeypatch.setattr(
        repository, "_read_state", lambda _connection: StatementCommitState(
            orders={}, items=(), committed_statements=(), order_adjustments=(),
            committed_statement_rows=(), committed_financial_component_rows=(),
        )
    )
    original = postgres_statement_repository._insert_values
    calls = 0

    def fail_at_requested_domain(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_on_insert:
            raise SQLAlchemyError(f"injected SG failure at Statement domain {calls}")
        return original(*args, **kwargs)

    monkeypatch.setattr(postgres_statement_repository, "_insert_values", fail_at_requested_domain)
    with pytest.raises(StatementCommitBlocked, match="rolled back"):
        repository.write_statement_batch(_sg_plan())
    assert _sg_counts(repository) == before


def test_sg_postgres_readback_mismatch_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _local_sg_repository_or_skip()
    before = _sg_counts(repository)
    monkeypatch.setattr(
        postgres_statement_repository, "_verify_inserted",
        lambda _connection, _plan: (_ for _ in ()).throw(
            StatementCommitBlocked("injected full-field readback mismatch")
        ),
    )

    with pytest.raises(StatementCommitBlocked, match="readback mismatch"):
        repository.write_statement_batch(_sg_plan())
    assert _sg_counts(repository) == before


def test_sg_statement_advisory_lock_is_market_specific() -> None:
    assert postgres_statement_repository._STATEMENT_ADVISORY_LOCKS == {
        "Shopee": "invoicegather:my-statement",
        "Shopee SG": "invoicegather:sg-statement",
    }


def test_sg_advisory_lock_serializes_only_competing_sg_writers() -> None:
    repository = _local_sg_repository_or_skip()
    holder_acquired = Event()
    release_holder = Event()
    waiter_acquired = Event()
    elapsed: list[float] = []

    def hold_lock() -> None:
        with repository._engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                    "key": "invoicegather:sg-statement",
                })
                holder_acquired.set()
                assert release_holder.wait(timeout=5)

    def await_lock() -> None:
        assert holder_acquired.wait(timeout=5)
        started = monotonic()
        with repository._engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                    "key": "invoicegather:sg-statement",
                })
        elapsed.append(monotonic() - started)
        waiter_acquired.set()

    holder = Thread(target=hold_lock)
    waiter = Thread(target=await_lock)
    holder.start()
    assert holder_acquired.wait(timeout=5)
    waiter.start()
    assert not waiter_acquired.wait(timeout=0.2)
    release_holder.set()
    holder.join(timeout=5)
    waiter.join(timeout=5)

    assert not holder.is_alive()
    assert not waiter.is_alive()
    assert waiter_acquired.is_set()
    assert elapsed and elapsed[0] >= 0.2
