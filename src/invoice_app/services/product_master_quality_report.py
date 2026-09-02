"""Read-only diagnostics for Shopee Product Listing data quality.

This module deliberately does not participate in invoice parsing, price lookup,
pricing allocation, validation, or reporting aggregation. It makes the exact
Product Listing candidate pool visible so ecommerce can correct source master
data without the application guessing a price.
"""

from __future__ import annotations

import re

import csv
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.invoice_app.services.product_price_master import (
    PriceLookupStatus,
    ProductPriceMaster,
    ProductPriceMasterRecord,
)
from src.invoice_app.utils.normalize import (
    normalize_match_text,
    normalize_product_identity,
    normalize_sku_text,
    normalize_whitespace,
    prepare_product_identity_for_matching,
)


LOOKUP_STATUS_MATCHED = "MATCHED"
LOOKUP_STATUS_NOT_FOUND = "PRICE_NOT_FOUND"
LOOKUP_STATUS_PRICE_CONFIRMED_IDENTITY_AMBIGUOUS = "PRICE_CONFIRMED_IDENTITY_AMBIGUOUS"
LOOKUP_STATUS_CONFLICT = "PRICING_CONFLICT"
LOOKUP_STATUS_ALIAS = "MATCHED_BY_ALIAS"
LOOKUP_STATUS_NAME_VARIATION = "MATCHED_BY_NAME_VARIATION"
_MATCHED_STATUSES = frozenset(
    {LOOKUP_STATUS_MATCHED, LOOKUP_STATUS_ALIAS, LOOKUP_STATUS_NAME_VARIATION}
)
_CSV_COLUMNS = (
    "Platform",
    "Order ID",
    "Invoice Seller SKU",
    "Invoice Product Name",
    "Invoice Variation",
    "Matched Via",
    "Raw Invoice Product Name",
    "Raw Invoice Variation",
    "Removed Variation Token",
    "Cleanup Reason",
    "Master Source Row(s)",
    "Master SKU",
    "Alias Rule",
    "Source Text Flags",
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
    raw_invoice_product_name: str
    raw_invoice_variation: str
    removed_variation_token: str
    cleanup_reason: str
    master_source_rows: str
    master_sku: str
    master_parent_sku: str
    master_product_name: str
    master_variation: str
    alias_rule: str
    source_text_flags: str
    candidate_count: int
    unique_candidate_prices: str
    lookup_status: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "Platform": self.platform,
            "Order ID": self.order_id,
            "Raw Invoice Product Name": self.raw_invoice_product_name,
            "Raw Invoice Variation": self.raw_invoice_variation,
            "Removed Variation Token": self.removed_variation_token,
            "Cleanup Reason": self.cleanup_reason,
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
            "Alias Rule": self.alias_rule,
            "Source Text Flags": self.source_text_flags,
        }


@dataclass(frozen=True)
class ProductMasterQualityReportSummary:
    total_product_rows_checked: int
    matched_count: int
    price_not_found_count: int
    pricing_conflict_count: int
    matched_by_alias_count: int
    matched_by_name_variation_count: int
    top_conflict_skus: tuple[tuple[str, int], ...]
    price_confirmed_identity_ambiguous_count: int
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
        matched_count=sum(row.lookup_status in _MATCHED_STATUSES for row in rows),
        matched_by_alias_count=sum(row.lookup_status == LOOKUP_STATUS_ALIAS for row in rows),
        matched_by_name_variation_count=sum(row.lookup_status == LOOKUP_STATUS_NAME_VARIATION for row in rows),
        price_confirmed_identity_ambiguous_count=sum(
            row.lookup_status == LOOKUP_STATUS_PRICE_CONFIRMED_IDENTITY_AMBIGUOUS for row in rows
        ),
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
    seller_sku = normalize_sku_text(invoice_row.get("seller_sku"))
    raw_product_name = _raw(invoice_row.get("product_name"))
    raw_variation = _raw(
        invoice_row.get("variation_name")
        or invoice_row.get("variation")
        or invoice_row.get("reporting_variation_name")
    )
    identity = prepare_product_identity_for_matching(
        raw_product_name,
        raw_variation,
        coordinate_confirmed_amount=_coordinate_confirmed_group_total(invoice_row),
    )
    lookup = price_master.lookup(
        seller_sku=seller_sku,
        product_name=identity.product_name,
        variation_name=identity.variation,
    )
    direct_candidates = _exact_candidate_pool(price_master.records, seller_sku)
    alias_candidates = (
        _exact_candidate_pool(price_master.records, seller_sku.removesuffix("-Less"))
        if not direct_candidates and seller_sku.endswith("-Less")
        else ()
    )
    source_rows = set(lookup.source_rows)
    resolved_records = tuple(
        record for record in price_master.records if record.source_row in source_rows
    )
    evidence = direct_candidates or alias_candidates or resolved_records
    mismatch_reasons = _identity_mismatch_reasons(
        direct_candidates, identity.product_name, identity.variation
    )
    contamination = _source_text_flags(raw_product_name, raw_variation)
    reason_parts = [part for part in (lookup.reason, *mismatch_reasons) if part]
    if contamination:
        reason_parts.append("SOURCE_TEXT_CONTAMINATION")

    return ProductMasterQualityReportRow(
        platform=platform,
        order_id=order_id,
        invoice_seller_sku=seller_sku,
        invoice_product_name=identity.product_name,
        invoice_variation=identity.variation,
        raw_invoice_product_name=identity.raw_product_name,
        raw_invoice_variation=identity.raw_variation,
        removed_variation_token=identity.removed_variation_token,
        cleanup_reason=identity.cleanup_reason,
        matched_via=_lookup_matched_via(lookup.status, evidence, seller_sku),
        master_source_rows=_join(record.source_row for record in evidence),
        master_sku=_join(record.seller_sku for record in evidence),
        master_parent_sku=_join(record.parent_sku for record in evidence),
        master_product_name=_join(record.product_name for record in evidence),
        master_variation=_join(record.variation_name for record in evidence),
        candidate_count=len(evidence),
        unique_candidate_prices=_join_prices(evidence),
        lookup_status=_report_status(lookup.status),
        alias_rule=lookup.alias_rule or "",
        source_text_flags="; ".join(contamination),
        reason="; ".join(reason_parts),
    )


def _report_status(status: PriceLookupStatus) -> str:
    return {
        PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS: LOOKUP_STATUS_PRICE_CONFIRMED_IDENTITY_AMBIGUOUS,
        PriceLookupStatus.MATCHED: LOOKUP_STATUS_MATCHED,
        PriceLookupStatus.MATCHED_BY_ALIAS: LOOKUP_STATUS_ALIAS,
        PriceLookupStatus.MATCHED_BY_NAME_VARIATION: LOOKUP_STATUS_NAME_VARIATION,
        PriceLookupStatus.PRICE_NOT_FOUND: LOOKUP_STATUS_NOT_FOUND,
        PriceLookupStatus.PRICING_CONFLICT: LOOKUP_STATUS_CONFLICT,
    }.get(status, LOOKUP_STATUS_CONFLICT)


def _lookup_matched_via(
    status: PriceLookupStatus,
    candidates: tuple[ProductPriceMasterRecord, ...],
    seller_sku: str,
) -> str:
    if status == PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS:
        return "SKU_UNIQUE_PRICE"
    if status == PriceLookupStatus.MATCHED_BY_NAME_VARIATION:
        return "None"
    if status == PriceLookupStatus.MATCHED_BY_ALIAS:
        return "SKU"
    return _matched_via(candidates, seller_sku)


def _source_text_flags(*source_values: str) -> tuple[str, ...]:
    if any(re.search(r"(?:^|\s)(?:RM\s*)?\d{1,6}\.\d{2}(?=\s|$)", value) for value in source_values):
        return ("SOURCE_TEXT_CONTAMINATION",)
    return ()


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
    return normalize_sku_text(value)


def _display(value: Any) -> str:
    return normalize_whitespace(str(value or ""))


def _raw(value: Any) -> str:
    return "" if value is None else str(value)


def _coordinate_confirmed_group_total(invoice_row: Mapping[str, Any]) -> Decimal | None:
    value = invoice_row.get("source_group_total")
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "")
    if text.upper().startswith("RM"):
        text = text[2:].strip()
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", text):
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _match_text(value: str) -> str:
    return normalize_match_text(value)
