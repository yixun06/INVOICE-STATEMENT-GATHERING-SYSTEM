from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.domain.order_adjustment import make_statement_adjustment
from src.invoice_app.repositories.shopee_full_order_current_repository import FullOrderCurrentSnapshot
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentItem, FullOrderCurrentOrder
from src.invoice_app.services.shopee_sg_analysis import (
    SgAnalysisDateBasis,
    SgAnalysisFilters,
    build_shopee_sg_analysis,
    load_shopee_sg_analysis_snapshot,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_order_adjustment_read import (
    OrderAdjustmentReadError,
    read_committed_order_adjustments,
)
from src.invoice_app.services.shopee_statement_financial_read import StatementFinancialReadError
from src.invoice_app.services.shopee_statement_persistence import StatementCommitState
from src.invoice_app.ui.shopee_sg_analysis import render_shopee_sg_analysis


class _Repository:
    def __init__(self, snapshot: FullOrderCurrentSnapshot) -> None:
        self.snapshot = snapshot

    def read_snapshot(self) -> FullOrderCurrentSnapshot:
        return self.snapshot


class _StatementReader:
    def __init__(self, platform: str, facts: CommittedStatementFinancialFacts, adjustments=()) -> None:
        self.persisted_platform = platform
        self.facts = facts
        self.adjustments = tuple(adjustments)

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts:
        return self.facts

    def reload_active_commit_state(self) -> StatementCommitState:
        return StatementCommitState(orders={}, committed_statements=(), order_adjustments=self.adjustments)


def _order(order_id: str, created: date, *, market: str = "shopee_sg") -> FullOrderCurrentOrder:
    return FullOrderCurrentOrder(
        market=market, order_id=order_id, order_status="Completed",
        order_created_date=created, source_file="sg.xlsx", snapshot_hash=order_id,
        last_synced_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )


def _item(order_id: str, index: int, quantity: int, subtotal: str) -> FullOrderCurrentItem:
    return FullOrderCurrentItem(
        market="shopee_sg", order_id=order_id, item_index=index,
        source_parent_sku="", source_seller_sku="SRC", resolved_seller_sku="SKU",
        nav="2000842", product_name="Papaya", variation="", quantity=quantity,
        unit_price=Decimal("9.90"), product_subtotal=Decimal(subtotal),
        return_refund_status="", resolve_sku="SKU",
    )


def _order_fact(order_id: str, payout: date, released: str) -> CommittedStatementOrderFact:
    return CommittedStatementOrderFact(
        order_id=order_id, payout_completed_date=payout, total_released_amount=Decimal(released),
        statement_product_price=Decimal("10.00"), statement_batch_id="batch-sg",
        statement_file_hash="a" * 64, statement_period_from=date(2026, 9, 1),
        statement_period_to=date(2026, 9, 30), source_row_number=2,
    )


def _component(order_id: str, name: str, amount: str, row: int) -> CommittedStatementFinancialComponentFact:
    return CommittedStatementFinancialComponentFact(
        record_type="ORDER", order_id=order_id, component_name=name,
        component_amount=Decimal(amount), statement_batch_id="batch-sg",
        statement_file_hash="a" * 64, statement_source_sheet="Income",
        statement_source_row_number=row, sequence_no=str(row), payout_completed_date=date(2026, 9, 7),
    )


def _snapshot():
    adjustment = make_statement_adjustment(
        linked_order_id="SG-SETTLED", adjustment_description="Return Refund Adjustment",
        adjustment_reason="native", adjustment_complete_date=date(2026, 9, 9),
        adjustment_amount=Decimal("-1.25"), payout_completed_date=date(2026, 9, 7),
        statement_batch_id="batch-sg", statement_sequence_no="99", statement_source_filename="sg.xlsx",
        statement_file_hash="a" * 64, first_observed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        persisted_platform="Shopee SG",
    )
    repository = _Repository(FullOrderCurrentSnapshot(
        (
            _order("SG-SETTLED", date(2026, 9, 2)),
            _order("SG-PENDING", date(2026, 9, 3)),
            _order("SG-SETTLED", date(2026, 8, 1), market="shopee_my"),
        ),
        (_item("SG-SETTLED", 0, 2, "18.00"), _item("SG-PENDING", 0, 1, "9.00")),
    ))
    reader = _StatementReader("Shopee SG", CommittedStatementFinancialFacts(
        platform="Shopee SG", order_facts=(_order_fact("SG-SETTLED", date(2026, 9, 7), "15.00"),),
        financial_components=(
            _component("SG-SETTLED", "Product Price", "10.00", 2),
            _component("SG-SETTLED", "Product Price", "5.00", 3),
            _component("SG-SETTLED", "Commission fee (Incl. GST)", "-0.50", 4),
        ),
    ), (adjustment,))
    return load_shopee_sg_analysis_snapshot(full_order_repository=repository, statement_reader=reader)


def test_sg_analysis_uses_only_full_order_for_order_created_and_only_statement_for_payout():
    snapshot = _snapshot()
    order_created = build_shopee_sg_analysis(snapshot, SgAnalysisFilters(
        SgAnalysisDateBasis.ORDER_CREATED, date(2026, 9, 2), date(2026, 9, 3)))
    assert order_created.order_count == 2
    assert order_created.item_quantity == 3
    assert order_created.product_subtotal == Decimal("27.00")
    assert {
        row.order_id: row.released_amount for row in order_created.rows
    } == {"SG-SETTLED": Decimal("15.00"), "SG-PENDING": None}

    payout = build_shopee_sg_analysis(snapshot, SgAnalysisFilters(
        SgAnalysisDateBasis.PAYOUT, date(2026, 9, 7), date(2026, 9, 7)))
    assert [row.order_id for row in payout.rows] == ["SG-SETTLED"]
    assert payout.settled_order_count == 1
    assert payout.released_amount == Decimal("15.00")
    assert all(row.order_created_date != date(2026, 8, 1) for row in order_created.rows)


def test_sg_analysis_delegates_cross_authority_composition_to_order_financial_projection():
    source = (Path(__file__).resolve().parents[1] / "src" / "invoice_app" / "services" / "shopee_sg_analysis.py").read_text(encoding="utf-8")
    assert "read_order_financial_projections(" in source
    assert "read_committed_statement_financials(" not in source


def test_sg_analysis_uses_projection_status_and_order_date_for_order_rows():
    baseline = _snapshot()
    changed_current = FullOrderCurrentSnapshot(
        (
            _order("SG-SETTLED", date(2026, 8, 1)),
            _order("SG-PENDING", date(2026, 8, 1)),
        ),
        baseline.current.items,
    )
    report = build_shopee_sg_analysis(
        replace(baseline, current=changed_current),
        SgAnalysisFilters(SgAnalysisDateBasis.ORDER_CREATED, date(2026, 9, 2), date(2026, 9, 3)),
    )

    assert [(row.order_id, row.order_created_date, row.order_status) for row in report.rows] == [
        ("SG-PENDING", date(2026, 9, 3), "Completed"),
        ("SG-SETTLED", date(2026, 9, 2), "Completed"),
    ]


def test_sg_component_rows_and_adjustments_are_preserved_separately_from_released_amount():
    report = build_shopee_sg_analysis(_snapshot(), SgAnalysisFilters(
        SgAnalysisDateBasis.PAYOUT, date(2026, 9, 7), date(2026, 9, 7)))
    assert len(report.components_for_order("SG-SETTLED")) == 3
    assert [(row.component_name, row.amount) for row in report.component_totals_for_order("SG-SETTLED")] == [
        ("Commission fee (Incl. GST)", Decimal("-0.50")),
        ("Product Price", Decimal("15.00")),
    ]
    assert report.released_amount == Decimal("15.00")
    assert [event.adjustment_amount for event in report.adjustments_for_order("SG-SETTLED")] == [Decimal("-1.25")]
    assert report.released_amount == Decimal("15.00")


def test_sg_analysis_fails_closed_when_statement_reader_is_not_sg():
    snapshot = _snapshot()
    repository = _Repository(snapshot.current)
    reader = _StatementReader("Shopee", CommittedStatementFinancialFacts("Shopee", (), ()))
    with pytest.raises(StatementFinancialReadError, match="does not match"):
        load_shopee_sg_analysis_snapshot(full_order_repository=repository, statement_reader=reader)


def test_sg_adjustment_reader_is_market_locked_even_when_order_ids_overlap():
    snapshot = _snapshot()
    reader = _StatementReader("Shopee SG", CommittedStatementFinancialFacts("Shopee SG", (), ()), snapshot.adjustments)
    assert len(read_committed_order_adjustments(
        market_context=SHOPEE_SG, statement_reader=reader, order_ids=("SG-SETTLED",)
    ).events) == 1
    with pytest.raises(OrderAdjustmentReadError, match="does not match"):
        read_committed_order_adjustments(
            market_context=SHOPEE_MY, statement_reader=reader, order_ids=("SG-SETTLED",)
        )


def test_sg_analysis_route_does_not_reuse_the_my_renderer():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    branch = source[source.index('elif selected_page == "Shopee SG":'):]
    assert "render_shopee_sg_analysis()" in branch
    assert "render_shopee_my_live_analysis()" not in branch.split('elif selected_page == "TikTok":', 1)[0]


def test_sg_analysis_ui_shows_authoritative_metrics_without_invoice_or_original_sales_fields():
    snapshot = _snapshot()
    app = AppTest.from_string(
        "from src.invoice_app.ui.shopee_sg_analysis import render_shopee_sg_analysis\n"
        "from tests.test_shopee_sg_analysis import _snapshot\n"
        "render_shopee_sg_analysis(_snapshot())\n"
    )
    app.run(timeout=20)
    assert app.exception == []
    assert [title.value for title in app.title] == ["Shopee SG · Live Analysis"]
    assert {metric.label for metric in app.metric} == {
        "Orders", "Item quantity", "Product subtotal", "Settlement unavailable",
        "Adjustment total (separate)",
    }
    values = " ".join(metric.label for metric in app.metric)
    assert "Original Sales" not in values and "Discount" not in values and "Final Amount" not in values
