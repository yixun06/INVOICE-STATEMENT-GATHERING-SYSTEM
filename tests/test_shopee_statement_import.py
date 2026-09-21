from __future__ import annotations

from dataclasses import replace
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
    StatementSummaryLine,
    SettlementAdjustment,
    SettlementIncomeRow,
    SourceValueIssue,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_statement_import import (
    check_statement_review_currency,
    commit_statement_review,
    refresh_statement_review,
    review_statement_upload,
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
    stage_parsed_shopee_weekly_statement,
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
    def __init__(self, order=None, items=(_item(),), committed_statements=()):
        self.order = order
        self.items = tuple(items)
        self.committed_statements = tuple(committed_statements)
        self.writes = 0

    def reload_commit_state(self):
        orders = {} if self.order is None else {"ORDER-1": self.order}
        return StatementCommitState(
            orders=orders,
            committed_statements=self.committed_statements,
            items=self.items,
        )

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
    writer = _Writer(
        _order() if order is _DEFAULT_ORDER else order,
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


def test_unrelated_source_issue_stays_blocking_without_blanketing_settlement(monkeypatch):
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
    settlement = {
        item.label: item.value for item in presentation.reconciliation.summary
    }["Settlement"]

    assert review.reconciliation_v2.orders[0].summary.settlement_basis.value == "EXACT"
    assert review.reconciliation_v2.orders[0].evidence.settlement.unexplained_residual == Decimal("0.00")
    assert len(presentation.validation.blocking_issues) == 1
    assert "Income row 742" in presentation.validation.blocking_issues[0].reason
    assert settlement == "1 Exact · 0 Explained · 0 unexplained"


def test_missing_order_coverage_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(monkeypatch, order=None)

    assert review.ready is False
    assert review.plan is None
    assert any("no persisted Invoice order coverage" in reason for reason in review.blockers)


def test_refresh_replaces_old_missing_invoice_blocker_after_invoice_is_persisted(
    monkeypatch,
):
    review, repository, writer = _review(monkeypatch, order=None, items=())
    assert any(
        "no persisted Invoice order coverage" in reason
        for reason in review.blockers
    )

    writer.order = _order(refund=None)
    writer.items = (_item("Other Product", seller_sku="OTHER-SKU"),)

    refreshed = refresh_statement_review(
        review,
        repository=repository,
        writer=writer,
        product_master=_master(),
        now=lambda: NOW,
    )

    assert not any(
        "no persisted Invoice order coverage" in reason
        for reason in refreshed.blockers
    )
    assert any("product identity is unresolved" in reason for reason in refreshed.blockers)


def test_existing_invoice_order_without_items_is_not_full_invoice_missing(monkeypatch):
    review, _, _ = _review(monkeypatch, order=_order(refund=None), items=())

    assert not any(
        "no persisted Invoice order coverage" in reason
        for reason in review.blockers
    )
    assert any(
        "Invoice order exists but has no Invoice_Items coverage" in reason
        for reason in review.blockers
    )


def test_unresolved_sku_needs_review_and_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(
        monkeypatch,
        items=(_item("Other Product", seller_sku="OTHER-SKU"),),
    )

    assert review.ready is False
    assert review.plan is None
    assert review.sku_matches.matches[0].status.value == "NEEDS_REVIEW"
    assert not any(
        "no persisted Invoice order coverage" in reason
        for reason in review.blockers
    )


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
    assert attempt.reasons == (
        "STALE_REVIEW:INVOICE_SNAPSHOT_CHANGED",
        "STALE_REVIEW:RECONCILIATION_RESULT_CHANGED",
    )
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
    assert any(button.label == "Upload another Statement" for button in app.button)


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
    assert len(plan.protected_invoice_items) == 2


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

    assert [(item.order_id, item.item_index) for item in plan.invoice_item_updates] == [
        ("ORDER-1", 0)
    ]
    assert {
        (item.order_id, item.item_index) for item in plan.protected_invoice_items
    } == {("ORDER-1", 1), ("ORDER-1", 2)}


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
                subtotal="10.00",
                promotion_group_id="PROMO-1",
                source_group_total="18.00",
            ),
            _item(
                item_index=1,
                subtotal="12.00",
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
    assert review.reconciliation_v2.orders[0].evidence.promotion.promotion_group_ids == (
        "PROMO-1",
    )


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
    assert len(review.plan.protected_invoice_items) == 2
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
    assert review.plan is None
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
    assert "STALE_REVIEW:RECONCILIATION_RESULT_CHANGED" in attempt.reasons
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
    assert "STALE_REVIEW:RECONCILIATION_RESULT_CHANGED" in attempt.reasons
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


def test_group_improving_to_item_after_review_is_stale_and_writes_nothing(
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

    assert attempt.committed is False
    assert "STALE_REVIEW:INVOICE_SNAPSHOT_CHANGED" in attempt.reasons
    assert "STALE_REVIEW:RECONCILIATION_RESULT_CHANGED" in attempt.reasons
    assert writer.writes == 0


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
    assert forward.disabled is True
    assert forward.proto.type == "secondary"
    assert any(button.label == "Back" for button in app.button)
    assert any(
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


def test_direct_statement_commit_access_remains_blocked_and_routes_to_review(
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
