"""Read-only Product Master recovery for the current staged Invoice batch."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.invoice_app.repositories.historical_invoice_repository import (
    HistoricalInvoiceRepository,
)

from .historical_invoice_intake import (
    PRODUCT_MASTER_ENRICHMENT_REQUIRED,
    InvoiceIntakeEntry,
    build_current_batch_staging,
    classify_staging,
)
from .product_price_master import ProductPriceMaster


@dataclass(frozen=True)
class ProductMasterRevalidationResult:
    previous_pm_blockers: int
    remaining_pm_blockers: int
    entries: tuple[InvoiceIntakeEntry, ...]


def has_product_master_dependent_blocker(
    entries: Sequence[InvoiceIntakeEntry],
) -> bool:
    """Return whether current historical staging has a PM-specific blocker."""

    return any(
        entry.reason_code == PRODUCT_MASTER_ENRICHMENT_REQUIRED
        for entry in entries
    )


def revalidate_current_invoice_batch(
    state: MutableMapping[str, Any],
    *,
    price_master: ProductPriceMaster,
    repository: HistoricalInvoiceRepository,
    staging_signature: str,
) -> ProductMasterRevalidationResult:
    """Atomically rebuild read-only historical staging with a newer PM.

    Existing source hashes are reused, so this path neither uploads nor reads
    or parses source PDFs. Classification is read-only; persistence remains an
    explicit later Review & Commit action.
    """

    previous_entries = tuple(state.get("uat2_historical_commit_entries", ()))
    if not has_product_master_dependent_blocker(previous_entries):
        raise ValueError(
            "The current Invoice batch has no Product Master-dependent blocker."
        )
    source_hashes = {
        (entry.source_filename, entry.order_id or ""): entry.source_hash
        for entry in previous_entries
        if entry.source_hash
    }
    candidates = build_current_batch_staging(
        batch_id=state.get("batch_id"),
        orders=state.get("orders", ()),
        products=state.get("products", ()),
        reviews=state.get("reviews", ()),
        price_master=price_master,
        source_hashes=source_hashes,
    )
    refreshed_entries = classify_staging(candidates, repository)

    # Commit the calculated result only after every refresh/revalidation step
    # succeeds, leaving current staging and prior classification intact on error.
    state["uat2_historical_commit_entries"] = refreshed_entries
    state["uat2_historical_commit_refresh_required"] = False
    state["uat2_historical_commit_signature"] = staging_signature
    state.pop("historical_validation_blocker", None)
    return ProductMasterRevalidationResult(
        previous_pm_blockers=sum(
            entry.reason_code == PRODUCT_MASTER_ENRICHMENT_REQUIRED
            for entry in previous_entries
        ),
        remaining_pm_blockers=sum(
            entry.reason_code == PRODUCT_MASTER_ENRICHMENT_REQUIRED
            for entry in refreshed_entries
        ),
        entries=refreshed_entries,
    )
