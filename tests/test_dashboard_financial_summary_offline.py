from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from src.invoice_app.pg_orm.pg_models import StatementFinancialComponent as C
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.dashboard_financial_summary import (
 read_component_summaries, read_native_order_details, validate_revision_infrastructure, _REVISION_TABLES)

@pytest.mark.parametrize("context", [SHOPEE_MY, SHOPEE_SG])
def test_summary_signs_null_unlinked_and_native_detail(context):
    engine = create_engine("sqlite:///:memory:")
    C.__table__.create(engine)
    def row(i, amount, **kw):
        return dict(id=i, statement_batch_id="batch", statement_source_sheet="Income",
          statement_source_row_number=i, statement_file_hash="synthetic", record_type="ORDER",
          platform=context.persisted_platform, order_id="SAME", component_name="Transaction Fee",
          component_amount=amount, commit_status="COMMITTED", **kw)
    rows=[row(1,Decimal("-5")),row(2,Decimal("2")),row(3,Decimal("-3")),row(4,None)]
    unlinked=row(5,Decimal("1"));unlinked["order_id"]=None; rows.append(unlinked)
    sku=row(6,Decimal("9"));sku["record_type"]="SKU";rows.append(sku)
    foreign=row(7,Decimal("99"));foreign["platform"]="Shopee SG" if context==SHOPEE_MY else "Shopee";rows.append(foreign)
    with engine.begin() as conn: conn.execute(C.__table__.insert(),rows)
    with engine.connect() as conn:
        summaries=read_component_summaries(conn,context)
        detail=read_native_order_details(conn,context,"SAME")
    assert sum(s.source_rows for s in summaries)==6
    fees=[s for s in summaries if s.order_id=="SAME" and s.record_type=="ORDER"]
    assert {s.amount_sign:s.amount for s in fees}=={-1:Decimal("-8"),1:Decimal("2"),None:None}
    assert len(detail)==5 and [r["id"] for r in detail]==[1,2,3,4,6]
    assert all(r["statement_file_hash"]=="synthetic" for r in detail)
    assert any(s.order_id is None for s in summaries)
    engine.dispose()


def test_revision_requires_all_enabled_triggers():
    class Result:
        def __init__(self, rows): self.rows=rows
        def all(self): return self.rows
    class Connection:
        def __init__(self, rows): self.rows=rows
        def execute(self, _query): return Result(self.rows)
    rows=[(table,f"ig_revision_{table}_{action}","O") for table in _REVISION_TABLES for action in ("insert","update","delete")]
    rows.extend((table,"ig_revision_truncate","O") for table in _REVISION_TABLES)
    validate_revision_infrastructure(Connection(rows))
    with pytest.raises(RuntimeError,match="missing or disabled"):
        validate_revision_infrastructure(Connection(rows[:-1]))
    rows[0]=(rows[0][0],rows[0][1],"D")
    with pytest.raises(RuntimeError,match="missing or disabled"):
        validate_revision_infrastructure(Connection(rows))
