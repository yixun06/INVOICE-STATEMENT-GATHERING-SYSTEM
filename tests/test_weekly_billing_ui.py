from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.ui.weekly_billing import WEEKLY_BILLING_PAGE


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_weekly_billing_page_contract_is_business_facing():
    assert WEEKLY_BILLING_PAGE == "Weekly Billing"


def test_weekly_billing_remains_the_single_uat2_sidebar_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = WEEKLY_BILLING_PAGE
    app.session_state["batch_id"] = "active-batch"
    app.session_state["orders"] = [{"platform": "Shopee", "order_id": "SHP-1", "status": "Accepted"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.run(timeout=20)

    assert app.exception == []
    assert WEEKLY_BILLING_PAGE in {title.value for title in app.title}
    labels = {button.label for button in app.button}
    assert {"Data Import", "Dashboard", WEEKLY_BILLING_PAGE} <= labels
    assert "Settlement Test Lab" not in labels
    assert app.session_state.filtered_state["batch_id"] == "active-batch"
    assert app.session_state.filtered_state["orders"] == [
        {"platform": "Shopee", "order_id": "SHP-1", "status": "Accepted"}
    ]


def test_weekly_billing_success_path_renders_summary_in_streamlit():
    app = AppTest.from_string(
        """
from datetime import date, datetime, timezone
from decimal import Decimal
from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder
from src.invoice_app.domain.weekly_billing import BillingPeriod
from src.invoice_app.domain.weekly_billing import (
    FinancialControl, FinancialSummaryRow, ProductSummaryRow,
    WeeklyBillingFinancialSummary, WeeklyBillingReport, WeeklyBillingSummary,
)
from src.invoice_app.services.weekly_billing import WeeklyBillingDataset
from src.invoice_app.ui import weekly_billing as billing_ui
from src.invoice_app.ui.weekly_billing import render_weekly_billing

period = BillingPeriod(date(2026, 8, 31), date(2026, 9, 6), "batch-1", "a" * 64)
order = CanonicalInvoiceOrder(
    platform="Shopee",
    order_id="ORDER-1",
    first_imported_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
)
item = CanonicalInvoiceItem(
    platform="Shopee",
    order_id="ORDER-1",
    item_index=0,
    seller_sku="SKU-1",
    nav="5000001",
    product_name="Product One",
    quantity=2,
    unit_price=Decimal("10.00"),
    line_subtotal=Decimal("16.00"),
)
dataset = WeeklyBillingDataset(
    periods=(period,), order_ids_by_batch={"batch-1": ("ORDER-1",)},
    orders={"ORDER-1": order}, items=(item,),
)
product = WeeklyBillingSummary(
    period=period, order_count=1, invoice_item_count=1, source_items=(),
    product_rows=(ProductSummaryRow(
        number=1, nav="5000001", product_name="Product One", uom=None,
        unit_price=Decimal("10.00"), quantity=2, discount_percent=None,
        discount_amount=Decimal("4.00"), amount=Decimal("16.00"),
        source_item_count=1,
    ),), total_quantity=2, total_standard_amount=Decimal("20.00"),
    total_discount_amount=Decimal("4.00"), total_amount=Decimal("16.00"),
    normal_amount_total=Decimal("16.00"), promotion_amount_total=Decimal("0.00"),
    promotion_group_count=0, same_price_promotion_count=0, mixed_price_promotion_count=0,
)
financial = WeeklyBillingFinancialSummary(
    period=period, currency="RM",
    rows=(FinancialSummaryRow(1, "1. Total Revenue", "TOTAL", None, Decimal("16.00"), "RM"),),
    controls=(FinancialControl("Total Revenue", Decimal("16.00"), Decimal("16.00"), True),),
    export_ready=True, validation_failures=(),
)
billing_ui.build_weekly_billing_report = lambda _dataset, _period: WeeklyBillingReport(product, financial)
billing_ui.load_configured_product_price_master = lambda: (type("Master", (), {"records": ()})(), "Test")
render_weekly_billing(dataset)
"""
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [metric.label for metric in app.metric] == [
        "Orders",
        "Products",
        "Total Quantity",
        "Total Amount",
    ]
    assert [metric.value for metric in app.metric] == ["1", "1", "2", "RM 16.00"]
    assert app.selectbox[0].label == "Statement Period"
    assert tuple(app.dataframe[0].value.columns) == (
        "No.",
        "Item/Barcode",
        "Description",
        "Qty",
        "UOM",
        "Unit Price",
        "Dis%",
        "Disc Amt",
        "Amount",
    )
    assert tuple(app.dataframe[1].value.columns) == ("Description", "Amount")


def test_weekly_billing_ui_has_no_source_ingestion_or_second_calculation_path():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "invoice_app"
        / "ui"
        / "weekly_billing.py"
    ).read_text(encoding="utf-8").casefold()

    assert "load_configured_product_price_master" in source
    assert "process_pdf_file_with_outcome" not in source
    assert "file_uploader" not in source
    assert "create_repository" not in source
    assert "statement_financial_components" not in source
    assert "build_weekly_billing_report" in source
    assert "product_master_records=product_master.records" in source
