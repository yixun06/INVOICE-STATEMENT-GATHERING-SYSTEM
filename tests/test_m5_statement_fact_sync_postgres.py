"""Opt-in Statement/Fact integration in an isolated disposable PostgreSQL schema."""
import os
from dataclasses import replace
import pytest
from sqlalchemy import text
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services import incremental_financial_fact as fact_sync
from src.invoice_app.services import financial_fact_poc
from src.invoice_app.services import postgres_statement_repository as statement_module
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_full_order_adapter import full_order_snapshot_hash
from src.invoice_app.services.shopee_order_financial_projection import read_order_financial_projections
from src.invoice_app.services.shopee_statement_persistence import StatementCommitBlocked
from tests.test_m4a_sync_postgres import market_batch
from tests.test_postgres_my_statement_cutover import _rollback_control_plan
from tests.test_postgres_sg_statement_cutover import _sg_plan


@pytest.fixture
def engine(monkeypatch):
    if os.environ.get('INVOICEGATHER_M5_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicit isolated M5 PostgreSQL test')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as c:
        assert c.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm5_statement_gate')
    monkeypatch.setenv('INVOICEGATHER_TEST_STATEMENT_FACT_SYNC', '1')
    return engine


def snapshot(connection, market):
    return (connection.execute(text('select * from analysis_order_financial_fact where market=:m order by order_id'), {'m': market}).all(),
            connection.execute(text('select revision from analysis_source_revision where market=:m'), {'m': market}).scalar_one(),
            connection.execute(text('select source_revision from analysis_financial_fact_revision where market=:m'), {'m': market}).scalar_one(),
            connection.execute(text('select * from analysis_financial_fact_pending where market=:m order by source_revision,order_id,change_kind'), {'m': market}).all())


def prepare(engine, context):
    plan = _rollback_control_plan() if context == SHOPEE_MY else _sg_plan()
    identity = plan.statement.order_rows[0].order_id
    batch = market_batch(context)
    items = tuple(replace(item, order_id=identity) for item in batch.items)
    order = replace(batch.orders[0], order_id=identity)
    order = replace(order, snapshot_hash=full_order_snapshot_hash(market=context.key.value, order_id=identity,
        order_status=order.order_status, order_created_date=order.order_created_date, items=list(items)))
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    assert repo.sync(replace(batch, orders=(order,), items=items)).new_count == 1
    with engine.begin() as c:
        fact_sync.sync_test_facts(c, context)
    return plan, repo


def assert_legacy_parity(engine, context, repo, identity):
    legacy = read_order_financial_projections(market_context=context, full_order_repository=repo,
        statement_reader=PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform), order_ids=(identity,))
    assert len(legacy) == 1 and legacy[0].settlement_state.value == 'AVAILABLE'
    with engine.connect() as c:
        actual = OrderFact(**c.execute(text('select * from analysis_order_financial_fact where market=:m and order_id=:id'),
                                     {'m': context.key.value, 'id': identity}).mappings().one())
    assert actual == fact_from_projection(legacy[0])
    assert actual.released_amount == 10 and actual.order_component_count >= 2


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_real_statement_commit_and_duplicate(engine, context):
    plan, repo = prepare(engine, context)
    market = context.key.value
    other = SHOPEE_SG.key.value if context == SHOPEE_MY else SHOPEE_MY.key.value
    with engine.connect() as c:
        before_other = snapshot(c, other)
    writer = PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform)
    assert writer.write_statement_batch(plan) is None
    with engine.connect() as c:
        after = snapshot(c, market)
        assert after[1] == after[2] and not after[3] and snapshot(c, other) == before_other
    assert_legacy_parity(engine, context, repo, plan.statement.order_rows[0].order_id)
    with pytest.raises(StatementCommitBlocked, match='ALREADY_IMPORTED'):
        writer.write_statement_batch(plan)
    with engine.connect() as c:
        assert snapshot(c, market) == after and snapshot(c, other) == before_other


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_postcommit_failure_keeps_source_and_retry(engine, context, monkeypatch, caplog):
    plan, repo = prepare(engine, context)
    market = context.key.value
    writer = PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform)
    with engine.connect() as c:
        before = snapshot(c, market)
    original = fact_sync.sync_test_facts
    original_publisher = fact_sync.publish_test_facts
    def fail(*args, **kwargs):
        original_publisher(*args, **kwargs)
        raise RuntimeError('synthetic sync failure')
    with monkeypatch.context() as m:
        m.setattr(fact_sync, 'publish_test_facts', fail)
        assert writer.write_statement_batch(plan) is None
    assert 'Statement committed; disposable fact sync pending retry' in caplog.text
    with engine.connect() as c:
        stale = snapshot(c, market)
        assert stale[0] == before[0] and stale[1] > stale[2] and stale[3]
        assert c.execute(text('select count(*) from statement_data where statement_batch_id=:batch'),
                         {'batch': plan.audit.statement_batch_id}).scalar_one() > 0
    def disposable_reader_guard(db_engine, connection):
        assert (db_engine.url.host, db_engine.url.port, db_engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
        assert connection.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm5_statement_gate')
    with monkeypatch.context() as m:
        m.setattr(financial_fact_poc, 'assert_test_target', disposable_reader_guard)
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            with c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'))
                with pytest.raises(RuntimeError, match='stale or unavailable'):
                    financial_fact_poc.read_checked_facts(c, context)
    with engine.begin() as c:
        original(c, context)
    with engine.connect() as c:
        final = snapshot(c, market)
        assert final[1] == final[2] and not final[3]
    assert_legacy_parity(engine, context, repo, plan.statement.order_rows[0].order_id)


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_source_rollback_never_runs_postcommit_sync(engine, context, monkeypatch):
    plan, _ = prepare(engine, context)
    market = context.key.value
    with engine.connect() as c:
        before = snapshot(c, market)
    calls = []
    monkeypatch.setattr(fact_sync, 'sync_test_facts', lambda *args: calls.append(True))
    def fail(*args):
        raise StatementCommitBlocked('synthetic source rollback')
    monkeypatch.setattr(statement_module, '_verify_inserted', fail)
    with pytest.raises(StatementCommitBlocked, match='synthetic source rollback'):
        PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform).write_statement_batch(plan)
    with engine.connect() as c:
        assert snapshot(c, market) == before
        assert c.execute(text('select count(*) from statement_data where statement_batch_id=:batch'),
                         {'batch': plan.audit.statement_batch_id}).scalar_one() == 0
    assert calls == []


def test_default_disabled_does_not_run_postcommit_sync(engine, monkeypatch):
    context = SHOPEE_MY
    plan, _ = prepare(engine, context)
    with engine.connect() as c:
        before = snapshot(c, context.key.value)
    calls = []
    monkeypatch.delenv('INVOICEGATHER_TEST_STATEMENT_FACT_SYNC', raising=False)
    monkeypatch.setattr(fact_sync, 'sync_test_facts', lambda *args: calls.append(True))
    assert PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform).write_statement_batch(plan) is None
    with engine.connect() as c:
        after = snapshot(c, context.key.value)
    assert calls == [] and after[0] == before[0] and after[1] > after[2] and after[3]
