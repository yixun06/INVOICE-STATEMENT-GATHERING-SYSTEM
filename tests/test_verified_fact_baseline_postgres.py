"""Explicit disposable-target acceptance for verified initial baselines."""
import os
from pathlib import Path
import threading
from decimal import Decimal

import pytest
from sqlalchemy import text

from src.invoice_app.pg_orm.pg_models import (FullOrderCurrentOrder, FullOrderCurrentItem,
    StatementData, StatementFinancialComponent, OrderAdjustment)
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.services import verified_fact_baseline as baseline
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from tests.test_fact_migration_chain_postgres import MIGRATIONS


@pytest.fixture
def db(monkeypatch):
    if os.environ.get('INVOICEGATHER_BASELINE_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicitly authorized disposable PostgreSQL')
    monkeypatch.setenv('INVOICEGATHER_ATOMICITY_TEST_MODE', '1')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    created = False
    try:
        with engine.begin() as c:
            assert c.execute(text('SELECT current_database()')).scalar_one() == 'invoicegather_fact_atomicity_test'
            assert not c.execute(text("SELECT EXISTS(SELECT FROM pg_namespace WHERE nspname='ig_fact_baseline_task2')")).scalar_one()
            c.execute(text('CREATE SCHEMA ig_fact_baseline_task2'))
            c.execute(text('SET LOCAL search_path TO ig_fact_baseline_task2'))
            for model in (FullOrderCurrentOrder, FullOrderCurrentItem, StatementData, StatementFinancialComponent, OrderAdjustment):
                model.__table__.create(c)
            for market, platform in (('shopee_my','Shopee'), ('shopee_sg','Shopee SG')):
                for suffix in ('available','missing'):
                    order = market + '-' + suffix
                    c.execute(text("INSERT INTO full_order_current_orders(market,order_id,order_created_date,last_synced_at) VALUES (:m,:o,'2026-01-01','2026-01-01T00:00:00')"), {'m': market, 'o': order})
                    c.execute(text('INSERT INTO full_order_current_items(market,order_id,item_index,quantity,unit_price,product_subtotal) VALUES (:m,:o,1,1,10,10)'), {'m': market, 'o': order})
                c.execute(text("INSERT INTO statement_data(statement_batch_id,statement_file_hash,record_type,sequence_no,platform,order_id,validation_status,commit_status,total_released_amount,payout_completed_date) VALUES (:m,:m,'ORDER','1',:p,:o,'PASSED','COMMITTED',10,'2026-01-02')"), {'m': market,'p':platform,'o':market+'-available'})
                for i, (name, amount) in enumerate((('Fee',-3),('Credit',2),('Refund Amount',-1)), 1):
                    c.execute(text("INSERT INTO statement_financial_components(statement_batch_id,statement_file_hash,record_type,statement_source_sheet,statement_source_row_number,component_name,component_amount,platform,order_id,commit_status) VALUES (:m,:m,'ORDER','synthetic',:i,:n,:a,:p,:o,'COMMITTED')"), {'m':market,'i':i,'n':name,'a':amount,'p':platform,'o':market+'-available'})
            for name in MIGRATIONS:
                sql = (Path(__file__).resolve().parents[1] / 'scripts/migrations' / name).read_text(encoding='utf8')
                with c.connection.driver_connection.cursor() as cursor:
                    cursor.execute(sql.replace('BEGIN;\n','',1).rsplit('COMMIT;',1)[0])
        created = True
        yield engine
    finally:
        if created:
            with engine.begin() as c:
                assert c.execute(text('SELECT current_database()')).scalar_one() == 'invoicegather_fact_atomicity_test'
                c.execute(text('DROP SCHEMA ig_fact_baseline_task2 CASCADE'))
        engine.dispose()


def scoped(c):
    c.execute(text('SET LOCAL search_path TO ig_fact_baseline_task2'))


def test_missing_market_and_wrong_target_fail_before_sql():
    from types import SimpleNamespace
    class Connection:
        engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=5433, database='invoicegather'))
        def in_transaction(self): return True
        def execute(self, *args): raise AssertionError('must reject before SQL')
    with pytest.raises(RuntimeError, match='Explicit market'):
        baseline.create_verified_baseline(Connection(), None)
    with pytest.raises(RuntimeError, match='Unapproved baseline target'):
        baseline.create_verified_baseline(Connection(), SHOPEE_MY)


def test_my_sg_parity_and_isolation(db):
    my_facts = None
    for context in (SHOPEE_MY, SHOPEE_SG):
        with db.begin() as c:
            scoped(c)
            if my_facts is not None:
                assert c.execute(text("SELECT * FROM analysis_order_financial_fact WHERE market='shopee_my' ORDER BY order_id")).all() == my_facts
            result = baseline.create_verified_baseline(c, context)
            assert (result['source_count'], result['fact_count'], result['mismatches'], result['revision']) == (2,2,0,0)
            assert set(c.execute(text('SELECT settlement_state FROM analysis_order_financial_fact WHERE market=:m'), {'m':context.key.value}).scalars()) == {'AVAILABLE','MISSING_STATEMENT'}
            available = c.execute(text("SELECT charges_amount,credits_amount,refund_amount FROM analysis_order_financial_fact WHERE market=:m AND settlement_state='AVAILABLE'"), {'m':context.key.value}).one()
            assert available == (Decimal('-3'), Decimal('2'), Decimal('-1'))
            assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:m'), {'m':context.key.value}).scalar_one() == 0
            if context == SHOPEE_MY:
                assert c.execute(text("SELECT count(*) FROM analysis_order_financial_fact WHERE market='shopee_sg'")).scalar_one() == 0
                my_facts = c.execute(text("SELECT * FROM analysis_order_financial_fact WHERE market='shopee_my' ORDER BY order_id")).all()
            else:
                assert c.execute(text("SELECT * FROM analysis_order_financial_fact WHERE market='shopee_my' ORDER BY order_id")).all() == my_facts
    with db.begin() as c:
        scoped(c)
        assert c.execute(text('SELECT count(*) FROM analysis_order_financial_fact')).scalar_one() == 4


def test_failure_retry_and_invalid_coverage(db, monkeypatch):
    with db.begin() as c:
        scoped(c)
        original = baseline.calculate_targeted_facts
        monkeypatch.setattr(baseline, 'calculate_targeted_facts', lambda *args: ())
        with pytest.raises(RuntimeError, match='parity mismatch'):
            baseline.create_verified_baseline(c, SHOPEE_MY)
        assert c.execute(text('SELECT count(*) FROM analysis_order_financial_fact')).scalar_one() == 0
        assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_revision')).scalar_one() == 0
        monkeypatch.setattr(baseline, 'calculate_targeted_facts', original)
        c.execute(text("INSERT INTO analysis_financial_fact_pending(market,order_id,change_kind,source_transaction,source_revision) VALUES ('shopee_my','','unresolved',1,NULL)"))
        with pytest.raises(Exception, match='Unverified historical'):
            baseline.create_verified_baseline(c, SHOPEE_MY)
        assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_pending')).scalar_one() == 1
        # Roll back the synthetic invalid record rather than deleting unverified history.
        c.rollback()
    with db.begin() as c:
        scoped(c)
        assert baseline.create_verified_baseline(c, SHOPEE_MY)['mismatches'] == 0


def test_pending_coverage_and_existing_fact_fail_closed(db):
    with db.begin() as c:
        scoped(c)
        # A covered source change is included in the baseline and acknowledged.
        c.execute(text("UPDATE full_order_current_orders SET order_status='changed' WHERE market='shopee_my' AND order_id='shopee_my-available'"))
        assert baseline.create_verified_baseline(c, SHOPEE_MY)['revision'] == 1
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE market='shopee_my'")).scalar_one() == 0
        c.execute(text("INSERT INTO statement_data(statement_batch_id,statement_file_hash,record_type,sequence_no,platform,validation_status,commit_status) VALUES ('unknown','unknown','ORDER','1','Shopee SG','PASSED','COMMITTED')"))
        with pytest.raises(Exception, match='Unresolved fact capture'):
            baseline.create_verified_baseline(c, SHOPEE_SG)
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE market='shopee_sg' AND change_kind='unresolved'")).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_revision WHERE market='shopee_sg'")).scalar_one() == 0
        c.rollback()
    with db.begin() as c:
        scoped(c)
        c.execute(text("UPDATE analysis_source_revision SET revision=1 WHERE market='shopee_sg'"))
        with pytest.raises(Exception, match='revision gap'):
            baseline.create_verified_baseline(c, SHOPEE_SG)
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_revision WHERE market='shopee_sg'")).scalar_one() == 0
        c.rollback()
    with db.begin() as c:
        scoped(c)
        c.execute(text("INSERT INTO analysis_order_financial_fact VALUES ('shopee_sg','extra','2026-01-01','MISSING_STATEMENT',NULL,NULL,0,0,0,0,0)"))
        with pytest.raises(RuntimeError, match='no existing facts'):
            baseline.create_verified_baseline(c, SHOPEE_SG)
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_revision WHERE market='shopee_sg'")).scalar_one() == 0


def test_interruption_after_fact_write_rolls_back_and_retry(db, monkeypatch):
    real_text = baseline.text
    def fail_revision(sql):
        if sql.startswith('INSERT INTO analysis_financial_fact_revision'):
            raise RuntimeError('injected before certification')
        return real_text(sql)
    with db.begin() as c:
        scoped(c)
        c.execute(text("UPDATE full_order_current_orders SET order_status='changed' WHERE market='shopee_my' AND order_id='shopee_my-available'"))
        with monkeypatch.context() as m:
            m.setattr(baseline, 'text', fail_revision)
            with pytest.raises(RuntimeError, match='injected before certification'):
                baseline.create_verified_baseline(c, SHOPEE_MY)
        assert c.execute(text('SELECT count(*) FROM analysis_order_financial_fact')).scalar_one() == 0
        assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_revision')).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE market='shopee_my' AND source_revision=1")).scalar_one() == 1
        assert baseline.create_verified_baseline(c, SHOPEE_MY)['fact_count'] == 2
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE market='shopee_my'")).scalar_one() == 0


def test_orphan_item_is_not_certified(db):
    with db.begin() as c:
        scoped(c)
        c.execute(text("INSERT INTO full_order_current_items(market,order_id,item_index,quantity,unit_price,product_subtotal) VALUES ('shopee_my','orphan',1,1,1,1)"))
        with pytest.raises(RuntimeError, match='Orphaned'):
            baseline.create_verified_baseline(c, SHOPEE_MY)
        assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_revision')).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE order_id='orphan'")).scalar_one() == 1


def test_source_change_waits_then_makes_baseline_stale(db, monkeypatch):
    ready, release, attempted = threading.Event(), threading.Event(), threading.Event()
    errors, writer_pid = [], []
    original = baseline.calculate_targeted_facts
    def pause(*args):
        result = original(*args)
        ready.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(baseline, 'calculate_targeted_facts', pause)
    def run_baseline():
        try:
            with db.begin() as c:
                scoped(c)
                baseline.create_verified_baseline(c, SHOPEE_MY)
        except BaseException as error:
            errors.append(error)
    def write_source():
        try:
            with db.begin() as c:
                scoped(c)
                c.execute(text("SET LOCAL lock_timeout='10s'"))
                writer_pid.append(c.execute(text('SELECT pg_backend_pid()')).scalar_one())
                attempted.set()
                c.execute(text("UPDATE full_order_current_orders SET order_status='changed' WHERE market='shopee_my' AND order_id='shopee_my-available'"))
        except BaseException as error:
            errors.append(error)
    a = threading.Thread(target=run_baseline)
    b = threading.Thread(target=write_source)
    a.start()
    try:
        assert ready.wait(10)
        b.start()
        assert attempted.wait(10)
        # lock_timeout=0 causes an immediate, deterministic lock probe.
        with db.begin() as c:
            scoped(c)
            c.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(Exception, match='lock timeout'):
                with c.begin_nested():
                    c.execute(text("UPDATE full_order_current_orders SET order_status='probe' WHERE market='shopee_my' AND order_id='shopee_my-missing'"))
    finally:
        release.set()
        a.join(15)
        if b.ident: b.join(15)
    assert not errors and not a.is_alive() and not b.is_alive()
    with db.begin() as c:
        scoped(c)
        source, fact = c.execute(text("SELECT s.revision,f.source_revision FROM analysis_source_revision s JOIN analysis_financial_fact_revision f USING (market) WHERE s.market='shopee_my'")).one()
        assert source > fact
        assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE market='shopee_my' AND source_revision=:r"), {'r':source}).scalar_one() == 1
    from src.invoice_app.services import financial_fact_poc
    monkeypatch.setattr(financial_fact_poc, 'assert_test_target', lambda engine, c: None)
    with db.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        with c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            scoped(c)
            with pytest.raises(RuntimeError, match='stale or unavailable'):
                financial_fact_poc.read_checked_facts(c, SHOPEE_MY)
