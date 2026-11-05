"""Disposable DB recovery test; requires the isolated M4-B1 harness."""
import os
import subprocess
import sys
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services import incremental_financial_fact as sync_module
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_order_financial_projection import read_order_financial_projections
from tests.test_m4a_sync_postgres import market_batch


@pytest.fixture
def engine():
    if os.environ.get('INVOICEGATHER_M4B1_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicit disposable recovery test')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as c:
        assert c.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm4b1_gate_v2')
    return engine


def state(c, market):
    return (c.execute(text('select * from analysis_order_financial_fact where market=:m order by order_id'), {'m': market}).all(),
            c.execute(text('select source_revision from analysis_financial_fact_revision where market=:m'), {'m': market}).scalar_one(),
            c.execute(text('select * from analysis_financial_fact_pending where market=:m order by source_revision,order_id,change_kind'), {'m': market}).all())


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_recovery_after_failure_and_fresh_process(engine, context, monkeypatch):
    market = context.key.value
    other = SHOPEE_SG.key.value if market == SHOPEE_MY.key.value else SHOPEE_MY.key.value
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    with engine.connect() as c:
        before = state(c, market)
        other_before = state(c, other)
    assert repo.sync(market_batch(context)).new_count == 1  # source commits without sync
    with engine.connect() as c:
        stale = state(c, market)
        source_revision = c.execute(text('select revision from analysis_source_revision where market=:m'), {'m': market}).scalar_one()
    assert stale[0] == before[0] and stale[1] == before[1] and stale[2] and source_revision > stale[1]
    publisher = sync_module.publish_test_facts
    def fail_after_publish(*args, **kwargs):
        publisher(*args, **kwargs)
        raise RuntimeError('injected publication failure')
    with monkeypatch.context() as m:
        m.setattr(sync_module, 'publish_test_facts', fail_after_publish)
        with pytest.raises(RuntimeError, match='injected publication failure'):
            with engine.begin() as c:
                sync_module.sync_test_facts(c, context)
    with engine.connect() as c:
        assert state(c, market) == stale and state(c, other) == other_before
    # Fail after acknowledgement as well: all three writes must roll back.
    with pytest.raises(RuntimeError, match='after acknowledgement'):
        with engine.begin() as c:
            sync_module.sync_test_facts(c, context)
            raise RuntimeError('after acknowledgement')
    with engine.connect() as c:
        assert state(c, market) == stale and state(c, other) == other_before
    # A genuinely new Python process performs recovery using only persisted state.
    child = r'''import os,sys
from sqlalchemy import create_engine,text
from sqlalchemy.engine import URL
from src.invoice_app.services.incremental_financial_fact import sync_test_facts
from src.invoice_app.services.market_context import SHOPEE_MY,SHOPEE_SG
url=URL.create('postgresql+psycopg2',username=os.environ['M4B1_DB_USER'],password=os.environ['M4B1_DB_PASSWORD'],host='127.0.0.1',port=5433,database='invoicegather_fact_atomicity_test')
assert (url.host,url.port,url.database)==('127.0.0.1',5433,'invoicegather_fact_atomicity_test')
engine=create_engine(url,connect_args={'options':'-c search_path=m4b1_gate_v2'})
with engine.begin() as c:
 assert c.execute(text('select current_database(),current_schema()')).one()==('invoicegather_fact_atomicity_test','m4b1_gate_v2')
 assert sync_test_facts(c,SHOPEE_MY if sys.argv[1]=='shopee_my' else SHOPEE_SG)['affected_orders']==1
'''
    result = subprocess.run([sys.executable, '-c', child, market], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr[-2000:]
    legacy = read_order_financial_projections(market_context=context, full_order_repository=repo,
        statement_reader=PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform))
    with engine.connect() as c:
        recovered = state(c, market)
        assert len(recovered[0]) == 1 and OrderFact(**c.execute(text('select * from analysis_order_financial_fact where market=:m'), {'m': market}).mappings().one()) == fact_from_projection(legacy[0])
        assert recovered[1] == source_revision and not recovered[2] and state(c, other) == other_before
    writes = []
    def record(_conn, _cursor, sql, _params, _ctx, _many):
        if sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            writes.append(sql.split()[0])
    event.listen(engine, 'before_cursor_execute', record)
    try:
        with engine.begin() as c:
            assert sync_module.sync_test_facts(c, context)['affected_orders'] == 0
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert writes == []
    with engine.connect() as c:
        assert state(c, market) == recovered and state(c, other) == other_before


def test_unresolved_capture_is_fail_closed(engine):
    context = SHOPEE_MY
    market = context.key.value
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    assert repo.sync(market_batch(context, '11.00')).updated_count == 1
    with engine.connect() as c:
        before = state(c, market)
        other_before = state(c, SHOPEE_SG.key.value)
    assert before[2]
    with pytest.raises(DBAPIError, match='Unresolved fact capture'):
        with engine.begin() as c:
            c.execute(text('''update analysis_financial_fact_pending set change_kind='unresolved'
                where market=:m and source_revision=(select min(source_revision)
                from analysis_financial_fact_pending where market=:m)'''), {'m': market})
            sync_module.sync_test_facts(c, context)
    with engine.connect() as c:
        assert state(c, market) == before and state(c, SHOPEE_SG.key.value) == other_before
