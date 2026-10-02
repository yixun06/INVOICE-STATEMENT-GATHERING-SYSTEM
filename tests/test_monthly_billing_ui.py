from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_monthly_billing_renderer_uses_a_distinct_state_and_cache_namespace():
    from src.invoice_app.ui import monthly_billing, weekly_billing

    source = Path(monthly_billing.__file__).read_text(encoding="utf-8")

    assert monthly_billing._load_monthly_billing_dataset is not weekly_billing._load_weekly_billing_dataset
    assert "monthly_billing_month" in source
    assert "monthly_billing_product_summary" in source
    assert "monthly_billing_financial_summary" in source
    assert "monthly_billing_export" in source
    assert "monthly_billing_product_summary_barcode_pdf_export" in source
    assert "spreadsheet_id" in source
    assert 'platform_col, month_col = st.columns(2, gap="small")' in source


def test_weekly_and_monthly_billing_render_in_one_session_without_widget_collision():
    app = AppTest.from_string(
        '''
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder
from src.invoice_app.domain.weekly_billing import (
    BillingPeriod, FinancialControl, FinancialSummaryRow, ProductSummaryRow,
    WeeklyBillingFinancialSummary, WeeklyBillingReport, WeeklyBillingSummary,
)
from src.invoice_app.services.weekly_billing import WeeklyBillingDataset
from src.invoice_app.ui import monthly_billing, weekly_billing

period = BillingPeriod(date(2026, 8, 1), date(2026, 8, 31), "batch-1", "a" * 64)
order = CanonicalInvoiceOrder(platform="Shopee", order_id="ORDER-1", first_imported_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
item = CanonicalInvoiceItem(platform="Shopee", order_id="ORDER-1", item_index=0, seller_sku="SKU-1", nav="5000001", product_name="Product", quantity=1, unit_price=Decimal("10.00"), line_subtotal=Decimal("8.00"))
dataset = WeeklyBillingDataset(periods=(period,), order_ids_by_batch={"batch-1": ("ORDER-1",)}, orders={"ORDER-1": order}, items=(item,))
product = WeeklyBillingSummary(period=period, order_count=1, invoice_item_count=1, source_items=(), product_rows=(ProductSummaryRow(1, "SKU-1", "5000001", "Product", "EA", Decimal("10.00"), 1, None, Decimal("2.00"), Decimal("8.00"), 1),), total_quantity=1, total_standard_amount=Decimal("10.00"), total_discount_amount=Decimal("2.00"), total_amount=Decimal("8.00"), normal_amount_total=Decimal("8.00"), promotion_amount_total=Decimal("0.00"), promotion_group_count=0, same_price_promotion_count=0, mixed_price_promotion_count=0)
financial = WeeklyBillingFinancialSummary(period=period, currency="RM", rows=(FinancialSummaryRow(1, "1. Total Revenue", "TOTAL", None, Decimal("8.00"), "RM"),), controls=(FinancialControl("Total Revenue", Decimal("8.00"), Decimal("8.00"), True),), export_ready=True, validation_failures=())
report = WeeklyBillingReport(product, financial)
weekly_billing.build_weekly_billing_report = lambda *_: report
weekly_billing.load_configured_product_price_master = lambda: (SimpleNamespace(records=()), "Test")
weekly_billing.export_weekly_billing_report = lambda *_args, **_kwargs: b"xlsx"
weekly_billing.export_product_summary_barcode_table_pdf = lambda *_: b"pdf"
weekly_billing.summarize_product_summary_barcodes = lambda *_: SimpleNamespace(label_count=1, valid_ean13_count=0, unavailable_count=1)
monthly_billing.build_monthly_billing_report = lambda *_: report
monthly_billing.load_configured_product_price_master = lambda: (SimpleNamespace(records=()), "Test")
monthly_billing.export_weekly_billing_report = lambda *_args, **_kwargs: b"xlsx"
monthly_billing.export_monthly_product_summary_barcode_table_pdf = lambda *_: b"pdf"
monthly_billing.summarize_product_summary_barcodes = lambda *_: SimpleNamespace(label_count=1, valid_ean13_count=0, unavailable_count=1)
weekly_billing.render_weekly_billing(dataset)
monthly_billing.render_monthly_billing(dataset)
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [widget.label for widget in app.selectbox] == [
        "Platform", "Statement Period", "Platform", "Month"
    ]
    assert app.selectbox[0].options == ["Shopee MY", "Shopee SG", "Lazada", "TikTok"]
    assert app.selectbox[2].options == ["Shopee MY", "Shopee SG", "Lazada", "TikTok"]
    assert "All" not in app.selectbox[0].options
    assert "All" not in app.selectbox[2].options
    state = app.session_state.filtered_state
    assert state["weekly_billing_platform"] == "Shopee MY"
    assert state["monthly_billing_platform"] == "Shopee MY"
    assert "weekly_billing_statement_period" in state
    assert "monthly_billing_month" in state
    assert {button.label for button in app.download_button} >= {
        "Export Weekly Billing Excel",
        "Export Monthly Billing Excel",
        "Export Product Summary Barcode PDF",
    }
    assert {heading.value for heading in app.subheader} >= {
        "Product Summary",
        "Financial Summary",
    }
    assert "Staging Data" not in {heading.value for heading in app.subheader}


def test_monthly_billing_is_a_distinct_app_route(tmp_path, monkeypatch):
    from src.invoice_app.ui.monthly_billing import MONTHLY_BILLING_PAGE

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(app_path))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = MONTHLY_BILLING_PAGE
    app.session_state["orders"] = []
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.run(timeout=20)

    assert app.exception == []
    assert MONTHLY_BILLING_PAGE in {title.value for title in app.title}
    assert MONTHLY_BILLING_PAGE in {button.label for button in app.button}


def test_monthly_billing_unsupported_platforms_fail_closed_without_my_data():
    for platform in ("Shopee SG", "Lazada", "TikTok"):
        app = AppTest.from_string(
            f'''
import streamlit as st
from src.invoice_app.ui.monthly_billing import render_monthly_billing

st.session_state["monthly_billing_platform"] = {platform!r}
render_monthly_billing(object())
'''
        )
        app.run(timeout=20)

        assert app.exception == []
        assert [widget.options for widget in app.selectbox] == [
            ["Shopee MY", "Shopee SG", "Lazada", "TikTok"]
        ]
        assert [message.value for message in app.info] == [
            f"{platform} billing is not configured yet."
        ]
        assert app.dataframe == []
        assert app.download_button == []
