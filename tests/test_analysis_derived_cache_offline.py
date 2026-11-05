from dataclasses import replace
from datetime import date
import pytest
from tests.test_shopee_my_live_analysis import _snapshot
from src.invoice_app.services.shopee_analysis import AnalysisDateBasis, ShopeeAnalysisFilters
from src.invoice_app.ui import shopee_analysis_dashboard as ui

@pytest.mark.parametrize("market", ["my", "sg"])
def test_cache_tracks_snapshot_market_revision_and_filter(monkeypatch, market):
    monkeypatch.setattr(ui.st,"session_state",{})
    snapshot=replace(_snapshot(market="shopee_"+market,currency="MYR" if market=="my" else "SGD"), cache_token="one", source_revision=4)
    cache=ui._snapshot_derived(snapshot)
    calls=[]
    def options(): calls.append("options"); return ("stable",)
    assert ui._derived_value(cache,"product_options",options)==("stable",)
    assert ui._derived_value(ui._snapshot_derived(replace(snapshot)),"product_options",options)==("stable",)
    assert calls==["options"]
    built=[]
    def build(source,filters): built.append((source.market,filters)); return object()
    monkeypatch.setattr(ui,"build_shopee_analysis",build)
    f=ShopeeAnalysisFilters(AnalysisDateBasis.ORDER_CREATED,date(2026,1,1),date(2026,12,31))
    a=ui._derived_report(cache,snapshot,f)
    assert ui._derived_report(cache,snapshot,f) is a and len(built)==1
    assert ui._derived_report(cache,snapshot,replace(f,order_id_query="x")) is not a and len(built)==2
    for newer in (replace(snapshot,source_revision=5),replace(snapshot,cache_token="two")):
        assert ui._snapshot_derived(newer) is not cache
    unversioned=replace(snapshot,cache_token=None)
    assert ui._snapshot_derived(unversioned) is not ui._snapshot_derived(unversioned)

def test_market_caches_cannot_collide(monkeypatch):
    monkeypatch.setattr(ui.st,"session_state",{})
    my=replace(_snapshot(),cache_token="same",source_revision=1)
    sg=replace(_snapshot(market="shopee_sg",currency="SGD"),cache_token="same",source_revision=1)
    assert ui._snapshot_derived(my) is not ui._snapshot_derived(sg)
