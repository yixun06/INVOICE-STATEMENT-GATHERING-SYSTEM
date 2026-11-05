from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.invoice_app.services.financial_fact_poc import (assert_test_target, fact_from_projection,
    read_checked_facts, FactSnapshot, build_fact_report)
from src.invoice_app.services.shopee_order_financial_projection import OrderFinancialProjectionState
from test_shopee_order_financial_projection import _component


def test_fact_keeps_signed_charges_credits_refunds_and_component_presence():
    components = (_component('A', 'Product Price', '10', 1),
                  _component('A', 'Fee', '-3', 2), _component('A', 'Fee', '1', 3),
                  _component('A', 'Refund Amount', '-2', 4), _component('A', 'Zero', '0', 5))
    projection = SimpleNamespace(market='shopee_my', order_id='A', order_created_date=date(2026,1,1),
        settlement_state=OrderFinancialProjectionState.AVAILABLE, payout_completed_date=None,
        released_amount=Decimal('6'), financial_components=components)
    fact = fact_from_projection(projection)
    assert (fact.charges_amount, fact.credits_amount, fact.refund_amount, fact.non_product_amount) == (
        Decimal('-3'), Decimal('1'), Decimal('-2'), Decimal('-4'))
    assert fact.order_component_count == 5


def test_poc_refuses_other_database_before_executing_sql():
    engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=5433, database='invoicegather'))
    class ForbiddenConnection:
        def execute(self, _):
            raise AssertionError('Must reject before executing SQL')
    with pytest.raises(RuntimeError, match='Refusing'):
        assert_test_target(engine, ForbiddenConnection())


def test_poc_baseline_refuses_even_authorized_server_before_connecting(monkeypatch):
    from src.invoice_app.services.financial_fact_poc import prepare_isolated_facts
    monkeypatch.setenv('INVOICEGATHER_FACT_SERVER_ENABLED', '1')
    monkeypatch.delenv('INVOICEGATHER_ATOMICITY_TEST_MODE', raising=False)
    monkeypatch.setenv('INVOICEGATHER_PG_HOST', '127.0.0.1')
    monkeypatch.setenv('INVOICEGATHER_PG_PORT', '55434')
    monkeypatch.setenv('INVOICEGATHER_PG_DATABASE', 'invoicegather_uat2_import')
    engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=55434, database='invoicegather_uat2_import'))
    with pytest.raises(RuntimeError, match='Server baseline is not certified'):
        prepare_isolated_facts(engine)


def test_isolated_upload_reader_checks_database_and_schema(monkeypatch):
    monkeypatch.setenv('INVOICEGATHER_ATOMICITY_TEST_MODE', '1')
    engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=5433, database='invoicegather_fact_atomicity_test'))
    class Connection:
        def __init__(self, schema): self.schema = schema
        def execute(self, _): return SimpleNamespace(one=lambda: ('invoicegather_fact_atomicity_test', self.schema))
    assert_test_target(engine, Connection('ig_real_upload_uat'))
    with pytest.raises(RuntimeError, match='schema'):
        assert_test_target(engine, Connection('public'))
    engine.url.database = 'invoicegather_perf_scale'
    with pytest.raises(RuntimeError, match='target'):
        assert_test_target(engine, Connection('ig_real_upload_uat'))


def test_stale_revision_is_rejected_without_reading_any_facts(monkeypatch):
    from src.invoice_app.services.market_context import SHOPEE_MY
    monkeypatch.setattr('src.invoice_app.services.financial_fact_poc.validate_revision_infrastructure', lambda c: None)
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
        def one_or_none(self): return self.value
    class Connection:
        engine = SimpleNamespace(url=SimpleNamespace(host='127.0.0.1', port=5433, database='invoicegather_perf_scale'))
        def execute(self, query, params=None):
            sql = str(query)
            if 'current_database()' in sql: return Result('invoicegather_perf_scale')
            if 'transaction_isolation' in sql: return Result('repeatable read')
            if 'transaction_read_only' in sql: return Result('on')
            if 'analysis_source_revision' in sql: return Result((3, 2))
            raise AssertionError('Must reject stale data before reading facts')
    with pytest.raises(RuntimeError, match='stale'):
        read_checked_facts(Connection(), SHOPEE_MY)


def test_fact_snapshot_exposes_filter_options_without_native_components():
    from tests.test_shopee_my_live_analysis import _snapshot
    from src.invoice_app.services.shopee_analysis import AnalysisDateBasis, available_fee_types, available_statuses
    source = _snapshot()
    fact = FactSnapshot(source.market, source.current, tuple(fact_from_projection(p) for p in source.projections),
                        (), available_fee_types(source), 1)
    assert fact.presentation == source.presentation
    assert available_statuses(fact) == available_statuses(source)
    assert available_fee_types(fact) == available_fee_types(source)
    for basis in AnalysisDateBasis:
        assert fact.available_dates(basis) == source.available_dates(basis)


@pytest.mark.parametrize('market,currency,platform', [('shopee_my','MYR','Shopee'), ('shopee_sg','SGD','Shopee SG')])
@pytest.mark.parametrize('basis', ['ORDER_CREATED', 'PAYOUT'])
@pytest.mark.parametrize('extra', [{}, {'has_refund': True}, {'has_refund': False},
    {'fee_types': frozenset({'Transaction Fee'})}, {'product_keys': frozenset({'5000001|RESOLVED-1|55.00'})}])
def test_fact_report_preserves_existing_financial_results(market, currency, platform, basis, extra):
    from dataclasses import fields, replace
    from tests.test_shopee_my_live_analysis import _snapshot
    from src.invoice_app.services.shopee_analysis import (AnalysisDateBasis, ShopeeAnalysisFilters,
        build_shopee_analysis)
    source = _snapshot(market=market, currency=currency)
    event = replace(source.projections[0].adjustment_events[0], platform=platform)
    source = replace(source, projections=(replace(source.projections[0], adjustment_events=(event,)),))
    snap = FactSnapshot(market, source.current, tuple(fact_from_projection(p) for p in source.projections),
        (event,), ('Transaction Fee',), 1)
    if 'product_keys' in extra:
        from src.invoice_app.services.shopee_analysis import product_options
        extra = {'product_keys': frozenset((product_options(source)[0].key,))}
    filters = ShopeeAnalysisFilters(getattr(AnalysisDateBasis, basis), date(2026,10,1), date(2026,10,31), **extra)
    expected = build_shopee_analysis(source, filters)
    breakdown = tuple((c.component_name,c.amount) for c in expected.component_totals)
    actual = build_fact_report(snap, filters, matching_fee_orders={'MY-1'}, breakdown=breakdown)
    assert actual.products == expected.products and actual.component_totals == expected.component_totals
    assert len(actual.rows) == len(expected.rows)
    for a,b in zip(actual.rows,expected.rows):
        for f in fields(a):
            if f.name not in ('component_totals','financial_components'):
                assert getattr(a,f.name) == getattr(b,f.name), f.name
