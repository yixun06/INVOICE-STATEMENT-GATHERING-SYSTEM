from dataclasses import replace
from datetime import date
import pytest
from tests.test_shopee_my_live_analysis import _snapshot
from src.invoice_app.ui.analysis_session_snapshot import discard_session_snapshot, get_session_snapshot
from src.invoice_app.services.dashboard_financial_summary import DashboardRefreshRequired
from src.invoice_app.services.shopee_analysis import AnalysisDateBasis, ShopeeAnalysisFilters, build_shopee_analysis

class Loader:
 def __init__(self, snapshot): self.snapshot=snapshot; self.calls=0; self.clears=0; self.refreshed=None
 def __call__(self): self.calls+=1; return self.snapshot
 def clear(self):
  self.clears+=1
  if self.refreshed is not None: self.snapshot=self.refreshed

def make(market="shopee_my",revision=1,token="a",created=100):
 return replace(_snapshot(market=market,currency="MYR" if market=="shopee_my" else "SGD"),source_revision=revision,cache_token=token,cache_created_monotonic=created,cache_target_id="db")

def call(state, loader, clock, rev, market="shopee_my", target="db"):
 return get_session_snapshot(state,market=market,target=target,loader=loader,read_current_revision=lambda:rev[0],now=lambda:clock[0])

def test_repeated_reruns_and_ttl_do_not_extend_creation_time():
 state={};clock=[100];rev=[1];first=make();loader=Loader(first)
 assert call(state,loader,clock,rev) is first
 clock[0]=144.9; assert call(state,loader,clock,rev) is first and loader.calls==1
 fresh=make(token="b",created=145);loader.refreshed=fresh
 clock[0]=145; assert call(state,loader,clock,rev) is fresh
 assert loader.calls==3 and loader.clears==1

def test_revision_change_rejects_stale_shared_cache():
 state={};clock=[100];rev=[1];loader=Loader(make());call(state,loader,clock,rev)
 rev[0]=2;new=make(revision=2,token="b");loader.refreshed=new
 with pytest.raises(DashboardRefreshRequired): call(state,loader,clock,rev)
 assert not state
 assert call(state,loader,clock,rev) is new and loader.clears==1

def test_market_switch_target_and_identity_invalidation():
 state={"_analysis_derived_shopee_my":{"old":True}};clock=[100];rev=[1];loader=Loader(make())
 call(state,loader,clock,rev);other=Loader(make(market="shopee_sg"))
 assert call(state,other,clock,rev,market="shopee_sg").market=="shopee_sg"
 assert "_analysis_derived_shopee_my" not in state
 other.refreshed=replace(other.snapshot,cache_target_id="other-db",cache_token="new-target")
 assert call(state,other,clock,rev,market="shopee_sg",target="other-db").market=="shopee_sg" and other.calls==3
 state["_analysis_session_snapshot"]["token"]="tampered"
 call(state,other,clock,rev,market="shopee_sg",target="other-db")
 assert other.calls==4

def test_failed_revision_check_does_not_return_stale_snapshot():
 state={};clock=[100];loader=Loader(make())
 with pytest.raises(RuntimeError,match="refresh"):
  call(state,loader,clock,[2])
 assert not state and loader.clears==2

def test_report_financial_parity_after_session_hit():
 state={};clock=[100];rev=[1];loader=Loader(make());a=call(state,loader,clock,rev);clock[0]=101;b=call(state,loader,clock,rev)
 filters=ShopeeAnalysisFilters(AnalysisDateBasis.ORDER_CREATED,date(2026,1,1),date(2026,12,31))
 assert build_shopee_analysis(a,filters)==build_shopee_analysis(b,filters)


def test_discard_releases_snapshot_and_derived_values():
 state={};clock=[100];loader=Loader(make());call(state,loader,clock,[1])
 state["_analysis_derived_shopee_my"]={"report":object()}
 discard_session_snapshot(state)
 assert not state
