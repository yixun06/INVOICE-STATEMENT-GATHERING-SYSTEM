"""Controlled source/sync interleavings; disposable PostgreSQL only."""
import os
import threading
import time
import pytest
from sqlalchemy import text
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services import financial_fact_poc, incremental_financial_fact as sync
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from tests.test_m4a_sync_postgres import market_batch


@pytest.fixture
def engine():
    if os.environ.get('INVOICEGATHER_M4_CONCURRENCY_TEST') != '1':
        pytest.skip('requires explicit disposable PostgreSQL concurrency test')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as c:
        assert c.execute(text('SELECT current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm4_concurrency_gate_v2')
    return engine


def revisions(c, market):
    return c.execute(text('''SELECT s.revision,f.source_revision FROM analysis_source_revision s
        JOIN analysis_financial_fact_revision f USING(market) WHERE s.market=:market'''), {'market': market}).one()


@pytest.mark.parametrize('context,pause_at', [(SHOPEE_MY, 'calculate'), (SHOPEE_SG, 'publish')])
def test_source_change_during_sync_never_appears_fresh(engine, context, pause_at, monkeypatch):
    market = context.key.value
    other = SHOPEE_SG.key.value if market == SHOPEE_MY.key.value else SHOPEE_MY.key.value
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    assert repo.sync(market_batch(context)).new_count == 1
    with engine.begin() as c:
        sync.sync_test_facts(c, context)
    with engine.begin() as c:
        c.execute(text("UPDATE full_order_current_orders SET order_created_date='2026-10-02' WHERE market=:market AND order_id='MY-PG-1'"), {'market': market})
    with engine.connect() as c:
        initial = revisions(c, market)
        other_before = revisions(c, other)
        assert initial[0] > initial[1]
    ready = threading.Event()
    release = threading.Event()
    writer_attempt = threading.Event()
    writer_pid = []
    errors = []
    result = []
    original = sync.calculate_targeted_facts if pause_at == 'calculate' else sync.publish_test_facts

    def intercept(*args, **kwargs):
        value = original(*args, **kwargs)
        ready.set()
        if not release.wait(10):
            raise AssertionError('test did not release sync transaction')
        return value

    monkeypatch.setattr(sync, 'calculate_targeted_facts' if pause_at == 'calculate' else 'publish_test_facts', intercept)

    def syncing():
        try:
            with engine.begin() as c:
                result.append(sync.sync_test_facts(c, context))
        except BaseException as error:
            errors.append(error)

    def source_writer():
        try:
            with engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout='10s'"))
                writer_pid.append(c.execute(text('SELECT pg_backend_pid()')).scalar_one())
                writer_attempt.set()
                c.execute(text("UPDATE full_order_current_orders SET order_created_date='2026-10-03' WHERE market=:market AND order_id='MY-PG-1'"), {'market': market})
        except BaseException as error:
            errors.append(error)

    sync_thread = threading.Thread(target=syncing)
    source_thread = threading.Thread(target=source_writer)
    sync_thread.start()
    try:
        assert ready.wait(10), 'sync never reached the selected synchronization point'
        source_thread.start()
        assert writer_attempt.wait(10)
        deadline = time.monotonic() + 8
        blocked = False
        while time.monotonic() < deadline:
            with engine.connect() as c:
                blocked = bool(c.execute(text('SELECT pg_blocking_pids(:pid)'), {'pid': writer_pid[0]}).scalar_one())
                # The uncommitted Fact publication must not become visible.
                current_fact = c.execute(text('SELECT order_created_date FROM analysis_order_financial_fact WHERE market=:market'), {'market': market}).scalar_one()
                assert str(current_fact) == '2026-10-01'
                assert revisions(c, market) == initial
                assert c.execute(text('SELECT order_created_date FROM full_order_current_orders WHERE market=:market'),
                                 {'market': market}).scalar_one() == '2026-10-02'
            if blocked:
                break
            threading.Event().wait(.02)
        assert blocked, 'source update did not wait on revision lock'
    finally:
        release.set()
        sync_thread.join(12)
        if source_thread.ident is not None:
            source_thread.join(12)
    assert not sync_thread.is_alive() and not source_thread.is_alive() and not errors
    assert len(result) == 1 and result[0]['revision'] == initial[0]
    with engine.connect() as c:
        final = revisions(c, market)
        assert final == (initial[0] + 1, initial[0])
        assert revisions(c, other) == other_before
        assert str(c.execute(text('SELECT order_created_date FROM analysis_order_financial_fact WHERE market=:market'), {'market': market}).scalar_one()) == '2026-10-02'
        assert c.execute(text('''SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:market
            AND source_revision=:revision AND change_kind='order' AND order_id='MY-PG-1' '''),
            {'market': market, 'revision': final[0]}).scalar_one() == 1
        assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:market AND source_revision<=:revision'),
            {'market': market, 'revision': final[1]}).scalar_one() == 0

    # Reader guard normally admits only the performance DB. Override it in this
    # test only, with the same exact disposable target checks as the fixture.
    def test_target(db_engine, connection):
        assert (db_engine.url.host, db_engine.url.port, db_engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
        assert connection.execute(text('SELECT current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm4_concurrency_gate_v2')
    monkeypatch.setattr(financial_fact_poc, 'assert_test_target', test_target)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        with c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            with pytest.raises(RuntimeError, match='stale or unavailable'):
                financial_fact_poc.read_checked_facts(c, context)
