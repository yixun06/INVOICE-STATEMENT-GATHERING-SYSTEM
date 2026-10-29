"""Shopee SG entry point for the shared canonical Shopee analysis dashboard."""

from __future__ import annotations

import streamlit as st

from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.services.shopee_analysis import ShopeeAnalysisSnapshot
from src.invoice_app.services.shopee_sg_analysis import load_shopee_sg_analysis_snapshot
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings
from src.invoice_app.ui.shopee_analysis_dashboard import render_shopee_analysis_dashboard


@st.cache_data(ttl=45, show_spinner=False)
def _load_shopee_sg_analysis_snapshot() -> ShopeeAnalysisSnapshot:
    """Load SG's canonical PostgreSQL authorities with an explicit market lock."""

    settings = configured_uat2_data_settings(SHOPEE_SG, require_explicit_context=True)
    return load_shopee_sg_analysis_snapshot(
        full_order_repository=settings.create_full_order_current_repository(),
        statement_reader=settings.create_statement_writer(),
    )


def render_shopee_sg_analysis(snapshot: ShopeeAnalysisSnapshot | None = None) -> None:
    """Render SG through the shared market-neutral analysis dashboard."""

    try:
        source = snapshot if snapshot is not None else _load_shopee_sg_analysis_snapshot()
    except RuntimeError as error:
        st.error(f"Shopee SG Analysis V2 is unavailable: {error}")
        return
    render_shopee_analysis_dashboard(source)
