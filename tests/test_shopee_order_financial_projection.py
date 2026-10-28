from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.invoice_app.domain.order_adjustment import make_statement_adjustment
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
)
from src.invoice_app.services.shopee_order_financial_projection import (
    OrderFinancialProjectionError,
    OrderFinancialProjectionState,
    read_order_financial_projections,
)
from src.invoice_app.services.shopee_statement_financial_read import (
    StatementFinancialReadError,
)
from src.invoice_app.services.shopee_statement_persistence import StatementCommitState


class _PostgresFullOrderRepository:
    """Small PostgreSQL-repository seam; no legacy Invoice source is provided."""

    def __init__(self, snapshot: FullOrderCurrentSnapshot) -> None:
        self.snapshot = snapshot
        self.reads = 0

    def read_snapshot(self) -> FullOrderCurrentSnapshot:
        self.reads += 1
        return self.snapshot


class _PostgresStatementReader:
    def __init__(
        self,
        platform: str,
        facts: CommittedStatementFinancialFacts,
        adjustments=(),
    ) -> None:
        self.persisted_platform = platform
        self.facts = facts
        self.adjustments = tuple(adjustments)
        self.financial_reads = 0
        self.adjustment_reads = 0

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts:
        self.financial_reads += 1
        return self.facts

    def reload_active_commit_state(self) -> StatementCommitState:
        self.adjustment_reads += 1
        return StatementCommitState(
            orders={}, committed_statements=(), order_adjustments=self.adjustments
        )


class _RaisingLegacyInvoiceRepository:
    """A guard proving this projection never needs legacy Invoice dependencies."""

    def __init__(self) -> None:
        self.calls = 0

    def read_invoice_orders_or_items(self):  # pragma: no cover - must never run
        self.calls += 1
        raise AssertionError("OrderFinancialProjection must not read legacy Invoice data.")


def _order(order_id: str, *, market: str, created: date, status: str = "Completed"):
    return FullOrderCurrentOrder(
        market=market,
        order_id=order_id,
        order_status=status,
        order_created_date=created,
        source_file="full-order.xlsx",
        snapshot_hash=f"hash-{market}-{order_id}",
        last_synced_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
    )


def _item(order_id: str, item_index: int, *, market: str) -> FullOrderCurrentItem:
    return FullOrderCurrentItem(
        market=market,
        order_id=order_id,
        item_index=item_index,
        source_parent_sku="",
        source_seller_sku="source-sku",
        resolved_seller_sku="resolved-sku",
        nav="5000001",
        product_name="Product",
        variation="",
        quantity=1,
        unit_price=Decimal("10.00"),
        product_subtotal=Decimal("10.00"),
        return_refund_status="",
        resolve_sku="resolved-sku",
    )


def _order_fact(order_id: str, *, released: str, payout: date) -> CommittedStatementOrderFact:
    return CommittedStatementOrderFact(
        order_id=order_id,
        payout_completed_date=payout,
        total_released_amount=Decimal(released),
        statement_product_price=Decimal("15.00"),
        statement_batch_id="batch-1",
        statement_file_hash="a" * 64,
        statement_period_from=date(2026, 10, 1),
        statement_period_to=date(2026, 10, 7),
        source_row_number=2,
    )


def _component(order_id: str, name: str, amount: str, row: int):
    return CommittedStatementFinancialComponentFact(
        record_type="ORDER",
        order_id=order_id,
        component_name=name,
        component_amount=Decimal(amount),
        statement_batch_id="batch-1",
        statement_file_hash="a" * 64,
        statement_source_sheet="Income",
        statement_source_row_number=row,
        sequence_no=str(row),
        payout_completed_date=date(2026, 10, 7),
    )


def test_projection_uses_market_bound_postgres_authorities_without_component_multiplication():
    repository = _PostgresFullOrderRepository(
        FullOrderCurrentSnapshot(
            (
                _order("SAME-ID", market="shopee_my", created=date(2026, 10, 2)),
                _order("SAME-ID", market="shopee_sg", created=date(2026, 10, 3)),
                _order("MY-UNPAID", market="shopee_my", created=date(2026, 10, 4)),
            ),
            (
                _item("SAME-ID", 0, market="shopee_my"),
                _item("SAME-ID", 1, market="shopee_my"),
                _item("SAME-ID", 2, market="shopee_my"),
            ),
        )
    )
    adjustment = make_statement_adjustment(
        linked_order_id="SAME-ID",
        adjustment_description="Return Refund Adjustment",
        adjustment_reason="native event",
        adjustment_complete_date=date(2026, 10, 8),
        adjustment_amount=Decimal("-1.25"),
        payout_completed_date=date(2026, 10, 7),
        statement_batch_id="batch-1",
        statement_sequence_no="9",
        statement_source_filename="statement.xlsx",
        statement_file_hash="a" * 64,
        first_observed_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
        persisted_platform="Shopee",
    )
    my_reader = _PostgresStatementReader(
        "Shopee",
        CommittedStatementFinancialFacts(
            platform="Shopee",
            order_facts=(_order_fact("SAME-ID", released="14.50", payout=date(2026, 10, 7)),),
            financial_components=(
                _component("SAME-ID", "Product Price", "15.00", 2),
                _component("SAME-ID", "Commission Fee", "-0.50", 3),
                _component("SAME-ID", "PromoXtra Fee", "0.00", 4),
            ),
        ),
        (adjustment,),
    )

    projections = read_order_financial_projections(
        market_context=SHOPEE_MY,
        full_order_repository=repository,
        statement_reader=my_reader,
    )

    assert [projection.order_id for projection in projections] == ["MY-UNPAID", "SAME-ID"]
    settled = next(projection for projection in projections if projection.order_id == "SAME-ID")
    unpaid = next(projection for projection in projections if projection.order_id == "MY-UNPAID")
    assert settled.market == "shopee_my"
    assert settled.order_status == "Completed"
    assert settled.payout_completed_date == date(2026, 10, 7)
    assert settled.released_amount == Decimal("14.50")
    assert len(settled.financial_components) == 3
    assert [(total.component_name, total.amount) for total in settled.component_totals] == [
        ("Commission Fee", Decimal("-0.50")),
        ("Product Price", Decimal("15.00")),
        ("PromoXtra Fee", Decimal("0.00")),
    ]
    assert settled.adjustment_total == Decimal("-1.25")
    assert [event.adjustment_amount for event in settled.adjustment_events] == [Decimal("-1.25")]
    assert settled.released_amount != settled.released_amount + settled.adjustment_total
    assert not hasattr(settled, "net_income")
    assert not hasattr(settled, "item_financial_components")
    assert unpaid.settlement_state is OrderFinancialProjectionState.MISSING_STATEMENT
    assert unpaid.released_amount is None and unpaid.financial_components == ()
    assert repository.reads == my_reader.financial_reads == my_reader.adjustment_reads == 1


def test_projection_keeps_same_order_id_isolated_for_sg_and_rejects_cross_market_reader():
    repository = _PostgresFullOrderRepository(
        FullOrderCurrentSnapshot(
            (
                _order("SAME-ID", market="shopee_my", created=date(2026, 10, 2)),
                _order("SAME-ID", market="shopee_sg", created=date(2026, 10, 3)),
            ),
            (),
        )
    )
    sg_reader = _PostgresStatementReader(
        "Shopee SG",
        CommittedStatementFinancialFacts(
            platform="Shopee SG",
            order_facts=(_order_fact("SAME-ID", released="22.00", payout=date(2026, 10, 8)),),
            financial_components=(),
        ),
    )

    [projection] = read_order_financial_projections(
        market_context=SHOPEE_SG,
        full_order_repository=repository,
        statement_reader=sg_reader,
        order_ids=("SAME-ID",),
    )
    assert (projection.market, projection.order_created_date, projection.released_amount) == (
        "shopee_sg", date(2026, 10, 3), Decimal("22.00")
    )

    with pytest.raises(StatementFinancialReadError, match="does not match"):
        read_order_financial_projections(
            market_context=SHOPEE_MY,
            full_order_repository=repository,
            statement_reader=sg_reader,
            order_ids=("SAME-ID",),
        )


def test_projection_fails_closed_for_statement_without_full_order_and_never_reads_legacy_invoice():
    repository = _PostgresFullOrderRepository(FullOrderCurrentSnapshot((), ()))
    reader = _PostgresStatementReader(
        "Shopee",
        CommittedStatementFinancialFacts(
            platform="Shopee",
            order_facts=(_order_fact("MISSING-FULL-ORDER", released="9.90", payout=date(2026, 10, 7)),),
            financial_components=(_component("MISSING-FULL-ORDER", "Service Fee", "-0.10", 2),),
        ),
    )
    legacy = _RaisingLegacyInvoiceRepository()

    with pytest.raises(OrderFinancialProjectionError, match="no matching Full Order Current"):
        read_order_financial_projections(
            market_context=SHOPEE_MY,
            full_order_repository=repository,
            statement_reader=reader,
        )

    assert legacy.calls == 0
    assert reader.financial_reads == 1
    assert reader.adjustment_reads == 0
    source = Path(__file__).resolve().parents[1].joinpath(
        "src", "invoice_app", "services", "shopee_order_financial_projection.py"
    ).read_text(encoding="utf-8")
    assert "Invoice_Orders" not in source
    assert "Invoice_Items" not in source
    assert "GoogleSheetsHistoricalInvoiceRepository" not in source
