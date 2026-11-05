"""Disposable PostgreSQL regression for Statement adjustment capture."""
import os
import pytest
from sqlalchemy import text
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from tests.test_postgres_my_statement_cutover import _rollback_control_plan
from tests.test_postgres_sg_statement_cutover import _sg_plan


@pytest.mark.skipif(os.environ.get('INVOICEGATHER_M3_DISPOSABLE_TEST') != '1',
                    reason='requires explicit disposable PostgreSQL test')
@pytest.mark.parametrize('market,platform,plan_factory', [
    ('shopee_my', 'Shopee', _rollback_control_plan),
    ('shopee_sg', 'Shopee SG', _sg_plan),
])
def test_statement_writer_captures_adjustment_without_unresolved(market, platform, plan_factory):
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        '127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as connection:
        assert connection.execute(text('select current_database(),current_schema()')).one() == (
            'invoicegather_fact_atomicity_test', 'm3_writer_gate')
        before = connection.execute(text('select revision from analysis_source_revision where market=:market'),
                                    {'market': market}).scalar_one()
    plan = plan_factory()
    order_id = plan.statement.order_rows[0].order_id
    PostgresStatementRepository(engine=engine, persisted_platform=platform).write_statement_batch(plan)
    with engine.connect() as connection:
        rows = connection.execute(text('''select source_revision, change_kind, order_id
            from analysis_financial_fact_pending where market=:market and source_revision>:revision'''),
            {'market': market, 'revision': before}).all()
        end = connection.execute(text('select revision from analysis_source_revision where market=:market'),
                                 {'market': market}).scalar_one()
    assert {row.source_revision for row in rows} == set(range(before + 1, end + 1))
    assert {row.change_kind for row in rows} == {'order', 'adjustment'}
    assert any(row.change_kind == 'order' and row.order_id == order_id for row in rows)
