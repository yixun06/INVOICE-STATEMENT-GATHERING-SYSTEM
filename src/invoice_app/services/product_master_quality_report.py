"""Read-only diagnostics for Shopee Product Listing data quality.

This module deliberately does not participate in invoice parsing, price lookup,
pricing allocation, validation, or reporting aggregation. It makes the exact
Product Listing candidate pool visible so ecommerce can correct source master
data without the application guessing a price.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.invoice_app.services.product_price_master import (
    ProductPriceMaster,
    ProductPriceMasterRecord,
)
from src.invoice_app.utils.normalize import normalize_whitespace


LOOKUP_STATUS_MATCHED = "MATCHED"
LOOKUP_STATUS_NOT_FOUND = "PRICE_NOT_FOUND"
LOOKUP_STATUS_CONFLICT = "PRICING_CONFLICT"
_CSV_COLUMNS = (
    "Platform",
    "Order ID",
    "Invoice Seller SKU",
    "Invoice Product Name",
    "Invoice Variation",
    "Matched Via",
    "Master Source Row(s)",
    "Master SKU",
    "Master Parent SKU",
    "Master Product Name",
    "Master Variation",
    "Candidate Count",
    "Unique Candidate Prices",
    "Lookup Status",
    "Reason",
)


@dataclass(frozen=True)
class ProductMasterQualityReportRow:
    """One auditable invoice-product diagnosis against one selected master."""

    platform: str
    order_id: str
    invoice_seller_sku: str
    invoice_product_name: str
    invoice_variation: str
    matched_via: str
    master_source_rows: str
    master_sku: str
    master_parent_sku: str
    master_product_name: str
    master_variation: str
    candidate_count: int
    unique_candidate_prices: str
    lookup_status: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "Platform": self.platform,
            "Order ID": self.order_id,
            "Invoice Seller SKU": self.invoice_seller_sku,
            "Invoice Product Name": self.invoice_product_name,
            "Invoice Variation": self.invoice_variation,
            "Matched Via": self.matched_via,
            "Master Source Row(s)": self.master_source_rows,
            "Master SKU": self.master_sku,
            "Master Parent SKU": self.master_parent_sku,
            "Master Product Name": self.master_product_name,
            "Master Variation": self.master_variation,
            "Candidate Count": self.candidate_count,
            "Unique Candidate Prices": self.unique_candidate_prices,
            "Lookup Status": self.lookup_status,
            "Reason": self.reason,
        }


@dataclass(frozen=True)
class ProductMasterQualityReportSummary:
    total_product_rows_checked: int
    matched_count: int
    price_not_found_count: int
    pricing_conflict_count: int
    top_conflict_skus: tuple[tuple[str, int], ...]
    top_not_found_skus: tuple[tuple[str, int], ...]
    name_or_variation_mismatch_rows: tuple[ProductMasterQualityReportRow, ...]


def build_product_master_quality_report(
    invoice_product_rows: Iterable[Mapping[str, Any]],
    price_master: ProductPriceMaster,
) -> tuple[ProductMasterQualityReportRow, ...]:
    """Diagnose rows selected by the caller for a single Product Listing.

    The candidate pool is limited to exact Invoice Seller SKU matches against
    both Master SKU and Master Parent SKU. It does not invoke any parser,
    pricing, or validation workflow.
    """
    return tuple(_diagnose_row(row, price_master) for row in invoice_product_rows)


def summarize_product_master_quality_report(
    report_rows: Iterable[ProductMasterQualityReportRow],
    *,
    top_limit: int = 10,
) -> ProductMasterQualityReportSummary:
    """Summarise a report without changing any invoice or master data."""
    rows = tuple(report_rows)
    conflicts = Counter(
        row.invoice_seller_sku
        for row in rows
        if row.lookup_status == LOOKUP_STATUS_CONFLICT
    )
    not_found = Counter(
        row.invoice_seller_sku
        for row in rows
        if row.lookup_status == LOOKUP_STATUS_NOT_FOUND
    )
    mismatch_rows = tuple(
        row
        for row in rows
        if "Invoice Product Name does not exactly match" in row.reason
        or "Invoice Variation does not exactly match" in row.reason
    )
    return ProductMasterQualityReportSummary(
        total_product_rows_checked=len(rows),
        matched_count=sum(row.lookup_status == LOOKUP_STATUS_MATCHED for row in rows),
        price_not_found_count=sum(
            row.lookup_status == LOOKUP_STATUS_NOT_FOUND for row in rows
        ),
        pricing_conflict_count=sum(
            row.lookup_status == LOOKUP_STATUS_CONFLICT for row in rows
        ),
        top_conflict_skus=_top_skus(conflicts, top_limit),
        top_not_found_skus=_top_skus(not_found, top_limit),
        name_or_variation_mismatch_rows=mismatch_rows,
    )


def write_product_master_quality_report_csv(
    report_rows: Iterable[ProductMasterQualityReportRow],
    destination: str | Path,
) -> Path:
    """Write an explicit developer-requested CSV; never write one implicitly."""
    path = Path(destination)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in report_rows:
            writer.writerow(row.as_dict())
    return path


def _diagnose_row(
    invoice_row: Mapping[str, Any],
    price_master: ProductPriceMaster,
) -> ProductMasterQualityReportRow:
    platform = _display(invoice_row.get("platform"))
    order_id = _display(invoice_row.get("order_id"))
    seller_sku = _sku_text(invoice_row.get("seller_sku"))
    product_name = _display(invoice_row.get("product_name"))
    variation = _display(
        invoice_row.get("variation_name")
        or invoice_row.get("variation")
        or invoice_row.get("reporting_variation_name")
    )
    candidates = _exact_candidate_pool(price_master.records, seller_sku)
    matched_via = _matched_via(candidates, seller_sku)
    status, primary_reason = _lookup_outcome(candidates, seller_sku, product_name, variation)
    mismatch_reasons = _identity_mismatch_reasons(candidates, product_name, variation)
    reason = "; ".join((primary_reason, *mismatch_reasons))

    return ProductMasterQualityReportRow(
        platform=platform,
        order_id=order_id,
        invoice_seller_sku=seller_sku,
        invoice_product_name=product_name,
        invoice_variation=variation,
        matched_via=matched_via,
        master_source_rows=_join(record.source_row for record in candidates),
        master_sku=_join(record.seller_sku for record in candidates),
        master_parent_sku=_join(record.parent_sku for record in candidates),
        master_product_name=_join(record.product_name for record in candidates),
        master_variation=_join(record.variation_name for record in candidates),
        candidate_count=len(candidates),
        unique_candidate_prices=_join_prices(candidates),
        lookup_status=status,
        reason=reason,
    )


def _exact_candidate_pool(
    records: Iterable[ProductPriceMasterRecord],
    seller_sku: str,
) -> tuple[ProductPriceMasterRecord, ...]:
    if not seller_sku:
        return ()
    return tuple(
        record
        for record in records
        if record.seller_sku == seller_sku or record.parent_sku == seller_sku
    )


def _matched_via(
    candidates: tuple[ProductPriceMasterRecord, ...],
    seller_sku: str,
) -> str:
    if not candidates:
        return "None"
    has_sku = any(record.seller_sku == seller_sku for record in candidates)
    has_parent = any(record.parent_sku == seller_sku for record in candidates)
    if has_sku and has_parent:
        return "Both"
    return "SKU" if has_sku else "Parent SKU"


def _lookup_outcome(
    candidates: tuple[ProductPriceMasterRecord, ...],
    seller_sku: str,
    product_name: str,
    variation: str,
) -> tuple[str, str]:
    if not seller_sku:
        return LOOKUP_STATUS_NOT_FOUND, "Invoice Seller SKU is blank."
    if not candidates:
        return (
            LOOKUP_STATUS_NOT_FOUND,
            "No master candidate exact-matches Invoice Seller SKU in Master SKU or Master Parent SKU.",
        )
    if _has_one_price(candidates):
        return (
            LOOKUP_STATUS_MATCHED,
            f"{len(candidates)} exact candidate row(s) share one unique price.",
        )
    narrowed = _match_name_variation(candidates, product_name, variation)
    if narrowed and _has_one_price(narrowed):
        return (
            LOOKUP_STATUS_MATCHED,
            "Multiple candidate prices were uniquely resolved by exact normalized Product Name / Variation matching.",
        )
    if narrowed:
        return (
            LOOKUP_STATUS_CONFLICT,
            "Multiple master prices remain after Product Name / Variation matching.",
        )
    if _match_text(product_name) or _match_text(variation):
        return (
            LOOKUP_STATUS_CONFLICT,
            "Product Name / Variation did not uniquely identify one master price.",
        )
    return (
        LOOKUP_STATUS_CONFLICT,
        "Multiple master prices require Product Name or Variation disambiguation.",
    )


def _identity_mismatch_reasons(
    candidates: tuple[ProductPriceMasterRecord, ...],
    product_name: str,
    variation: str,
) -> tuple[str, ...]:
    if not candidates:
        return ()
    reasons: list[str] = []
    product_key = _match_text(product_name)
    variation_key = _match_text(variation)
    if product_key and not any(_match_text(record.product_name) == product_key for record in candidates):
        reasons.append("Invoice Product Name does not exactly match any master candidate.")
    if variation_key and not any(_match_text(record.variation_name) == variation_key for record in candidates):
        reasons.append("Invoice Variation does not exactly match any master candidate.")
    return tuple(reasons)


def _match_name_variation(
    candidates: tuple[ProductPriceMasterRecord, ...],
    product_name: str,
    variation: str,
) -> tuple[ProductPriceMasterRecord, ...]:
    product_key = _match_text(product_name)
    variation_key = _match_text(variation)
    if not product_key and not variation_key:
        return ()
    return tuple(
        record
        for record in candidates
        if (not product_key or _match_text(record.product_name) == product_key)
        and (not variation_key or _match_text(record.variation_name) == variation_key)
    )


def _has_one_price(records: tuple[ProductPriceMasterRecord, ...]) -> bool:
    return len({record.unit_selling_price for record in records}) == 1


def _join(values: Iterable[Any]) -> str:
    unique_values = tuple(dict.fromkeys(str(value) for value in values if str(value)))
    return "; ".join(unique_values)


def _join_prices(records: Iterable[ProductPriceMasterRecord]) -> str:
    prices = sorted({record.unit_selling_price for record in records})
    return "; ".join(_format_price(price) for price in prices)


def _format_price(price: Decimal) -> str:
    return f"{price:.2f}"


def _top_skus(counts: Counter[str], limit: int) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _sku_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _display(value: Any) -> str:
    return normalize_whitespace(str(value or ""))


def _match_text(value: str) -> str:
    return normalize_whitespace(value).casefold()
