from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.utils.monetary import streamlit_currency_format


def test_my_analysis_v2_renders_the_shared_dashboard_with_canonical_snapshot():
    app = AppTest.from_string(
        "from src.invoice_app.ui.shopee_my_live_analysis import render_shopee_my_live_analysis\n"
        "from tests.test_shopee_my_live_analysis import _snapshot\n"
        "render_shopee_my_live_analysis(_snapshot())\n"
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [title.value for title in app.title] == ["Shopee MY - Analysis V2"]
    assert {tab.label for tab in app.tabs} == {
        "Overview", "Product Analysis", "Financial & Reconciliation",
    }
    assert {metric.label for metric in app.metric} >= {
        "Product Sales", "Total Orders", "Product Sales with Statement Evidence",
        "Orders with committed Statement evidence", "Pending Statement evidence",
    }
    assert "More filters" in [expander.label for expander in app.expander]
    assert [element.label for element in app.selectbox[:1]] == ["Date basis"]
    assert any(element.label == "Product" for element in app.multiselect)
    assert any(element.label == "Order status" for element in app.multiselect)
    assert any(element.label == "Search Order ID..." for element in app.text_input)
    assert not any(element.label == "Order" for element in app.selectbox)
    aggregations = [element for element in app.selectbox if element.label == "Aggregation"]
    assert aggregations and all(element.options == ["Daily", "Weekly", "Monthly"] for element in aggregations)
    assert any("Order Created Date" in caption.value for caption in app.caption)
    assert any("of 1 selected orders have committed Statement evidence" in caption.value for caption in app.caption)


def test_shared_dashboard_overview_keeps_only_coverage_and_breakdown_not_duplicate_trends():
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "src/invoice_app/ui/shopee_analysis_dashboard.py").read_text(encoding="utf-8")
    overview = renderer[renderer.index("def _render_overview"):renderer.index("def _render_settlement_breakdown")]

    assert "_render_trend(" not in overview
    assert "_render_settlement_breakdown(report)" in overview
    assert "_render_reconciliation_summary(report)" in overview
    assert "Statement-evidenced Product Sales minus Released Amount." in overview


def test_financial_order_inspection_precedes_reconciliation_table_and_chart_titles_are_explicit():
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "src/invoice_app/ui/shopee_analysis_dashboard.py").read_text(encoding="utf-8")
    financial = renderer[renderer.index("def _render_financial_analysis"):renderer.index("def _render_order_inspector")]

    assert financial.index("_render_order_inspector(report)") < financial.index('st.subheader("Order reconciliation")')
    assert 'st.markdown(f"**{metric} by {basis} · {aggregation}**")' in renderer
    assert "_render_reconciliation_bar(frame)" in renderer
    assert "mark_arc" not in renderer


def test_order_inspection_is_search_first_and_missing_statement_is_a_lifecycle_state():
    app = AppTest.from_string(
        "from src.invoice_app.ui.shopee_my_live_analysis import render_shopee_my_live_analysis\n"
        "from tests.test_shopee_my_live_analysis import _snapshot\n"
        "render_shopee_my_live_analysis(_snapshot(include_statement=False))\n"
    )
    app.run(timeout=20)

    search = next(element for element in app.text_input if element.label == "Search Order ID...")
    search.set_value("MY-1").run(timeout=20)
    order = next(element for element in app.selectbox if element.label == "Matching Order ID")
    order.set_value("MY-1").run(timeout=20)

    assert app.exception == []
    assert any(message.value == "Statement not available yet" for message in app.info)
    assert any("pending settlement evidence, not a reconciliation mismatch" in caption.value for caption in app.caption)


def test_my_analysis_v2_has_a_canonical_empty_state_without_a_legacy_fallback():
    app = AppTest.from_string(
        "from src.invoice_app.repositories.shopee_full_order_current_repository import FullOrderCurrentSnapshot\n"
        "from src.invoice_app.services.shopee_analysis import ShopeeAnalysisPresentation, ShopeeAnalysisSnapshot\n"
        "from src.invoice_app.ui.shopee_my_live_analysis import render_shopee_my_live_analysis\n"
        "render_shopee_my_live_analysis(ShopeeAnalysisSnapshot(ShopeeAnalysisPresentation('Shopee MY', 'MYR', 'RM'), 'shopee_my', FullOrderCurrentSnapshot((), ()), ()))\n"
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [message.value for message in app.info] == [
        "No canonical dates are available for this source authority."
    ]


def test_shared_renderer_uses_sg_currency_without_a_market_specific_ui_copy():
    app = AppTest.from_string(
        "from src.invoice_app.ui.shopee_analysis_dashboard import render_shopee_analysis_dashboard\n"
        "from tests.test_shopee_my_live_analysis import _snapshot\n"
        "render_shopee_analysis_dashboard(_snapshot(market='shopee_sg', currency='SGD'))\n"
    )
    app.run(timeout=20)

    assert app.exception == []
    assert [title.value for title in app.title] == ["Shopee SG - Analysis V2"]
    assert streamlit_currency_format("SGD") == "S$ %,.2f"
    renderer = (Path(__file__).resolve().parents[1] / "src/invoice_app/ui/shopee_analysis_dashboard.py").read_text(encoding="utf-8")
    assert "currency = report.presentation.currency_code" in renderer


def test_my_v2_modules_do_not_depend_on_legacy_invoice_reading_or_formulas():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "src/invoice_app/services/shopee_my_live_analysis.py",
            "src/invoice_app/services/shopee_analysis.py",
            "src/invoice_app/ui/shopee_my_live_analysis.py",
            "src/invoice_app/ui/shopee_analysis_dashboard.py",
        )
    )

    forbidden = (
        "Invoice_Orders", "Invoice_Items", "invoice_financial_layout", "final_amount",
        "line_subtotal", "NORMAL_ORDER", "RETURN_REFUND",
        "create_cross_platform_product_summary_reader", "load_snapshot_from_reporting_snapshot",
        "google_sheets_historical_invoice_repository", "CanonicalInvoice",
    )
    assert not [term for term in forbidden if term in source]


def test_application_route_remains_on_the_my_analysis_entry_point():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    branch = source[source.index('elif selected_page == "Shopee MY":'):]

    assert "render_shopee_my_live_analysis()" in branch
