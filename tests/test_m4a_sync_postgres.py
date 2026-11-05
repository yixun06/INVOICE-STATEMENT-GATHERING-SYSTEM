"""Explicitly gated M4-A test using synthetic data in the disposable DB."""
import os
from dataclasses import replace
import pytest
from sqlalchemy import text
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection
from src.invoice_app.services.incremental_financial_fact import sync_test_facts
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_full_order_adapter import full_order_snapshot_hash
from src.invoice_app.services.shopee_order_financial_projection import read_order_financial_projections
from tests.test_postgres_full_order_runtime import _batch


@pytest.fixture
def engine():
    if os.environ.get('INVOICEGATHER_M4A_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicitly configured disposable M4-A test')
    value = PostgresRuntimeConfig.from_environment().engine()
    assert (value.url.host, value.url.port, value.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with value.connect() as connection:
        assert connection.execute(text('SELECT current_database(), current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm4a_gate')
    return value


def market_batch(context, subtotal='12.00'):
    batch = _batch(subtotal=subtotal)
    market = context.key.value
    items = tuple(replace(item, market=market) for item in batch.items)
    order = replace(batch.orders[0], market=market)
    order = replace(order, snapshot_hash=full_order_snapshot_hash(market=market, order_id=order.order_id,
        order_status=order.order_status, order_created_date=order.order_created_date, items=list(items)))
    return replace(batch, orders=(order,), items=items)


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_sync_exact_parity_atomic_commit_and_rollback(engine, context):
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    market = context.key.value
    assert repo.sync(market_batch(context)).new_count == 1
    legacy = read_order_financial_projections(market_context=context, full_order_repository=repo,
        statement_reader=PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform))
    expected = fact_from_projection(legacy[0])
    with engine.begin() as connection:
        outcome = sync_test_facts(connection, context)
        assert outcome['affected_orders'] == 1
    with engine.connect() as connection:
        row = connection.execute(text('SELECT * FROM analysis_order_financial_fact WHERE market=:market'), {'market': market}).mappings().one()
        assert OrderFact(**row) == expected
        source = connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:market'), {'market': market}).scalar_one()
        assert connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market'), {'market': market}).scalar_one() == source
        assert connection.execute(text('SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:market'), {'market': market}).scalar_one() == 0
        other = SHOPEE_SG.key.value if market == SHOPEE_MY.key.value else SHOPEE_MY.key.value
        other_before = connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market'), {'market': other}).scalar_one()
    assert repo.sync(market_batch(context, '11.00')).updated_count == 1
    with engine.connect() as connection:
        pending_before = connection.execute(text('SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:market'), {'market': market}).scalar_one()
    with pytest.raises(RuntimeError, match='forced rollback'):
        with engine.begin() as connection:
            sync_test_facts(connection, context)
            raise RuntimeError('forced rollback')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market'), {'market': market}).scalar_one() == source
        assert OrderFact(**connection.execute(text('SELECT * FROM analysis_order_financial_fact WHERE market=:market'), {'market': market}).mappings().one()) == expected
        assert connection.execute(text('SELECT count(*) FROM analysis_financial_fact_pending WHERE market=:market'), {'market': market}).scalar_one() == pending_before
        assert connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market'), {'market': other}).scalar_one() == other_before
