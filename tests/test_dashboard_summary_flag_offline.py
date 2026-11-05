from dataclasses import replace
from types import SimpleNamespace
import pytest
from tests.test_shopee_my_live_analysis import _snapshot

@pytest.mark.parametrize("market", ["my", "sg"])
@pytest.mark.parametrize("enabled", [False, True])
def test_dashboard_selects_opt_in_pipeline_and_lazy_detail(monkeypatch, market, enabled):
    if market == "my":
        import src.invoice_app.ui.shopee_my_live_analysis as page
        render=page.render_shopee_my_live_analysis
    else:
        import src.invoice_app.ui.shopee_sg_analysis as page
        render=page.render_shopee_sg_analysis
    snapshot=_snapshot(market="shopee_"+market, currency="MYR" if market=="my" else "SGD")
    chosen=replace(snapshot,source_revision=7) if enabled else snapshot
    calls=[]
    monkeypatch.setenv("INVOICEGATHER_FINANCIAL_SUMMARY_V2", "1" if enabled else "0")
    monkeypatch.setattr(page, f"_load_shopee_{market}_analysis_snapshot", lambda: (calls.append("legacy"),chosen)[1])
    monkeypatch.setattr(page, "_session_optimized_snapshot", lambda: (calls.append("optimized"),chosen)[1])
    monkeypatch.setattr(page, "configured_uat2_data_settings", lambda *args,**kwargs: SimpleNamespace(create_statement_writer=lambda: SimpleNamespace(_engine="fake-engine")))
    monkeypatch.setattr(page, "read_checked_order_details", lambda engine,context,order_id,revision: (calls.append((context.key.value,order_id,revision)),())[1])
    def renderer(source, detail_loader=None):
        assert source is chosen
        if enabled:
            assert detail_loader is not None
            detail_loader("A")
        else:
            assert detail_loader is None
    monkeypatch.setattr(page,"render_shopee_analysis_dashboard",renderer)
    render()
    assert calls[0]==("optimized" if enabled else "legacy")
    if enabled: assert calls[1]==("shopee_"+market,"A",7)


@pytest.mark.parametrize("market", ["my", "sg"])
def test_revision_change_shows_refresh_required(monkeypatch, market):
    from src.invoice_app.services.dashboard_financial_summary import DashboardRefreshRequired
    if market == "my":
        import src.invoice_app.ui.shopee_my_live_analysis as page
        render=page.render_shopee_my_live_analysis
    else:
        import src.invoice_app.ui.shopee_sg_analysis as page
        render=page.render_shopee_sg_analysis
    warnings=[]
    monkeypatch.setenv("INVOICEGATHER_FINANCIAL_SUMMARY_V2", "1")
    def stale(): raise DashboardRefreshRequired("stale")
    monkeypatch.setattr(page,"_session_optimized_snapshot",stale)
    monkeypatch.setattr(page.st,"warning",warnings.append)
    render()
    assert len(warnings)==1 and "Refresh Required" in warnings[0]
