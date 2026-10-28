from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_statement_financial_read import (
    StatementFinancialReadError,
    StatementFinancialRecordState,
    read_committed_statement_financials,
)


class _StatementReader:
    def __init__(self, platform: str, facts: CommittedStatementFinancialFacts) -> None:
        self.persisted_platform = platform
        self.facts = facts
        self.reads = 0

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts:
        self.reads += 1
        return self.facts


def _order_fact(order_id: str, *, payout: date = date(2026, 9, 7), released="9.90"):
    return CommittedStatementOrderFact(
        order_id=order_id,
        payout_completed_date=payout,
        total_released_amount=Decimal(released),
        statement_product_price=Decimal("10.00"),
        statement_batch_id="batch-1",
        statement_file_hash="a" * 64,
        statement_period_from=date(2026, 8, 30),
        statement_period_to=date(2026, 9, 7),
        source_row_number=2,
    )


def _component(order_id: str | None, *, name="Refund Amount", amount="-0.10", record_type="ORDER"):
    return CommittedStatementFinancialComponentFact(
        record_type=record_type,
        order_id=order_id,
        component_name=name,
        component_amount=Decimal(amount),
        statement_batch_id="batch-1",
        statement_file_hash="a" * 64,
        statement_source_sheet="Income",
        statement_source_row_number=2,
        sequence_no="1",
        payout_completed_date=date(2026, 9, 7),
    )


def test_financial_read_returns_only_native_statement_facts_and_components():
    reader = _StatementReader(
        "Shopee",
        CommittedStatementFinancialFacts(
            platform="Shopee",
            order_facts=(_order_fact(" MY-1 "),),
            financial_components=(
                _component("MY-1"),
                _component("MY-1", name="Product Price", amount="10.00", record_type="SKU"),
                _component(None, name="Adjustment", amount="1.00", record_type="ADJUSTMENT"),
            ),
        ),
    )

    result = read_committed_statement_financials(
        market_context=SHOPEE_MY,
        statement_reader=reader,
    )

    assert result.market == "shopee_my"
    assert len(result.records) == 1
    record = result.records[0]
    assert record.order_id == "MY-1"
    assert record.state is StatementFinancialRecordState.AVAILABLE
    assert record.payout_completed_date == date(2026, 9, 7)
    assert record.released_amount == Decimal("9.90")
    assert len(record.financial_components) == 2
    assert record.refund_components == (_component("MY-1"),)
    assert len(result.unlinked_components) == 1
    assert reader.reads == 1


def test_financial_read_keeps_same_order_id_strictly_scoped_by_market_context():
    my_reader = _StatementReader(
        "Shopee",
        CommittedStatementFinancialFacts(
            "Shopee", (_order_fact("SAME-ID", payout=date(2026, 9, 7), released="11.00"),), ()
        ),
    )
    sg_reader = _StatementReader(
        "Shopee SG",
        CommittedStatementFinancialFacts(
            "Shopee SG", (_order_fact("SAME-ID", payout=date(2026, 9, 8), released="22.00"),), ()
        ),
    )

    my_result = read_committed_statement_financials(market_context=SHOPEE_MY, statement_reader=my_reader)
    sg_result = read_committed_statement_financials(market_context=SHOPEE_SG, statement_reader=sg_reader)

    assert [(row.market, row.order_id, row.released_amount) for row in my_result.records] == [
        ("shopee_my", "SAME-ID", Decimal("11.00"))
    ]
    assert [(row.market, row.order_id, row.released_amount) for row in sg_result.records] == [
        ("shopee_sg", "SAME-ID", Decimal("22.00"))
    ]


def test_financial_read_keeps_duplicate_and_conflicting_statement_evidence_explicit():
    reader = _StatementReader(
        "Shopee",
        CommittedStatementFinancialFacts(
            platform="Shopee",
            order_facts=(
                _order_fact("DUPLICATE"),
                _order_fact("DUPLICATE"),
                _order_fact("CONFLICT"),
                _order_fact("CONFLICT", payout=date(2026, 9, 8)),
            ),
            financial_components=(),
        ),
    )

    result = read_committed_statement_financials(market_context=SHOPEE_MY, statement_reader=reader)

    assert {record.order_id: record.state for record in result.records} == {
        "DUPLICATE": StatementFinancialRecordState.DUPLICATE_STATEMENT,
        "CONFLICT": StatementFinancialRecordState.CONFLICTING_STATEMENT,
    }
    assert all(record.released_amount is None for record in result.records)


def test_financial_read_has_no_full_order_dependency_and_fails_closed_for_wrong_market():
    reader = _StatementReader(
        "Shopee", CommittedStatementFinancialFacts("Shopee", (_order_fact("MY-1"),), ())
    )
    source = Path(__file__).resolve().parents[1].joinpath(
        "src", "invoice_app", "services", "shopee_statement_financial_read.py"
    ).read_text(encoding="utf-8")
    assert "ShopeeFullOrderCurrentRepository" not in source
    assert "FullOrderCurrentSnapshot" not in source
    assert ".read_snapshot(" not in source

    with pytest.raises(StatementFinancialReadError, match="does not match"):
        read_committed_statement_financials(market_context=SHOPEE_SG, statement_reader=reader)
