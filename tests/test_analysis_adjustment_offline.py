from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import pytest

from sqlalchemy.dialects import postgresql

from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.services.shopee_order_adjustment_read import read_committed_order_adjustments
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG


class Engine:
    def __init__(self, rows=()):
        self.queries = []
        self.rows = rows

    @contextmanager
    def connect(self):
        yield self

    def execute(self, query):
        self.queries.append(query)
        return self

    def mappings(self):
        platform = list(self.queries[-1].compile(dialect=postgresql.dialect()).params.values())[0]
        return [row for row in self.rows if row['platform'] == platform]


def row(platform, order_id, amount, fingerprint, day):
    return dict(platform=platform, linked_order_id=order_id, adjustment_type="TEST",
                adjustment_description="test", adjustment_reason=None,
                adjustment_complete_date=date(2026, 1, day).isoformat(), adjustment_amount=Decimal(amount),
                payout_completed_date=None, statement_batch_id="batch", statement_sequence_no="1",
                statement_source_filename="fixture.xlsx", statement_file_hash="hash",
                adjustment_event_fingerprint=fingerprint, evidence_status="STATEMENT_ONLY",
                invoice_evidence_date=None, invoice_evidence_amount=None, invoice_evidence_final_amount=None,
                invoice_evidence_reason=None, invoice_source_pdf=None, invoice_source_hash=None,
                first_observed_at=None)


@pytest.mark.parametrize('context,platform', [(SHOPEE_MY, 'Shopee'), (SHOPEE_SG, 'Shopee SG')])
def test_adjustment_reconstruction_market_scope_and_order(context, platform):
    other = 'Shopee SG' if platform == 'Shopee' else 'Shopee'
    engine = Engine([row(platform, 'SAME', '-1.25', 'b', 2), row(platform, 'SAME', '2.50', 'a', 1),
                     row(platform, '', '0.00', 'u', 3), row(other, 'SAME', '99', 'foreign', 1)])
    repo = PostgresStatementRepository(engine=engine, persisted_platform=platform)
    repo.reload_active_commit_state = lambda: pytest.fail('full state reload')
    all_events = read_committed_order_adjustments(market_context=context, statement_reader=repo).events
    assert [e.adjustment_event_fingerprint for e in all_events] == ['a', 'b', 'u']
    assert [e.adjustment_amount for e in all_events] == [Decimal('2.50'), Decimal('-1.25'), Decimal('0')]
    assert all_events[0].statement_source_filename == 'fixture.xlsx'
    assert all_events[0].statement_file_hash == 'hash' and all_events[0].adjustment_reason is None
    assert all_events[0].payout_completed_date is None
    assert all(e.platform == platform for e in all_events)
    selected = read_committed_order_adjustments(market_context=context, statement_reader=repo, order_ids=['SAME']).events
    assert [e.adjustment_event_fingerprint for e in selected] == ['a', 'b']


def test_adjustment_query_scope_and_large_candidate_set():
    engine = Engine()
    repo = PostgresStatementRepository(engine=engine, persisted_platform="Shopee")
    assert repo.read_order_adjustments([]) == ()
    assert not engine.queries
    assert repo.read_order_adjustments(None) == ()
    assert repo.read_order_adjustments([str(i) for i in range(20000)]) == ()
    assert len(engine.queries) == 2
    for query in engine.queries:
        compiled = query.compile(dialect=postgresql.dialect())
        sql = str(compiled).lower()
        assert "order_adjustments" in sql
        assert "statement_data" not in sql and "statement_financial_components" not in sql
        assert list(compiled.params.values()) == ["Shopee"]
