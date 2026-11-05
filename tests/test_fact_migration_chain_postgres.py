"""Opt-in migration rehearsal; all synthetic schema changes roll back."""
import os
from pathlib import Path

import pytest
import psycopg2
from sqlalchemy import text

from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.pg_orm.pg_models import (FullOrderCurrentOrder, FullOrderCurrentItem,
    StatementData, StatementFinancialComponent, OrderAdjustment)


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    'analysis_source_revision_up.sql',
    'analysis_financial_fact_pending_up.sql',
    'analysis_financial_fact_pending_revision_up.sql',
    'analysis_financial_fact_tables_up.sql',
)


def test_populated_schema_migration_chain(monkeypatch):
    if os.environ.get('INVOICEGATHER_MIGRATION_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicitly authorized disposable PostgreSQL')
    monkeypatch.setenv('INVOICEGATHER_ATOMICITY_TEST_MODE', '1')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        '127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as c:
        assert c.execute(text('SELECT current_database()')).scalar_one() == 'invoicegather_fact_atomicity_test'
        c.rollback()
        tx = c.begin()
        try:
            c.execute(text('CREATE SCHEMA ig_fact_chain_rehearsal'))
            c.execute(text('SET LOCAL search_path TO ig_fact_chain_rehearsal'))
            for model in (FullOrderCurrentOrder, FullOrderCurrentItem, StatementData, StatementFinancialComponent, OrderAdjustment):
                model.__table__.create(c)
            c.execute(text("INSERT INTO full_order_current_orders(market,order_id) VALUES ('shopee_my','existing-my'), ('shopee_sg','existing-sg')"))
            c.execute(text("INSERT INTO statement_data(statement_batch_id,record_type,sequence_no,platform,order_id) VALUES ('my','ORDER','1','Shopee','existing-my'), ('sg','ORDER','1','Shopee SG','existing-sg')"))
            c.execute(text("INSERT INTO full_order_current_items(market,order_id,item_index) VALUES ('shopee_my','existing-my',1)"))
            c.execute(text("INSERT INTO statement_financial_components(statement_batch_id,statement_source_sheet,statement_source_row_number,component_name,platform,order_id) VALUES ('my','synthetic',1,'Fee','Shopee','existing-my')"))
            source_names = ('full_order_current_orders', 'full_order_current_items', 'statement_data', 'statement_financial_components')
            before = {name: c.exec_driver_sql(f'SELECT * FROM {name} ORDER BY 1,2').all() for name in source_names}
            for name in MIGRATIONS:
                sql = (ROOT / 'scripts' / 'migrations' / name).read_text(encoding='utf8')
                # The caller owns one encompassing test transaction; SQL bodies are unchanged.
                with c.connection.driver_connection.cursor() as cursor:
                    cursor.execute(sql.replace('BEGIN;\n', '', 1).rsplit('COMMIT;', 1)[0])
            assert before == {name: c.exec_driver_sql(f'SELECT * FROM {name} ORDER BY 1,2').all() for name in source_names}
            assert c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname=current_schema() AND tablename IN ('analysis_source_revision','analysis_financial_fact_pending','analysis_order_financial_fact','analysis_financial_fact_revision')")).scalar_one() == 4
            expected_columns = {
                'analysis_source_revision': {'market', 'revision'},
                'analysis_financial_fact_pending': {'market', 'order_id', 'change_kind', 'source_transaction', 'source_revision'},
                'analysis_financial_fact_revision': {'market', 'source_revision'},
                'analysis_order_financial_fact': {'market', 'order_id', 'order_created_date', 'settlement_state', 'payout_completed_date', 'released_amount', 'order_component_count', 'charges_amount', 'credits_amount', 'refund_amount', 'non_product_amount'},
            }
            for table, columns in expected_columns.items():
                actual = set(c.execute(text('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=:t'), {'t': table}).scalars())
                assert actual == columns
            assert c.execute(text("SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND t.tgname LIKE 'ig_revision_%' AND t.tgenabled IN ('O','A') AND position('analysis_financial_fact_pending' in pg_get_functiondef(t.tgfoid)) > 0")).scalar_one() == 20
            triggers = c.execute(text("SELECT r.relname, pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND t.tgname LIKE 'ig_revision_%'")).all()
            for table in (*source_names, 'order_adjustments'):
                definitions = [(definition, body) for name, definition, body in triggers if name == table]
                for operation in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
                    matches = [(definition, body) for definition, body in definitions if f'AFTER {operation}' in definition]
                    assert len(matches) == 1 and 'analysis_source_revision' in matches[0][1]
            assert c.execute(text("SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND indexname='ig_fact_pending_revision_identity'")).scalar_one() == 1
            assert c.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='analysis_financial_fact_pending' AND column_name IN ('market','order_id','change_kind','source_transaction','source_revision')")).scalar_one() == 5
            assert c.execute(text("SELECT count(*) FROM pg_constraint k JOIN pg_class r ON r.oid=k.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND k.contype='p' AND r.relname IN ('analysis_source_revision','analysis_order_financial_fact','analysis_financial_fact_revision')")).scalar_one() == 3
            assert c.execute(text("SELECT to_regprocedure('ig_fact_assert_capture_coverage(text,bigint,bigint)') IS NOT NULL")).scalar_one()
            assert c.execute(text('SELECT count(*) FROM analysis_order_financial_fact')).scalar_one() == 0
            assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_revision')).scalar_one() == 0
            for market, platform, order_id in (('shopee_my', 'Shopee', 'new-my'), ('shopee_sg', 'Shopee SG', 'new-sg')):
                c.execute(text("INSERT INTO statement_data(statement_batch_id,record_type,sequence_no,platform,order_id) VALUES (:m,'ORDER','2',:p,:o)"), {'m': market, 'p': platform, 'o': order_id})
                assert c.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:m'), {'m': market}).scalar_one() == 1
                assert c.execute(text('SELECT market,order_id,change_kind,source_revision FROM analysis_financial_fact_pending WHERE market=:m'), {'m': market}).one() == (market, order_id, 'order', 1)
                c.execute(text('INSERT INTO full_order_current_orders(market,order_id) VALUES (:m,:o)'), {'m': market, 'o': order_id})
                assert c.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:m'), {'m': market}).scalar_one() == 2
                assert c.execute(text('SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:m AND order_id=:o AND source_revision=2'), {'m': market, 'o': order_id}).scalar_one() == 1
            with c.begin_nested() as savepoint:
                c.execute(text("INSERT INTO full_order_current_orders(market,order_id) VALUES ('shopee_my','rolled-back')"))
                savepoint.rollback()
            assert c.execute(text("SELECT revision FROM analysis_source_revision WHERE market='shopee_my'")).scalar_one() == 2
            assert c.execute(text("SELECT count(*) FROM analysis_financial_fact_pending WHERE order_id='rolled-back'")).scalar_one() == 0
            for name in MIGRATIONS:
                with pytest.raises(psycopg2.Error, match='already exists'):
                    with c.begin_nested():
                        sql = (ROOT / 'scripts/migrations' / name).read_text(encoding='utf8')
                        with c.connection.driver_connection.cursor() as cursor:
                            cursor.execute(sql.replace('BEGIN;\n', '', 1).rsplit('COMMIT;', 1)[0])
            assert c.execute(text("SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal AND t.tgname LIKE 'ig_revision_%' AND position('analysis_financial_fact_pending' in pg_get_functiondef(t.tgfoid)) > 0")).scalar_one() == 20
        finally:
            tx.rollback()
    engine.dispose()
