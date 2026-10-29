"""Shopee MY provider for the shared canonical analysis dashboard."""

from __future__ import annotations

from src.invoice_app.repositories.shopee_full_order_current_repository import (
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.market_context import SHOPEE_MY
from src.invoice_app.services.shopee_analysis import (
    ShopeeAnalysisSnapshot,
    ShopeeAnalysisStatementReader,
    load_shopee_analysis_snapshot,
)


def load_shopee_my_analysis_snapshot(
    *,
    full_order_repository: ShopeeFullOrderCurrentRepository,
    statement_reader: ShopeeAnalysisStatementReader,
) -> ShopeeAnalysisSnapshot:
    """Load MY-only PostgreSQL Full Order and Statement projection evidence."""

    return load_shopee_analysis_snapshot(
        market_context=SHOPEE_MY,
        full_order_repository=full_order_repository,
        statement_reader=statement_reader,
    )
