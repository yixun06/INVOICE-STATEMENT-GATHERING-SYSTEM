import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize("market", ["my", "sg"])
def test_filters_are_applied_only_on_request_and_reset(market):
    app = AppTest.from_string(
        f"from tests.test_shopee_{market}_" + ("live_analysis" if market == "my" else "analysis") + " import _snapshot\n"
        "from src.invoice_app.ui.shopee_analysis_dashboard import render_shopee_analysis_dashboard\n"
        "render_shopee_analysis_dashboard(_snapshot())\n"
    ).run(timeout=30)
    assert not app.exception
    baseline = next(metric.value for metric in app.metric if metric.label == "Total Orders")
    next(item for item in app.text_input if item.label == "Order ID contains").set_value("NO-MATCH").run(timeout=30)
    assert next(metric.value for metric in app.metric if metric.label == "Total Orders") == baseline
    assert not any("NO-MATCH" in item.value for item in app.caption if "Applied filters:" in item.value)
    next(item for item in app.button if item.label == "Apply filters").click().run(timeout=30)
    assert not app.exception
    assert next(metric.value for metric in app.metric if metric.label == "Total Orders") == "0"
    assert any("NO-MATCH" in item.value for item in app.caption if "Applied filters:" in item.value)
    next(control for control in app.get("button_group") if control.label == "Analysis workspace").set_value("Product Analysis").run(timeout=30)
    assert any("NO-MATCH" in item.value for item in app.caption if "Applied filters:" in item.value)
    next(control for control in app.get("button_group") if control.label == "Analysis workspace").set_value("Overview").run(timeout=30)
    next(item for item in app.button if item.label == "Reset filters").click().run(timeout=30)
    assert not app.exception
    assert next(metric.value for metric in app.metric if metric.label == "Total Orders") == baseline


@pytest.mark.parametrize("market", ["my", "sg"])
def test_module_navigation_and_trend_aggregations(market):
    module = "live_analysis" if market == "my" else "analysis"
    app = AppTest.from_string(
        f"from tests.test_shopee_{market}_{module} import _snapshot\n"
        "from src.invoice_app.ui.shopee_analysis_dashboard import render_shopee_analysis_dashboard\n"
        "render_shopee_analysis_dashboard(_snapshot())\n"
    ).run(timeout=30)
    for name in ("Product Analysis", "Financial & Reconciliation"):
        next(control for control in app.get("button_group") if control.label == "Analysis workspace").set_value(name).run(timeout=30)
        assert not app.exception
        for aggregation in ("Daily", "Weekly", "Monthly"):
            next(control for control in app.selectbox if control.label == "Aggregation").select(aggregation).run(timeout=30)
            assert not app.exception
        assert app.session_state[f"analysis_module_Shopee {market.upper()}"] == name
