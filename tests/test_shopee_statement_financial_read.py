from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentOrder
from src.invoice_app.services.shopee_statement_financial_read import (
    StatementFinancialDateBasis,
    StatementFinancialReadError,
    StatementFinancialRecordState,
    read_committed_statement_financials,
)


class _FullOrderRepository:
    def __init__(self, snapshot: FullOrderCurrentSnapshot) -> None:
        self.snapshot = snapshot

    def read_snapshot(self) -> FullOrderCurrentSnapshot:
        return self.snapshot


class _StatementReader:
    def __init__(self, platform: str, facts: CommittedStatementFinancialFacts) -> None:
        self.persisted_platform = platform
        self.facts = facts

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts:
        return self.facts


def _order(order_id: str, *, market: str = "shopee_my", created: date = date(2026, 9, 1)):
    return FullOrderCurrentOrder(
        market=market,
        order_id=order_id,
        order_status="Completed",
        order_created_date=created,
        source_file="current.xlsx",
        snapshot_hash=f"hash-{order_id}",
        last_synced_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


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


def test_financial_read_joins_only_matching_market_and_preserves_native_components():
    repository = _FullOrderRepository(
        FullOrderCurrentSnapshot((_order("MY-1"), _order("SG-1", market="shopee_sg")), ())
    )
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
        full_order_repository=repository,
        statement_reader=reader,
    )

    assert result.market == "shopee_my"
    assert len(result.records) == 1
    record = result.records[0]
    assert record.order_id == "MY-1"
    assert record.state is StatementFinancialRecordState.AVAILABLE
    assert record.order_created_date == date(2026, 9, 1)
    assert record.payout_completed_date == date(2026, 9, 7)
    assert record.released_amount == Decimal("9.90")
    assert len(record.financial_components) == 2
    assert record.refund_components == (_component("MY-1"),)
    assert len(result.unlinked_components) == 1


def test_financial_read_keeps_missing_duplicate_conflicting_and_unmatched_states_explicit():
    repository = _FullOrderRepository(
        FullOrderCurrentSnapshot(
            (_order("MISSING"), _order("DUPLICATE"), _order("CONFLICT")), ()
        )
    )
    reader = _StatementReader(
        "Shopee",
        CommittedStatementFinancialFacts(
            platform="Shopee",
            order_facts=(
                _order_fact("DUPLICATE"),
                _order_fact("DUPLICATE"),
                _order_fact("CONFLICT"),
                _order_fact("CONFLICT", payout=date(2026, 9, 8)),
                _order_fact("UNMATCHED"),
            ),
            financial_components=(),
        ),
    )

    result = read_committed_statement_financials(
        market_context=SHOPEE_MY,
        full_order_repository=repository,
        statement_reader=reader,
    )

    assert {record.order_id: record.state for record in result.records} == {
        "MISSING": StatementFinancialRecordState.MISSING_STATEMENT,
        "DUPLICATE": StatementFinancialRecordState.DUPLICATE_STATEMENT,
        "CONFLICT": StatementFinancialRecordState.CONFLICTING_STATEMENT,
        "UNMATCHED": StatementFinancialRecordState.UNMATCHED_FULL_ORDER,
    }
    assert all(record.released_amount is None for record in result.records if record.state is not StatementFinancialRecordState.AVAILABLE)


def test_financial_read_supports_explicit_date_basis_and_fails_closed_for_wrong_market():
    repository = _FullOrderRepository(
        FullOrderCurrentSnapshot((_order("MY-1", created=date(2026, 9, 3)),), ())
    )
    reader = _StatementReader(
        "Shopee",
        CommittedStatementFinancialFacts("Shopee", (_order_fact("MY-1"),), ()),
    )

    result = read_committed_statement_financials(
        market_context=SHOPEE_MY,
        full_order_repository=repository,
        statement_reader=reader,
        date_basis=StatementFinancialDateBasis.ORDER_CREATED,
        from_date=date(2026, 9, 3),
        to_date=date(2026, 9, 3),
    )
    assert [record.order_id for record in result.records] == ["MY-1"]

    with pytest.raises(StatementFinancialReadError, match="does not match"):
        read_committed_statement_financials(
            market_context=SHOPEE_SG,
            full_order_repository=repository,
            statement_reader=reader,
        )
