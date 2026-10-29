"""Validate-first orchestration for one Shopee Full Order current sync."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from src.invoice_app.services.market_context import (
    MarketContext,
    MarketKey,
    resolve_market_context,
)
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
    comparison_error: str | None = None

    @property
    def ready(self) -> bool:
        return self.current_batch.syncable and self.sync_preview is not None


class ShopeeFullOrderMarketMismatch(ValueError):
    """A valid Full Order source belongs to a different explicit market."""

    def __init__(
        self,
        *,
        source_bytes: bytes,
        parsed: ParsedShopeeFullOrder,
        current_market: MarketContext,
    ) -> None:
        self.source_bytes = source_bytes
        self.parsed = parsed
        self.current_market = current_market
        super().__init__(
            "MARKET_MISMATCH: current market "
            f"{current_market.display_name}; detected source "
            f"{parsed.market.display_name}."
        )


def inspect_shopee_full_order_upload(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str,
) -> tuple[bytes, ParsedShopeeFullOrder]:
    """Read source-owned market facts before any market-specific dependency."""

    source_bytes = _source_bytes(source)
    return source_bytes, parse_shopee_full_order(
        source_bytes,
        source_filename=source_filename,
    )


def review_shopee_full_order_upload(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str,
    market: MarketContext | MarketKey | str,
    product_master: ProductPriceMaster,
    repository: ShopeeFullOrderCurrentRepository,
) -> ShopeeFullOrderImportReview:
    expected_market = resolve_market_context(market)
    source_bytes, parsed = inspect_shopee_full_order_upload(
        source, source_filename=source_filename
    )
    if parsed.market != expected_market:
        raise ShopeeFullOrderMarketMismatch(
            source_bytes=source_bytes,
            parsed=parsed,
            current_market=expected_market,
        )
    batch = adapt_shopee_full_order(parsed, product_master=product_master)
    # Preview only complete orders, while retaining all issues as a whole-batch
    # commit blocker. The repository owns the canonical comparison semantics.
    preview = None
    comparison_error = None
    try:
        if batch.orders:
            preview = repository.classify(replace(batch, issues=()))
            expected = {(order.market, order.order_id) for order in batch.orders}
            actual = {(decision.market, decision.order_id) for decision in preview.decisions}
            if (actual != expected or len(preview.decisions) != len(expected)
                or preview.new_count + preview.updated_count + preview.unchanged_count != len(expected)):
                raise ValueError("Full Order classification is incomplete.")
        else:
            preview = FullOrderSyncResult(decisions=())
        issue_ids = {issue.order_id for issue in batch.issues}
        valid_ids = {order.order_id for order in batch.orders}
        if issue_ids & valid_ids or issue_ids | valid_ids != set(parsed.order_ids):
            raise ValueError("Full Order validation does not cover every workbook order.")
    except Exception:
        preview = None
        comparison_error = "Current-store classification is unavailable. Validate again."
    return ShopeeFullOrderImportReview(source_bytes, parsed, batch, preview, comparison_error)


def refresh_shopee_full_order_review(
    review: ShopeeFullOrderImportReview,
    *,
    product_master: ProductPriceMaster,
    repository: ShopeeFullOrderCurrentRepository,
) -> ShopeeFullOrderImportReview:
    """Revalidate one retained Full Order source without any Current-data write."""

    return review_shopee_full_order_upload(
        review.source_bytes,
        source_filename=review.parsed.source_filename,
        market=review.parsed.market,
        product_master=product_master,
        repository=repository,
    )


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
