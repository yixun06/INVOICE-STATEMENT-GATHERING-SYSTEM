from contextlib import contextmanager
from decimal import Decimal
import pytest
from sqlalchemy.dialects import postgresql
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository

class Engine:
    def __init__(self, platform): self.queries=[]; self.platform=platform
    @contextmanager
    def connect(self): yield self
    def execute(self, query):
        self.queries.append(query); return self
    def mappings(self):
        if "statement_financial_components" not in str(self.queries[-1]): return []
        return [dict(record_type="ORDER", order_id="A", component_name="Fee", component_amount=Decimal(v),
            statement_batch_id="batch", statement_file_hash="hash", statement_source_sheet="Income",
            statement_source_row_number=i, sequence_no=str(i), payout_completed_date="2026-01-01")
            for i,v in enumerate(("-5.00","2.00","-3.00"))]

@pytest.mark.parametrize("platform",["Shopee", "Shopee SG"])
def test_compact_and_legacy_read_identical_facts(monkeypatch,platform):
    engine=Engine(platform); repo=PostgresStatementRepository(engine=engine,persisted_platform=platform)
    monkeypatch.delenv("INVOICEGATHER_COMPACT_COMPONENT_READ",raising=False)
    before=repo.read_committed_financial_facts(); old=engine.queries[-1]
    monkeypatch.setenv("INVOICEGATHER_COMPACT_COMPONENT_READ","1")
    after=repo.read_committed_financial_facts(); new=engine.queries[-1]
    assert before==after and len(after.financial_components)==3
    assert len(old.selected_columns)==17+1 and len(new.selected_columns)==10
    compiled=new.compile(dialect=postgresql.dialect());assert platform in compiled.params.values()
    assert "component_amount IS NOT NULL" in str(compiled)
