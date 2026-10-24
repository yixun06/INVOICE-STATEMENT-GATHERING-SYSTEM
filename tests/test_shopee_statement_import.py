from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.domain.order_adjustment import make_statement_adjustment
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    StatementSummaryLine,
    SettlementAdjustment,
    SettlementIncomeRow,
    SourceValueIssue,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
)
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_statement_import import (
    ADDITIONAL_EVIDENCE_REQUIRED,
    INVOICE_COVERAGE_INCOMPLETE,
    check_statement_review_currency,
    classify_statement_invoice_coverage,
    commit_statement_review,
    expected_full_order_proof_rule_version,
    refresh_statement_review,
    review_statement_upload,
    statement_review_requires_proof_rule_refresh,
)
from src.invoice_app.services.data_import_state import (
    replace_statement_review_after_refresh,
)
from src.invoice_app.services.import_result_adapters import (
    adapt_shopee_weekly_statement_import_result,
)
from src.invoice_app.services.shopee_statement_persistence import (
    CommittedStatementReference,
    StatementCommitState,
)
from src.invoice_app.services.uat2_persistence_schema import STATEMENT_DATA_HEADERS
from src.invoice_app.services.shopee_weekly_statement_service import (
    NEEDS_REVIEW,
    stage_parsed_shopee_weekly_statement,
)
from src.invoice_app.domain.weekly_billing import BillingPeriod
from src.invoice_app.services.weekly_billing import (
    WeeklyBillingDataset,
    build_weekly_billing_summary,
)
from src.invoice_app.ui import data_import


NOW = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
_DEFAULT_ORDER = object()
APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _income(
    view_by: str,
    row_number: int,
    *,
    product: str = "10.00",
    refund: str = "0.00",
    fees: str | None = "0.00",
    sequence: str | None = None,
    product_id: str = "PRODUCT-1",
    product_name: str = "Green Tea",
) -> SettlementIncomeRow:
    components = {name: Decimal("0.00") for name in INCOME_COMPONENT_COLUMNS}
    components["Product Price"] = Decimal(product)
    components["Refund Amount"] = Decimal(refund)
    components["Commission Fee (incl. SST)"] = Decimal(fees)
    return SettlementIncomeRow(
        sequence_no=sequence or str(row_number),
        view_by=view_by,
        order_id="ORDER-1",
        product_id=product_id,
        product_name=product_name,
        order_creation_date=date(2026, 8, 10),
        payout_completed_date=date(2026, 8, 16),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=sum(components.values(), Decimal("0.00")),
        financial_components=components,
        source_values={},
        source_row_number=row_number,
    )


def _statement(
    *,
    sku_rows: tuple[SettlementIncomeRow, ...] | None = None,
    adjustments: tuple[SettlementAdjustment, ...] = (),
) -> ParsedShopeeWeeklyStatement:
    sku_rows = sku_rows or (_income("Sku", 3),)
    order_components = {
        name: sum(
            (row.financial_components[name] for row in sku_rows), Decimal("0.00")
        )
        for name in INCOME_COMPONENT_COLUMNS
    }
    order_row = _income("Order", 2)
    order_row = SettlementIncomeRow(
        **{
            **order_row.__dict__,
            "financial_components": order_components,
            "total_released_amount": sum(order_components.values(), Decimal("0.00")),
        }
    )
    adjustment_total = sum(
        (row.adjustment_amount or Decimal("0.00") for row in adjustments),
        Decimal("0.00"),
    )
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash="statement-hash",
        statement_period_from=date(2026, 8, 10),
        statement_period_to=date(2026, 8, 16),
        summary_total_released=order_row.total_released_amount,
        adjustment_control_total=adjustment_total,
        adjustment_footer_total=adjustment_total,
        income_rows=(order_row, *sku_rows),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=adjustments,
        source_value_issues=(),
        dimension_fallback_sheets=(),
        summary_lines=(
            StatementSummaryLine(
                "Summary", 40, "3. Total Released Amount", "TOTAL", None,
                order_row.total_released_amount, "RM",
            ),
        ),
    )


def _order(
    final_amount: str | None = "10.00",
    *,
    income: str | None = None,
    income_type: str = "Final",
    product: str = "10.00",
    refund: str | None = "0.00",
    fees: str = "0.00",
) -> CanonicalInvoiceOrder:
    income = final_amount if income is None else income
    return CanonicalInvoiceOrder(
        platform="Shopee",
        order_id="ORDER-1",
        invoice_financial_layout="NORMAL_ORDER",
        product_price=Decimal(product),
        refund_amount=Decimal(refund) if refund is not None else None,
        shipping_subtotal=Decimal("0.00"),
        vouchers_rebates_total=Decimal("0.00"),
        fees_charges_total=Decimal(fees) if fees is not None else None,
        final_amount=Decimal(final_amount) if final_amount is not None else None,
        order_income=Decimal(income) if income is not None else None,
        income_type=income_type,
        source_hash="invoice-hash",
    )


def _item(
    product_name: str = "Green Tea",
    *,
    item_index: int = 0,
    seller_sku: str = "SKU-1",
    subtotal: str = "10.00",
    promotion_group_id: str | None = None,
    source_group_total: str | None = None,
) -> CanonicalInvoiceItem:
    return CanonicalInvoiceItem(
        platform="Shopee",
        order_id="ORDER-1",
        item_index=item_index,
        seller_sku=seller_sku,
        product_name=product_name,
        variation="Original",
        quantity=1,
        actual_selling_unit_price=Decimal(subtotal),
        line_subtotal=Decimal(subtotal),
        promotion_group_id=promotion_group_id,
        source_group_total=(
            Decimal(source_group_total) if source_group_total is not None else None
        ),
        source_pdf="invoice.pdf",
    )


def _master(*, include_second_candidate: bool = False) -> ProductPriceMaster:
    rows = [
        {
            "product_id": "PRODUCT-1",
            "seller_sku": "SKU-1",
            "parent_sku": "PARENT-1",
            "product_name": "Green Tea",
            "variation_name": "Original",
            "unit_selling_price": "12.00",
        }
    ]
    if include_second_candidate:
        rows.append(
            {
                "product_id": "PRODUCT-1",
                "seller_sku": "SKU-2",
                "parent_sku": "PARENT-2",
                "product_name": "Green Tea",
                "variation_name": "Original",
                "unit_selling_price": "12.00",
            }
        )
    return ProductPriceMaster.from_rows(
        rows
    )


def _mixed_master() -> ProductPriceMaster:
    return ProductPriceMaster.from_rows(
        (
            {
                "product_id": "PRODUCT-1",
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Green Tea",
                "variation_name": "Original",
                "unit_selling_price": "12.00",
            },
            {
                "product_id": "PRODUCT-1",
                "seller_sku": "SKU-2",
                "parent_sku": "PARENT-2",
                "product_name": "Green Tea",
                "variation_name": "Original",
                "unit_selling_price": "12.00",
            },
            {
                "product_id": "PRODUCT-2",
                "seller_sku": "SKU-B",
                "parent_sku": "PARENT-B",
                "product_name": "Blue Tea",
                "variation_name": "Original",
                "unit_selling_price": "6.00",
            },
        )
    )


class _Repository(InMemoryHistoricalInvoiceRepository):
    def __init__(self, items=(_item(),)):
        super().__init__()
        self.items = tuple(items)
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1

    def get_items_by_order_ids(self, platform, order_ids):
        return {"ORDER-1": self.items} if self.items else {}


class _Writer:
    def __init__(
        self, order=None, items=(_item(),), committed_statements=(), order_adjustments=()
    ):
        self.order = order
        self.items = tuple(items)
        self.committed_statements = tuple(committed_statements)
        self.order_adjustments = tuple(order_adjustments)
        self.writes = 0

    def reload_commit_state(self):
        orders = {} if self.order is None else {"ORDER-1": self.order}
        return StatementCommitState(
            orders=orders,
            committed_statements=self.committed_statements,
            items=self.items,
            order_adjustments=self.order_adjustments,
        )

    def reload_active_commit_state(self):
        return self.reload_commit_state()

    def write_statement_batch(self, plan):
        self.writes += 1


class _FullOrderCurrentRepository:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.reads = 0

    def read_snapshot(self):
        self.reads += 1
        return self.snapshot


class _CurrentCanonicalWriter:
    """Small stateful writer proving the service-level Current-to-canonical plan."""

    def __init__(self):
        self.orders = {}
        self.items = ()
        self.committed_statements = ()
        self.order_adjustments = ()
        self.writes = 0
        self.plans = []

    def reload_commit_state(self):
        return StatementCommitState(
            orders=dict(self.orders),
            committed_statements=self.committed_statements,
            items=self.items,
            order_adjustments=self.order_adjustments,
        )

    def reload_active_commit_state(self):
        return self.reload_commit_state()

    def write_statement_batch(self, plan):
        self.writes += 1
        self.plans.append(plan)
        self.orders.update({order.order_id: order for order in plan.invoice_order_inserts})
        self.items = (*self.items, *plan.invoice_item_inserts)
        self.committed_statements = (
            *self.committed_statements,
            CommittedStatementReference(
                file_hash=plan.statement.file_hash,
                statement_period_from=plan.statement.statement_period_from,
                statement_period_to=plan.statement.statement_period_to,
            ),
        )


def _review(
    monkeypatch,
    *,
    order=_DEFAULT_ORDER,
    items=(_item(),),
    statement=None,
    product_master=None,
    order_adjustments=(),
):
    statement = statement or _statement()
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )
    repository = _Repository(items)
    writer = _Writer(
        _order() if order is _DEFAULT_ORDER else order,
        items,
        order_adjustments=order_adjustments,
    )
    current_snapshot = _full_order_snapshot_from_items(
        () if order is None else ("ORDER-1",),
        items,
    )
    review = review_statement_upload(
        b"synthetic",
        source_filename="statement.xlsx",
        batch_id="batch-1",
        uploaded_by="admin",
        repository=repository,
        writer=writer,
        product_master=product_master or _master(),
        full_order_repository=_FullOrderCurrentRepository(current_snapshot),
        now=lambda: NOW,
    )
    return review, repository, writer


def _full_order_snapshot_from_items(
    order_ids: tuple[str, ...],
    items: tuple[CanonicalInvoiceItem, ...] | list[CanonicalInvoiceItem],
) -> FullOrderCurrentSnapshot:
    current_orders = tuple(
        FullOrderCurrentOrder(
            market="shopee_my",
            order_id=order_id,
            order_status="Completed",
            order_created_date=date(2026, 8, 10),
            source_file="full-order.xlsx",
            snapshot_hash=f"current-{order_id}",
            last_synced_at=NOW,
        )
        for order_id in order_ids
    )
    current_items = tuple(
        FullOrderCurrentItem(
            market="shopee_my",
            order_id=item.order_id,
            item_index=item.item_index,
            source_parent_sku="PARENT-1",
            source_seller_sku=item.seller_sku,
            resolved_seller_sku=item.seller_sku,
            nav="5001",
            product_name=item.product_name,
            variation=item.variation,
            quantity=item.quantity,
            unit_price=Decimal("12.00"),
            product_subtotal=item.line_subtotal,
            return_refund_status="",
        )
        for item in items
        if item.order_id in order_ids
    )
    return FullOrderCurrentSnapshot(current_orders, current_items)


def test_statement_review_is_ready_when_full_order_existence_is_complete(monkeypatch):
    review, _, _ = _review(monkeypatch)

    assert review.ready is True
    assert review.blockers == ()
    assert review.evidence_coverage.already_verified_order_ids == ()
    assert review.evidence_coverage.ready_from_full_order_order_ids == ("ORDER-1",)
    assert review.evidence_coverage.additional_evidence == ()
    assert review.stage.order_reconciliations[0].status == "Unmatched Order"
    assert review.active_gate == "FULL_ORDER_CURRENT_ORDER_EXISTENCE"
    assert review.reconciliation_v2 is None
    assert review.sku_matches.eligible_for_commit is False


def test_my_uses_the_shared_any_period_full_order_existence_gate(monkeypatch):
    base = _statement()
    statement = replace(
        base,
        statement_period_from=date(2026, 8, 29),
        statement_period_to=date(2026, 9, 9),
        income_rows=tuple(
            replace(
                row,
                order_creation_date=date(2026, 8, 29),
                payout_completed_date=date(2026, 9, 9),
            )
            for row in base.income_rows
        ),
    )
    review, _, _ = _review(monkeypatch, statement=statement)

    assert SHOPEE_MY.statement_order_existence_gate is True
    assert SHOPEE_MY.statement_semantic_overlap_dedupe is True
    assert review.active_gate == "FULL_ORDER_CURRENT_ORDER_EXISTENCE"
    assert review.reconciliation_v2 is None
    assert review.plan is not None
    assert review.plan.authority == "FULL_ORDER_CURRENT"
    assert review.plan.statement.statement_period_from == date(2026, 8, 29)
    assert review.plan.statement.statement_period_to == date(2026, 9, 9)


def test_statement_commit_readiness_does_not_use_legacy_invoice_totals(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        order=_order("9.50", income_type="Estimated", fees="-0.50"),
    )

    assert review.ready is True
    assert review.reconciliation_v2 is None
    assert review.plan is not None
    assert review.plan.authority == "FULL_ORDER_CURRENT"


def test_unrelated_source_issue_stays_blocking_without_legacy_reconciliation(monkeypatch):
    statement = replace(
        _statement(),
        source_value_issues=(
            SourceValueIssue(
                code="unrecognized_income_row",
                sheet="Income",
                row_number=742,
                column="View By",
                message="Income row 742 has invalid or missing View By: ''.",
            ),
        ),
    )

    review, _, _ = _review(monkeypatch, statement=statement)
    presentation = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id=review.batch_id,
        review=review,
    )
    assert review.reconciliation_v2 is None
    assert presentation.reconciliation.available is True
    assert any(
        "Income row 742" in issue.reason
        for issue in presentation.validation.blocking_issues
    )


def test_missing_order_coverage_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(monkeypatch, order=None)

    assert review.ready is False
    assert review.plan is None
    assert review.blockers == ()
    assert review.persistence_blockers[-1] == ADDITIONAL_EVIDENCE_REQUIRED
    assert review.reconciliation_v2 is None
    assert review.evidence_coverage.additional_evidence[0].order_id == "ORDER-1"


def test_partial_coverage_does_not_run_downstream_reconciliation(monkeypatch):
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.evaluate_statement_reconciliation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("reconciliation must not run before coverage is complete")
        ),
    )

    review, _, _ = _review(monkeypatch, order=None, items=())

    assert review.reconciliation_v2 is None
    assert review.evidence_coverage.additional_evidence[0].order_id == "ORDER-1"


def test_refresh_does_not_use_legacy_invoice_data_to_clear_missing_full_order(
    monkeypatch,
):
    review, repository, writer = _review(monkeypatch, order=None, items=())
    assert review.evidence_coverage.additional_evidence[0].order_id == "ORDER-1"

    writer.order = _order(refund=None)
    writer.items = (_item("Other Product", seller_sku="OTHER-SKU"),)

    refreshed = refresh_statement_review(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(),
        now=lambda: NOW,
    )

    assert refreshed.evidence_coverage.complete is False
    assert refreshed.evidence_coverage.additional_evidence[0].order_id == "ORDER-1"


def test_refresh_replaces_four_missing_orders_with_current_two(monkeypatch):
    order_rows = tuple(
        replace(_income("Order", index + 2), order_id=f"ORDER-{index}")
        for index in range(4)
    )
    statement = replace(
        _statement(),
        income_rows=order_rows,
        summary_total_released=Decimal("40.00"),
        summary_lines=(
            StatementSummaryLine(
                "Summary", 40, "3. Total Released Amount", "TOTAL", None,
                Decimal("40.00"), "RM",
            ),
        ),
    )
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    class CoverageWriter:
        orders: tuple[CanonicalInvoiceOrder, ...] = ()
        items: tuple[CanonicalInvoiceItem, ...] = ()

        def reload_commit_state(self):
            return StatementCommitState(
                orders={order.order_id: order for order in self.orders},
                committed_statements=(),
                items=self.items,
            )

        def reload_active_commit_state(self):
            return self.reload_commit_state()

    writer = CoverageWriter()
    review = review_statement_upload(
        b"synthetic",
        source_filename="statement.xlsx",
        batch_id="batch-coverage-refresh",
        uploaded_by="admin",
        repository=_Repository(()),
        writer=writer,
        product_master=_master(),
        now=lambda: NOW,
    )
    assert tuple(item.order_id for item in review.evidence_coverage.additional_evidence) == (
        "ORDER-0", "ORDER-1", "ORDER-2", "ORDER-3",
    )

    writer.orders = tuple(
        replace(_order(), order_id=f"ORDER-{index}") for index in range(2)
    )
    writer.items = tuple(
        replace(_item(), order_id=f"ORDER-{index}") for index in range(2)
    )
    refreshed = refresh_statement_review(
        review,
        repository=_Repository(()),
        writer=writer,
        product_master=_master(),
        now=lambda: NOW,
    )

    assert tuple(item.order_id for item in refreshed.evidence_coverage.additional_evidence) == (
        "ORDER-0", "ORDER-1", "ORDER-2", "ORDER-3",
    )


def test_full_order_without_product_items_satisfies_existence_gate(monkeypatch):
    review, _, _ = _review(monkeypatch, order=_order(refund=None), items=())

    assert review.reconciliation_v2 is None
    assert review.commit_ready is True
    assert review.evidence_coverage.additional_evidence == ()


def test_partial_coverage_adapter_keeps_statement_summary_and_hides_reconciliation(
    monkeypatch,
):
    review, _, _ = _review(monkeypatch, order=None, items=())

    result = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id=review.batch_id,
        review=review,
    )
    summary = {item.label: item.value for item in result.source_summary.items}

    assert result.batch_status == "Not Ready"
    assert len(result.validation.blocking_issues) == 1
    assert result.reconciliation.available is False
    assert result.reconciliation.status == "Full Order coverage required"
    assert summary["Statement Period"].startswith("10/08/2026")
    assert summary["Statement Period"].endswith("16/08/2026")


def test_partial_coverage_counts_exact_missing_normal_orders():
    order_rows = tuple(
        replace(_income("Order", index + 2), order_id=f"ORDER-{index}")
        for index in range(10)
    )
    statement = replace(_statement(), income_rows=order_rows)
    orders = tuple(replace(_order(), order_id=f"ORDER-{index}") for index in range(6))
    items = tuple(replace(_item(), order_id=f"ORDER-{index}") for index in range(6))

    coverage = classify_statement_invoice_coverage(statement, orders, items)

    assert len(coverage.statement_order_ids) == 10
    assert len(coverage.invoice_order_ids) == 6
    assert coverage.missing_invoice_order_ids == (
        "ORDER-6", "ORDER-7", "ORDER-8", "ORDER-9",
    )
    assert coverage.missing_invoice_item_order_ids == ()


def test_coverage_separates_missing_order_from_missing_items():
    statement = _statement()

    coverage = classify_statement_invoice_coverage(statement, (_order(),), ())

    assert coverage.missing_invoice_order_ids == ()
    assert coverage.missing_invoice_item_order_ids == ("ORDER-1",)


def test_adjustment_only_linked_order_does_not_create_coverage_requirement():
    adjustment = SettlementAdjustment(
        sequence_no="ADJUSTMENT-1",
        adjustment_complete_date=date(2026, 8, 18),
        adjustment_type="Other",
        adjustment_reason="Post-order correction",
        adjustment_amount=Decimal("1.00"),
        linked_order_id="OLD-ORDER-123",
        payout_completed_date=date(2026, 8, 18),
        source_row_number=2,
    )
    statement = replace(_statement(), income_rows=(), adjustments=(adjustment,))

    coverage = classify_statement_invoice_coverage(statement, (), ())

    assert coverage.statement_order_ids == ()
    assert coverage.complete is True


def test_normal_order_plus_adjustment_counts_order_once():
    adjustment = SettlementAdjustment(
        sequence_no="ADJUSTMENT-1",
        adjustment_complete_date=date(2026, 8, 18),
        adjustment_type="Other",
        adjustment_reason="Post-order correction",
        adjustment_amount=Decimal("1.00"),
        linked_order_id="ORDER-1",
        payout_completed_date=date(2026, 8, 18),
        source_row_number=2,
    )
    statement = replace(_statement(), adjustments=(adjustment,))

    coverage = classify_statement_invoice_coverage(statement, (), ())

    assert coverage.statement_order_ids == ("ORDER-1",)
    assert coverage.missing_invoice_order_ids == ("ORDER-1",)


def test_unresolved_sku_does_not_replace_full_order_existence_gate(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )

    assert review.ready is True
    assert review.commit_ready is True
    assert review.sku_matches.matches == ()
    assert review.plan is not None


def test_commit_ignores_legacy_invoice_state(monkeypatch):
    review, repository, writer = _review(monkeypatch)
    writer.order = _order("9.00")

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is True
    assert writer.writes == 1


def test_ready_statement_commit_reaches_existing_atomic_writer(monkeypatch):
    review, repository, writer = _review(monkeypatch)

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is True
    assert writer.writes == 1


def test_valid_weeks_keep_validate_and_fresh_precommit_ready_without_invoices(monkeypatch):
    """The active path reuses Full Order + Statement evidence for every week."""

    for period_from, period_to, file_hash in (
        (date(2026, 8, 10), date(2026, 8, 16), "week-a"),
        (date(2026, 9, 7), date(2026, 9, 13), "week-b"),
    ):
        statement = replace(
            _statement(),
            statement_period_from=period_from,
            statement_period_to=period_to,
            file_hash=file_hash,
            income_rows=tuple(
                replace(
                    row,
                    order_creation_date=period_from,
                    payout_completed_date=period_to,
                )
                for row in _statement().income_rows
            ),
        )
        monkeypatch.setattr(
            "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
            lambda *args, existing_orders=(), existing_statements=(), _statement=statement, **kwargs: (
                stage_parsed_shopee_weekly_statement(
                    _statement,
                    existing_orders=existing_orders,
                    existing_statements=existing_statements,
                )
            ),
        )
        writer = _Writer(order=None, items=())
        full_order_repository = _FullOrderCurrentRepository(
            _full_order_snapshot_from_items(("ORDER-1",), (_item(),))
        )
        review = review_statement_upload(
            b"statement-without-invoice",
            source_filename="statement.xlsx",
            batch_id=f"batch-{file_hash}",
            uploaded_by="admin",
            writer=writer,
            product_master=_master(),
            full_order_repository=full_order_repository,
            now=lambda: NOW,
        )
        refreshed = refresh_statement_review(
            review,
            writer=writer,
            product_master=_master(),
            full_order_repository=full_order_repository,
            now=lambda: NOW,
        )

        assert review.commit_ready is True, (
            review.blockers,
            review.persistence_blockers,
        )
        assert refreshed.commit_ready is True, (
            refreshed.blockers,
            refreshed.persistence_blockers,
        )
        assert refreshed.evidence_version == review.evidence_version
        assert review.plan.authority == "FULL_ORDER_CURRENT"


def test_statement_product_price_conflict_is_not_a_retired_product_master_gate(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        statement=_statement(sku_rows=(_income("Sku", 3, product="11.00"),)),
    )

    assert review.commit_ready is True
    assert review.reconciliation_v2 is None


def test_sg_statement_review_cannot_fallback_to_my_full_order(monkeypatch):
    statement = _statement()
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )
    my_snapshot = _full_order_snapshot_from_items(("ORDER-1",), (_item(),))
    writer = _Writer(order=None, items=())
    blocked = review_statement_upload(
        b"sg-statement",
        source_filename="statement.xlsx",
        batch_id="sg-batch",
        uploaded_by="admin",
        writer=writer,
        product_master=_master(),
        full_order_repository=_FullOrderCurrentRepository(my_snapshot),
        market_context=SHOPEE_SG,
        now=lambda: NOW,
    )
    sg_snapshot = FullOrderCurrentSnapshot(
        tuple(replace(order, market="shopee_sg") for order in my_snapshot.orders),
        tuple(replace(item, market="shopee_sg") for item in my_snapshot.items),
    )
    ready = review_statement_upload(
        b"sg-statement",
        source_filename="statement.xlsx",
        batch_id="sg-batch",
        uploaded_by="admin",
        writer=writer,
        product_master=_master(),
        full_order_repository=_FullOrderCurrentRepository(sg_snapshot),
        market_context=SHOPEE_SG,
        now=lambda: NOW,
    )

    assert blocked.commit_ready is False
    assert ready.commit_ready is True, (ready.blockers, ready.persistence_blockers)


def test_clean_full_order_current_statement_commit_writes_no_canonical_invoice_rows(
    monkeypatch,
):
    """A canonical-missing normal order may be proven without a PDF."""

    statement = _statement()
    current_order = FullOrderCurrentOrder(
        market="shopee_my",
        order_id="ORDER-1",
        order_status="Completed",
        order_created_date=date(2026, 8, 10),
        source_file="full-order.xlsx",
        snapshot_hash="current-snapshot-hash",
        last_synced_at=NOW,
    )
    current_item = FullOrderCurrentItem(
        market="shopee_my",
        order_id="ORDER-1",
        item_index=0,
        source_parent_sku="PARENT-1",
        source_seller_sku="SKU-1",
        resolved_seller_sku="SKU-1",
        nav="5001",
        product_name="Green Tea",
        variation="Original",
        quantity=1,
        unit_price=Decimal("12.00"),
        product_subtotal=Decimal("10.00"),
        return_refund_status="",
    )
    current_snapshot = FullOrderCurrentSnapshot((current_order,), (current_item,))
    current_repository = _FullOrderCurrentRepository(current_snapshot)
    writer = _CurrentCanonicalWriter()
    repository = _Repository(items=())
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    review = review_statement_upload(
        b"current-statement",
        source_filename="statement.xlsx",
        batch_id="current-batch",
        uploaded_by="admin",
        repository=repository,
        writer=writer,
        product_master=_master(),
        full_order_repository=current_repository,
        now=lambda: NOW,
    )

    assert review.commit_ready is True
    assert review.full_order_canonical_inserts == ()
    assert review.plan is not None
    assert review.plan.authority == "FULL_ORDER_CURRENT"
    assert review.plan.invoice_order_inserts == ()
    assert review.plan.invoice_item_inserts == ()

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
        full_order_repository=current_repository,
    )

    assert attempt.committed is True
    assert writer.writes == 1
    assert writer.orders == {}
    assert writer.items == ()
    assert current_repository.reads >= 2
    assert current_repository.snapshot == current_snapshot

    duplicate = review_statement_upload(
        b"current-statement",
        source_filename="statement.xlsx",
        batch_id="current-batch-duplicate",
        uploaded_by="admin",
        repository=repository,
        writer=writer,
        product_master=_master(),
        full_order_repository=current_repository,
        now=lambda: NOW,
    )
    duplicate_attempt = commit_statement_review(
        duplicate,
        repository=repository,
        writer=writer,
        load_product_master=_master,
        full_order_repository=current_repository,
    )

    assert duplicate.stage.already_imported is True
    assert duplicate_attempt.reasons == ("ALREADY_IMPORTED",)
    assert writer.writes == 1
    assert writer.orders == {}


def test_marked_full_order_item_commits_only_with_exact_nonzero_item_refund(
    monkeypatch,
):
    statement = _statement(sku_rows=(_income("Sku", 3, refund="-2.00"),))
    current_order = FullOrderCurrentOrder(
        market="shopee_my",
        order_id="ORDER-1",
        order_status="Completed",
        order_created_date=date(2026, 8, 10),
        source_file="full-order.xlsx",
        snapshot_hash="current-snapshot-hash",
        last_synced_at=NOW,
    )
    current_item = FullOrderCurrentItem(
        market="shopee_my",
        order_id="ORDER-1",
        item_index=0,
        source_parent_sku="PARENT-1",
        source_seller_sku="SKU-1",
        resolved_seller_sku="SKU-1",
        nav="5001",
        product_name="Green Tea",
        variation="Original",
        quantity=1,
        unit_price=Decimal("12.00"),
        product_subtotal=Decimal("10.00"),
        return_refund_status="Request Approved",
    )
    current_snapshot = FullOrderCurrentSnapshot((current_order,), (current_item,))
    current_repository = _FullOrderCurrentRepository(current_snapshot)
    writer = _CurrentCanonicalWriter()
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    review = review_statement_upload(
        b"marked-current-statement",
        source_filename="statement.xlsx",
        batch_id="marked-current-batch",
        uploaded_by="admin",
        repository=_Repository(items=()),
        writer=writer,
        product_master=_master(),
        full_order_repository=current_repository,
        now=lambda: NOW,
    )

    assert review.commit_ready is True
    assert review.evidence_coverage.ready_from_full_order_order_ids == ("ORDER-1",)
    assert review.full_order_canonical_inserts == ()
    assert current_snapshot.items[0].return_refund_status == "Request Approved"

    attempt = commit_statement_review(
        review,
        repository=_Repository(items=()),
        writer=writer,
        load_product_master=_master,
        full_order_repository=current_repository,
    )

    assert attempt.committed is True
    assert writer.items == ()


def test_statement_evidence_coverage_keeps_clean_current_orders_when_one_marker_blocks(
    monkeypatch,
):
    """A Current exception must not hide independently proven normal orders."""

    order_ids = ("CANONICAL", "CURRENT-CLEAN-1", "CURRENT-CLEAN-2", "CURRENT-REFUND")
    statement = _statement_for_order_ids(order_ids)
    canonical_order = replace(_order(refund=None), order_id="CANONICAL")
    canonical_item = replace(_item(), order_id="CANONICAL")
    writer = _CurrentCanonicalWriter()
    writer.orders = {"CANONICAL": canonical_order}
    writer.items = (canonical_item,)
    current_snapshot = _full_order_snapshot(
        "CANONICAL",
        "CURRENT-CLEAN-1",
        "CURRENT-CLEAN-2",
        "CURRENT-REFUND",
        marker_order_id="CURRENT-REFUND",
    )
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    review = review_statement_upload(
        b"evidence-coverage",
        source_filename="statement.xlsx",
        batch_id="evidence-coverage",
        uploaded_by="admin",
        repository=_Repository(items=()),
        writer=writer,
        product_master=_master(),
        full_order_repository=_FullOrderCurrentRepository(current_snapshot),
        now=lambda: NOW,
    )

    coverage = review.evidence_coverage
    assert coverage is not None
    assert coverage.already_verified_order_ids == ()
    assert coverage.ready_from_full_order_order_ids == (
        "CANONICAL",
        "CURRENT-CLEAN-1",
        "CURRENT-CLEAN-2",
        "CURRENT-REFUND",
    )
    assert coverage.additional_evidence == ()
    assert review.full_order_canonical_inserts == ()
    assert ADDITIONAL_EVIDENCE_REQUIRED not in review.persistence_blockers


def test_statement_evidence_coverage_requires_current_evidence_when_canonical_is_missing(
    monkeypatch,
):
    statement = _statement_for_order_ids(("CURRENT-ABSENT",))
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    review = review_statement_upload(
        b"evidence-absent",
        source_filename="statement.xlsx",
        batch_id="evidence-absent",
        uploaded_by="admin",
        repository=_Repository(items=()),
        writer=_CurrentCanonicalWriter(),
        product_master=_master(),
        full_order_repository=_FullOrderCurrentRepository(FullOrderCurrentSnapshot((), ())),
        now=lambda: NOW,
    )

    coverage = review.evidence_coverage
    assert coverage is not None
    assert coverage.ready_from_full_order_order_ids == ()
    assert coverage.additional_evidence == (
        type(coverage.additional_evidence[0])(
            "CURRENT-ABSENT", None, "Full Order Current order is missing for this market."
        ),
    )


def _statement_for_order_ids(order_ids: tuple[str, ...]) -> ParsedShopeeWeeklyStatement:
    sku_rows = tuple(
        replace(_income("Sku", index + 10), order_id=order_id, sequence_no=f"SKU-{index}")
        for index, order_id in enumerate(order_ids)
    )
    order_rows = tuple(
        replace(_income("Order", index + 2), order_id=order_id, sequence_no=f"ORDER-{index}")
        for index, order_id in enumerate(order_ids)
    )
    total = Decimal("10.00") * len(order_ids)
    return replace(
        _statement(),
        income_rows=(*order_rows, *sku_rows),
        summary_total_released=total,
        summary_lines=(
            StatementSummaryLine("Summary", 40, "3. Total Released Amount", "TOTAL", None, total, "RM"),
        ),
    )


def _full_order_snapshot(*order_ids: str, marker_order_id: str | None = None) -> FullOrderCurrentSnapshot:
    orders = tuple(
        FullOrderCurrentOrder(
            market="shopee_my",
            order_id=order_id,
            order_status="Completed",
            order_created_date=date(2026, 8, 10),
            source_file="full-order.xlsx",
            snapshot_hash=f"current-{order_id}",
            last_synced_at=NOW,
        )
        for order_id in order_ids
    )
    items = tuple(
        FullOrderCurrentItem(
            market="shopee_my",
            order_id=order_id,
            item_index=0,
            source_parent_sku="PARENT-1",
            source_seller_sku="SKU-1",
            resolved_seller_sku="SKU-1",
            nav="5001",
            product_name="Green Tea",
            variation="Original",
            quantity=1,
            unit_price=Decimal("12.00"),
            product_subtotal=Decimal("10.00"),
            return_refund_status="Request Approved" if order_id == marker_order_id else "",
        )
        for order_id in order_ids
    )
    return FullOrderCurrentSnapshot(orders, items)


def test_exact_committed_statement_is_terminal_success_without_reconciliation_or_write(
    monkeypatch,
):
    statement = _statement()
    reference = CommittedStatementReference(
        file_hash=statement.file_hash,
        statement_period_from=statement.statement_period_from,
        statement_period_to=statement.statement_period_to,
    )
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, **kwargs: stage_parsed_shopee_weekly_statement(
            statement,
            existing_statements=(reference,),
        ),
    )
    repository = _Repository()
    writer = _Writer(_order(), committed_statements=(reference,))

    review = review_statement_upload(
        b"same-statement-bytes",
        source_filename="statement.xlsx",
        batch_id="duplicate-batch",
        uploaded_by="admin",
        repository=repository,
        writer=writer,
        product_master=_master(),
        now=lambda: NOW,
    )
    presentation = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id=review.batch_id,
        review=review,
    )
    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert review.stage.already_imported is True
    assert review.reconciliation_v2 is None
    assert review.blockers == ()
    assert review.persistence_blockers == ()
    assert presentation.batch_status == "ALREADY_IMPORTED"
    assert presentation.validation.blocking_issues == ()
    assert presentation.reconciliation.status == "ALREADY_IMPORTED"
    assert attempt.committed is False
    assert attempt.reasons == ("ALREADY_IMPORTED",)
    assert writer.writes == 0


def test_data_import_statement_review_is_compact_and_commit_ready(
    tmp_path, monkeypatch
):
    review, _, _ = _review(monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "batch-1"
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 4
    app.session_state["weekly_statement_stage"] = review.stage
    app.session_state["weekly_statement_review"] = review

    app.run(timeout=20)

    assert app.exception == []
    assert "Reconciliation V2 review" in {
        element.value for element in app.subheader
    }
    review_table = next(
        element.value
        for element in app.dataframe
        if "Identity" in element.value.columns
    )
    assert review_table.iloc[0]["Identity"] == "ITEM"
    assert review_table.iloc[0]["Merchandise"] == "Reconciled"
    assert review_table.iloc[0]["Settlement"] == "Exact"
    assert any(
        button.label == "Continue to review & commit" and not button.disabled
        for button in app.button
    )

    app.session_state["data_import_step"] = 5
    app.run(timeout=20)

    assert "Reconciliation V2 review" not in {
        element.value for element in app.subheader
    }
    commit = next(button for button in app.button if button.label == "Commit Statement")
    assert not commit.disabled
    assert commit.proto.type == "primary"
    assert "Reconciliation review complete — V2 found no business blockers." in {
        element.value for element in app.success
    }


def test_data_import_exact_statement_reupload_is_success_not_attention(
    tmp_path, monkeypatch
):
    statement = _statement()
    reference = CommittedStatementReference(
        file_hash=statement.file_hash,
        statement_period_from=statement.statement_period_from,
        statement_period_to=statement.statement_period_to,
    )
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, **kwargs: stage_parsed_shopee_weekly_statement(
            statement,
            existing_statements=(reference,),
        ),
    )
    review = review_statement_upload(
        b"same-statement-bytes",
        source_filename="statement.xlsx",
        batch_id="duplicate-batch",
        uploaded_by="admin",
        repository=_Repository(),
        writer=_Writer(_order(), committed_statements=(reference,)),
        product_master=_master(),
        now=lambda: NOW,
    )
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "duplicate-batch"
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 3
    app.session_state["weekly_statement_stage"] = review.stage
    app.session_state["weekly_statement_review"] = review

    app.run(timeout=20)

    assert app.exception == []
    assert any("Statement already imported" in element.value for element in app.success)
    assert "Needs Attention" not in {element.value for element in app.subheader}
    assert all("unresolved issue" not in element.value for element in app.error)
    assert not any("This Statement item needs review" in element.value for element in app.error)
    assert {"View Weekly Billing", "Upload another Statement"} <= {
        button.label for button in app.button
    }


def test_data_import_ui_has_no_direct_google_write_call():
    source = Path("src/invoice_app/ui/data_import.py").read_text(encoding="utf-8")

    assert "batch_update(" not in source
    assert "write_statement_batch(" not in source
    assert "commit_statement_review(" in source
    assert "View Weekly Billing" in source
    assert "Live Analysis" not in source


def _group_review(monkeypatch):
    statement = _statement(
        sku_rows=(
            _income("Sku", 3, product="10.00", sequence="1"),
            _income("Sku", 4, product="10.00", sequence="2"),
        )
    )
    return _review(
        monkeypatch,
        order=_order("20.00", product="20.00"),
        items=(
            _item(item_index=0),
            _item(item_index=1),
        ),
        statement=statement,
    )


def test_v2_group_is_valid_review_with_unresolved_allocation_but_no_fake_mapping(
    monkeypatch,
):
    review, _, _ = _group_review(monkeypatch)
    order = review.reconciliation_v2.orders[0]

    assert review.ready is True
    assert order.summary.identity_scope.value == "GROUP"
    assert order.summary.allocation_resolved is False
    assert order.evidence.identities[0].selected_pairs == ()
    assert any("individual Statement row allocation" in item for item in review.limitations)


def test_v2_group_plan_persists_source_rows_with_blank_item_index_and_zero_enrichment(
    monkeypatch,
):
    review, _, _ = _group_review(monkeypatch)
    plan = review.plan
    assert plan is not None

    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    sku_rows = [
        row
        for row in plan.rows
        if row[positions["record_type"]] == "SKU"
    ]
    assert len(sku_rows) == 2
    assert {row[positions["matched_item_index"]] for row in sku_rows} == {""}
    assert {
        row[positions["match_method"]] for row in sku_rows
    } == {"Product Group Multiset (Unallocated)"}
    assert plan.invoice_item_updates == ()
    assert plan.protected_invoice_items == ()


def test_v2_mixed_item_and_group_order_updates_only_the_exact_item(monkeypatch):
    statement = _statement(
        sku_rows=(
            _income(
                "Sku",
                3,
                product="5.00",
                sequence="1",
                product_id="PRODUCT-2",
                product_name="Blue Tea",
            ),
            _income("Sku", 4, product="10.00", sequence="2"),
            _income("Sku", 5, product="10.00", sequence="3"),
        )
    )
    review, _, _ = _review(
        monkeypatch,
        order=_order("25.00", product="25.00"),
        items=(
            _item("Blue Tea", item_index=0, seller_sku="SKU-B", subtotal="5.00"),
            _item(item_index=1, seller_sku="SKU-1"),
            _item(item_index=2, seller_sku="SKU-2"),
        ),
        statement=statement,
        product_master=_mixed_master(),
    )
    plan = review.plan
    assert plan is not None

    assert plan.invoice_item_updates == ()
    assert plan.protected_invoice_items == ()


def test_refund_sensitive_group_preserves_source_without_item_allocation(monkeypatch):
    statement = _statement(
        sku_rows=(
            _income("Sku", 3, refund="-2.00", sequence="1"),
            _income("Sku", 4, sequence="2"),
        )
    )
    review, _, _ = _review(
        monkeypatch,
        order=_order(
            "18.00",
            income="18.00",
            product="20.00",
            refund="-2.00",
        ),
        items=(_item(item_index=0), _item(item_index=1)),
        statement=statement,
    )
    plan = review.plan
    assert plan is not None

    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    sku_rows = [
        row for row in plan.rows if row[positions["record_type"]] == "SKU"
    ]
    assert [row[positions["statement_refund_amount"]] for row in sku_rows] == [
        "-2.00",
        "0.00",
    ]
    assert all(not row[positions["matched_item_index"]] for row in sku_rows)
    assert plan.invoice_item_updates == ()


def test_promotion_group_never_allocates_statement_money_to_members(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        order=_order("18.00", product="18.00"),
        items=(
            _item(
                item_index=0,
                subtotal="9.00",
                promotion_group_id="PROMO-1",
                source_group_total="18.00",
            ),
            _item(
                item_index=1,
                subtotal="9.00",
                promotion_group_id="PROMO-1",
                source_group_total="18.00",
            ),
        ),
        statement=_statement(
            sku_rows=(
                _income("Sku", 3, product="9.00", sequence="1"),
                _income("Sku", 4, product="9.00", sequence="2"),
            )
        ),
    )

    assert review.plan is not None
    assert review.plan.invoice_item_updates == ()
    assert review.reconciliation_v2.orders[0].evidence.promotion.promotion_group_ids == ()


def test_legacy_matcher_disagreement_is_not_exposed_as_v2_business_blocker(monkeypatch):
    review, _, _ = _group_review(monkeypatch)
    result = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id="batch-1",
        review=review,
    )

    assert review.sku_matches.eligible_for_commit is False
    assert review.plan is not None
    assert review.plan.invoice_item_updates == ()
    assert review.plan.protected_invoice_items == ()
    assert result.validation.blocking_issues == ()
    assert result.batch_status == "Ready to Commit"
    assert result.commit_readiness.database_commit_available is True
    assert all(
        "exactly one" not in issue.reason.casefold()
        for issue in (*result.validation.blocking_issues, *result.validation.warnings)
    )


def test_v2_adapter_separates_identity_merchandise_and_exact_settlement(monkeypatch):
    review, _, _ = _review(monkeypatch)
    result = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id="batch-1",
        review=review,
    )
    summary = {item.label: item.value for item in result.reconciliation.summary}

    assert summary["Orders"] == "1 / 1"
    assert summary["Products"] == "1 / 1"
    assert summary["Identity"] == "1 ITEM · 0 GROUP · 0 unresolved"
    assert summary["Merchandise"] == "1 / 1"
    assert summary["Settlement"] == "1 Exact · 0 Explained · 0 unexplained"


def test_legacy_invoice_settlement_values_do_not_change_active_statement_result(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        order=_order(
            None,
            income="9.00",
            income_type="Estimated",
            fees="-1.00",
        ),
        statement=_statement(
            sku_rows=(_income("Sku", 3, fees="0.00"),)
        ),
    )
    result = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id="batch-1",
        review=review,
    )

    assert review.reconciliation_v2 is None
    assert review.blockers == ()
    assert result.validation.blocking_issues == ()


def test_legacy_invoice_conflict_is_not_an_active_statement_blocker(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        order=_order(
            None,
            income="10.00",
            income_type="Estimated",
            fees=None,
        ),
        statement=_statement(
            sku_rows=(_income("Sku", 3, fees="-1.00"),)
        ),
    )

    assert review.reconciliation_v2 is None
    assert review.ready is True
    assert review.commit_ready is True


def test_statement_refund_is_preserved_without_using_invoice_refund(monkeypatch):
    invoice = _order(
        None,
        income="10.00",
        income_type="Estimated",
        refund=None,
    )
    review, _, _ = _review(
        monkeypatch,
        order=invoice,
        statement=_statement(
            sku_rows=(_income("Sku", 3, refund="-2.00"),)
        ),
    )
    assert review.reconciliation_v2 is None
    assert review.plan is not None
    assert invoice.refund_amount is None


def test_statement_adjustment_remains_separate_from_original_settlement(monkeypatch):
    adjustment = SettlementAdjustment(
        sequence_no="A1",
        adjustment_complete_date=None,
        adjustment_type="Other",
        adjustment_reason="Separate",
        adjustment_amount=Decimal("122.20"),
        linked_order_id="ORDER-1",
        payout_completed_date=None,
        source_row_number=2,
    )
    review, _, _ = _review(
        monkeypatch,
        statement=_statement(adjustments=(adjustment,)),
    )
    order = review.reconciliation_v2.orders[0]

    assert review.reconciliation_v2.statement_adjustment_total == Decimal("122.20")
    assert order.summary.settlement_basis.value == "EXACT"
    assert order.evidence.settlement.statement_total == Decimal("10.00")


def test_review_does_not_use_legacy_cross_period_invoice_adjustments(monkeypatch):
    adjustment = make_statement_adjustment(
        linked_order_id="ORDER-1",
        adjustment_description="Return Refund Adjustment/Compensation",
        adjustment_reason="Compensation",
        adjustment_complete_date=date(2026, 9, 2),
        adjustment_amount=Decimal("137.24"),
        payout_completed_date=date(2026, 9, 3),
        statement_batch_id="later-committed-batch",
        statement_sequence_no="A1",
        statement_source_filename="later-statement.xlsx",
        statement_file_hash="later-committed-statement-hash",
        first_observed_at=NOW,
    )
    assert adjustment is not None
    review, _, _ = _review(
        monkeypatch,
        order=_order("130.36", income="-6.88", product="-6.88"),
        items=(_item(subtotal="-6.88"),),
        statement=_statement(sku_rows=(_income("Sku", 3, product="-6.88"),)),
        order_adjustments=(adjustment,),
    )
    settlement = review.reconciliation_v2.orders[0].evidence.settlement

    assert review.ready is True, review.blockers
    assert settlement.invoice_basis == Decimal("-6.88")
    assert settlement.statement_total == Decimal("-6.88")
    assert review.reconciliation_v2.statement_adjustment_total == Decimal("0.00")
    assert settlement.internal_effects == ()


def test_legacy_cross_period_adjustment_change_does_not_mark_review_stale(monkeypatch):
    adjustment = make_statement_adjustment(
        linked_order_id="ORDER-1",
        adjustment_description="Return Refund Adjustment/Compensation",
        adjustment_reason="Compensation",
        adjustment_complete_date=date(2026, 9, 2),
        adjustment_amount=Decimal("137.24"),
        payout_completed_date=date(2026, 9, 3),
        statement_batch_id="later-committed-batch",
        statement_sequence_no="A1",
        statement_source_filename="later-statement.xlsx",
        statement_file_hash="later-committed-statement-hash",
        first_observed_at=NOW,
    )
    assert adjustment is not None
    review, repository, writer = _review(
        monkeypatch,
        order=_order("130.36", income="-6.88", product="-6.88"),
        items=(_item(subtotal="-6.88"),),
        statement=_statement(sku_rows=(_income("Sku", 3, product="-6.88"),)),
        order_adjustments=(adjustment,),
    )
    writer.order_adjustments = ()

    stale = check_statement_review_currency(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(),
    )
    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert stale.is_current is True
    assert attempt.committed is True
    assert writer.writes == 1


def test_statement_quantity_absence_is_a_nonblocking_source_limitation(monkeypatch):
    review, _, _ = _review(monkeypatch)
    quantity = review.reconciliation_v2.orders[0].evidence.quantity

    assert quantity.invoice_quantity_available is True
    assert quantity.statement_quantity_available is False
    assert review.ready is True
    assert any("no quantity-match claim" in item for item in review.limitations)


def test_product_master_snapshot_change_marks_existing_review_stale(monkeypatch):
    review, repository, writer = _review(monkeypatch)

    current = check_statement_review_currency(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(),
    )
    stale = check_statement_review_currency(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(include_second_candidate=True),
    )

    assert current.is_current is True
    assert stale.is_current is False
    assert "PRODUCT_MASTER_SNAPSHOT_CHANGED" in stale.changed_evidence


def test_full_order_proof_rule_change_marks_existing_review_stale(monkeypatch):
    review, repository, writer = _review(monkeypatch)
    assert review.evidence_version is not None
    legacy_review = replace(
        review,
        evidence_version=replace(
            review.evidence_version,
            full_order_proof_rule_version="legacy-full-order-proof-rule",
        ),
    )

    stale = check_statement_review_currency(
        legacy_review,
        repository=repository,
        writer=writer,
        product_master=_master(),
    )

    assert stale.is_current is False
    assert "FULL_ORDER_PROOF_RULE_CHANGED" in stale.changed_evidence
    assert writer.writes == 0


def test_product_master_change_after_review_blocks_commit_with_zero_write(monkeypatch):
    review, repository, writer = _review(monkeypatch)

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=lambda: _master(include_second_candidate=True),
    )

    assert attempt.committed is False
    assert "STALE_REVIEW:PRODUCT_MASTER_SNAPSHOT_CHANGED" in attempt.reasons
    assert "STALE_REVIEW:RECONCILIATION_RESULT_CHANGED" not in attempt.reasons
    assert writer.writes == 0


def test_statement_source_change_after_review_blocks_commit_with_zero_write(
    monkeypatch,
):
    review, repository, writer = _review(monkeypatch)
    changed_statement = replace(_statement(), file_hash="changed-statement-hash")
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                changed_statement,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is False
    assert "STALE_REVIEW:STATEMENT_FILE_CHANGED" in attempt.reasons
    assert "STALE_REVIEW:RECONCILIATION_RESULT_CHANGED" not in attempt.reasons
    assert writer.writes == 0


def test_statement_row_order_permutation_after_review_is_stale(monkeypatch):
    review, repository, writer = _group_review(monkeypatch)
    original = review.stage.statement
    assert original is not None
    changed = replace(
        original,
        file_hash="permuted-statement-hash",
        income_rows=(original.order_rows[0], *reversed(original.sku_rows)),
    )
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                changed,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is False
    assert "STALE_REVIEW:STATEMENT_FILE_CHANGED" in attempt.reasons
    assert writer.writes == 0


def test_missing_statement_row_after_review_fails_before_write(monkeypatch):
    review, repository, writer = _group_review(monkeypatch)
    original = review.stage.statement
    assert original is not None
    changed = replace(
        original,
        file_hash="missing-row-statement-hash",
        income_rows=(original.order_rows[0], original.sku_rows[0]),
    )
    monkeypatch.setattr(
        "src.invoice_app.services.shopee_statement_import.stage_shopee_weekly_statement",
        lambda *args, existing_orders=(), existing_statements=(), **kwargs: (
            stage_parsed_shopee_weekly_statement(
                changed,
                existing_orders=existing_orders,
                existing_statements=existing_statements,
            )
        ),
    )

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is False
    assert writer.writes == 0


def test_invoice_snapshot_change_does_not_change_active_review(monkeypatch):
    review, repository, writer = _review(monkeypatch)
    writer.order = _order("9.00")

    stale = check_statement_review_currency(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(),
    )

    assert stale.is_current is True
    assert stale.changed_evidence == ()


def test_legacy_invoice_group_change_does_not_change_active_review(
    monkeypatch,
):
    statement = _statement(
        sku_rows=(
            _income("Sku", 3, product="9.00", sequence="1"),
            _income("Sku", 4, product="11.00", sequence="2"),
        )
    )
    review, repository, writer = _review(
        monkeypatch,
        order=_order("20.00", product="20.00"),
        items=(
            _item(item_index=0, subtotal="10.00"),
            _item(item_index=1, subtotal="10.00"),
        ),
        statement=statement,
    )
    assert review.reconciliation_v2.orders[0].summary.identity_scope.value == "GROUP"
    writer.items = (
        _item(item_index=0, subtotal="9.00"),
        _item(item_index=1, subtotal="11.00"),
    )

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is True
    assert writer.writes == 1


def test_v2_group_ui_is_a_limitation_and_never_displays_matched_item_index(
    tmp_path, monkeypatch
):
    review, _, _ = _group_review(monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "batch-1"
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 4
    app.session_state["weekly_statement_stage"] = review.stage
    app.session_state["weekly_statement_review"] = review

    app.run(timeout=20)

    assert app.exception == []
    review_table = next(
        element.value
        for element in app.dataframe
        if "Identity" in element.value.columns
    )
    assert review_table.iloc[0]["Identity"] == "GROUP"
    assert review_table.iloc[0]["Allocation"] == "Group only"
    assert all("Matched Item" not in element.value.columns for element in app.dataframe)
    assert "Reconciliation notes" in {item.label for item in app.expander}
    assert any("product-group scope" in info.value for info in app.info)
    assert not any("exactly one" in error.value.casefold() for error in app.error)
    assert not any("Quantity Matched" in caption.value for caption in app.caption)
    assert any(
        "Statement quantity: not provided by source" in caption.value
        for caption in app.caption
    )
    assert any(
        button.label == "Continue to review & commit" and not button.disabled
        for button in app.button
    )


def test_stale_product_master_review_is_visible_and_commit_is_disabled(
    tmp_path, monkeypatch
):
    review, _, _ = _review(monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "batch-1"
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 5
    app.session_state["weekly_statement_stage"] = review.stage
    app.session_state["weekly_statement_review"] = review
    app.session_state["weekly_statement_review_stale_reason"] = (
        "Product Master was refreshed after this Statement review."
    )

    app.run(timeout=20)

    assert app.exception == []
    assert any("Product Master was refreshed" in warning.value for warning in app.warning)
    assert any(
        button.label == "Commit Statement" and button.disabled
        for button in app.button
    )
    assert next(
        button for button in app.button if button.label == "Commit Statement"
    ).proto.type == "secondary"


def test_sg_obsolete_proof_review_refreshes_once_without_repeating_stale_state(
    monkeypatch,
):
    """A new SG existence-gate review must not immediately self-mark stale."""

    review, _, _ = _review(monkeypatch)
    assert review.evidence_version is not None
    refreshed = replace(
        review,
        active_gate="FULL_ORDER_CURRENT_ORDER_EXISTENCE",
        reconciliation_v2=None,
        evidence_version=replace(
            review.evidence_version,
            rule_version="FULL_ORDER_EXISTENCE_V1",
            full_order_proof_rule_version=expected_full_order_proof_rule_version(
                SHOPEE_SG
            ),
        ),
    )
    obsolete = replace(
        refreshed,
        evidence_version=replace(
            refreshed.evidence_version,
            full_order_proof_rule_version="legacy-full-order-proof-rule",
        ),
    )
    assert (
        obsolete.evidence_version.full_order_proof_rule_version
        != expected_full_order_proof_rule_version(SHOPEE_SG)
    )

    assert statement_review_requires_proof_rule_refresh(obsolete, SHOPEE_SG)

    state = {
        "weekly_statement_review": obsolete,
        "weekly_statement_review_stale_reason": (
            "Full Order Statement proof rules changed after this review."
        ),
    }
    replace_statement_review_after_refresh(state, refreshed)

    assert state["weekly_statement_review"] == refreshed
    assert "weekly_statement_review_stale_reason" not in state
    assert not statement_review_requires_proof_rule_refresh(refreshed, SHOPEE_SG)


def test_sg_current_proof_rule_advances_validate_reconcile_and_review(
    tmp_path, monkeypatch
):
    review, _, _ = _review(monkeypatch)
    assert review.evidence_version is not None
    refreshed = replace(
        review,
        active_gate="FULL_ORDER_CURRENT_ORDER_EXISTENCE",
        reconciliation_v2=None,
        evidence_version=replace(
            review.evidence_version,
            rule_version="FULL_ORDER_EXISTENCE_V1",
            full_order_proof_rule_version=expected_full_order_proof_rule_version(
                SHOPEE_SG
            ),
        ),
    )
    monkeypatch.setattr(data_import, "_refresh_statement_review", lambda _review: True)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "sg-current-proof-batch",
        "data_import.active_platform": "shopee_sg",
        "data_import.active_market": "shopee_sg",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 3,
        "weekly_statement_stage": refreshed.stage,
        "weekly_statement_review": refreshed,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    validate_next = next(
        button for button in app.button if button.label == "Continue to reconcile"
    )
    assert not validate_next.disabled
    validate_next.click().run(timeout=20)

    reconcile_next = next(
        button for button in app.button if button.label == "Continue to review & commit"
    )
    assert not reconcile_next.disabled
    reconcile_next.click().run(timeout=20)

    commit = next(button for button in app.button if button.label == "Commit Statement")
    assert not commit.disabled


def test_sg_settlement_conflict_is_visible_and_commit_is_disabled(tmp_path, monkeypatch):
    review, _, _ = _review(monkeypatch)
    assert review.evidence_version is not None
    conflict = "Conflicting committed settlement evidence for Order ID: ORDER-1"
    blocked = replace(
        review,
        stage=replace(
            review.stage,
            result=NEEDS_REVIEW,
            review_reasons=(conflict,),
            duplicate_status="SETTLEMENT_EVIDENCE_CONFLICT",
        ),
        active_gate="FULL_ORDER_CURRENT_ORDER_EXISTENCE",
        reconciliation_v2=None,
        plan=None,
        blockers=(conflict,),
        persistence_blockers=(conflict,),
        evidence_version=replace(
            review.evidence_version,
            rule_version="FULL_ORDER_EXISTENCE_V1",
            full_order_proof_rule_version=expected_full_order_proof_rule_version(
                SHOPEE_SG
            ),
        ),
    )
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "sg-conflict-batch",
        "data_import.active_platform": "shopee_sg",
        "data_import.active_market": "shopee_sg",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 5,
        "weekly_statement_stage": blocked.stage,
        "weekly_statement_review": blocked,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    assert any(conflict in error.value for error in app.error)
    commit = next(button for button in app.button if button.label == "Commit Statement")
    assert commit.disabled


def test_statement_back_is_navigation_only_and_forward_restores_staging(
    tmp_path, monkeypatch
):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )
    monkeypatch.setattr(data_import, "_refresh_statement_review", lambda _review: True)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    invoice_orders = [{"order_id": "COMMITTED-INVOICE", "status": "Accepted"}]
    invoice_products = [{"order_id": "COMMITTED-INVOICE", "item_index": 0}]
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "statement-back-batch",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 3,
        "weekly_statement_stage": review.stage,
        "weekly_statement_review": review,
        "orders": invoice_orders,
        "products": invoice_products,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    forward = next(
        button for button in app.button
        if button.label == "Continue to reconcile"
    )
    assert forward.disabled is False
    assert forward.proto.type == "primary"
    assert any(button.label == "Back" for button in app.button)
    assert not any(
        "still need attention before continuing" in caption.value
        for caption in app.caption
    )
    next(button for button in app.button if button.key == "statement_back_4").click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert state["data_import_step"] == 2
    assert state["weekly_statement_stage"] == review.stage
    assert state["weekly_statement_review"] == review
    assert state["orders"] == invoice_orders
    assert state["products"] == invoice_products
    assert not any(
        button.label in {"Leave Statement Review", "Remove Statement"}
        for button in app.button
    )

    next(
        button for button in app.button
        if button.label == "Continue to validate"
    ).click().run(timeout=20)

    restored = app.session_state.filtered_state
    assert restored["data_import_step"] == 3
    assert restored["weekly_statement_stage"] == review.stage
    assert restored["weekly_statement_review"] == review


def test_retired_v2_direct_statement_commit_access_is_archived(
    tmp_path, monkeypatch
):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )
    expected = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id="blocked-direct-batch",
        review=review,
    )
    expected_issues = len(expected.validation.blocking_issues)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "blocked-direct-batch",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 5,
        "weekly_statement_stage": review.stage,
        "weekly_statement_review": review,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    blocker = next(error.value for error in app.error if "Cannot commit Statement" in error.value)
    assert "1 order still needs" in blocker
    assert f"{expected_issues} unresolved issue" in blocker
    assert all(reason not in blocker for reason in review.blockers)
    assert any(
        button.label == "Commit Statement" and button.disabled
        for button in app.button
    )
    assert next(
        button for button in app.button if button.label == "Commit Statement"
    ).proto.type == "secondary"
    assert "Reconciliation V2 review" not in {
        element.value for element in app.subheader
    }

    next(
        button for button in app.button
        if button.label == "Back to Statement Review"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert state["data_import_step"] == 3
    assert state["weekly_statement_stage"] == review.stage
    assert state["weekly_statement_review"] == review
    assert "Needs Attention" in {element.value for element in app.subheader}
    assert "All reconciliation evidence" in {
        expander.label for expander in app.expander
    }


def test_exit_statement_review_cancel_and_confirm_are_narrow(
    tmp_path, monkeypatch
):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    invoice_orders = [{"order_id": "COMMITTED-INVOICE", "status": "Accepted"}]
    invoice_products = [{"order_id": "COMMITTED-INVOICE", "item_index": 0}]
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "statement-exit-batch",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 3,
        "weekly_statement_stage": review.stage,
        "weekly_statement_review": review,
        "weekly_statement_review_stale_reason": "Refresh required.",
        "weekly_statement_issue_order_id": "ORDER-1",
        "weekly_statement_uploader_version": 4,
        "orders": invoice_orders,
        "products": invoice_products,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Exit Statement Review"
    ).click().run(timeout=20)

    pending = app.session_state.filtered_state
    assert pending["weekly_statement_stage"] == review.stage
    assert pending["weekly_statement_review"] == review
    assert any(button.label == "Leave Statement Review" for button in app.button)
    next(
        button for button in app.button
        if button.key == "cancel_exit_statement_review"
    ).click().run(timeout=20)

    cancelled = app.session_state.filtered_state
    assert cancelled["data_import_step"] == 3
    assert cancelled["weekly_statement_stage"] == review.stage
    assert cancelled["orders"] == invoice_orders

    next(
        button for button in app.button
        if button.label == "Exit Statement Review"
    ).click().run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Leave Statement Review"
    ).click().run(timeout=20)

    exited = app.session_state.filtered_state
    assert app.exception == []
    assert exited["data_import_step"] == 2
    for key in (
        "weekly_statement_stage",
        "weekly_statement_review",
        "weekly_statement_review_stale_reason",
        "weekly_statement_issue_order_id",
    ):
        assert key not in exited
    assert exited["weekly_statement_uploader_version"] == 5
    assert exited["orders"] == invoice_orders
    assert exited["products"] == invoice_products
    assert exited["batch_id"] == "statement-exit-batch"


def test_failed_statement_exit_keeps_work_and_current_step(
    tmp_path, monkeypatch
):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )

    def fail_recovery(_state, _action):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(
        "src.invoice_app.ui.data_import.execute_current_batch_recovery",
        fail_recovery,
    )
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "statement-failure-batch",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 3,
        "weekly_statement_stage": review.stage,
        "weekly_statement_review": review,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Exit Statement Review"
    ).click().run(timeout=20)
    next(
        button for button in app.button
        if button.label == "Leave Statement Review"
    ).click().run(timeout=20)

    state = app.session_state.filtered_state
    assert app.exception == []
    assert state["data_import_step"] == 3
    assert state["weekly_statement_stage"] == review.stage
    assert state["weekly_statement_review"] == review
    assert any(
        "Unable to remove the staged Statement" in error.value
        for error in app.error
    )
    assert not any("Recovery complete" in success.value for success in app.success)


def test_committed_statement_does_not_offer_destructive_exit(tmp_path, monkeypatch):
    review, _, _ = _review(monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "completed-statement-batch",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 5,
        "weekly_statement_stage": review.stage,
        "weekly_statement_review": review,
        "weekly_statement_commit_completed": True,
    }.items():
        app.session_state[key] = value

    app.run(timeout=20)

    assert app.exception == []
    assert not any(
        button.label == "Exit Statement Review" for button in app.button
    )


# These tests preserve the retired V2 reconciliation presentation contract for
# reference only.  The approved MY/SG any-period path deliberately has no
# Product Master, Invoice/PDF, or item-allocation gate to exercise here.
_RETIRED_V2_CONTRACT_TESTS = (
    "test_data_import_statement_review_is_compact_and_commit_ready",
    "test_v2_group_is_valid_review_with_unresolved_allocation_but_no_fake_mapping",
    "test_v2_group_plan_persists_source_rows_with_blank_item_index_and_zero_enrichment",
    "test_promotion_group_never_allocates_statement_money_to_members",
    "test_v2_adapter_separates_identity_merchandise_and_exact_settlement",
    "test_statement_adjustment_remains_separate_from_original_settlement",
    "test_review_does_not_use_legacy_cross_period_invoice_adjustments",
    "test_statement_quantity_absence_is_a_nonblocking_source_limitation",
    "test_product_master_snapshot_change_marks_existing_review_stale",
    "test_product_master_change_after_review_blocks_commit_with_zero_write",
    "test_legacy_invoice_group_change_does_not_change_active_review",
    "test_v2_group_ui_is_a_limitation_and_never_displays_matched_item_index",
    "test_stale_product_master_review_is_visible_and_commit_is_disabled",
    "test_retired_v2_direct_statement_commit_access_is_archived",
)

for _retired_test_name in _RETIRED_V2_CONTRACT_TESTS:
    globals()[_retired_test_name] = pytest.mark.skip(
        reason="Retired V2/Product Master/Invoice-PDF Statement contract."
    )(globals()[_retired_test_name])
