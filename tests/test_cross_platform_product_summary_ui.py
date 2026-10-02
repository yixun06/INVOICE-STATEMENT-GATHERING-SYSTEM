from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
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
    number_formats = []
    original_number_column = cross_ui.st.column_config.NumberColumn

    monkeypatch.setattr(cross_ui.st, "title", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cross_ui.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cross_ui.st.column_config,
        "NumberColumn",
        lambda *args, **kwargs: (
            number_formats.append(kwargs.get("format")),
            original_number_column(*args, **kwargs),
        )[1],
    )
    monkeypatch.setattr(
        cross_ui,
        "_render_platform_filter",
        lambda: ("Shopee MY", True, None, None),
    )
    monkeypatch.setattr(
        cross_ui,
        "_render_date_filters",
        lambda *_args, **_kwargs: (None, None),
    )
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
    assert number_formats.count("RM %,.2f") == 4


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
    monkeypatch.setattr(
        cross_ui,
        "_render_platform_filter",
        lambda: ("Shopee MY", True, None, None),
    )
    monkeypatch.setattr(
        cross_ui,
        "_render_date_filters",
        lambda *_args, **_kwargs: (None, None),
    )
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

    app.toggle[0].set_value(False).run(timeout=20)
    app.date_input[0].set_value(date(2026, 8, 9)).run(timeout=20)
    app.date_input[1].set_value(date(2026, 8, 9)).run(timeout=20)

    assert [(element.label, element.value) for element in app.metric] == [
        ("Total Product", "1"),
        ("Total Quantity", "2"),
        ("Total Original Sales", "RM 20.00"),
        ("Total Discount Given", "RM 0.00"),
        ("Total Amount", "RM 20.00"),
    ]

    app.selectbox[0].set_value("TikTok")
    app.run(timeout=20)

    assert app.metric == []
    assert [message.value for message in app.info] == [
        "TikTok Cross Platform reporting is not configured yet."
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
    assert [element.label for element in app.selectbox] == ["Platform"]
    assert app.selectbox[0].options == ["All", "Shopee MY", "Lazada", "TikTok"]
    assert "Shopee SG" not in app.selectbox[0].options
    assert "Shopee" not in app.selectbox[0].options
    assert "ZENXIN" not in app.selectbox[0].options
    assert [(element.label, element.value) for element in app.date_input] == [
        ("From Date", date(2026, 8, 8)),
        ("To Date", date(2026, 8, 8)),
    ]
    assert [(element.label, element.value) for element in app.toggle] == [
        ("All dates", True)
    ]
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
st.session_state["cross_platform_reporting_all_dates"] = False
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


def test_cross_platform_calendar_accepts_an_ordinary_inclusive_date_range():
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

def capture_export(summary):
    st.session_state["exported_payout_dates"] = [
        item.payout_completed_date.isoformat() for item in summary.source_items
    ]
    return b"%PDF-test"

cross_ui.export_cross_platform_product_summary_barcode_table_pdf = capture_export
st.session_state["cross_platform_reporting_all_dates"] = False
st.session_state["cross_platform_reporting_from_date"] = date(2026, 8, 2)
st.session_state["cross_platform_reporting_to_date"] = date(2026, 8, 5)
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 1), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 3), seller_sku="SKU-2", nav="300002",
        description="Persisted Coffee", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-3", item_index=0,
        payout_completed_date=date(2026, 8, 5), seller_sku="SKU-3", nav="300003",
        description="Persisted Cocoa", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert app.error == []
    assert app.dataframe[0].value["SKU Code"].tolist() == ["SKU-2", "SKU-3"]
    assert app.session_state["exported_payout_dates"] == ["2026-08-03", "2026-08-05"]
    assert [element.label for element in app.get("download_button")] == [
        "Cross Platform Barcode Table PDF"
    ]


def test_cross_platform_valid_range_without_rows_shows_the_normal_empty_state():
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

st.session_state["cross_platform_reporting_all_dates"] = False
st.session_state["cross_platform_reporting_from_date"] = date(2026, 8, 2)
st.session_state["cross_platform_reporting_to_date"] = date(2026, 8, 2)
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 1), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 3), seller_sku="SKU-2", nav="300002",
        description="Persisted Coffee", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert app.error == []
    assert [message.value for message in app.info] == [
        "No committed product rows match the selected platform and payout dates."
    ]
    assert app.dataframe == []
    assert app.get("download_button") == []


@pytest.mark.parametrize("platform", ("Lazada", "TikTok"))
def test_cross_platform_unsupported_platform_stops_before_my_snapshot_read(platform):
    app = AppTest.from_string(
        f'''
import streamlit as st

from src.invoice_app.ui import cross_platform_product_summary as cross_ui

st.session_state["cross_platform_reporting_platform"] = {platform!r}
st.session_state["reporting_snapshot_loader_calls"] = 0

def forbidden_my_snapshot_load():
    st.session_state["reporting_snapshot_loader_calls"] += 1
    raise AssertionError("unsupported platform must stop before the MY snapshot read")

cross_ui._load_cross_platform_reporting_snapshot = forbidden_my_snapshot_load
cross_ui.render_cross_platform_product_summary()
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert app.session_state["reporting_snapshot_loader_calls"] == 0
    assert [message.value for message in app.info] == [
        f"{platform} Cross Platform reporting is not configured yet."
    ]
    assert app.dataframe == []
    assert app.get("download_button") == []


def test_cross_platform_filters_share_one_row():
    from src.invoice_app.ui import cross_platform_product_summary as summary_ui

    source = Path(summary_ui.__file__).read_text(encoding="utf-8")

    assert (
        'platform_col, from_date_col, to_date_col = st.columns(3, gap="small")'
        in source
    )
