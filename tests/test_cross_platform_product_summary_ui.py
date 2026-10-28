from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def test_cross_platform_renderer_receives_the_canonical_column_order(monkeypatch):
    from src.invoice_app.services.cross_platform_product_summary import (
        CommittedReportingItem,
        CrossPlatformDateType,
        CrossPlatformReportingSnapshot,
    )
    from src.invoice_app.ui import cross_platform_product_summary as cross_ui

    snapshot = CrossPlatformReportingSnapshot(
        (
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
                payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 1), seller_sku="SKU-1",
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
        lambda: (CrossPlatformDateType.PAYOUT, "Shopee MY", True, None, None),
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
        CrossPlatformDateType,
        CrossPlatformReportingSnapshot,
    )
    from src.invoice_app.ui import cross_platform_product_summary as cross_ui

    snapshot = CrossPlatformReportingSnapshot(
        (
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
                payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 1), seller_sku="SKU-A",
                nav="300001", description="Persisted Tea", variation="", quantity=1,
                unit_price=Decimal("10.00"), line_subtotal=Decimal("18.00"),
                promotion_group_id=None, promotion_label=None, source_group_total=None,
            ),
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
                payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 1), seller_sku="SKU-A",
                nav="300001", description="Persisted Tea", variation="", quantity=2,
                unit_price=Decimal("10.00"), line_subtotal=Decimal("22.00"),
                promotion_group_id=None, promotion_label=None, source_group_total=None,
            ),
            CommittedReportingItem(
                platform="Shopee", currency="RM", order_id="SHP-3", item_index=0,
                payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 1), seller_sku="SKU-B",
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
        lambda: (CrossPlatformDateType.PAYOUT, "Shopee MY", True, None, None),
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


def test_cross_platform_dashboard_uses_the_calendar_range_to_filter_results():
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
if "calendar_range_seeded" not in st.session_state:
    st.session_state["calendar_range_seeded"] = True
    st.session_state["cross_platform_reporting_date_scope"] = "Selected dates"
    st.session_state["cross_platform_reporting_from_date_calendar"] = {
        "value": "2026-08-08"
    }
    st.session_state["cross_platform_reporting_to_date_calendar"] = {
        "value": "2026-08-09"
    }
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 8), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 9), order_created_date=date(2026, 8, 9), seller_sku="SKU-2", nav="300002",
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

    app.selectbox[1].set_value("Shopee SG").run(timeout=20)

    assert app.metric == []
    assert [message.value for message in app.info] == [
        "No order created dates are available for the selected platform."
    ]
    assert app.dataframe == []
    assert app.session_state["cross_platform_reporting_from_date_calendar"] == {}
    assert app.session_state["cross_platform_reporting_to_date_calendar"] == {}


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
        payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 1), seller_sku="9555208107347",
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
    assert [element.value for element in app.title] == ["Consolidated Product Summary"]
    assert [element.label for element in app.selectbox] == [
        "Date Type", "Platform", "Date range"
    ]
    assert app.selectbox[0].value.value == "Order Created Date"
    assert app.selectbox[1].options == ["Shopee MY", "Shopee SG"]
    assert "Shopee SG" in app.selectbox[1].options
    assert "All" not in app.selectbox[1].options
    assert "Shopee" not in app.selectbox[1].options
    assert "ZENXIN" not in app.selectbox[1].options
    assert [element.value for element in app.caption] == [
        "Current view: Order Created Date",
        "Select a valid From and To order created date.",
    ]
    assert app.date_input == []
    assert app.selectbox[2].value == "Selected dates"
    assert app.toggle == []
    assert app.dataframe == []
    assert app.get("download_button") == []


def test_cross_platform_date_type_switches_the_current_view_and_payout_eligibility():
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
        platform="Shopee", currency="RM", order_id="SHP-PENDING", item_index=0,
        payout_completed_date=None, order_created_date=date(2026, 8, 1),
        seller_sku="SKU-PENDING", nav="300001", description="Pending Tea", variation="",
        quantity=1, unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-RELEASED", item_index=0,
        payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 2),
        seller_sku="SKU-RELEASED", nav="300002", description="Released Coffee", variation="",
        quantity=1, unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert [element.value for element in app.caption] == [
        "Current view: Order Created Date",
        "Select a valid From and To order created date.",
    ]

    app.selectbox[2].set_value("All dates").run(timeout=20)

    calendars = [json.loads(calendar.proto.json) for calendar in app.get("bidi_component")]
    assert [(calendar["value"], calendar["disabled"]) for calendar in calendars] == [
        ("2026-08-01", True),
        ("2026-08-02", True),
    ]
    assert app.dataframe[0].value["SKU Code"].tolist() == [
        "SKU-PENDING",
        "SKU-RELEASED",
    ]

    app.selectbox[0].set_value("Payout Date").run(timeout=20)

    assert [element.value for element in app.caption] == [
        "Current view: Payout Date"
    ]
    assert app.dataframe[0].value["SKU Code"].tolist() == ["SKU-RELEASED"]


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
st.session_state["cross_platform_reporting_from_date_calendar"] = {
    "value": "2026-08-10"
}
st.session_state["cross_platform_reporting_to_date_calendar"] = {
    "value": "2026-08-08"
}
st.session_state["cross_platform_reporting_date_scope"] = "Selected dates"
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 8), order_created_date=date(2026, 8, 8), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 10), order_created_date=date(2026, 8, 10), seller_sku="SKU-2", nav="300002",
        description="Persisted Coffee", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
))
cross_ui.render_cross_platform_product_summary(snapshot)
'''
    )
    app.run(timeout=20)

    assert app.error == []
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
st.session_state["cross_platform_reporting_date_scope"] = "Selected dates"
st.session_state["cross_platform_reporting_from_date_calendar"] = {
    "value": "2026-08-03"
}
st.session_state["cross_platform_reporting_to_date_calendar"] = {
    "value": "2026-08-05"
}
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 1), order_created_date=date(2026, 8, 1), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 3), order_created_date=date(2026, 8, 3), seller_sku="SKU-2", nav="300002",
        description="Persisted Coffee", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-3", item_index=0,
        payout_completed_date=date(2026, 8, 5), order_created_date=date(2026, 8, 5), seller_sku="SKU-3", nav="300003",
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
    calendars = [json.loads(calendar.proto.json) for calendar in app.get("bidi_component")]
    assert calendars[1]["allowed_dates"] == ["2026-08-05"]
    assert app.dataframe[0].value["SKU Code"].tolist() == ["SKU-2", "SKU-3"]
    assert app.session_state["exported_payout_dates"] == ["2026-08-03", "2026-08-05"]
    assert [element.label for element in app.get("download_button")] == [
        "Consolidated Product Summary Barcode Table PDF",
        "Export Compact Barcode PDF",
    ]


def test_cross_platform_calendar_resets_a_stale_date_that_is_not_allowed():
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

st.session_state["cross_platform_reporting_date_scope"] = "All dates"
st.session_state["cross_platform_reporting_from_date_calendar"] = {
    "value": "2026-08-02"
}
st.session_state["cross_platform_reporting_to_date_calendar"] = {
    "value": "2026-08-02"
}
snapshot = CrossPlatformReportingSnapshot((
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-1", item_index=0,
        payout_completed_date=date(2026, 8, 1), order_created_date=date(2026, 8, 1), seller_sku="SKU-1", nav="300001",
        description="Persisted Tea", variation="", quantity=1,
        unit_price=Decimal("10.00"), line_subtotal=Decimal("10.00"),
        promotion_group_id=None, promotion_label=None, source_group_total=None,
    ),
    CommittedReportingItem(
        platform="Shopee", currency="RM", order_id="SHP-2", item_index=0,
        payout_completed_date=date(2026, 8, 3), order_created_date=date(2026, 8, 3), seller_sku="SKU-2", nav="300002",
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
    assert app.dataframe[0].value["SKU Code"].tolist() == ["SKU-1", "SKU-2"]
    assert app.get("download_button")


def test_cross_platform_calendar_explains_when_the_order_created_date_domain_is_empty():
    app = AppTest.from_string(
        '''
from src.invoice_app.services.cross_platform_product_summary import CrossPlatformReportingSnapshot
from src.invoice_app.ui import cross_platform_product_summary as cross_ui

cross_ui.export_cross_platform_product_summary_barcode_table_pdf = lambda *args, **kwargs: b"%PDF-test"
cross_ui.render_cross_platform_product_summary(CrossPlatformReportingSnapshot(()))
'''
    )
    app.run(timeout=20)

    assert app.exception == []
    assert "No order created dates are available for the selected platform." in [
        message.value for message in app.info
    ]
    calendars = app.get("bidi_component")
    assert len(calendars) == 2
    assert [json.loads(calendar.proto.json) for calendar in calendars] == [
        {
            "label": "From Date",
            "allowed_dates": [],
            "min_date": None,
            "max_date": None,
            "value": None,
            "disabled": True,
            "scope_id": "Shopee MY:ORDER_CREATED",
            "align_end": False,
        },
        {
            "label": "To Date",
            "allowed_dates": [],
            "min_date": None,
            "max_date": None,
            "value": None,
            "disabled": True,
            "scope_id": "Shopee MY:ORDER_CREATED",
            "align_end": True,
        },
    ]
    assert app.session_state["cross_platform_reporting_from_date_calendar"] == {}
    assert app.session_state["cross_platform_reporting_to_date_calendar"] == {}


def test_consolidated_summary_sg_loader_uses_only_the_sg_market_context(monkeypatch):
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        CrossPlatformReportingSnapshot,
    )
    from src.invoice_app.services.market_context import SHOPEE_SG
    from src.invoice_app.ui import cross_platform_product_summary as cross_ui

    snapshot = CrossPlatformReportingSnapshot(())
    contexts = []
    calls = []

    class Reader:
        def load_snapshot(self):
            raise AssertionError("Product Summary must not use the legacy snapshot loader.")

        def load_payout_snapshot(self):
            calls.append("payout")
            return snapshot

        def load_order_created_snapshot(self):
            calls.append("order_created")
            return snapshot

    class Settings:
        def create_cross_platform_product_summary_reader(self):
            return Reader()

    monkeypatch.setattr(
        cross_ui,
        "configured_uat2_data_settings",
        lambda context: contexts.append(context) or Settings(),
    )
    cross_ui._load_cross_platform_reporting_snapshot.clear()

    assert cross_ui._load_cross_platform_reporting_snapshot("Shopee SG") is snapshot
    assert (
        cross_ui._load_cross_platform_reporting_snapshot(
            "Shopee SG", CrossPlatformDateType.PAYOUT
        )
        is snapshot
    )
    assert contexts == [SHOPEE_SG, SHOPEE_SG]
    assert calls == ["order_created", "payout"]


def test_cross_platform_filters_use_the_component_calendar_instead_of_native_date_inputs():
    from src.invoice_app.ui import cross_platform_product_summary as summary_ui

    source = Path(summary_ui.__file__).read_text(encoding="utf-8")

    assert (
        'date_type_col, platform_col, from_date_col, to_date_col, all_dates_col = st.columns('
        in source
    )
    assert '"Date range"' in source
    assert "available_payout_date_picker(" in source
    assert "st.date_input(" not in source
