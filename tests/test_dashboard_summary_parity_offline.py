from dataclasses import fields, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from collections import defaultdict
from src.invoice_app.domain.order_adjustment import make_statement_adjustment
import pickle
import pytest

from test_shopee_order_financial_projection import (_order, _item, _order_fact, _component,
    _PostgresFullOrderRepository, _PostgresStatementReader)
from src.invoice_app.repositories.shopee_full_order_current_repository import FullOrderCurrentSnapshot
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.google_sheets_statement_writer import CommittedStatementFinancialFacts
from src.invoice_app.services.dashboard_financial_summary import DashboardComponentSummary
from src.invoice_app.services.shopee_analysis import (AnalysisDateBasis, ShopeeAnalysisFilters,
    build_shopee_analysis, load_shopee_analysis_snapshot)

@pytest.mark.parametrize("context", [SHOPEE_MY, SHOPEE_SG])
@pytest.mark.parametrize("basis", list(AnalysisDateBasis))
def test_summary_financial_differential_and_workload(context, basis):
    market=context.key.value
    orders=tuple(_order(x, market=market, created=date(2026,10,2)) for x in ("A","B","C","D"))
    items=tuple(_item(x,0,market=market) for x in ("A","B","C","D"))
    facts=(_order_fact("A",released="9.00",payout=date(2026,10,7)),
           _order_fact("B",released="3.00",payout=date(2026,10,7)),
           _order_fact("B",released="3.00",payout=date(2026,10,7)),
           _order_fact("C",released="4.00",payout=date(2026,10,7)),
           _order_fact("C",released="5.00",payout=date(2026,10,7)))
    native=tuple(_component("A",name,amount,i) for i,(name,amount) in enumerate(
        [("Commission Fee", "-0.01")]*100 + [("Commission Fee", "0.01")]*30 +
        [("Refund Amount", "-0.02")]*20 + [("Product Price", "1.00")]*10, 1))
    native += (replace(_component("A", "Unlinked", "2.00", 999), order_id=None),)
    adjustment=make_statement_adjustment(linked_order_id="A", adjustment_description="Refund Adjustment",
        adjustment_reason="synthetic", adjustment_complete_date=date(2026,10,8), adjustment_amount=Decimal("-1.25"),
        payout_completed_date=date(2026,10,7), statement_batch_id="batch-1", statement_sequence_no="9",
        statement_source_filename="synthetic", statement_file_hash="a"*64,
        first_observed_at=datetime(2026,10,28,tzinfo=timezone.utc), persisted_platform=context.persisted_platform)
    grouped=defaultdict(list)
    for c in native:
        grouped[(c.order_id,c.record_type,c.component_name,(c.component_amount>0)-(c.component_amount<0))].append(c.component_amount)
    summaries=tuple(DashboardComponentSummary(*key, sum(values, Decimal("0")),len(values)) for key,values in grouped.items())
    def snap(components):
        reader=_PostgresStatementReader(context.persisted_platform,
            CommittedStatementFinancialFacts(context.persisted_platform, facts, components), (adjustment,))
        return load_shopee_analysis_snapshot(market_context=context,
            full_order_repository=_PostgresFullOrderRepository(FullOrderCurrentSnapshot(orders,items)), statement_reader=reader)
    old,new=snap(native),snap(summaries)
    for extra in ({}, {"order_id_query":"A"}, {"has_refund":True}, {"has_adjustment":True}, {"fee_types":frozenset({"Commission Fee"})}):
        filters=ShopeeAnalysisFilters(basis,date(2026,10,1),date(2026,10,31),**extra)
        left,right=build_shopee_analysis(old,filters),build_shopee_analysis(new,filters)
        assert left.products == right.products
        assert len(left.rows)==len(right.rows)
        for a,b in zip(left.rows,right.rows):
            for f in fields(a):
                if f.name != "financial_components":
                    assert getattr(a,f.name)==getattr(b,f.name),f.name
    assert len(native)==161 and len(summaries)==5
    assert len(pickle.dumps(new)) < len(pickle.dumps(old))
