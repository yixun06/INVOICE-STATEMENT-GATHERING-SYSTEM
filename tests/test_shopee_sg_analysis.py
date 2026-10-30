from __future__ import annotations

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
from src.invoice_app.services.shopee_analysis import (
    AnalysisDateBasis,
    ReconciliationStatus,
    ShopeeAnalysisFilters,
    build_shopee_analysis,
)
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentItem, FullOrderCurrentOrder
from src.invoice_app.services.shopee_order_financial_projection import OrderFinancialProjectionState
from src.invoice_app.services.shopee_sg_analysis import load_shopee_sg_analysis_snapshot
from src.invoice_app.services.shopee_statement_financial_read import StatementFinancialReadError
from src.invoice_app.services.shopee_statement_persistence import StatementCommitState
from src.invoice_app.ui.shopee_sg_analysis import render_shopee_sg_analysis
from src.invoice_app.utils.monetary import streamlit_currency_format


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


def _order(order_id: str, created: date) -> FullOrderCurrentOrder:
    return FullOrderCurrentOrder(
        market="shopee_sg", order_id=order_id, order_status="Completed",
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


def _snapshot(*, include_statement: bool = True):
    adjustment = make_statement_adjustment(
        linked_order_id="SG-SETTLED", adjustment_description="Return Refund Adjustment",
        adjustment_reason="native", adjustment_complete_date=date(2026, 9, 9),
        adjustment_amount=Decimal("-1.25"), payout_completed_date=date(2026, 9, 7),
        statement_batch_id="batch-sg", statement_sequence_no="99", statement_source_filename="sg.xlsx",
        statement_file_hash="a" * 64, first_observed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        persisted_platform="Shopee SG",
    )
    repository = _Repository(FullOrderCurrentSnapshot(
        (_order("SG-SETTLED", date(2026, 9, 2)), _order("SG-PENDING", date(2026, 9, 3))),
        (_item("SG-SETTLED", 0, 2, "18.00"), _item("SG-PENDING", 0, 1, "9.00")),
    ))
    facts = CommittedStatementFinancialFacts(
        platform="Shopee SG",
        order_facts=(_order_fact("SG-SETTLED", date(2026, 9, 7), "15.00"),) if include_statement else (),
        financial_components=(
            _component("SG-SETTLED", "Product Price", "10.00", 2),
            _component("SG-SETTLED", "Product Price", "5.00", 3),
            _component("SG-SETTLED", "Commission fee (Incl. GST)", "-0.50", 4),
        ) if include_statement else (),
    )
    return load_shopee_sg_analysis_snapshot(
        full_order_repository=repository,
        statement_reader=_StatementReader("Shopee SG", facts, (adjustment,)),
    )


def _filters(*, basis: AnalysisDateBasis = AnalysisDateBasis.ORDER_CREATED) -> ShopeeAnalysisFilters:
    return ShopeeAnalysisFilters(basis, date(2026, 9, 2), date(2026, 9, 7))


def test_sg_provider_uses_shared_snapshot_and_canonical_product_and_settlement_cohorts():
    report = build_shopee_analysis(_snapshot(), _filters())

    assert report.presentation.market_label == "Shopee SG"
    assert report.presentation.currency_display == "S$"
    assert report.product_sales == Decimal("27.00")
    assert report.total_orders == 2
    assert report.units_sold == 3
    assert report.settled_product_sales == Decimal("18.00")
    assert report.released_amount == Decimal("15.00")
    assert report.settlement_difference == Decimal("3.00")
    assert report.settled_orders == 1
    assert report.missing_statement_orders == 1
    assert report.order("SG-PENDING").reconciliation_status is ReconciliationStatus.INSUFFICIENT_EVIDENCE


def test_sg_payout_date_is_statement_only_and_missing_statement_is_pending_not_mismatch():
    snapshot = _snapshot(include_statement=False)
    order_report = build_shopee_analysis(snapshot, _filters())
    payout_report = build_shopee_analysis(snapshot, _filters(basis=AnalysisDateBasis.PAYOUT))

    assert order_report.total_orders == 2
    assert all(row.settlement_state is OrderFinancialProjectionState.MISSING_STATEMENT for row in order_report.rows)
    assert all(row.reconciliation_status is ReconciliationStatus.INSUFFICIENT_EVIDENCE for row in order_report.rows)
    assert payout_report.rows == ()


def test_sg_components_are_signed_native_facts_and_adjustments_stay_independent():
    report = build_shopee_analysis(_snapshot(), _filters())
    order = report.order("SG-SETTLED")
    assert order is not None

    assert {component.component_name: component.amount for component in order.component_totals} == {
        "Commission fee (Incl. GST)": Decimal("-0.50"),
        "Product Price": Decimal("15.00"),
    }
    assert order.adjustment_total == Decimal("-1.25")
    assert report.released_amount == Decimal("15.00")
    assert report.adjustment_total == Decimal("-1.25")


def test_sg_provider_is_market_locked_and_uses_the_shared_projection_loader():
    source = (Path(__file__).resolve().parents[1] / "src/invoice_app/services/shopee_sg_analysis.py").read_text(encoding="utf-8")
    snapshot = _snapshot()
    reader = _StatementReader("Shopee", CommittedStatementFinancialFacts("Shopee", (), ()))

    assert "market_context=SHOPEE_SG" in source
    assert "load_shopee_analysis_snapshot(" in source
    with pytest.raises(StatementFinancialReadError, match="does not match"):
        load_shopee_sg_analysis_snapshot(full_order_repository=_Repository(snapshot.current), statement_reader=reader)


def test_sg_ui_reuses_the_shared_renderer_with_sgd_controls_and_search_first_inspection():
    app = AppTest.from_string(
        "from src.invoice_app.ui.shopee_sg_analysis import render_shopee_sg_analysis\n"
        "from tests.test_shopee_sg_analysis import _snapshot\n"
        "render_shopee_sg_analysis(_snapshot())\n"
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [title.value for title in app.title] == ["Shopee SG - Analysis V2"]
    assert any(element.label == "Date basis" for element in app.selectbox)
    next(control for control in app.get("button_group") if control.label == "Analysis workspace").set_value("Financial & Reconciliation").run(timeout=20)
    assert any(element.label == "Search Order ID..." for element in app.text_input)
    assert streamlit_currency_format("SGD") == "S$ %,.2f"


def test_sg_order_inspection_presents_missing_statement_as_pending_evidence():
    app = AppTest.from_string(
        "from src.invoice_app.ui.shopee_sg_analysis import render_shopee_sg_analysis\n"
        "from tests.test_shopee_sg_analysis import _snapshot\n"
        "render_shopee_sg_analysis(_snapshot(include_statement=False))\n"
    )
    app.run(timeout=20)
    next(control for control in app.get("button_group") if control.label == "Analysis workspace").set_value("Financial & Reconciliation").run(timeout=20)

    search = next(element for element in app.text_input if element.label == "Search Order ID...")
    search.set_value("SG-PENDING").run(timeout=20)
    selection = next(element for element in app.selectbox if element.label == "Matching Order ID")
    selection.set_value("SG-PENDING").run(timeout=20)

    assert app.exception == []
    assert any(message.value == "Statement not available yet" for message in app.info)
    assert any("pending settlement evidence, not a reconciliation mismatch" in caption.value for caption in app.caption)


def test_sg_entry_is_a_thin_shared_dashboard_consumer_without_legacy_invoice_reads():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join((root / path).read_text(encoding="utf-8") for path in (
        "src/invoice_app/services/shopee_sg_analysis.py",
        "src/invoice_app/ui/shopee_sg_analysis.py",
    ))
    forbidden = (
        "Invoice_Orders", "Invoice_Items", "invoice_financial_layout", "final_amount",
        "line_subtotal", "CanonicalInvoice", "google_sheets_historical_invoice_repository",
    )

    assert "render_shopee_analysis_dashboard(source)" in source
    assert not [term for term in forbidden if term in source]


def test_sg_route_remains_scoped_to_the_sg_entry_point():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    branch = source[source.index('elif selected_page == "Shopee SG":'):]

    assert "render_shopee_sg_analysis()" in branch
    assert "render_shopee_my_live_analysis()" not in branch.split('elif selected_page == "TikTok":', 1)[0]
