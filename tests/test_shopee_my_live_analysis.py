from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pandas as pd

from src.invoice_app.domain.order_adjustment import CanonicalOrderAdjustment, EvidenceStatus
from src.invoice_app.repositories.shopee_full_order_current_repository import FullOrderCurrentSnapshot
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
)
from src.invoice_app.services.shopee_analysis import (
    AnalysisDateBasis,
    ReconciliationStatus,
    ShopeeAnalysisError,
    ShopeeAnalysisFilters,
    ShopeeAnalysisPresentation,
    ShopeeAnalysisSnapshot,
    build_shopee_analysis,
    load_shopee_analysis_snapshot,
    available_statuses,
    product_options,
)
from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentItem, FullOrderCurrentOrder
from src.invoice_app.services.shopee_my_live_analysis import load_shopee_my_analysis_snapshot
from src.invoice_app.services.shopee_order_financial_projection import (
    OrderFinancialProjection,
    OrderFinancialProjectionState,
)
from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.ui.shopee_analysis_dashboard import (
    _aggregate_trend_frame,
    _default_trend_aggregation,
    _financial_trend_frame,
    _product_chart_label,
    _product_trend_frame,
    _reconciliation_status_frame,
    _trend_chart,
)


def _order(order_id: str = "MY-1", *, market: str = "shopee_my", status: str = "Completed") -> FullOrderCurrentOrder:
    return FullOrderCurrentOrder(
        market=market,
        order_id=order_id,
        order_status=status,
        order_created_date=date(2026, 10, 1),
        source_file="full-order.csv",
        snapshot_hash="hash",
        last_synced_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )


def _item(order_id: str = "MY-1", *, market: str = "shopee_my", subtotal: str = "100.00") -> FullOrderCurrentItem:
    return FullOrderCurrentItem(
        market=market,
        order_id=order_id,
        item_index=0,
        source_parent_sku="PARENT-1",
        source_seller_sku="SKU-1",
        resolved_seller_sku="SKU-1",
        nav="5000064",
        product_name="Canonical Tea",
        variation="500g",
        quantity=2,
        unit_price=Decimal("55.00"),
        product_subtotal=Decimal(subtotal),
        return_refund_status="",
        resolve_sku="RESOLVED-1",
    )


def _component(name: str, amount: str, *, record_type: str = "ORDER") -> CommittedStatementFinancialComponentFact:
    return CommittedStatementFinancialComponentFact(
        record_type=record_type,
        order_id="MY-1",
        component_name=name,
        component_amount=Decimal(amount),
        statement_batch_id="batch-1",
        statement_file_hash="statement-hash",
        statement_source_sheet="Income",
        statement_source_row_number=10,
        sequence_no="1",
        payout_completed_date=date(2026, 10, 5),
    )


def _adjustment() -> CanonicalOrderAdjustment:
    return CanonicalOrderAdjustment(
        platform="Shopee",
        linked_order_id="MY-1",
        adjustment_type="RETURN_REFUND_AFTER_ORDER_COMPLETED",
        adjustment_description="Return Refund Adjustment After Order Completed",
        adjustment_reason=None,
        adjustment_complete_date=date(2026, 10, 8),
        adjustment_amount=Decimal("-2.00"),
        payout_completed_date=date(2026, 10, 5),
        statement_batch_id="batch-1",
        statement_sequence_no="adjustment-1",
        statement_source_filename="statement.xlsx",
        statement_file_hash="statement-hash",
        adjustment_event_fingerprint="event-hash",
        evidence_status=EvidenceStatus.STATEMENT_ONLY,
    )


def _snapshot(*, market: str = "shopee_my", currency: str = "MYR", include_statement: bool = True) -> ShopeeAnalysisSnapshot:
    components = (
        _component("Product Price", "100.00"),
        _component("Transaction Fee", "-5.00"),
        _component("Refund Amount", "-10.00"),
        _component("Transaction Fee", "-5.00", record_type="SKU"),
    ) if include_statement else ()
    projection = OrderFinancialProjection(
        market=market,
        order_id="MY-1",
        order_created_date=date(2026, 10, 1),
        order_status="Completed",
        settlement_state=(OrderFinancialProjectionState.AVAILABLE if include_statement else OrderFinancialProjectionState.MISSING_STATEMENT),
        payout_completed_date=date(2026, 10, 5) if include_statement else None,
        released_amount=Decimal("85.00") if include_statement else None,
        statement_product_price=Decimal("100.00") if include_statement else None,
        statement_order_facts=(),
        financial_components=components,
        component_totals=(),
        refund_components=tuple(component for component in components if component.component_name == "Refund Amount"),
        adjustment_total=Decimal("-2.00"),
        adjustment_events=(_adjustment(),),
    )
    return ShopeeAnalysisSnapshot(
        presentation=ShopeeAnalysisPresentation("Shopee SG" if market == "shopee_sg" else "Shopee MY", currency, "S$" if currency == "SGD" else "RM"),
        market=market,
        current=FullOrderCurrentSnapshot((_order(market=market),), (_item(market=market),)),
        projections=(projection,),
    )


def _filters(**kwargs: object) -> ShopeeAnalysisFilters:
    return ShopeeAnalysisFilters(
        date_basis=AnalysisDateBasis.ORDER_CREATED,
        from_date=date(2026, 10, 1),
        to_date=date(2026, 10, 31),
        **kwargs,
    )


def test_canonical_projection_facts_drive_my_metrics_without_component_multiplication():
    report = build_shopee_analysis(_snapshot(), _filters())

    assert report.product_sales == Decimal("100.00")
    assert report.total_orders == 1
    assert report.units_sold == 2
    assert report.average_order_value == Decimal("100.00")
    assert report.released_amount == Decimal("85.00")
    assert report.refund_amount == Decimal("10.00")
    assert report.adjustment_total == Decimal("-2.00")
    assert report.statement_charges == Decimal("-5.00")
    assert report.rows[0].reconciliation_status is ReconciliationStatus.RECONCILED
    assert {row.component_name: row.amount for row in report.component_totals} == {
        "Product Price": Decimal("100.00"),
        "Refund Amount": Decimal("-10.00"),
        "Transaction Fee": Decimal("-5.00"),
    }
    assert len(report.rows[0].financial_components) == 4


def test_missing_statement_fails_closed_without_invoice_fallback():
    report = build_shopee_analysis(_snapshot(include_statement=False), _filters())

    assert report.rows[0].released_amount is None
    assert report.rows[0].reconciliation_status is ReconciliationStatus.INSUFFICIENT_EVIDENCE
    assert report.rows[0].expected_released_amount is None


def test_payout_date_status_and_empty_selection_use_one_canonical_order_set():
    snapshot = _snapshot()
    payout_report = build_shopee_analysis(
        snapshot,
        ShopeeAnalysisFilters(AnalysisDateBasis.PAYOUT, date(2026, 10, 5), date(2026, 10, 5)),
    )
    empty_report = build_shopee_analysis(
        snapshot,
        ShopeeAnalysisFilters(AnalysisDateBasis.ORDER_CREATED, date(2026, 10, 2), date(2026, 10, 2)),
    )

    assert payout_report.total_orders == 1
    assert payout_report.rows[0].order_status == "Completed"
    assert empty_report.rows == ()
    assert empty_report.products == ()


def test_cancelled_is_excluded_but_canceled_retains_the_shared_status_behavior():
    snapshot = _snapshot()
    cancelled = replace(snapshot.projections[0], order_status="Cancelled")
    canceled = replace(snapshot.projections[0], order_status="Canceled")
    report = build_shopee_analysis(replace(snapshot, projections=(cancelled,)), _filters())
    retained = build_shopee_analysis(replace(snapshot, projections=(canceled,)), _filters())

    assert report.rows == ()
    assert available_statuses(replace(snapshot, projections=(cancelled,))) == ()
    assert retained.total_orders == 1
    assert available_statuses(replace(snapshot, projections=(canceled,))) == ("Canceled",)


def test_settlement_metrics_use_only_the_committed_statement_order_cohort():
    snapshot = _snapshot()
    missing_statement = replace(
        snapshot.projections[0],
        order_id="MY-2",
        settlement_state=OrderFinancialProjectionState.MISSING_STATEMENT,
        payout_completed_date=None,
        released_amount=None,
        statement_product_price=None,
        financial_components=(),
        refund_components=(),
        adjustment_total=Decimal("0.00"),
        adjustment_events=(),
    )
    combined = replace(
        snapshot,
        current=FullOrderCurrentSnapshot(
            snapshot.current.orders + (_order("MY-2"),),
            snapshot.current.items + (_item("MY-2", subtotal="40.00"),),
        ),
        projections=snapshot.projections + (missing_statement,),
    )
    report = build_shopee_analysis(combined, _filters())

    assert report.product_sales == Decimal("140.00")
    assert report.settled_product_sales == Decimal("100.00")
    assert report.released_amount == Decimal("85.00")
    assert report.settlement_difference == Decimal("15.00")
    assert report.settled_orders == 1
    assert report.missing_statement_orders == 1
    assert report.settlement_coverage == Decimal("0.5")
    assert report.reconciliation_counts[ReconciliationStatus.INSUFFICIENT_EVIDENCE] == 1


def test_reconciliation_coverage_separates_pending_statement_from_mismatch():
    snapshot = _snapshot()
    missing = replace(
        snapshot.projections[0],
        order_id="MY-2",
        settlement_state=OrderFinancialProjectionState.MISSING_STATEMENT,
        payout_completed_date=None,
        released_amount=None,
        statement_product_price=None,
        financial_components=(),
        refund_components=(),
        adjustment_events=(),
    )
    mismatch = replace(snapshot.projections[0], order_id="MY-3", released_amount=Decimal("84.00"))
    combined = replace(
        snapshot,
        current=FullOrderCurrentSnapshot(
            snapshot.current.orders + (_order("MY-2"), _order("MY-3")),
            snapshot.current.items + (_item("MY-2"), _item("MY-3")),
        ),
        projections=(snapshot.projections[0], missing, mismatch),
    )

    frame, other_pending = _reconciliation_status_frame(build_shopee_analysis(combined, _filters()))

    assert frame.to_dict("records") == [
        {"Status": "Reconciled", "Orders": 1, "Percent": pytest.approx(1 / 3)},
        {"Status": "Pending Statement evidence", "Orders": 1, "Percent": pytest.approx(1 / 3)},
        {"Status": "Mismatch", "Orders": 1, "Percent": pytest.approx(1 / 3)},
    ]
    assert other_pending == 0


def test_product_chart_label_is_compact_but_deterministically_traceable():
    full_name = "Organic extra virgin cold pressed olive oil with a very long audited product name"

    assert _product_chart_label(full_name, limit=24) == "Organic extra virgin co…"
    assert _product_chart_label("  Canonical   Tea  ") == "Canonical Tea"


def test_trend_frames_never_include_a_date_outside_the_selected_scope():
    report = build_shopee_analysis(_snapshot(), _filters())

    product_frame = _product_trend_frame(report, "Product Sales")
    financial_frame = _financial_trend_frame(report, "Released Amount")

    assert product_frame["Date"].between(report.filters.from_date, report.filters.to_date).all()
    assert financial_frame["Date"].between(report.filters.from_date, report.filters.to_date).all()
    chart = _trend_chart(product_frame, metric="Product Sales", report=report, financial=False)
    assert chart.to_dict()["encoding"]["x"]["scale"]["domain"] == ["2026-10-01", "2026-10-31"]


def test_trend_aggregation_is_bounded_and_defaults_to_the_selected_range_length():
    filters = ShopeeAnalysisFilters(
        AnalysisDateBasis.ORDER_CREATED,
        date(2026, 10, 15),
        date(2027, 2, 15),
    )
    frame = pd.DataFrame(
        {
            "Date": [date(2026, 10, 16), date(2026, 10, 20), date(2026, 11, 1), date(2027, 2, 10)],
            "Product Sales": [10.0, 20.0, 30.0, 40.0],
        }
    )

    weekly = _aggregate_trend_frame(frame, metric="Product Sales", aggregation="Weekly", filters=filters)
    monthly = _aggregate_trend_frame(frame, metric="Product Sales", aggregation="Monthly", filters=filters)

    assert _default_trend_aggregation(filters) == "Weekly"
    assert _default_trend_aggregation(_filters()) == "Daily"
    assert _default_trend_aggregation(
        ShopeeAnalysisFilters(AnalysisDateBasis.ORDER_CREATED, date(2026, 1, 1), date(2027, 1, 2))
    ) == "Monthly"
    assert weekly["Date"].between(filters.from_date, filters.to_date).all()
    assert monthly["Date"].between(filters.from_date, filters.to_date).all()
    assert monthly["Date"].tolist() == [date(2026, 10, 15), date(2026, 11, 1), date(2027, 2, 1)]
    assert monthly["Product Sales"].tolist() == [30.0, 30.0, 40.0]


def test_reconciliation_mismatch_is_visible_and_never_forced():
    snapshot = _snapshot()
    projection = replace(snapshot.projections[0], released_amount=Decimal("84.00"))
    report = build_shopee_analysis(replace(snapshot, projections=(projection,)), _filters())

    assert report.rows[0].reconciliation_status is ReconciliationStatus.MISMATCH
    assert report.rows[0].reconciliation_difference == Decimal("-1.00")


def test_credits_remain_signed_native_statement_components():
    snapshot = _snapshot()
    projection = snapshot.projections[0]
    credit = _component("Shipping Credit", "3.00")
    updated = replace(
        projection,
        financial_components=projection.financial_components + (credit,),
        released_amount=Decimal("88.00"),
    )
    report = build_shopee_analysis(replace(snapshot, projections=(updated,)), _filters())

    assert report.statement_charges == Decimal("-5.00")
    assert report.statement_credits == Decimal("3.00")
    assert report.rows[0].reconciliation_status is ReconciliationStatus.RECONCILED


def test_product_filter_never_allocates_whole_order_financial_facts():
    snapshot = _snapshot()
    product_key = product_options(snapshot)[0].key
    report = build_shopee_analysis(snapshot, _filters(product_keys=frozenset((product_key,))))

    assert report.rows[0].released_amount == Decimal("85.00")
    assert report.rows[0].reconciliation_status is ReconciliationStatus.INSUFFICIENT_EVIDENCE
    assert "do not allocate" in report.rows[0].reconciliation_reason


def test_shared_contract_accepts_sg_and_preserves_sg_currency():
    snapshot = _snapshot(market="shopee_sg", currency="SGD")
    report = build_shopee_analysis(snapshot, _filters())

    assert report.presentation.currency_display == "S$"
    assert report.product_sales == Decimal("100.00")


def test_snapshot_rejects_cross_market_full_order_facts():
    with pytest.raises(ShopeeAnalysisError, match="market boundary"):
        ShopeeAnalysisSnapshot(
            presentation=ShopeeAnalysisPresentation("Shopee MY", "MYR", "RM"),
            market="shopee_my",
            current=FullOrderCurrentSnapshot((_order(),), (_item(market="shopee_sg"),)),
            projections=(),
        )


def test_my_provider_reads_only_full_order_and_statement_projection_facets():
    snapshot = FullOrderCurrentSnapshot((_order(),), (_item(),))

    class FullOrderRepository:
        def read_snapshot(self):
            return snapshot

    class StatementReader:
        persisted_platform = "Shopee"

        def read_committed_financial_facts(self):
            return CommittedStatementFinancialFacts("Shopee", (), ())

        def reload_active_commit_state(self):
            return SimpleNamespace(order_adjustments=())

    result = load_shopee_my_analysis_snapshot(
        full_order_repository=FullOrderRepository(),
        statement_reader=StatementReader(),
    )

    assert result.market == "shopee_my"
    assert result.projections[0].settlement_state is OrderFinancialProjectionState.MISSING_STATEMENT


def test_shared_loader_uses_sg_presentation_contract_without_my_fallback():
    snapshot = FullOrderCurrentSnapshot((_order(market="shopee_sg"),), (_item(market="shopee_sg"),))

    class FullOrderRepository:
        def read_snapshot(self):
            return snapshot

    class StatementReader:
        persisted_platform = "Shopee SG"

        def read_committed_financial_facts(self):
            return CommittedStatementFinancialFacts("Shopee SG", (), ())

        def reload_active_commit_state(self):
            return SimpleNamespace(order_adjustments=())

    result = load_shopee_analysis_snapshot(
        market_context=SHOPEE_SG,
        full_order_repository=FullOrderRepository(),
        statement_reader=StatementReader(),
    )

    assert result.market == "shopee_sg"
    assert result.presentation.currency_display == "S$"
