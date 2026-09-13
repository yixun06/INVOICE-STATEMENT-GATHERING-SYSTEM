from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from streamlit.testing.v1 import AppTest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    SettlementIncomeRow,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_statement_import import (
    check_statement_review_currency,
    commit_statement_review,
    review_statement_upload,
)
from src.invoice_app.services.import_result_adapters import (
    adapt_shopee_weekly_statement_import_result,
)
from src.invoice_app.services.shopee_statement_persistence import (
    StatementCommitState,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    stage_parsed_shopee_weekly_statement,
)


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
) -> SettlementIncomeRow:
    components = {name: Decimal("0.00") for name in INCOME_COMPONENT_COLUMNS}
    components["Product Price"] = Decimal(product)
    components["Refund Amount"] = Decimal(refund)
    components["Commission Fee (incl. SST)"] = Decimal(fees)
    return SettlementIncomeRow(
        sequence_no=sequence or str(row_number),
        view_by=view_by,
        order_id="ORDER-1",
        product_id="PRODUCT-1",
        product_name="Green Tea",
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
    def __init__(self, order=None):
        self.order = order
        self.writes = 0

    def reload_commit_state(self):
        orders = {} if self.order is None else {"ORDER-1": self.order}
        return StatementCommitState(orders=orders, committed_statements=())

    def write_statement_batch(self, plan):
        self.writes += 1


def _review(
    monkeypatch,
    *,
    order=_DEFAULT_ORDER,
    items=(_item(),),
    statement=None,
    product_master=None,
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
    writer = _Writer(_order() if order is _DEFAULT_ORDER else order)
    review = review_statement_upload(
        b"synthetic",
        source_filename="statement.xlsx",
        batch_id="batch-1",
        uploaded_by="admin",
        repository=repository,
        writer=writer,
        product_master=product_master or _master(),
        now=lambda: NOW,
    )
    return review, repository, writer


def test_statement_review_is_ready_when_coverage_and_sku_matching_are_complete(monkeypatch):
    review, _, _ = _review(monkeypatch)

    assert review.ready is True
    assert review.blockers == ()
    assert review.stage.order_reconciliations[0].status == "Matched"
    assert review.sku_matches.eligible_for_commit is True


def test_legacy_different_is_v2_explained_and_does_not_block_review(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        order=_order("9.50", income_type="Estimated", fees="-0.50"),
    )

    assert review.stage.order_reconciliations[0].status == "Different"
    assert review.ready is True
    assert review.reconciliation_v2.orders[0].summary.settlement_basis.value == "EXPLAINED"


def test_missing_order_coverage_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(monkeypatch, order=None)

    assert review.ready is False
    assert review.plan is None
    assert any("no persisted Invoice order coverage" in reason for reason in review.blockers)


def test_unresolved_sku_needs_review_and_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )

    assert review.ready is False
    assert review.plan is None
    assert review.sku_matches.matches[0].status.value == "NEEDS_REVIEW"


def test_commit_uses_guarded_boundary_and_stale_order_state_produces_zero_write(monkeypatch):
    review, repository, writer = _review(monkeypatch)
    writer.order = _order("9.00")

    attempt = commit_statement_review(
        review,
        repository=repository,
        writer=writer,
        load_product_master=_master,
    )

    assert attempt.committed is False
    assert attempt.reasons == ("COMPARISON_STATE_CHANGED:ORDER-1",)
    assert writer.writes == 0


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
    app.session_state["data_import_step"] = 5
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
        button.label == "Commit Statement" and not button.disabled
        for button in app.button
    )
    assert "Reconciliation review complete — V2 found no business blockers." in {
        element.value for element in app.success
    }


def test_data_import_ui_has_no_direct_google_write_call():
    source = Path("src/invoice_app/ui/data_import.py").read_text(encoding="utf-8")

    assert "batch_update(" not in source
    assert "write_statement_batch(" not in source
    assert "commit_statement_review(" in source
    assert "Billing" not in source
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


def test_legacy_matcher_disagreement_is_not_exposed_as_v2_business_blocker(monkeypatch):
    review, _, _ = _group_review(monkeypatch)
    result = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id="batch-1",
        review=review,
    )

    assert review.sku_matches.eligible_for_commit is False
    assert review.plan is None
    assert result.validation.blocking_issues == ()
    assert result.batch_status == "Review Complete"
    assert result.commit_readiness.database_commit_available is False
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


def test_v2_explained_settlement_is_not_a_validation_error(monkeypatch):
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

    assert review.reconciliation_v2.orders[0].summary.settlement_basis.value == "EXPLAINED"
    assert review.blockers == ()
    assert result.validation.blocking_issues == ()


def test_v2_none_settlement_is_a_real_fail_closed_blocker(monkeypatch):
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

    assert review.reconciliation_v2.orders[0].summary.merchandise_reconciled is True
    assert review.reconciliation_v2.orders[0].summary.settlement_basis.value == "NONE"
    assert review.ready is False
    assert review.plan is not None
    assert review.commit_ready is False
    assert any("settlement" in reason for reason in review.blockers)


def test_late_statement_refund_is_explained_without_mutating_invoice(monkeypatch):
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
    settlement = review.reconciliation_v2.orders[0].evidence.settlement

    assert settlement.basis.value == "EXPLAINED"
    assert settlement.internal_effects == ("LATE_STATEMENT_REFUND_EFFECT",)
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


def test_invoice_snapshot_change_marks_existing_review_stale(monkeypatch):
    review, repository, writer = _review(monkeypatch)
    writer.order = _order("9.00")

    stale = check_statement_review_currency(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(),
    )

    assert stale.is_current is False
    assert "INVOICE_SNAPSHOT_CHANGED" in stale.changed_evidence


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
    app.session_state["data_import_step"] = 5
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
    assert any("product-group scope" in warning.value for warning in app.warning)
    assert not any("exactly one" in error.value.casefold() for error in app.error)
    assert not any("Quantity Matched" in caption.value for caption in app.caption)
    assert any(
        "Statement quantity: not provided by source" in caption.value
        for caption in app.caption
    )
    assert any(
        button.label == "Commit Statement" and button.disabled
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
