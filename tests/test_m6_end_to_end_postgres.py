"""M6 synthetic workflow verification; explicitly gated disposable DB only."""
import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import text
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services import financial_fact_poc
from src.invoice_app.services.dashboard_financial_summary import load_optimized_snapshot, read_checked_order_details, read_native_order_details
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection, load_fact_snapshot, build_checked_fact_report
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_analysis import AnalysisDateBasis, ShopeeAnalysisFilters, available_fee_types, build_shopee_analysis, product_options
from src.invoice_app.services.shopee_full_order_adapter import full_order_snapshot_hash
from src.invoice_app.services.shopee_order_financial_projection import OrderFinancialProjectionState
from src.invoice_app.services.shopee_statement_persistence import prepare_full_order_existence_statement_commit_plan
from tests.test_m4a_sync_postgres import market_batch
from tests.test_m5_statement_fact_sync_postgres import snapshot
from tests.test_postgres_my_statement_cutover import _rollback_control_plan
from tests.test_postgres_sg_statement_cutover import _sg_plan


@pytest.fixture
def engine(monkeypatch):
    if os.environ.get('INVOICEGATHER_M6_DISPOSABLE_TEST') != '1':
        pytest.skip('requires explicit isolated M6 PostgreSQL test')
    engine = PostgresRuntimeConfig.from_environment().engine()
    assert (engine.url.host, engine.url.port, engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
    with engine.connect() as c:
        assert c.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm6_acceptance_gate_v3')
    monkeypatch.setenv('INVOICEGATHER_TEST_FULL_ORDER_FACT_SYNC', '1')
    monkeypatch.setenv('INVOICEGATHER_TEST_STATEMENT_FACT_SYNC', '1')
    def guard(db_engine, connection):
        assert (db_engine.url.host, db_engine.url.port, db_engine.url.database) == ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test')
        assert connection.execute(text('select current_database(),current_schema()')).one() == ('invoicegather_fact_atomicity_test', 'm6_acceptance_gate_v3')
    monkeypatch.setattr(financial_fact_poc, 'assert_test_target', guard)
    return engine


def report_equals(actual, expected):
    assert actual.products == expected.products
    assert actual.component_totals == expected.component_totals
    assert tuple(replace(row, component_totals=(), financial_components=()) for row in actual.rows) == tuple(
        replace(row, component_totals=(), financial_components=()) for row in expected.rows)
    for field in ('product_sales', 'total_orders', 'units_sold', 'released_amount', 'statement_charges',
                  'statement_credits', 'refund_amount', 'adjustment_total', 'reconciliation_counts', 'settlement_difference'):
        assert getattr(actual, field) == getattr(expected, field), field


@pytest.mark.parametrize('context', [SHOPEE_MY, SHOPEE_SG])
def test_full_order_statement_fact_report_and_inspector(engine, context):
    market = context.key.value
    other = SHOPEE_SG.key.value if context == SHOPEE_MY else SHOPEE_MY.key.value
    plan = _rollback_control_plan() if context == SHOPEE_MY else _sg_plan()
    fee_name = 'Transaction Fee (Incl. SST)' if context == SHOPEE_MY else 'Transaction Fee (Incl. Gst)'
    income_rows = tuple(replace(row, financial_components={
        'Product Price': Decimal('10.00'), fee_name: Decimal('-1.00'),
        'Shipping Rebate From Shopee': Decimal('2.00'), 'Refund Amount': Decimal('-1.00'),
    }) for row in plan.statement.income_rows)
    statement_fixture = replace(plan.statement, income_rows=income_rows,
        income_component_columns=('Product Price', fee_name, 'Shipping Rebate From Shopee', 'Refund Amount'))
    plan = prepare_full_order_existence_statement_commit_plan(statement_fixture, audit=plan.audit,
        full_order_ids=tuple(row.order_id for row in statement_fixture.order_rows), validation_passed=True,
        persisted_platform=context.persisted_platform, semantic_overlap_dedupe=True)
    identity = plan.statement.order_rows[0].order_id
    batch = market_batch(context)
    items = tuple(replace(item, order_id=identity) for item in batch.items)
    order = replace(batch.orders[0], order_id=identity)
    order = replace(order, snapshot_hash=full_order_snapshot_hash(market=market, order_id=identity,
        order_status=order.order_status, order_created_date=order.order_created_date, items=list(items)))
    repository = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    statement = PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform)
    with engine.connect() as c:
        other_before = snapshot(c, other)
    assert repository.sync(replace(batch, orders=(order,), items=items)).new_count == 1
    pending = load_fact_snapshot(engine=engine, market_context=context)
    assert len(pending.facts) == 1 and pending.facts[0].settlement_state == 'MISSING_STATEMENT'
    assert pending.facts[0].released_amount is None
    pending_legacy = load_optimized_snapshot(market_context=context, full_order_repository=repository, statement_reader=statement, compact_summary=True)
    pending_filters = ShopeeAnalysisFilters(AnalysisDateBasis.ORDER_CREATED, date(2026, 10, 1), date(2026, 10, 31),
        settlement_states=frozenset((OrderFinancialProjectionState.MISSING_STATEMENT,)))
    report_equals(build_checked_fact_report(engine, context, pending, pending_filters), build_shopee_analysis(pending_legacy, pending_filters))
    assert statement.write_statement_batch(plan) is None
    fact_snapshot = load_fact_snapshot(engine=engine, market_context=context)
    legacy = load_optimized_snapshot(market_context=context, full_order_repository=repository, statement_reader=statement, compact_summary=True)
    assert len(legacy.projections) == len(fact_snapshot.facts) == 1
    assert fact_snapshot.facts[0] == fact_from_projection(legacy.projections[0])
    fact = fact_snapshot.facts[0]
    assert fact.settlement_state == 'AVAILABLE' and fact.released_amount == 10
    assert fact.payout_completed_date == date(2026, 10, 7) and fact.order_component_count == 4
    assert (fact.charges_amount, fact.credits_amount, fact.refund_amount) == (Decimal('-1'), Decimal('2'), Decimal('-1'))
    with engine.connect() as c:
        current = snapshot(c, market)
        assert current[1] == current[2] and not current[3] and snapshot(c, other) == other_before
    fees = available_fee_types(legacy)
    products = product_options(legacy)
    assert fee_name in fees and products
    for basis in AnalysisDateBasis:
        for extra in ({}, {'product_keys': frozenset((products[0].key,))},
                      {'fee_types': frozenset((fee_name,))}, {'has_refund': False}, {'has_refund': True},
                      {'settlement_states': frozenset((OrderFinancialProjectionState.AVAILABLE,))},
                      {'settlement_states': frozenset((OrderFinancialProjectionState.MISSING_STATEMENT,))}):
            filters = ShopeeAnalysisFilters(basis, date(2026, 10, 1), date(2026, 10, 31), **extra)
            actual = build_checked_fact_report(engine, context, fact_snapshot, filters)
            report_equals(actual, build_shopee_analysis(legacy, filters))
            if filters.product_keys:
                assert actual.settlement_difference is None
    details = read_checked_order_details(engine, context, identity, fact_snapshot.source_revision)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
        with c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            direct = read_native_order_details(c, context, identity)
    assert details and details == direct
