"""Bounded per-session reuse of revision-checked Analysis snapshots."""
from __future__ import annotations
from time import monotonic
from hashlib import sha256
from src.invoice_app.services import analysis_perf as perf
from src.invoice_app.services.dashboard_financial_summary import DashboardRefreshRequired

_KEY = "_analysis_session_snapshot"
_TTL_SECONDS = 45.0

def target_identity(engine):
    return sha256(engine.url.render_as_string(hide_password=False).encode()).hexdigest()

def discard_session_snapshot(state):
    entry = state.pop(_KEY, None)
    if entry is not None:
        state.pop("_analysis_derived_" + entry["market"], None)

def get_session_snapshot(state, *, market, target, loader, read_current_revision, now=monotonic, variant=None):
    """Reuse only a fresh snapshot whose market, identity and DB revision agree.

    The timestamp belongs to the original cached computation; a hit never
    extends it. The loader retains its own st.cache_data TTL and isolation.
    """
    entry = state.get(_KEY)
    if entry is not None and (entry["market"] != market or entry.get("variant") != variant):
        discard_session_snapshot(state)
        entry = None
    revision = read_current_revision()
    if entry is not None:
        snap = entry["snapshot"]
        if entry["market"] == market and entry["target"] == target and snap.source_revision != revision:
            discard_session_snapshot(state)
            raise DashboardRefreshRequired("Financial data changed. Refresh the dashboard before viewing details.")
        age = now() - snap.cache_created_monotonic if snap.cache_created_monotonic is not None else -1
        if (entry["market"] == market and entry["target"] == target
            and snap.market == market and snap.source_revision == revision and snap.cache_target_id == target
            and snap.cache_token == entry["token"] and snap.cache_token
            and 0 <= age < _TTL_SECONDS):
            with perf.stage("session_snapshot_hit"):
                return snap
        discard_session_snapshot(state)
    for attempt in range(2):
        snapshot = loader()
        created = snapshot.cache_created_monotonic
        current = read_current_revision()
        if (snapshot.market == market and snapshot.cache_token and snapshot.cache_target_id == target
            and snapshot.source_revision == current
            and created is not None and 0 <= now() - created < _TTL_SECONDS):
            state[_KEY] = {"market": market, "target": target, "token": snapshot.cache_token, "snapshot": snapshot, "variant": variant}
            return snapshot
        loader.clear()
    raise RuntimeError("Analysis snapshot changed or expired while loading; refresh is required.")
