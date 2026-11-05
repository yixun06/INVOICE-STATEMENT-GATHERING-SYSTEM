"""Opt-in Full Order/Fact hook tests in disposable PostgreSQL only."""
import os
from dataclasses import replace
from datetime import date
from uuid import uuid4
import pytest
from sqlalchemy import event, text
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services import incremental_financial_fact as fact_sync, financial_fact_poc
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_full_order_adapter import full_order_snapshot_hash
from src.invoice_app.services.shopee_order_financial_projection import read_order_financial_projections
from tests.test_m4a_sync_postgres import market_batch


@pytest.fixture
def engine(monkeypatch):
    if os.environ.get('INVOICEGATHER_M5_FULLORDER_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicit isolated M5 Full Order PostgreSQL test')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as c:
        assert c.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm5_fullorder_gate')
    monkeypatch.setenv('INVOICEGATHER_TEST_FULL_ORDER_FACT_SYNC', '1')
    return engine


def batch_for(context, identity=None, created=date(2026, 10, 1)):
    batch = market_batch(context)
    identity = identity or 'M5-FO-' + uuid4().hex[:16]
    items = tuple(replace(item, order_id=identity) for item in batch.items)
    order = replace(batch.orders[0], order_id=identity, order_created_date=created)
    order = replace(order, snapshot_hash=full_order_snapshot_hash(market=context.key.value, order_id=identity,
        order_status=order.order_status, order_created_date=created, items=list(items)))
    return replace(batch, orders=(order,), items=items)


def state(c, market):
    return (c.execute(text('select * from analysis_order_financial_fact where market=:m order by order_id'), {'m': market}).all(),
            c.execute(text('select revision from analysis_source_revision where market=:m'), {'m': market}).scalar_one(),
            c.execute(text('select source_revision from analysis_financial_fact_revision where market=:m'), {'m': market}).scalar_one(),
            c.execute(text('select * from analysis_financial_fact_pending where market=:m order by source_revision,order_id,change_kind'), {'m': market}).all())


def parity(engine, context, repo, identity):
    legacy = read_order_financial_projections(market_context=context, full_order_repository=repo,
        statement_reader=PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform), order_ids=(identity,))
    assert len(legacy) == 1
    with engine.connect() as c:
        fact = OrderFact(**c.execute(text('select * from analysis_order_financial_fact where market=:m and order_id=:id'),
            {'m': context.key.value, 'id': identity}).mappings().one())
    assert fact == fact_from_projection(legacy[0])
    return fact


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_new_update_missing_and_noop(engine, context, monkeypatch):
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    market = context.key.value
    other = SHOPEE_SG.key.value if context == SHOPEE_MY else SHOPEE_MY.key.value
    batch = batch_for(context); identity = batch.orders[0].order_id
    with engine.connect() as c: other_before = state(c, other)
    component_queries = []
    def observe(_c, _cursor, sql, params, _ctx, _many):
        normalized = ' '.join(sql.lower().split())
        if normalized.startswith('select') and 'statement_financial_components' in normalized:
            component_queries.append((normalized, params))
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        assert repo.sync(batch).new_count == 1
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert len(component_queries) == 1
    sql, params = component_queries[0]
    assert ' where ' in sql and 'order_id' in sql.split(' where ', 1)[1] and 'platform' in sql.split(' where ', 1)[1]
    assert identity in params.values() and context.persisted_platform in params.values()
    with engine.connect() as c:
        after_new = state(c, market)
        assert after_new[1] == after_new[2] and not after_new[3] and state(c, other) == other_before
    assert parity(engine, context, repo, identity).order_created_date == date(2026, 10, 1)
    calls = []
    with monkeypatch.context() as m:
        m.setattr(fact_sync, 'sync_test_facts', lambda *args: calls.append(True))
        assert repo.sync(batch).unchanged_count == 1
        assert repo.sync_missing_only(batch).new_count == 0
    with engine.connect() as c: assert not calls and state(c, market) == after_new
    updated = batch_for(context, identity, date(2026, 10, 2))
    assert repo.sync(updated).updated_count == 1
    assert parity(engine, context, repo, identity).order_created_date == date(2026, 10, 2)
    addition = batch_for(context)
    mixed = replace(batch, orders=updated.orders + addition.orders, items=updated.items + addition.items)
    assert repo.sync_missing_only(mixed).new_count == 1
    assert parity(engine, context, repo, identity).order_created_date == date(2026, 10, 2)
    assert parity(engine, context, repo, addition.orders[0].order_id).order_created_date == date(2026, 10, 1)
    with engine.connect() as c:
        final = state(c, market)
        assert final[1] == final[2] and not final[3] and state(c, other) == other_before


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
@pytest.mark.parametrize('method', ['sync', 'sync_missing_only'])
def test_postcommit_sync_failure_retains_source_and_recovers(engine, context, method, monkeypatch, caplog):
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    batch = batch_for(context); market = context.key.value
    with engine.connect() as c: before = state(c, market)
    publisher = fact_sync.publish_test_facts
    def fail(*args, **kwargs):
        publisher(*args, **kwargs)
        raise RuntimeError('synthetic fact failure')
    with monkeypatch.context() as m:
        m.setattr(fact_sync, 'publish_test_facts', fail)
        assert getattr(repo, method)(batch).new_count == 1
    assert 'Full Order committed; disposable fact sync pending retry' in caplog.text
    with engine.connect() as c:
        stale = state(c, market)
        assert stale[0] == before[0] and stale[1] > stale[2] and stale[3]
        assert c.execute(text('select count(*) from full_order_current_orders where market=:m and order_id=:id'),
                         {'m': market, 'id': batch.orders[0].order_id}).scalar_one() == 1
    def guard(db_engine, connection):
        assert (db_engine.url.host, db_engine.url.port, db_engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
        assert connection.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm5_fullorder_gate')
    with monkeypatch.context() as m:
        m.setattr(financial_fact_poc, 'assert_test_target', guard)
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
            with c.begin():
                c.execute(text('SET TRANSACTION READ ONLY'))
                with pytest.raises(RuntimeError, match='stale or unavailable'):
                    financial_fact_poc.read_checked_facts(c, context)
    with engine.begin() as c: fact_sync.sync_test_facts(c, context)
    with engine.connect() as c:
        final = state(c, market)
        assert final[1] == final[2] and not final[3]
    parity(engine, context, repo, batch.orders[0].order_id)


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
@pytest.mark.parametrize('method', ['sync', 'sync_missing_only'])
def test_source_rollback_does_not_run_sync(engine, context, method, monkeypatch):
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    batch = batch_for(context); market = context.key.value
    with engine.connect() as c: before = state(c, market)
    calls = []; original = repo._write_expected
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic source rollback')
    monkeypatch.setattr(repo, '_write_expected', fail)
    monkeypatch.setattr(fact_sync, 'sync_test_facts', lambda *args: calls.append(True))
    with pytest.raises(RuntimeError, match='synthetic source rollback'):
        getattr(repo, method)(batch)
    with engine.connect() as c:
        assert state(c, market) == before
        assert c.execute(text('select count(*) from full_order_current_orders where market=:m and order_id=:id'),
                         {'m': market, 'id': batch.orders[0].order_id}).scalar_one() == 0
    assert not calls


def test_default_disabled_does_not_run_hook(engine, monkeypatch):
    context = SHOPEE_MY
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    batch = batch_for(context)
    with engine.connect() as c: before = state(c, context.key.value)
    calls = []
    with monkeypatch.context() as m:
        m.delenv('INVOICEGATHER_TEST_FULL_ORDER_FACT_SYNC', raising=False)
        m.setattr(fact_sync, 'sync_test_facts', lambda *args: calls.append(True))
        assert repo.sync(batch).new_count == 1
    with engine.connect() as c:
        after = state(c, context.key.value)
        assert not calls and after[0] == before[0] and after[1] > after[2] and after[3]
    with engine.begin() as c: fact_sync.sync_test_facts(c, context)
