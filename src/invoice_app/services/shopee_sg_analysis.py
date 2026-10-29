"""Shopee SG provider for the shared canonical analysis dashboard."""

from __future__ import annotations

from src.invoice_app.repositories.shopee_full_order_current_repository import (
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.market_context import SHOPEE_SG
from src.invoice_app.services.shopee_analysis import (
    ShopeeAnalysisSnapshot,
    ShopeeAnalysisStatementReader,
    load_shopee_analysis_snapshot,
)


def load_shopee_sg_analysis_snapshot(
    *,
    full_order_repository: ShopeeFullOrderCurrentRepository,
    statement_reader: ShopeeAnalysisStatementReader,
) -> ShopeeAnalysisSnapshot:
    """Load SG-only PostgreSQL Full Order and Statement projection evidence."""

    return load_shopee_analysis_snapshot(
        market_context=SHOPEE_SG,
        full_order_repository=full_order_repository,
        statement_reader=statement_reader,
    )
