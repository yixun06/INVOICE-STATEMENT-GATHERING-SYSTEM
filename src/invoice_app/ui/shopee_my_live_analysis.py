"""Shopee MY entry point for the shared canonical Shopee analysis dashboard."""

from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4
from time import monotonic
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from src.invoice_app.services import analysis_perf as perf
from src.invoice_app.services.dashboard_financial_summary import DashboardRefreshRequired, load_optimized_snapshot, read_checked_order_details, read_revision, validate_revision_infrastructure
from src.invoice_app.ui.analysis_session_snapshot import discard_session_snapshot, get_session_snapshot, target_identity

from src.invoice_app.services.market_context import SHOPEE_MY
from src.invoice_app.services.shopee_analysis import ShopeeAnalysisSnapshot
from src.invoice_app.services.shopee_my_live_analysis import load_shopee_my_analysis_snapshot
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings
from src.invoice_app.ui.shopee_analysis_dashboard import render_shopee_analysis_dashboard


@st.cache_data(ttl=45, show_spinner=False)
@perf.timed("cache_recompute")
def _load_shopee_my_analysis_snapshot() -> ShopeeAnalysisSnapshot:
    """Load MY's canonical PostgreSQL authorities with an explicit market lock."""

    perf.update(cache_state="MISS")
    settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
    return replace(load_shopee_my_analysis_snapshot(
        full_order_repository=settings.create_full_order_current_repository(),
        statement_reader=settings.create_statement_writer(),
    ), cache_token=uuid4().hex)


@st.cache_data(ttl=45, show_spinner=False)
@perf.timed("cache_recompute")
def _load_optimized_my_snapshot(compact_summary=False):
    perf.update(cache_state="MISS")
    settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
    statement_reader = settings.create_statement_writer()
    return replace(load_optimized_snapshot(market_context=SHOPEE_MY,
        full_order_repository=settings.create_full_order_current_repository(),
        statement_reader=statement_reader, compact_summary=compact_summary), cache_token=uuid4().hex, cache_created_monotonic=monotonic(),
        cache_target_id=target_identity(statement_reader._engine))


@st.cache_data(ttl=45, show_spinner=False)
@perf.timed("cache_recompute")
def _load_fact_my_snapshot():
    from src.invoice_app.services.financial_fact_poc import load_fact_snapshot
    settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
    engine = settings.create_statement_writer()._engine
    try:
        return replace(load_fact_snapshot(engine=engine, market_context=SHOPEE_MY),
            cache_token=uuid4().hex, cache_created_monotonic=monotonic(), cache_target_id=target_identity(engine))
    finally:
        engine.dispose()


def _session_fact_snapshot():
    settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
    engine = settings.create_statement_writer()._engine
    def revision():
        with engine.connect() as connection:
            validate_revision_infrastructure(connection)
            return read_revision(connection, SHOPEE_MY)
    try:
        return get_session_snapshot(st.session_state, market=SHOPEE_MY.key.value,
            target=target_identity(engine), loader=_load_fact_my_snapshot,
            read_current_revision=revision, variant="financial_fact_poc")
    finally:
        engine.dispose()


def _session_optimized_snapshot():
    settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
    engine = settings.create_statement_writer()._engine
    target = target_identity(engine)
    compact_summary = os.environ.get("INVOICEGATHER_COMPACT_FINANCIAL_SUMMARY") == "1"
    def loader():
        return _load_optimized_my_snapshot(compact_summary)
    loader.clear = lambda: _load_optimized_my_snapshot.clear(compact_summary)
    def revision():
        with engine.connect() as connection:
            validate_revision_infrastructure(connection)
            return read_revision(connection, SHOPEE_MY)
    try:
        return get_session_snapshot(st.session_state, market=SHOPEE_MY.key.value,
            target=target, loader=loader, read_current_revision=revision, variant=compact_summary)
    finally:
        engine.dispose()


def render_shopee_my_live_analysis(snapshot: ShopeeAnalysisSnapshot | None = None) -> None:
    """Render MY Analysis V2 without Invoice, PDF, or Google Sheets Invoice reads."""

    with perf.run("MY"):
        if perf.enabled():
            marker = "_analysis_perf_seen_MY"
            if not st.session_state.get(marker):
                perf.update(trigger="INITIAL")
                st.session_state[marker] = True
        try:
            with perf.stage("cache_lookup"):
                perf.update(cache_state="HIT" if snapshot is None else "BYPASS")
                if snapshot is not None:
                    discard_session_snapshot(st.session_state)
                    source = snapshot
                elif os.environ.get("INVOICEGATHER_FINANCIAL_FACT_POC") == "1":
                    source = _session_fact_snapshot()
                elif os.environ.get("INVOICEGATHER_FINANCIAL_SUMMARY_V2") == "1":
                    source = _session_optimized_snapshot()
                else:
                    discard_session_snapshot(st.session_state)
                    source = _load_shopee_my_analysis_snapshot()
        except DashboardRefreshRequired:
            st.warning("Refresh Required: financial data changed. Reload the dashboard before viewing details.")
            return
        except (RuntimeError, SQLAlchemyError) as error:
            st.error(f"Shopee MY Analysis V2 is unavailable: {error}")
            return
        detail_loader = None
        if source.source_revision is not None:
            def detail_loader(order_id):
                settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
                repository = settings.create_statement_writer()
                return read_checked_order_details(repository._engine, SHOPEE_MY, order_id, source.source_revision)
        from src.invoice_app.services.financial_fact_poc import FactSnapshot, build_checked_fact_report
        if isinstance(source, FactSnapshot):
            def report_loader(selected_snapshot, filters):
                settings = configured_uat2_data_settings(SHOPEE_MY, require_explicit_context=True)
                engine = settings.create_statement_writer()._engine
                try:
                    return build_checked_fact_report(engine, SHOPEE_MY, selected_snapshot, filters)
                finally:
                    engine.dispose()
            try:
                render_shopee_analysis_dashboard(source, detail_loader=detail_loader, report_loader=report_loader)
            except DashboardRefreshRequired:
                st.warning("Refresh Required: financial data changed. Reload the dashboard before viewing details.")
            except (RuntimeError, SQLAlchemyError) as error:
                st.error(f"Shopee MY Analysis V2 is unavailable: {error}")
        elif detail_loader is None:
            render_shopee_analysis_dashboard(source)
        else:
            render_shopee_analysis_dashboard(source, detail_loader=detail_loader)
