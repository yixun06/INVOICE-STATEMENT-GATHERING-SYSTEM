from dataclasses import astuple
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from src.invoice_app.pg_orm.pg_models import StatementFinancialComponent as Component
from src.invoice_app.services.dashboard_financial_summary import (
    CompactComponentSummary, read_compact_component_summaries, read_component_summaries,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_analysis import AnalysisDateBasis
from src.invoice_app.ui.analysis_session_snapshot import get_session_snapshot
from test_analysis_session_snapshot_offline import Loader, make
import test_dashboard_summary_parity_offline as parity


@pytest.mark.parametrize("market", [SHOPEE_MY, SHOPEE_SG])
def test_compact_reader_preserves_all_six_fields(market):
    engine = create_engine("sqlite:///:memory:")
    Component.__table__.create(engine)
    rows = []
    for i, (amount, order, kind) in enumerate(((Decimal('-2.00'), 'A', 'ORDER'),
            (Decimal('1.00'), 'A', 'ORDER'), (None, None, 'SKU')), 1):
        rows.append(dict(id=i, statement_batch_id='synthetic', statement_source_sheet='Income',
            statement_source_row_number=i, statement_file_hash='synthetic', record_type=kind,
            platform=market.persisted_platform, order_id=order, component_name='Fee',
            component_amount=amount, commit_status='COMMITTED'))
    with engine.begin() as connection:
        connection.execute(Component.__table__.insert(), rows)
    with engine.connect() as connection:
        original = read_component_summaries(connection, market)
        compact = read_compact_component_summaries(connection, market)
    assert {astuple(row) for row in original} == set(compact)
    assert all(isinstance(row, CompactComponentSummary) for row in compact)
    assert any(row.order_id is None and row.amount is None for row in compact)
    engine.dispose()


@pytest.mark.parametrize("market", [SHOPEE_MY, SHOPEE_SG])
@pytest.mark.parametrize("basis", list(AnalysisDateBasis))
def test_compact_projection_report_matches_native(monkeypatch, market, basis):
    monkeypatch.setattr(parity, 'DashboardComponentSummary', CompactComponentSummary)
    parity.test_summary_financial_differential_and_workload(market, basis)


def test_session_cache_variant_switch_does_not_reuse_snapshot():
    state = {}
    first, second = Loader(make()), Loader(make(token='compact'))
    def call(loader, variant):
        return get_session_snapshot(state, market='shopee_my', target='db', loader=loader,
            read_current_revision=lambda: 1, now=lambda: 100, variant=variant)
    assert call(first, False).cache_token == 'a'
    assert call(first, False).cache_token == 'a' and first.calls == 1
    assert call(second, True).cache_token == 'compact' and second.calls == 1
