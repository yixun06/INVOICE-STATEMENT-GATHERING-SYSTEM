"""Fail-closed matching from Shopee Statement SKU rows to invoice items."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import re
from typing import Iterable, Mapping, Protocol, Sequence
import unicodedata

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem
from src.invoice_app.parsers.shopee_weekly_statement_parser import SettlementIncomeRow
from src.invoice_app.utils.normalize import normalize_sku_text
from src.invoice_app.services.product_price_master import ProductPriceMaster


MONEY_TOLERANCE = Decimal("0.02")


class StatementItemMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class ProductFamilyCandidate:
    """One Product Master variant returned by an exact Product ID lookup."""

    product_id: str
    seller_sku: str = ""
    parent_sku: str = ""
    product_name: str = ""
    variation: str = ""


class ProductFamilyResolver(Protocol):
    """Product Master boundary; implementations must look up Product ID exactly."""

    def resolve(self, product_id: str) -> Sequence[ProductFamilyCandidate]: ...


@dataclass(frozen=True)
class MappingProductFamilyResolver:
    """Small adapter for an already-loaded exact Product ID family index."""

    families: Mapping[str, Sequence[ProductFamilyCandidate]]

    def resolve(self, product_id: str) -> Sequence[ProductFamilyCandidate]:
        return self.families.get(product_id, ())


def product_family_resolver_from_price_master(
    master: ProductPriceMaster,
) -> MappingProductFamilyResolver:
    """Project Product Master rows into the locked exact Product ID family boundary."""

    families: dict[str, list[ProductFamilyCandidate]] = defaultdict(list)
    for record in master.records:
        product_id = str(record.product_id or "").strip()
        if not product_id:
            continue
        families[product_id].append(
            ProductFamilyCandidate(
                product_id=product_id,
                seller_sku=record.seller_sku,
                parent_sku=record.parent_sku,
                product_name=record.product_name,
                variation=record.variation_name,
            )
        )
    return MappingProductFamilyResolver(
        {product_id: tuple(candidates) for product_id, candidates in families.items()}
    )


def business_match_method(method: str | None) -> str | None:
    """Return the locked business-readable wording for a match method."""

    return {
        "PRODUCT_MASTER_SKU": "Product Master SKU",
        "PRODUCT_MASTER_PARENT_SKU": "Product Master Parent SKU",
        "EXACT_PRODUCT_NAME": "Product Name",
        "LINE_SUBTOTAL_TIE_BREAK": "Product Name + Price",
        "VERIFIED_ARTIFACT_REPAIR": "Verified Name Repair",
    }.get(method or "")


@dataclass(frozen=True)
class VerifiedArtifactRepair:
    """An explicitly verified name repair for one original invoice PDF."""

    source_pdf: str
    raw_extracted_name: str
    repaired_name: str


@dataclass(frozen=True)
class StatementItemMatch:
    statement_source_row: int
    order_id: str
    product_id: str
    product_name: str
    status: StatementItemMatchStatus
    invoice_item_index: int | None
    match_method: str | None
    reason: str


@dataclass(frozen=True)
class StatementItemMatchBatch:
    matches: tuple[StatementItemMatch, ...]
    eligible_for_commit: bool


def normalize_statement_product_name(value: str | None) -> str:
    """Locked matching normalization without deleting meaningful inner spaces."""

    compatible = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", compatible).strip().casefold()


def match_statement_sku_rows(
    statement_rows: Iterable[SettlementIncomeRow],
    invoice_items: Iterable[CanonicalInvoiceItem],
    *,
    product_families: ProductFamilyResolver,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
) -> StatementItemMatchBatch:
    """Match all SKU rows and block the batch when any row is unresolved.

    This function only stages deterministic match decisions. It performs no writes.
    """

    items_by_order: dict[str, list[CanonicalInvoiceItem]] = defaultdict(list)
    for item in invoice_items:
        if item.platform == "Shopee":
            items_by_order[item.order_id].append(item)

    repairs = tuple(verified_artifact_repairs)
    results = [
        _match_one(
            row,
            items_by_order.get(row.order_id, ()),
            product_families=product_families,
            verified_artifact_repairs=repairs,
        )
        for row in statement_rows
        if row.view_by == "Sku"
    ]

    matches = tuple(results)
    return StatementItemMatchBatch(
        matches=matches,
        eligible_for_commit=bool(matches)
        and all(
            result.status is StatementItemMatchStatus.MATCHED
            for result in matches
        ),
    )


def _match_one(
    row: SettlementIncomeRow,
    order_items: Sequence[CanonicalInvoiceItem],
    *,
    product_families: ProductFamilyResolver,
    verified_artifact_repairs: Sequence[VerifiedArtifactRepair],
) -> StatementItemMatch:
    if not order_items:
        return _needs_review(row, "Exact Order ID has no committed Invoice_Items.")
    if not row.product_id:
        return _needs_review(row, "Statement Product ID is missing.")

    family = tuple(
        candidate
        for candidate in product_families.resolve(row.product_id)
        if candidate.product_id == row.product_id
    )
    if not family:
        return _needs_review(
            row, "Exact Product Master Product ID lookup returned no family candidates."
        )

    family_items = _filter_to_family(order_items, family)
    if not family_items:
        return _needs_review(
            row,
            "No Invoice_Item in the exact Order ID matches Product Master family identity.",
        )

    statement_name = normalize_statement_product_name(row.product_name)
    exact_name_items = tuple(
        item
        for item in family_items
        if statement_name
        and normalize_statement_product_name(item.product_name) == statement_name
    )
    if len(exact_name_items) == 1:
        return _matched(row, exact_name_items[0], "EXACT_PRODUCT_NAME")
    if not exact_name_items:
        repaired_items = _verified_repair_matches(
            row,
            family_items,
            verified_artifact_repairs,
        )
        if len(repaired_items) == 1:
            return _matched(row, repaired_items[0], "VERIFIED_ARTIFACT_REPAIR")
        return _needs_review(
            row,
            "Normalized exact Product Name did not leave exactly one family candidate.",
        )

    if any(_has_promotion(item) for item in exact_name_items):
        return _needs_review(
            row,
            "Promotion candidates remain ambiguous; amount tie-break is prohibited.",
        )

    statement_price = row.financial_components.get("Product Price")
    if statement_price is None:
        return _needs_review(
            row, "Ambiguous same-name candidates have no Statement Product Price."
        )
    amount_matches = tuple(
        item
        for item in exact_name_items
        if item.line_subtotal is not None
        and abs(statement_price - item.line_subtotal) <= MONEY_TOLERANCE
    )
    if len(amount_matches) == 1:
        return _matched(row, amount_matches[0], "LINE_SUBTOTAL_TIE_BREAK")
    return _needs_review(
        row,
        "Line subtotal within RM0.02 did not leave exactly one non-promotion candidate.",
    )


def _filter_to_family(
    items: Sequence[CanonicalInvoiceItem],
    family: Sequence[ProductFamilyCandidate],
) -> tuple[CanonicalInvoiceItem, ...]:
    exact_skus = {
        sku
        for candidate in family
        for sku in (
            normalize_sku_text(candidate.seller_sku),
            normalize_sku_text(candidate.parent_sku),
        )
        if sku
    }
    sku_matches = tuple(
        item
        for item in items
        if normalize_sku_text(item.seller_sku)
        and normalize_sku_text(item.seller_sku) in exact_skus
    )
    if sku_matches:
        return sku_matches

    return tuple(
        item
        for item in items
        if any(
            normalize_statement_product_name(item.product_name)
            and normalize_statement_product_name(item.product_name)
            == normalize_statement_product_name(candidate.product_name)
            and normalize_statement_product_name(item.variation)
            == normalize_statement_product_name(candidate.variation)
            for candidate in family
        )
    )


def _verified_repair_matches(
    row: SettlementIncomeRow,
    family_items: Sequence[CanonicalInvoiceItem],
    repairs: Sequence[VerifiedArtifactRepair],
) -> tuple[CanonicalInvoiceItem, ...]:
    statement_name = normalize_statement_product_name(row.product_name)
    matches: list[CanonicalInvoiceItem] = []
    for item in family_items:
        for repair in repairs:
            if (
                item.source_pdf
                and repair.source_pdf == item.source_pdf
                and repair.raw_extracted_name == (item.product_name or "")
                and normalize_statement_product_name(repair.repaired_name)
                == statement_name
            ):
                matches.append(item)
                break
    return tuple(matches)


def _has_promotion(item: CanonicalInvoiceItem) -> bool:
    return any(
        value is not None and value != ""
        for value in (
            item.promotion_group_id,
            item.promotion_label,
            item.source_group_total,
        )
    )


def _matched(
    row: SettlementIncomeRow,
    item: CanonicalInvoiceItem,
    method: str,
) -> StatementItemMatch:
    return StatementItemMatch(
        statement_source_row=row.source_row_number,
        order_id=row.order_id,
        product_id=row.product_id,
        product_name=row.product_name,
        status=StatementItemMatchStatus.MATCHED,
        invoice_item_index=item.item_index,
        match_method=method,
        reason="Deterministic match.",
    )


def _needs_review(row: SettlementIncomeRow, reason: str) -> StatementItemMatch:
    return StatementItemMatch(
        statement_source_row=row.source_row_number,
        order_id=row.order_id,
        product_id=row.product_id,
        product_name=row.product_name,
        status=StatementItemMatchStatus.NEEDS_REVIEW,
        invoice_item_index=None,
        match_method=None,
        reason=reason,
    )
