"""Validate-first orchestration for one Shopee Full Order current sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from src.invoice_app.parsers.shopee_full_order_parser import (
    ParsedShopeeFullOrder,
    parse_shopee_full_order,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderSyncResult,
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.market_context import MarketContext, MarketKey
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentBatch,
    adapt_shopee_full_order,
)


@dataclass(frozen=True)
class ShopeeFullOrderImportReview:
    source_bytes: bytes
    parsed: ParsedShopeeFullOrder
    current_batch: FullOrderCurrentBatch
    sync_preview: FullOrderSyncResult | None

    @property
    def ready(self) -> bool:
        return self.current_batch.syncable and self.sync_preview is not None


def review_shopee_full_order_upload(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str,
    market: MarketContext | MarketKey | str,
    product_master: ProductPriceMaster,
    repository: ShopeeFullOrderCurrentRepository,
) -> ShopeeFullOrderImportReview:
    source_bytes = _source_bytes(source)
    parsed = parse_shopee_full_order(
        source_bytes,
        source_filename=source_filename,
        market=market,
    )
    batch = adapt_shopee_full_order(parsed, product_master=product_master)
    preview = repository.classify(batch) if batch.syncable else None
    return ShopeeFullOrderImportReview(source_bytes, parsed, batch, preview)


def commit_shopee_full_order_review(
    review: ShopeeFullOrderImportReview,
    *,
    load_product_master: Callable[[], ProductPriceMaster],
    repository: ShopeeFullOrderCurrentRepository,
) -> FullOrderSyncResult:
    if not review.ready:
        raise ValueError("Shopee Full Order review is not ready to sync.")
    parsed = parse_shopee_full_order(
        review.source_bytes,
        source_filename=review.parsed.source_filename,
        market=review.parsed.market,
    )
    if parsed.source_hash != review.parsed.source_hash:
        raise ValueError("Shopee Full Order source changed after validation.")
    refreshed = adapt_shopee_full_order(
        parsed,
        product_master=load_product_master(),
    )
    if not refreshed.syncable:
        raise ValueError(
            "Shopee Full Order Product Master resolution changed; validate again."
        )
    return repository.sync(refreshed)


def _source_bytes(source: str | Path | bytes | bytearray | BinaryIO) -> bytes:
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "getvalue"):
        return bytes(source.getvalue())
    position = source.tell() if hasattr(source, "tell") else None
    data = source.read()
    if position is not None and hasattr(source, "seek"):
        source.seek(position)
    return bytes(data)
