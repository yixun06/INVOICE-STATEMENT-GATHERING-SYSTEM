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
from src.invoice_app.services.weekly_billing import WeeklyBillingDataset
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
render_weekly_billing(
    WeeklyBillingDataset(
        periods=(period,),
        order_ids_by_batch={"batch-1": ("ORDER-1",)},
        orders={"ORDER-1": order},
        items=(item,),
    )
)
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


def test_weekly_billing_ui_has_no_source_ingestion_or_second_calculation_path():
    source = (
        Path(__file__).parents[1]
        / "src"
        / "invoice_app"
        / "ui"
        / "weekly_billing.py"
    ).read_text(encoding="utf-8").casefold()

    assert "product_master" not in source
    assert "process_pdf_file_with_outcome" not in source
    assert "file_uploader" not in source
    assert "create_repository" not in source
    assert "statement_financial_components" not in source
    assert "build_weekly_billing_summary" in source
    assert "export_weekly_billing_summary(summary)" in source
