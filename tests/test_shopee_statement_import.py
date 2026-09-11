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
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_statement_import import (
    commit_statement_review,
    review_statement_upload,
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


def _income(view_by: str, row_number: int) -> SettlementIncomeRow:
    return SettlementIncomeRow(
        sequence_no=str(row_number),
        view_by=view_by,
        order_id="ORDER-1",
        product_id="PRODUCT-1",
        product_name="Green Tea",
        order_creation_date=date(2026, 8, 10),
        payout_completed_date=date(2026, 8, 16),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal("10.00"),
        financial_components={
            "Product Price": Decimal("10.00"),
            "Refund Amount": Decimal("0.00"),
        },
        source_values={},
        source_row_number=row_number,
    )


def _statement() -> ParsedShopeeWeeklyStatement:
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash="statement-hash",
        statement_period_from=date(2026, 8, 10),
        statement_period_to=date(2026, 8, 16),
        summary_total_released=Decimal("10.00"),
        adjustment_control_total=Decimal("0.00"),
        adjustment_footer_total=None,
        income_rows=(_income("Order", 2), _income("Sku", 3)),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )


def _order(final_amount: str = "10.00") -> CanonicalInvoiceOrder:
    return CanonicalInvoiceOrder(
        platform="Shopee",
        order_id="ORDER-1",
        final_amount=Decimal(final_amount),
        order_income=Decimal("9.00"),
        income_type="Final",
    )


def _item(product_name: str = "Green Tea") -> CanonicalInvoiceItem:
    return CanonicalInvoiceItem(
        platform="Shopee",
        order_id="ORDER-1",
        item_index=0,
        seller_sku="SKU-1",
        product_name=product_name,
        variation="Original",
        quantity=1,
        actual_selling_unit_price=Decimal("10.00"),
        line_subtotal=Decimal("10.00"),
        source_pdf="invoice.pdf",
    )


def _master() -> ProductPriceMaster:
    return ProductPriceMaster.from_rows(
        [
            {
                "product_id": "PRODUCT-1",
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Green Tea",
                "variation_name": "Original",
                "unit_selling_price": "12.00",
            }
        ]
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


def _review(monkeypatch, *, order=_DEFAULT_ORDER, items=(_item(),)):
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
    repository = _Repository(items)
    writer = _Writer(_order() if order is _DEFAULT_ORDER else order)
    review = review_statement_upload(
        b"synthetic",
        source_filename="statement.xlsx",
        batch_id="batch-1",
        uploaded_by="admin",
        repository=repository,
        writer=writer,
        product_master=_master(),
        now=lambda: NOW,
    )
    return review, repository, writer


def test_statement_review_is_ready_when_coverage_and_sku_matching_are_complete(monkeypatch):
    review, _, _ = _review(monkeypatch)

    assert review.ready is True
    assert review.blockers == ()
    assert review.stage.order_reconciliations[0].status == "Matched"
    assert review.sku_matches.eligible_for_commit is True


def test_different_is_visible_but_does_not_block_statement_plan(monkeypatch):
    review, _, _ = _review(monkeypatch, order=_order("9.50"))

    assert review.stage.order_reconciliations[0].status == "Different"
    assert review.ready is True


def test_missing_order_coverage_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(monkeypatch, order=None)

    assert review.ready is False
    assert review.plan is None
    assert any("coverage is incomplete" in reason for reason in review.blockers)


def test_unresolved_sku_needs_review_and_blocks_the_whole_statement(monkeypatch):
    review, _, _ = _review(monkeypatch, items=(_item("Other Product"),))

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
    assert {"Order ID Coverage and Amount Reconciliation", "SKU review"} <= {
        element.value for element in app.subheader
    }
    assert any(
        button.label == "Commit Statement" and not button.disabled
        for button in app.button
    )
    assert "Ready to Commit — current batch review is complete." in {
        element.value for element in app.success
    }


def test_data_import_ui_has_no_direct_google_write_call():
    source = Path("src/invoice_app/ui/data_import.py").read_text(encoding="utf-8")

    assert "batch_update(" not in source
    assert "write_statement_batch(" not in source
    assert "commit_statement_review(" in source
    assert "Billing" not in source
    assert "Live Analysis" not in source
