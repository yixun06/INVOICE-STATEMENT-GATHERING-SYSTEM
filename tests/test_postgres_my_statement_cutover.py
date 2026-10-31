from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import replace
from decimal import Decimal
import os
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import Integer, MetaData, Numeric, create_engine, delete, event, func, select, text
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
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig, PostgresRuntimeUnavailable
from src.invoice_app.services import postgres_statement_repository
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitBlocked,
    StatementCommitState,
    prepare_full_order_existence_statement_commit_plan,
)
from src.invoice_app.services.uat2_data_settings import UAT2DataSettings
from src.invoice_app.services.cross_platform_product_summary import (
    PostgresMyCrossPlatformProductSummaryReader,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    ORDER_ADJUSTMENTS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
    STATEMENT_DATA_TAB,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
    STATEMENT_DATA_HEADERS,
    STATEMENT_SUMMARY_TAB,
)
from src.invoice_app.services.weekly_billing import PostgresMyWeeklyBillingReader


def test_missing_postgres_credentials_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVOICEGATHER_PG_USER", raising=False)
    monkeypatch.delenv("INVOICEGATHER_PG_PASSWORD", raising=False)

    with pytest.raises(PostgresRuntimeUnavailable, match="never falls back"):
        PostgresRuntimeConfig.from_environment()


def test_invalid_postgres_target_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICEGATHER_PG_USER", "test-user")
    monkeypatch.setenv("INVOICEGATHER_PG_PASSWORD", "test-password")
    monkeypatch.setenv("INVOICEGATHER_PG_PORT", "not-a-port")

    with pytest.raises(PostgresRuntimeUnavailable, match="approved local PostgreSQL port"):
        PostgresRuntimeConfig.from_environment()


def test_my_statement_factory_is_postgres_not_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICEGATHER_PG_USER", "test-user")
    monkeypatch.setenv("INVOICEGATHER_PG_PASSWORD", "test-password")
    settings = UAT2DataSettings(
        google_spreadsheet_id="my-sheet",
        google_service_account={"type": "service_account"},
        full_order_backend="postgresql",
    )
    assert isinstance(settings.create_statement_writer(), PostgresStatementRepository)
    assert isinstance(
        settings.create_cross_platform_product_summary_reader(),
        PostgresMyCrossPlatformProductSummaryReader,
    )


def test_order_created_product_summary_factory_defers_google_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Full Order-only reader must be constructible without Invoice access."""

    monkeypatch.setenv("INVOICEGATHER_PG_USER", "test-user")
    monkeypatch.setenv("INVOICEGATHER_PG_PASSWORD", "test-password")
    settings = UAT2DataSettings(
        google_spreadsheet_id="my-sheet",
        google_credentials_path=None,
        google_service_account=None,
        full_order_backend="postgresql",
    )

    assert isinstance(
        settings.create_cross_platform_product_summary_reader(),
        PostgresMyCrossPlatformProductSummaryReader,
    )


def test_postgres_statement_runtime_accepts_explicit_sg_market() -> None:
    repository = PostgresStatementRepository(  # type: ignore[arg-type]
        engine=object(), persisted_platform="Shopee SG"
    )

    assert repository.persisted_platform == "Shopee SG"


def test_postgres_statement_runtime_rejects_unknown_market() -> None:
    with pytest.raises(ValueError, match="explicitly supported market"):
        PostgresStatementRepository(engine=object(), persisted_platform="Unknown")  # type: ignore[arg-type]


class ForbiddenLegacyStatementSheets:
    """A fake that turns any MY Statement Sheets request into a test failure."""

    def __init__(self) -> None:
        self.requested_tabs: list[tuple[str, ...]] = []

    def read_tabs(self, _spreadsheet_id: str, tabs: tuple[str, ...]):
        self.requested_tabs.append(tabs)
        assert STATEMENT_DATA_TAB not in tabs
        assert STATEMENT_FINANCIAL_COMPONENTS_TAB not in tabs
        assert STATEMENT_SUMMARY_TAB not in tabs
        assert tabs in {
            (INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB),
            (
                INVOICE_ORDERS_TAB,
                INVOICE_ITEMS_TAB,
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            ),
        }
        values = {
            INVOICE_ORDERS_TAB: [list(INVOICE_ORDERS_HEADERS)],
            INVOICE_ITEMS_TAB: [list(INVOICE_ITEMS_HEADERS)],
        }
        if SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB in tabs:
            values[SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB] = [
                list(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS)
            ]
            values[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB] = [
                list(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS)
            ]
        return values


class _PostgresStatementRows:
    def weekly_statement_rows(self):
        return {
            "statement_data": (),
            "statement_financial_components": (),
            "statement_summary": (),
        }

    def reporting_statement_data_rows(self):
        return ()


class _EmptyPostgresFullOrderRows:
    def read_snapshot(self) -> FullOrderCurrentSnapshot:
        return FullOrderCurrentSnapshot((), ())


def test_weekly_billing_never_requests_legacy_statement_sheets() -> None:
    reader = PostgresMyWeeklyBillingReader(
        spreadsheet_id="my-sheet",
        gateway=ForbiddenLegacyStatementSheets(),
        statement_repository=_PostgresStatementRows(),  # type: ignore[arg-type]
    )

    dataset = reader.load_dataset()

    assert dataset.periods == ()


def test_reporting_reader_never_requests_legacy_statement_sheets() -> None:
    gateway = ForbiddenLegacyStatementSheets()
    reader = PostgresMyCrossPlatformProductSummaryReader(
        spreadsheet_id="my-sheet",
        gateway=gateway,
        statement_repository=_PostgresStatementRows(),  # type: ignore[arg-type]
        full_order_repository=_EmptyPostgresFullOrderRows(),
    )

    snapshot = reader.load_snapshot()

    assert snapshot.eligible_items == ()
    assert gateway.requested_tabs == [(INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB)]


def _local_postgres_repository_or_skip() -> PostgresStatementRepository:
    if os.environ.get("INVOICEGATHER_PG_RUNTIME_INTEGRATION") != "1":
        pytest.skip("requires explicit local PostgreSQL runtime integration authorization")
    return PostgresStatementRepository(engine=PostgresRuntimeConfig.from_environment().engine())


def _rollback_control_plan():
    token = uuid4().hex
    order_id = f"PG-ROLLBACK-{token[:12]}"
    statement = ParsedShopeeWeeklyStatement(
        source_filename="postgres-rollback-control.xlsx",
        file_hash=token * 2,
        statement_period_from=date(2026, 10, 1),
        statement_period_to=date(2026, 10, 7),
        summary_total_released=Decimal("10.0"),
        adjustment_control_total=Decimal("-1"),
        adjustment_footer_total=None,
        income_rows=(
            SettlementIncomeRow(
                sequence_no="1", view_by="Order", order_id=order_id,
                product_id="PG-PRODUCT", product_name="PostgreSQL rollback control",
                order_creation_date=date(2026, 10, 1), payout_completed_date=date(2026, 10, 7),
                release_channel="Seller Wallet", order_type="Normal",
                total_released_amount=Decimal("10.0"),
                financial_components={
                    "Product Price": Decimal("10.0"),
                    "Refund Amount": Decimal("0"),
                },
                source_values={}, source_row_number=2,
            ),
            SettlementIncomeRow(
                sequence_no="1", view_by="SKU", order_id=order_id,
                product_id="PG-PRODUCT", product_name="PostgreSQL rollback control",
                order_creation_date=date(2026, 10, 1), payout_completed_date=date(2026, 10, 7),
                release_channel="Seller Wallet", order_type="Normal",
                total_released_amount=Decimal("10.0"),
                financial_components={
                    "Product Price": Decimal("10.0"),
                    "Refund Amount": Decimal("0"),
                },
                source_values={}, source_row_number=3,
            ),
        ),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(
            SettlementAdjustment(
                sequence_no="1", adjustment_complete_date=date(2026, 10, 7),
                adjustment_type="Adjustment", adjustment_reason="Rollback control",
                adjustment_amount=Decimal("-1.0"), linked_order_id=order_id,
                payout_completed_date=date(2026, 10, 7), source_row_number=4,
            ),
        ),
        source_value_issues=(),
        dimension_fallback_sheets=(),
        summary_lines=(
            StatementSummaryLine(
                "Summary", 5, "3. Total Released Amount", "TOTAL", None,
                Decimal("10.0"), "RM",
            ),
        ),
    )
    audit = StatementBatchAudit(
        statement_batch_id=f"pg-rollback-{token[:16]}",
        uploaded_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
        uploaded_by="postgres-runtime-test@example.test",
        committed_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )
    return prepare_full_order_existence_statement_commit_plan(
        statement,
        audit=audit,
        full_order_ids=(order_id,),
        validation_passed=True,
        persisted_platform="Shopee",
        semantic_overlap_dedupe=True,
    )


def _statement_table_counts(repository: PostgresStatementRepository) -> tuple[int, int, int, int]:
    with repository._engine.connect() as connection:  # controlled local integration assertion
        return tuple(
            int(connection.execute(select(func.count()).select_from(model.__table__)).scalar_one())
            for model in (StatementData, StatementFinancialComponent, StatementSummary, OrderAdjustment)
        )


def _readback_test_engine():
    """Isolated SQLite tables with the same fields and constraints as the ORM."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    for model in (StatementData, StatementFinancialComponent, StatementSummary, OrderAdjustment):
        table = model.__table__.to_metadata(metadata)
        if "id" in table.c:
            table.c.id.type = Integer()
    metadata.create_all(engine)
    return engine


def test_bulk_readback_verifies_four_domains_and_preserves_blank_numeric_semantics() -> None:
    engine = _readback_test_engine()
    plan = _rollback_control_plan()
    with engine.begin() as connection:
        postgres_statement_repository._insert_plan(connection, plan)
        postgres_statement_repository._verify_inserted(connection, plan)
    assert postgres_statement_repository._verification_cell(Numeric(12, 2), None) == ""
    assert postgres_statement_repository._verification_cell(Numeric(12, 2), "") == ""
    assert postgres_statement_repository._verification_cell(Numeric(12, 2), Decimal("-0.00")) == "0"


@pytest.mark.parametrize("failure", ["missing", "text", "numeric"])
def test_bulk_readback_mismatch_raises_and_rolls_back(failure: str) -> None:
    engine = _readback_test_engine()
    plan = _rollback_control_plan()
    rows = list(plan.financial_component_rows)
    if failure == "missing":
        row = list(rows[0])
        row[STATEMENT_FINANCIAL_COMPONENT_HEADERS.index("component_name")] = "absent"
    else:
        row = list(rows[0])
        field = "component_note" if failure == "text" else "component_amount"
        row[STATEMENT_FINANCIAL_COMPONENT_HEADERS.index(field)] = "changed" if failure == "text" else "999.99"
    changed = replace(plan, financial_component_rows=(tuple(row), *rows[1:]))
    with pytest.raises(StatementCommitBlocked):
        with engine.begin() as connection:
            postgres_statement_repository._insert_plan(connection, plan)
            postgres_statement_repository._verify_inserted(connection, changed)
    with engine.connect() as connection:
        for model in (StatementData, StatementFinancialComponent, StatementSummary, OrderAdjustment):
            assert connection.execute(select(func.count()).select_from(model.__table__)).scalar_one() == 0


def test_batch_readback_accepts_financial_component_numeric_scale() -> None:
    engine = _readback_test_engine()
    plan = _rollback_control_plan()
    rows = list(plan.financial_component_rows)
    amount_index = STATEMENT_FINANCIAL_COMPONENT_HEADERS.index("component_amount")
    row = list(rows[0])
    assert row[amount_index]
    row[amount_index] = str(Decimal(row[amount_index]).quantize(Decimal("0.00")))
    expected = replace(plan, financial_component_rows=(tuple(row), *rows[1:]))
    with engine.begin() as connection:
        postgres_statement_repository._insert_plan(connection, plan)
        postgres_statement_repository._verify_inserted(connection, expected)


def test_bulk_readback_unique_constraint_rejects_duplicate_identity() -> None:
    from sqlalchemy.exc import IntegrityError

    engine = _readback_test_engine()
    plan = _rollback_control_plan()
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            postgres_statement_repository._insert_plan(connection, plan)
            postgres_statement_repository._insert_values(
                connection, StatementFinancialComponent, STATEMENT_FINANCIAL_COMPONENT_HEADERS,
                (plan.financial_component_rows[0],),
            )


def test_bulk_readback_rejects_duplicate_returned_identity() -> None:
    engine = _readback_test_engine()
    plan = _rollback_control_plan()

    class DuplicateResult:
        def __init__(self, result):
            self.result = result

        def mappings(self):
            rows = list(self.result.mappings())
            return rows + rows[:1]

    class DuplicateConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement):
            return DuplicateResult(self.connection.execute(statement))

    with engine.begin() as connection:
        postgres_statement_repository._insert_plan(connection, plan)
        with pytest.raises(StatementCommitBlocked, match="stable identity"):
            postgres_statement_repository._verify_keys(
                DuplicateConnection(connection), StatementData,
                ("statement_batch_id", "record_type", "sequence_no"),
                STATEMENT_DATA_HEADERS, plan.rows,
            )


@pytest.mark.parametrize(("size", "expected_selects"), [(10, 1), (1000, 1)])
def test_bulk_component_readback_has_bounded_queries(size: int, expected_selects: int) -> None:
    engine = _readback_test_engine()
    template = _rollback_control_plan().financial_component_rows[0]
    source_row_index = STATEMENT_FINANCIAL_COMPONENT_HEADERS.index("statement_source_row_number")
    rows = []
    for index in range(size):
        row = list(template)
        row[source_row_index] = str(index + 1)
        rows.append(tuple(row))
    calls = []

    @event.listens_for(engine, "before_cursor_execute")
    def count_selects(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            calls.append(statement)

    with engine.begin() as connection:
        postgres_statement_repository._insert_values(
            connection, StatementFinancialComponent, STATEMENT_FINANCIAL_COMPONENT_HEADERS, rows
        )
        postgres_statement_repository._verify_keys(
            connection, StatementFinancialComponent,
            ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "component_name"),
            STATEMENT_FINANCIAL_COMPONENT_HEADERS, rows,
        )
    assert len(calls) == expected_selects


def test_postgres_plan_readback_verifies_all_four_domains_then_rolls_back() -> None:
    """Expected fields—not merely keys—are authoritatively readable before commit."""

    repository = _local_postgres_repository_or_skip()
    before = _statement_table_counts(repository)
    connection = repository._engine.connect()
    transaction = connection.begin()
    try:
        plan = _rollback_control_plan()
        postgres_statement_repository._insert_plan(connection, plan)
        postgres_statement_repository._verify_inserted(connection, plan)
    finally:
        transaction.rollback()
        connection.close()
    assert _statement_table_counts(repository) == before


@pytest.mark.parametrize(
    ("left", "right"),
    (
        (Decimal("0"), Decimal("0.00")),
        (Decimal("-0.00"), Decimal("0.00")),
        (Decimal("12.9"), Decimal("12.90")),
        (Decimal("107.4"), Decimal("107.40")),
        (Decimal("-6.34"), Decimal("-6.340")),
    ),
)
def test_postgres_readback_numeric_cells_ignore_insignificant_scale(
    left: Decimal, right: Decimal
) -> None:
    numeric = Numeric(12, 2)

    assert postgres_statement_repository._verification_cell(numeric, left) == (
        postgres_statement_repository._verification_cell(numeric, right)
    )


@pytest.mark.parametrize(
    ("left", "right"),
    (
        (Decimal("12.90"), Decimal("12.91")),
        (Decimal("0"), Decimal("0.01")),
        (Decimal("-6.34"), Decimal("-6.35")),
    ),
)
def test_postgres_readback_numeric_cells_reject_genuine_changes(
    left: Decimal, right: Decimal
) -> None:
    numeric = Numeric(12, 2)

    assert postgres_statement_repository._verification_cell(numeric, left) != (
        postgres_statement_repository._verification_cell(numeric, right)
    )


def test_postgres_readback_preserves_exact_non_numeric_cells() -> None:
    varchar = StatementData.__table__.c.order_id.type

    assert postgres_statement_repository._verification_cell(varchar, "ORDER-1") != (
        postgres_statement_repository._verification_cell(varchar, "ORDER-1 ")
    )


def test_postgres_full_field_numeric_mismatch_still_rolls_back() -> None:
    repository = _local_postgres_repository_or_skip()
    before = _statement_table_counts(repository)
    plan = _rollback_control_plan()
    amount_index = STATEMENT_DATA_HEADERS.index("total_released_amount")
    changed_row = list(plan.rows[0])
    changed_row[amount_index] = "10.01"
    changed_plan = replace(plan, rows=(tuple(changed_row), *plan.rows[1:]))
    connection = repository._engine.connect()
    transaction = connection.begin()
    try:
        postgres_statement_repository._insert_plan(connection, plan)
        with pytest.raises(StatementCommitBlocked, match="every expected committed fact"):
            postgres_statement_repository._verify_inserted(connection, changed_plan)
    finally:
        transaction.rollback()
        connection.close()
    assert _statement_table_counts(repository) == before


def test_postgres_scale_only_plan_can_commit_and_is_cleaned_up() -> None:
    """A generated fixture reaches COMMIT only after all four readbacks pass."""

    repository = _local_postgres_repository_or_skip()
    before = _statement_table_counts(repository)
    plan = _rollback_control_plan()
    adjustment_keys = tuple(
        postgres_statement_repository.serialize_order_adjustment(event)[
            ORDER_ADJUSTMENTS_HEADERS.index("adjustment_event_fingerprint")
        ]
        for event in plan.order_adjustments
    )
    connection = repository._engine.connect()
    transaction = connection.begin()
    try:
        postgres_statement_repository._insert_plan(connection, plan)
        postgres_statement_repository._verify_inserted(connection, plan)
        transaction.commit()
        connection.close()
        assert _statement_table_counts(repository) == (
            before[0] + len(plan.rows),
            before[1] + len(plan.financial_component_rows),
            before[2] + len(plan.summary_rows),
            before[3] + len(plan.order_adjustments),
        )
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        with repository._engine.begin() as connection:
            connection.execute(
                delete(StatementData).where(StatementData.statement_batch_id == plan.audit.statement_batch_id)
            )
            connection.execute(
                delete(StatementFinancialComponent).where(
                    StatementFinancialComponent.statement_batch_id == plan.audit.statement_batch_id
                )
            )
            connection.execute(
                delete(StatementSummary).where(
                    StatementSummary.statement_batch_id == plan.audit.statement_batch_id
                )
            )
            connection.execute(
                delete(OrderAdjustment).where(
                    OrderAdjustment.adjustment_event_fingerprint.in_(adjustment_keys)
                )
            )
    assert _statement_table_counts(repository) == before


@pytest.mark.parametrize("fail_on_insert", (1, 2, 3, 4))
def test_postgres_writer_rolls_back_each_four_domain_insert_failure(
    monkeypatch: pytest.MonkeyPatch,
    fail_on_insert: int,
) -> None:
    """Every staged-domain failure rolls back a real local transaction."""

    repository = _local_postgres_repository_or_skip()
    before = _statement_table_counts(repository)
    monkeypatch.setattr(
        repository,
        "_read_candidate_state",
        lambda _connection, _plan: StatementCommitState(
            orders={},
            items=(),
            committed_statements=(),
            order_adjustments=(),
            committed_statement_rows=(),
            committed_financial_component_rows=(),
        ),
    )
    original = postgres_statement_repository._insert_values
    calls = 0

    def fail_at_requested_domain(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_on_insert:
            raise SQLAlchemyError(f"injected failure at Statement domain {calls}")
        return original(*args, **kwargs)

    monkeypatch.setattr(postgres_statement_repository, "_insert_values", fail_at_requested_domain)

    with pytest.raises(StatementCommitBlocked, match="rolled back"):
        repository.write_statement_batch(_rollback_control_plan())

    assert _statement_table_counts(repository) == before


def test_postgres_advisory_lock_serializes_same_my_statement_evidence() -> None:
    """The database—not Streamlit session state—serializes competing MY writers."""

    repository = _local_postgres_repository_or_skip()
    holder_acquired = Event()
    release_holder = Event()
    waiter_acquired = Event()
    elapsed: list[float] = []

    def hold_lock() -> None:
        with repository._engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                    "key": "invoicegather:my-statement",
                })
                holder_acquired.set()
                assert release_holder.wait(timeout=5)

    def await_lock() -> None:
        assert holder_acquired.wait(timeout=5)
        started = monotonic()
        with repository._engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                    "key": "invoicegather:my-statement",
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
