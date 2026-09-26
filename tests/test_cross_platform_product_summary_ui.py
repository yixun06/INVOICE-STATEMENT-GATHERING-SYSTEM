from datetime import date
from decimal import Decimal

from streamlit.testing.v1 import AppTest


def test_cross_platform_renderer_receives_the_canonical_column_order(monkeypatch):
    from src.invoice_app.services.cross_platform_product_summary import (
        CommittedReportingItem,
        CrossPlatformReportingSnapshot,
    )
    from src.invoice_app.ui import cross_platform_product_summary as cross_ui

    snapshot = CrossPlatformReportingSnapshot(
        (
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
                payout_completed_date=date(2026, 8, 8), seller_sku="SKU-1",
                nav="300001", description="Persisted Tea", variation="", quantity=2,
                unit_price=Decimal("10.00"), line_subtotal=Decimal("18.00"),
                promotion_group_id=None, promotion_label=None, source_group_total=None,
            ),
        )
    )
    captured = {}

    monkeypatch.setattr(cross_ui.st, "title", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cross_ui.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cross_ui, "_render_filters", lambda _snapshot: ("Shopee", None, None))
    monkeypatch.setattr(
        cross_ui.st,
        "dataframe",
        lambda frame, **kwargs: captured.update(frame=frame, kwargs=kwargs),
    )
    monkeypatch.setattr(cross_ui.st, "download_button", lambda *_args, **_kwargs: None)

    cross_ui.render_cross_platform_product_summary(snapshot)

    assert tuple(captured["frame"].columns) == cross_ui.PRODUCT_SUMMARY_COLUMNS
    assert captured["kwargs"]["column_order"] == cross_ui.PRODUCT_SUMMARY_COLUMNS
    assert all(
        config.get("pinned") is not True
        for config in captured["kwargs"]["column_config"].values()
    )


def test_cross_platform_dashboard_uses_the_final_table_rowset_for_all_metrics(monkeypatch):
    from src.invoice_app.services.cross_platform_product_summary import (
        CommittedReportingItem,
        CrossPlatformReportingSnapshot,
    )
    from src.invoice_app.ui import cross_platform_product_summary as cross_ui

    snapshot = CrossPlatformReportingSnapshot(
        (
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
                payout_completed_date=date(2026, 8, 8), seller_sku="SKU-A",
                nav="300001", description="Persisted Tea", variation="", quantity=1,
                unit_price=Decimal("10.00"), line_subtotal=Decimal("18.00"),
                promotion_group_id=None, promotion_label=None, source_group_total=None,
            ),
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
                payout_completed_date=date(2026, 8, 8), seller_sku="SKU-A",
                nav="300001", description="Persisted Tea", variation="", quantity=2,
                unit_price=Decimal("10.00"), line_subtotal=Decimal("22.00"),
                promotion_group_id=None, promotion_label=None, source_group_total=None,
            ),
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-3", item_index=0,
                payout_completed_date=date(2026, 8, 8), seller_sku="SKU-B",
                nav="300002", description="Persisted Coffee", variation="", quantity=4,
                unit_price=Decimal("5.00"), line_subtotal=Decimal("21.00"),
                promotion_group_id=None, promotion_label=None, source_group_total=None,
            ),
        )
    )
    captured = {}
    metrics = []

    monkeypatch.setattr(cross_ui.st, "title", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cross_ui.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cross_ui, "_render_filters", lambda _snapshot: ("Shopee", None, None))
    monkeypatch.setattr(
        cross_ui.st,
        "metric",
        lambda label, value, **kwargs: metrics.append((label, value, kwargs)),
    )
    monkeypatch.setattr(
        cross_ui.st,
        "dataframe",
        lambda frame, **kwargs: captured.update(frame=frame, kwargs=kwargs),
    )
    monkeypatch.setattr(cross_ui.st, "download_button", lambda *_args, **_kwargs: None)

    cross_ui.render_cross_platform_product_summary(snapshot)

    assert len(captured["frame"]) == 2
    assert captured["frame"]["Qty"].sum() == 7
    assert captured["frame"]["Original Sales"].sum() == 50.0
    assert captured["frame"]["Disc Amt"].sum() == -11.0
    assert captured["frame"]["Amount"].sum() == 61.0
    assert metrics == [
        ("Total Product", "2", {"border": True}),
        ("Total Quantity", "7", {"border": True}),
        ("Total Original Sales", "RM 50.00", {"border": True}),
        ("Total Discount Given", "RM -11.00", {"border": True}),
        ("Total Amount", "RM 61.00", {"border": True}),
    ]


def test_cross_platform_dashboard_reacts_to_filters_and_shows_zero_for_a_valid_empty_result():
    app = AppTest.from_string(
        '''
from datetime import date
from decimal import Decimal

from src.invoice_app.services.cross_platform_product_summary import (
    CommittedReportingItem,
    CrossPlatformReportingSnapshot,
)
from src.invoice_app.ui import cross_platform_product_summary as cross_ui

cross_ui.export_cross_platform_product_summary_barcode_table_pdf = lambda *args, **kwargs: b"%PDF-test"
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 8), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 9), seller_sku="SKU-2", nav="300002",
        description="Persisted Coffee", variation="", quantity=2,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("20.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert [(element.label, element.value) for element in app.metric] == [
        ("Total Product", "2"),
        ("Total Quantity", "3"),
        ("Total Original Sales", "RM 30.00"),
        ("Total Discount Given", "RM 0.00"),
        ("Total Amount", "RM 30.00"),
    ]

    app.selectbox[1].set_value(date(2026, 8, 9))
    app.selectbox[2].set_value(date(2026, 8, 9))
    app.run(timeout=20)

    assert [(element.label, element.value) for element in app.metric] == [
        ("Total Product", "1"),
        ("Total Quantity", "2"),
        ("Total Original Sales", "RM 20.00"),
        ("Total Discount Given", "RM 0.00"),
        ("Total Amount", "RM 20.00"),
    ]

    app.selectbox[0].set_value("ZENXIN")
    app.run(timeout=20)

    assert [(element.label, element.value) for element in app.metric] == [
        ("Total Product", "0"),
        ("Total Quantity", "0"),
        ("Total Original Sales", "RM 0.00"),
        ("Total Discount Given", "RM 0.00"),
        ("Total Amount", "RM 0.00"),
    ]
    assert app.dataframe == []


def test_cross_platform_page_renders_only_the_committed_product_summary_table():
    app = AppTest.from_string(
        '''
from datetime import date
from decimal import Decimal

from src.invoice_app.services.cross_platform_product_summary import (
    CommittedReportingItem,
    CrossPlatformReportingSnapshot,
)
from src.invoice_app.ui import cross_platform_product_summary as cross_ui

cross_ui.export_cross_platform_product_summary_barcode_table_pdf = lambda *args, **kwargs: b"%PDF-test"
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 8), seller_sku="9555208107347",
        nav="300001", description="Persisted Tea", variation="500ml", quantity=2,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("18.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [element.value for element in app.title] == ["Cross Platform Summary"]
    assert [element.label for element in app.selectbox] == [
        "Platform", "From Date", "To Date"
    ]
    assert app.selectbox[0].options == ["All", "Shopee", "Lazada", "ZENXIN"]
    assert app.selectbox[1].options == ["All Dates", "2026-08-08"]
    assert app.selectbox[2].options == ["All Dates", "2026-08-08"]
    assert tuple(app.dataframe[0].value.columns) == (
        "No.", "SKU Code", "NAV", "Description", "Qty", "UOM",
        "Unit Price", "Original Sales", "Disc Amt", "Amount",
    )
    assert app.dataframe[0].value.iloc[0]["UOM"] == "EA"
    assert [element.label for element in app.get("download_button")] == [
        "Cross Platform Barcode Table PDF"
    ]
    visible = {element.value for element in app.subheader}
    assert not {"All Products", "Missing SKU / Not included in Product Summary", "Excluded from Product Summary", "All Manual Review"} & visible


def test_cross_platform_page_rejects_an_invalid_selected_payout_range_without_table_or_pdf():
    app = AppTest.from_string(
        '''
from datetime import date
from decimal import Decimal

import streamlit as st

from src.invoice_app.services.cross_platform_product_summary import (
    CommittedReportingItem,
    CrossPlatformReportingSnapshot,
)
from src.invoice_app.ui import cross_platform_product_summary as cross_ui

cross_ui.export_cross_platform_product_summary_barcode_table_pdf = lambda *args, **kwargs: b"%PDF-test"
st.session_state["cross_platform_reporting_from_date"] = date(2026, 8, 10)
st.session_state["cross_platform_reporting_to_date"] = date(2026, 8, 8)
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 8), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 10), seller_sku="SKU-2", nav="300002",
        description="Persisted Coffee", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert [element.value for element in app.error] == [
        "From Date must be on or before To Date."
    ]
    assert app.dataframe == []
    assert app.get("download_button") == []
    assert app.metric == []
