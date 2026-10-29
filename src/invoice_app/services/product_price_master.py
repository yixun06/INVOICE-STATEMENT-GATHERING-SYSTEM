from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import lru_cache
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping

from openpyxl import load_workbook

from src.invoice_app.utils.normalize import (
    normalize_match_text,
    normalize_sku_text,
    normalize_whitespace,
)


class PriceLookupStatus(str, Enum):
    MATCHED_BY_SKU = "matched_by_sku"
    MATCHED_BY_SKU_NAME_VARIATION = "matched_by_sku_name_variation"
    MATCHED_BY_PARENT_SKU = "matched_by_parent_sku"
    PRICE_CONFIRMED_IDENTITY_AMBIGUOUS = "price_confirmed_identity_ambiguous"
    MATCHED_BY_PARENT_SKU_NAME_VARIATION = "matched_by_parent_sku_name_variation"
    MATCHED = "matched"
    MATCHED_BY_ALIAS = "matched_by_alias"
    MATCHED_BY_NAME_VARIATION = "matched_by_name_variation"
    MATCHED_BY_FULL_ORDER_MATCH_ONLY = "matched_by_full_order_match_only"
    VERIFIED_ERP_IDENTITY_EQUIVALENCE = "verified_erp_identity_equivalence"
    PRICING_CONFLICT = "pricing_conflict"
    PRICE_NOT_FOUND = "price_not_found"


_NORMAL_MATCHED_STATUSES = frozenset(
    {
        PriceLookupStatus.MATCHED_BY_SKU,
        PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
        PriceLookupStatus.MATCHED_BY_PARENT_SKU,
        PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION,
        PriceLookupStatus.MATCHED,
        PriceLookupStatus.MATCHED_BY_ALIAS,
        PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
    }
)


@dataclass(frozen=True)
class ProductPriceMasterRecord:
    seller_sku: str
    parent_sku: str
    product_name: str
    variation_name: str
    unit_selling_price: Decimal
    source_row: int
    unit_price_usolf: Decimal | None = None
    nav_code: str | None = None
    product_id: str | None = None
    usoft_product_description: str | None = None
    uom: str | None = None
    remark: str | None = None
    resolve_sku: str | None = None


@dataclass(frozen=True)
class VerifiedErpIdentityEquivalence:
    """Explicit Full Order-only Product Master identity equivalence evidence."""

    product_id: str
    sku: str
    product_name: str
    allowed_variations: tuple[str, ...]
    nav_code: str
    usoft_product_description: str
    uom: str
    unit_selling_price: Decimal


@dataclass(frozen=True)
class ProductPriceMasterMetadata:
    source_filename: str
    sheet_name: str
    header_row: int | None
    workbook_row_count: int
    candidate_row_count: int
    loaded_row_count: int
    invalid_price_row_count: int


@dataclass(frozen=True)
class ProductPriceLookupResult:
    status: PriceLookupStatus
    unit_selling_price: Decimal | None
    matched_by: str | None
    matched_sku: str | None
    matched_parent_sku: str | None
    matched_product_name: str | None
    matched_variation_name: str | None
    source_metadata: ProductPriceMasterMetadata
    source_rows: tuple[int, ...]
    reason: str | None = None
    nav_code: str | None = None
    unit_price_usolf: Decimal | None = None

    alias_rule: str | None = None
    resolve_sku: str | None = None
    resolve_sku_reason: str | None = None

_REQUIRED_HEADERS = {
    "product_name": "product name",
    "variation_name": "variation name",
    "parent_sku": "parent sku",
    "seller_sku": "sku",
    "unit_selling_price": "price",
}
_NAV_HEADER = "nav code"
_PRODUCT_ID_HEADER = "product id"
_USOFT_PRODUCT_DESCRIPTION_HEADER = "usoft product description"
_UNIT_PRICE_USOLF_HEADER = "unit price usolf"
_UNIT_PRICE_USOLF_HEADERS = (
    _UNIT_PRICE_USOLF_HEADER,
    "unit price based usolf",
)
_UOM_HEADER = "uom"
_REMARK_HEADER = "remark"
_RESOLVE_SKU_HEADER = "resolved sku"
_FULL_ORDER_MATCH_ONLY_REMARK = "full_order_match_only"
_STRICT_PRICE = re.compile(
    r"^(?:RM\s*)?(-?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?)$",
    re.IGNORECASE,
)


class ProductPriceMaster:
    """Read-only Shopee Product Listing lookup index."""

    def __init__(
        self,
        records: Iterable[ProductPriceMasterRecord],
        metadata: ProductPriceMasterMetadata,
        *,
        sku_parent_peer_keys: bool = False,
        allow_less_suffix_alias: bool = True,
    ) -> None:
        self.metadata = metadata
        self.records = tuple(records)
        self._normal_records = tuple(
            record for record in self.records if not _is_full_order_match_only(record)
        )
        self._full_order_match_only_records = tuple(
            record for record in self.records if _is_full_order_match_only(record)
        )
        self._seller_sku_index = _build_index(self._normal_records, "seller_sku")
        self._parent_sku_index = _build_index(self._normal_records, "parent_sku")
        self._my_full_order_name_index: dict[str, tuple[ProductPriceMasterRecord, ...]] = {}
        self._my_full_order_resolve_index: dict[str, tuple[ProductPriceMasterRecord, ...]] = {}
        by_name: dict[str, list[ProductPriceMasterRecord]] = defaultdict(list)
        by_resolve: dict[str, list[ProductPriceMasterRecord]] = defaultdict(list)
        for record in self.records:
            by_name[_full_order_identity_text(record.product_name)].append(record)
            if record.resolve_sku:
                by_resolve[normalize_sku_text(record.resolve_sku)].append(record)
        self._my_full_order_name_index = {key: tuple(value) for key, value in by_name.items()}
        self._my_full_order_resolve_index = {key: tuple(value) for key, value in by_resolve.items()}
        self._sku_parent_peer_keys = sku_parent_peer_keys
        self._allow_less_suffix_alias = allow_less_suffix_alias

    @classmethod
    def from_xlsx(cls, source_path: str | Path) -> ProductPriceMaster:
        """Load the Product Listing on demand into an in-memory core-service index."""
        path = Path(source_path)
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            worksheet = workbook.active
            header_row, columns = _find_listing_headers(worksheet)
            records: list[ProductPriceMasterRecord] = []
            candidate_rows = 0
            invalid_prices = 0

            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=header_row + 1, values_only=True),
                start=header_row + 1,
            ):
                values = {
                    field: row[index] if index < len(row) else None
                    for field, index in columns.items()
                }
                seller_sku = normalize_sku_text(values["seller_sku"])
                parent_sku = normalize_sku_text(values["parent_sku"])
                if not seller_sku and not parent_sku:
                    continue

                candidate_rows += 1
                price = _strict_decimal(values["unit_selling_price"])
                if price is None:
                    invalid_prices += 1
                    continue

                records.append(
                    ProductPriceMasterRecord(
                        seller_sku=seller_sku,
                        parent_sku=parent_sku,
                        product_name=_display_text(values["product_name"]),
                        variation_name=_display_text(values["variation_name"]),
                        unit_selling_price=price,
                        source_row=row_number,
                        unit_price_usolf=_strict_decimal(
                            values.get("unit_price_usolf")
                        ),
                        nav_code=_optional_nav(values.get("nav_code")),
                        product_id=_display_text(values.get("product_id")) or None,
                        usoft_product_description=(
                            _display_text(values.get("usoft_product_description")) or None
                        ),
                        uom=_display_text(values.get("uom")) or None,
                        remark=_display_text(values.get("remark")) or None,
                        resolve_sku=_optional_resolve_sku(values.get("resolve_sku")),
                    )
                )

            metadata = ProductPriceMasterMetadata(
                source_filename=path.name,
                sheet_name=worksheet.title,
                header_row=header_row,
                workbook_row_count=worksheet.max_row,
                candidate_row_count=candidate_rows,
                loaded_row_count=len(records),
                invalid_price_row_count=invalid_prices,
            )
            return cls(records, metadata)
        finally:
            workbook.close()

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, Any]],
        *,
        source_filename: str = "<in-memory>",
    ) -> ProductPriceMaster:
        """Build a synthetic master without reading an XLSX."""
        records: list[ProductPriceMasterRecord] = []
        candidate_rows = 0
        invalid_prices = 0
        for source_row, row in enumerate(rows, start=1):
            seller_sku = normalize_sku_text(row.get("seller_sku"))
            parent_sku = normalize_sku_text(row.get("parent_sku"))
            if not seller_sku and not parent_sku:
                continue
            candidate_rows += 1
            price = _strict_decimal(row.get("unit_selling_price"))
            if price is None:
                invalid_prices += 1
                continue
            records.append(
                ProductPriceMasterRecord(
                    seller_sku=seller_sku,
                    parent_sku=parent_sku,
                    product_name=_display_text(row.get("product_name")),
                    variation_name=_display_text(row.get("variation_name")),
                    unit_selling_price=price,
                    source_row=source_row,
                    unit_price_usolf=_strict_decimal(row.get("unit_price_usolf")),
                    nav_code=_optional_nav(row.get("nav_code")),
                    product_id=_display_text(row.get("product_id")) or None,
                    usoft_product_description=(
                        _display_text(row.get("usoft_product_description")) or None
                    ),
                    uom=_display_text(row.get("uom")) or None,
                    remark=_display_text(row.get("remark")) or None,
                    resolve_sku=_optional_resolve_sku(row.get("resolve_sku")),
                )
            )
        return cls(
            records,
            ProductPriceMasterMetadata(
                source_filename=source_filename,
                sheet_name="<in-memory>",
                header_row=None,
                workbook_row_count=candidate_rows,
                candidate_row_count=candidate_rows,
                loaded_row_count=len(records),
                invalid_price_row_count=invalid_prices,
            ),
        )

    def lookup(
        self,
        *,
        seller_sku: str | None,
        product_name: str | None = None,
        variation_name: str | None = None,
    ) -> ProductPriceLookupResult:
        """Resolve one price only when it identifies one master product identity."""
        requested_seller_sku = normalize_sku_text(seller_sku)
        if _is_invalid_sku(requested_seller_sku):
            return self._resolve_name_variation(product_name, variation_name)

        if self._sku_parent_peer_keys:
            peer_candidates = self._sku_candidates(requested_seller_sku)
            if peer_candidates:
                return self._resolve_sku_candidates(
                    peer_candidates,
                    product_name=product_name,
                    variation_name=variation_name,
                )

        exact_seller_sku_candidates = self._seller_sku_index.get(requested_seller_sku, ())
        if exact_seller_sku_candidates:
            return self._resolve_sku_candidates(
                exact_seller_sku_candidates,
                product_name=product_name,
                variation_name=variation_name,
            )

        parent_sku_candidates = self._parent_sku_index.get(requested_seller_sku, ())
        if parent_sku_candidates:
            return self._resolve_sku_candidates(
                parent_sku_candidates,
                product_name=product_name,
                variation_name=variation_name,
            )

        if self._allow_less_suffix_alias and requested_seller_sku.endswith("-Less"):
            alias_candidates = self._sku_candidates(
                requested_seller_sku.removesuffix("-Less")
            )
            if alias_candidates:
                return self._resolve_sku_candidates(
                    alias_candidates,
                    product_name=product_name,
                    variation_name=variation_name,
                    alias_rule="REMOVE_SUFFIX_LESS",
                )
        return self._not_found(
            f"No Product Listing SKU or Parent SKU matches '{requested_seller_sku}'."
        )

    def lookup_full_order(
        self,
        *,
        seller_sku: str | None,
        parent_sku: str | None,
        product_name: str | None,
        variation_name: str | None,
        verified_erp_identities: tuple[VerifiedErpIdentityEquivalence, ...] = (),
        parent_family_variation_disambiguation: bool = False,
    ) -> ProductPriceLookupResult:
        """Resolve a Shopee Full Order row using its explicit compatibility rows last.

        ``FULL_ORDER_MATCH_ONLY`` records are intentionally absent from the normal
        SKU, Parent SKU, and name/variation indexes.  They are examined only after
        all normal Full Order resolution steps have returned not found.
        """
        if parent_family_variation_disambiguation:
            return self._lookup_my_full_order_identity_first(
                seller_sku=seller_sku, parent_sku=parent_sku,
                product_name=product_name, variation_name=variation_name,
            )
        earlier_ambiguous_result: ProductPriceLookupResult | None = None
        for source_sku in dict.fromkeys((seller_sku, parent_sku)):
            if not normalize_sku_text(source_sku):
                continue
            result = self.lookup(
                seller_sku=source_sku,
                product_name=product_name,
                variation_name=variation_name,
            )
            if result.status in _NORMAL_MATCHED_STATUSES:
                if len(result.source_rows) > 1:
                    selected = tuple(
                        r for r in self._normal_records if r.source_row in result.source_rows
                    )
                    consensus = self._full_order_canonical_consensus(selected)
                    if consensus is None:
                        return self._conflict(
                            selected, "Full Order Product Master candidates disagree or lack "
                            "complete NAV, canonical SKU, UNIT PRICE USOLF, or resolve_sku evidence."
                        )
                    result = consensus
                if parent_family_variation_disambiguation:
                    family_result = self._resolve_parent_family_variation(
                        result=result,
                        parent_sku=parent_sku,
                        variation_name=variation_name,
                    )
                    if family_result is not None:
                        family = self._parent_sku_index.get(
                            normalize_sku_text(parent_sku), ()
                        )
                        if self._full_order_canonical_consensus(family) is None:
                            return family_result
                return result
            # This is Full Order only: retain the ordinary lookup's SKU/parent
            # precedence, and collapse complete identical outputs, not rows.
            key = normalize_sku_text(source_sku)
            candidates = (
                self._sku_candidates(key) if self._sku_parent_peer_keys else
                self._seller_sku_index.get(key, ()) or self._parent_sku_index.get(key, ())
            )
            if len(candidates) > 1:
                narrowed = _match_name_variation(candidates, product_name, variation_name)
                if narrowed:
                    candidates = narrowed
                elif _match_text(variation_name):
                    candidates = _match_variation(candidates, variation_name)
                if len(candidates) > 1:
                    consensus = self._full_order_canonical_consensus(candidates)
                    if consensus is not None:
                        return consensus
                    if not narrowed and result.status is PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS:
                        return result
                    return self._conflict(
                        candidates, "Full Order Product Master candidates disagree or lack "
                        "complete NAV, canonical SKU, UNIT PRICE USOLF, or resolve_sku evidence."
                    )
            if (
                result.status
                is PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS
                and earlier_ambiguous_result is None
            ):
                earlier_ambiguous_result = result

        normal_name_result = self._resolve_name_variation(product_name, variation_name)
        if normal_name_result.status in _NORMAL_MATCHED_STATUSES:
            return normal_name_result
        if normal_name_result.status is PriceLookupStatus.PRICE_NOT_FOUND:
            compatibility_candidates = _exact_name_variation(
                self._full_order_match_only_records,
                product_name,
                variation_name,
            )
            if compatibility_candidates:
                if _has_one_company_identity(compatibility_candidates):
                    return self._matched_identity(
                        compatibility_candidates,
                        status=PriceLookupStatus.MATCHED_BY_FULL_ORDER_MATCH_ONLY,
                    )
                return self._conflict(
                    compatibility_candidates,
                    "FULL_ORDER_MATCH_ONLY Product Name / Variation matches multiple "
                    "company product identities.",
                )

        equivalence_result = self._resolve_verified_erp_identity_equivalence(
            seller_sku=seller_sku,
            parent_sku=parent_sku,
            product_name=product_name,
            variation_name=variation_name,
            verified_erp_identities=verified_erp_identities,
        )
        if equivalence_result is not None:
            return equivalence_result
        if normal_name_result.status is not PriceLookupStatus.PRICE_NOT_FOUND:
            return earlier_ambiguous_result or normal_name_result
        return earlier_ambiguous_result or self._not_found(
            "No normal Product Listing or FULL_ORDER_MATCH_ONLY identity exactly "
            "matches Product Name / Variation."
        )

    def _lookup_my_full_order_identity_first(
        self, *, seller_sku: str | None, parent_sku: str | None,
        product_name: str | None, variation_name: str | None,
    ) -> ProductPriceLookupResult:
        """Resolve structured MY source identity by exact evidence, never by price."""
        name = _full_order_identity_text(product_name)
        variation = _full_order_identity_text(variation_name)
        sku = normalize_sku_text(seller_sku)
        parent = normalize_sku_text(parent_sku)
        sku = "" if _is_invalid_sku(sku) else sku
        parent = "" if _is_invalid_sku(parent) else parent
        names = self._my_full_order_name_index.get(name, ()) if name else ()
        name_sku = tuple(r for r in names if sku and normalize_sku_text(r.seller_sku) == sku)
        name_variation = tuple(r for r in names if variation and _full_order_identity_text(r.variation_name) == variation)
        levels = [
            ("NAME_VARIATION_SKU", tuple(r for r in name_sku if variation and _full_order_identity_text(r.variation_name) == variation)),
            ("NAME_SKU", name_sku if not variation or not name_variation else ()),
            ("NAME_VARIATION", name_variation),
            ("NAME", names if not variation else ()),
        ]
        conflict = None
        for stage, candidates in levels:
            if not candidates:
                continue
            resolved = self._resolve_my_full_order_candidates(candidates, stage)
            if resolved.status is PriceLookupStatus.MATCHED:
                return resolved
            conflict = resolved

        # Seller SKU and Parent SKU are separate evidence pools. Existing exact
        # Name/Variation/SKU evidence narrows each pool without fuzzy matching.
        for stage, candidates in (
            ("SELLER_SKU", self._seller_sku_index.get(sku, ())),
            ("PARENT_SKU", self._parent_sku_index.get(parent, ())),
            ("RESOLVE_SKU", _unique_records(
                record for key in dict.fromkeys((sku, parent)) if key
                for record in self._my_full_order_resolve_index.get(key, ())
            )),
        ):
            if not candidates:
                continue
            for key, value in (("product_name", name), ("variation_name", variation)):
                exact = tuple(r for r in candidates if value and _full_order_identity_text(getattr(r, key)) == value)
                if exact:
                    candidates = exact
            if stage == "PARENT_SKU" and sku:
                exact = tuple(r for r in candidates if normalize_sku_text(r.seller_sku) == sku)
                if exact:
                    candidates = exact
            resolved = self._resolve_my_full_order_candidates(candidates, stage)
            if resolved.status is PriceLookupStatus.MATCHED:
                return resolved
            # Do not escape a conflicting stronger SKU family by looking at a
            # weaker key or operational POS mapping.
            return resolved
        return conflict or self._not_found("No exact Full Order Product Master identity was found.")

    def _resolve_my_full_order_candidates(
        self, candidates: tuple[ProductPriceMasterRecord, ...], stage: str,
    ) -> ProductPriceLookupResult:
        if len(candidates) == 1:
            return self._matched_identity(candidates, status=PriceLookupStatus.MATCHED, matched_by=stage)
        consensus = self._full_order_canonical_consensus(candidates)
        if consensus is not None:
            return consensus
        return self._conflict(
            candidates, f"{stage} candidates have conflicting or incomplete canonical SKU, NAV, UNIT PRICE USOLF, or resolve_sku evidence."
        )

    def _full_order_canonical_consensus(
        self, records: tuple[ProductPriceMasterRecord, ...]
    ) -> ProductPriceLookupResult | None:
        if len(records) < 2:
            return None
        outputs = {
            (normalize_sku_text(r.seller_sku) or normalize_sku_text(r.parent_sku),
             r.nav_code, r.unit_price_usolf, r.resolve_sku)
            for r in records
        }
        if len(outputs) != 1:
            return None
        sku, nav, usolf, resolve_sku = next(iter(outputs))
        if not sku or not nav or usolf is None or usolf <= 0:
            return None
        prices = {r.unit_selling_price for r in records}
        return ProductPriceLookupResult(
            status=PriceLookupStatus.MATCHED,
            unit_selling_price=next(iter(prices)) if len(prices) == 1 else None,
            matched_by="full_order_canonical_consensus",
            matched_sku=sku,
            matched_parent_sku=None,
            matched_product_name=None,
            matched_variation_name=None,
            source_metadata=self.metadata,
            source_rows=tuple(sorted(r.source_row for r in records)),
            nav_code=nav,
            unit_price_usolf=usolf,
            resolve_sku=resolve_sku,
        )

    def lookup_sg_full_order(
        self,
        *,
        seller_sku: str | None,
        product_name: str | None,
        variation_name: str | None,
    ) -> ProductPriceLookupResult:
        """Resolve one SG Listing row from exact name/variation evidence.

        The SG Full Order contract makes normalized Product Name plus Variation
        the primary identity.  A source Seller SKU can only reduce an already
        exact name/variation candidate set; it cannot create a match or select
        a duplicate Listing row.  This path deliberately excludes Parent SKU,
        price, aliases, and first-row selection.  It includes
        ``FULL_ORDER_MATCH_ONLY`` rows because those are explicit SG Full Order
        Listing identities, not a fallback to another market or a heuristic.
        """

        if not _match_text(product_name):
            return self._not_found(
                "Shopee SG Product Name is unavailable for exact Product Name / Variation matching."
            )
        candidates = _exact_name_variation(
            self.records,
            product_name,
            variation_name,
        )
        if len(candidates) == 1:
            return self._matched_identity(
                candidates,
                status=PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
                matched_by="sg_exact_name_variation",
            )
        if not candidates:
            return self._not_found(
                "No Shopee SG Product Listing row exactly matches Product Name / Variation."
            )

        source_sku = normalize_sku_text(seller_sku)
        sku_candidates = tuple(
            candidate
            for candidate in candidates
            if source_sku and normalize_sku_text(candidate.seller_sku) == source_sku
        )
        if len(sku_candidates) == 1:
            return self._matched_identity(
                sku_candidates,
                status=PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
                matched_by="sg_exact_name_variation_sku_disambiguation",
            )
        if len(sku_candidates) > 1:
            return self._conflict(
                sku_candidates,
                "Exact Shopee SG Product Name / Variation and Seller SKU still match multiple Listing rows.",
            )
        return self._conflict(
            candidates,
            "Exact Shopee SG Product Name / Variation matches multiple Listing rows; "
            "Seller SKU does not deterministically disambiguate one row.",
        )

    def _resolve_parent_family_variation(
        self,
        *,
        result: ProductPriceLookupResult,
        parent_sku: str | None,
        variation_name: str | None,
    ) -> ProductPriceLookupResult | None:
        """Correct a resolved Seller-SKU result only when variation contradicts it.

        Some historical MY Full Order exports carry a Parent SKU which is also a
        child Seller SKU.  The ordinary Seller-SKU-first path would otherwise
        return that child before seeing a contradictory exact source Variation.
        This narrow opt-in branch considers the Parent SKU family only in that
        contradiction, when the resolved record belongs to that same family.
        """
        parent_key = normalize_sku_text(parent_sku)
        family = self._parent_sku_index.get(parent_key, ())
        variation_key = _match_text(variation_name)
        if (
            not parent_key
            or not variation_key
            or variation_key == _match_text(result.matched_variation_name)
            or normalize_sku_text(result.matched_parent_sku) != parent_key
            or _has_one_identity(family)
        ):
            return None
        narrowed = _match_variation(family, variation_name)
        if narrowed and _has_one_identity(narrowed):
            return self._matched_identity(
                narrowed,
                status=PriceLookupStatus.MATCHED,
                matched_by="parent_family_unique_exact_variation",
            )
        if narrowed:
            return self._conflict(
                narrowed,
                "Exact Full Order Variation matches multiple Parent SKU family "
                "identities.",
            )
        return self._conflict(
            family,
            "Full Order Variation does not exactly identify one Parent SKU family "
            "identity.",
        )

    def _resolve_verified_erp_identity_equivalence(
        self,
        *,
        seller_sku: str | None,
        parent_sku: str | None,
        product_name: str | None,
        variation_name: str | None,
        verified_erp_identities: tuple[VerifiedErpIdentityEquivalence, ...],
    ) -> ProductPriceLookupResult | None:
        if not verified_erp_identities:
            return None
        candidates = _unique_records(
            candidate
            for source_sku in dict.fromkeys((seller_sku, parent_sku))
            if normalize_sku_text(source_sku)
            for candidate in self._sku_candidates(normalize_sku_text(source_sku))
        )
        equivalences = tuple(
            equivalence
            for equivalence in verified_erp_identities
            if _matches_verified_erp_identity_equivalence(
                candidates,
                equivalence,
                product_name=product_name,
                variation_name=variation_name,
            )
        )
        if len(equivalences) != 1:
            return None
        equivalence = equivalences[0]
        return ProductPriceLookupResult(
            status=PriceLookupStatus.VERIFIED_ERP_IDENTITY_EQUIVALENCE,
            unit_selling_price=equivalence.unit_selling_price,
            matched_by=PriceLookupStatus.VERIFIED_ERP_IDENTITY_EQUIVALENCE.value,
            matched_sku=None,
            matched_parent_sku=equivalence.sku,
            matched_product_name=None,
            matched_variation_name=None,
            source_metadata=self.metadata,
            source_rows=tuple(sorted(record.source_row for record in candidates)),
            nav_code=equivalence.nav_code,
            unit_price_usolf=_resolved_usolf_price(candidates),
        )

    def _sku_candidates(
        self,
        requested_sku: str,
    ) -> tuple[ProductPriceMasterRecord, ...]:
        return _unique_records(
            self._seller_sku_index.get(requested_sku, ())
            + self._parent_sku_index.get(requested_sku, ())
        )

    def _resolve_sku_candidates(
        self,
        candidates: tuple[ProductPriceMasterRecord, ...],
        *,
        product_name: str | None,
        variation_name: str | None,
        alias_rule: str | None = None,
    ) -> ProductPriceLookupResult:
        if _has_one_identity(candidates):
            return self._matched_identity(
                candidates,
                status=(
                    PriceLookupStatus.MATCHED_BY_ALIAS
                    if alias_rule
                    else PriceLookupStatus.MATCHED
                ),
                alias_rule=alias_rule,
            )

        narrowed = _match_name_variation(candidates, product_name, variation_name)
        if narrowed and _has_one_identity(narrowed):
            return self._matched_identity(
                narrowed,
                status=(
                    PriceLookupStatus.MATCHED_BY_ALIAS
                    if alias_rule
                    else PriceLookupStatus.MATCHED
                ),
                alias_rule=alias_rule,
            )
        variation_narrowed = _match_variation(candidates, variation_name)
        if variation_narrowed and _has_one_identity(variation_narrowed):
            return self._matched_identity(
                variation_narrowed,
                status=(
                    PriceLookupStatus.MATCHED_BY_ALIAS
                    if alias_rule
                    else PriceLookupStatus.MATCHED
                ),
                alias_rule=alias_rule,
                matched_by="sku_unique_exact_variation",
            )

        if _has_one_price(candidates):
            return self._price_confirmed_identity_ambiguous(
                candidates,
                alias_rule=alias_rule,
            )


        if narrowed:
            evidence = narrowed
            reason = (
                "Multiple master identities remain after exact Product Name / "
                "Variation matching."
            )
        elif _has_disambiguation_input(product_name, variation_name):
            evidence = candidates
            reason = (
                "Product Name / Variation did not identify one candidate master identity."
            )
        else:
            evidence = candidates
            reason = (
                "Multiple master identities require exact Product Name / Variation "
                "disambiguation."
            )
        return self._conflict(evidence, reason)

    def _resolve_name_variation(
        self,
        product_name: str | None,
        variation_name: str | None,
    ) -> ProductPriceLookupResult:
        if not _match_text(product_name):
            return self._not_found(
                "Seller SKU is blank, N/A, or exp and Product Name is unavailable."
            )
        candidates = _match_name_variation(
            self._normal_records, product_name, variation_name
        )
        if not candidates:
            return self._not_found(
                "No Product Listing identity exactly matches Product Name / Variation."
            )
        if _has_one_identity(candidates):
            return self._matched_identity(
                candidates,
                status=PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
            )
        return self._conflict(
            candidates,
            "Product Name / Variation matches multiple master identities.",
        )

    def _matched_identity(
        self,
        records: tuple[ProductPriceMasterRecord, ...],
        *,
        status: PriceLookupStatus,
        alias_rule: str | None = None,
        matched_by: str | None = None,
    ) -> ProductPriceLookupResult:
        record = min(records, key=lambda item: item.source_row)
        resolve_sku, resolve_sku_reason = _resolved_resolve_sku(records)
        return ProductPriceLookupResult(
            status=status,
            unit_selling_price=record.unit_selling_price,
            matched_by=matched_by or status.value,
            matched_sku=record.seller_sku or None,
            matched_parent_sku=record.parent_sku or None,
            matched_product_name=record.product_name or None,
            matched_variation_name=record.variation_name or None,
            source_metadata=self.metadata,
            source_rows=tuple(sorted(item.source_row for item in records)),
            alias_rule=alias_rule,
            nav_code=record.nav_code,
            unit_price_usolf=_resolved_usolf_price(records),
            resolve_sku=resolve_sku,
            resolve_sku_reason=resolve_sku_reason,
        )


    def _price_confirmed_identity_ambiguous(
        self,
        records: tuple[ProductPriceMasterRecord, ...],
        *,
        alias_rule: str | None = None,
    ) -> ProductPriceLookupResult:
        price = records[0].unit_selling_price
        return ProductPriceLookupResult(
            status=PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS,
            unit_selling_price=price,
            matched_by="unique_candidate_price",
            matched_sku=None,
            matched_parent_sku=None,
            matched_product_name=None,
            matched_variation_name=None,
            source_metadata=self.metadata,
            source_rows=tuple(sorted(item.source_row for item in records)),
            alias_rule=alias_rule,
            reason=(
                "Candidate identities remain unresolved, but all exact SKU / Parent SKU "
                "candidates share one unique Unit Selling Price."
            ),
            nav_code=None,
        )

    def _matched(
        self,
        records: tuple[ProductPriceMasterRecord, ...],
        match_scope: str,
        *,
        disambiguated: bool,
    ) -> ProductPriceLookupResult:
        record = min(records, key=lambda item: item.source_row)
        status = _matched_status(match_scope, disambiguated)
        resolve_sku, resolve_sku_reason = _resolved_resolve_sku(records)
        return ProductPriceLookupResult(
            status=status,
            unit_selling_price=record.unit_selling_price,
            matched_by=status.value,
            matched_sku=record.seller_sku or None,
            matched_parent_sku=record.parent_sku or None,
            matched_product_name=record.product_name or None,
            matched_variation_name=record.variation_name or None,
            source_metadata=self.metadata,
            source_rows=tuple(sorted(item.source_row for item in records)),
            nav_code=record.nav_code,
            unit_price_usolf=_resolved_usolf_price(records),
            resolve_sku=resolve_sku,
            resolve_sku_reason=resolve_sku_reason,
        )

    def _conflict(
        self,
        records: tuple[ProductPriceMasterRecord, ...],
        reason: str,
    ) -> ProductPriceLookupResult:
        return ProductPriceLookupResult(
            status=PriceLookupStatus.PRICING_CONFLICT,
            unit_selling_price=None,
            matched_by=None,
            matched_sku=None,
            matched_parent_sku=None,
            matched_product_name=None,
            matched_variation_name=None,
            source_metadata=self.metadata,
            source_rows=tuple(sorted(item.source_row for item in records)),
            reason=reason,
        )

    def _not_found(self, reason: str) -> ProductPriceLookupResult:
        return ProductPriceLookupResult(
            status=PriceLookupStatus.PRICE_NOT_FOUND,
            unit_selling_price=None,
            matched_by=None,
            matched_sku=None,
            matched_parent_sku=None,
            matched_product_name=None,
            matched_variation_name=None,
            source_metadata=self.metadata,
            source_rows=(),
            reason=reason,
        )


def load_shopee_product_price_master(source_path: str | Path) -> ProductPriceMaster:
    """Cache the source by path, so normal callers do not reread it per PDF."""
    return _load_cached_price_master(str(Path(source_path).resolve()))


@lru_cache(maxsize=4)
def _load_cached_price_master(resolved_path: str) -> ProductPriceMaster:
    return ProductPriceMaster.from_xlsx(resolved_path)


def _build_index(
    records: Iterable[ProductPriceMasterRecord],
    attribute: str,
) -> dict[str, tuple[ProductPriceMasterRecord, ...]]:
    index: dict[str, list[ProductPriceMasterRecord]] = defaultdict(list)
    for record in records:
        value = getattr(record, attribute)
        if value:
            index[value].append(record)
    return {key: tuple(value) for key, value in index.items()}


def _find_listing_headers(worksheet: Any) -> tuple[int, dict[str, int]]:
    for row_number, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
        headers = {
            _header_text(value): index
            for index, value in enumerate(row)
            if _header_text(value)
        }
        if all(expected in headers for expected in _REQUIRED_HEADERS.values()):
            columns = {
                field: headers[expected] for field, expected in _REQUIRED_HEADERS.items()
            }
            if _NAV_HEADER in headers:
                columns["nav_code"] = headers[_NAV_HEADER]
            if _PRODUCT_ID_HEADER in headers:
                columns["product_id"] = headers[_PRODUCT_ID_HEADER]
            if _USOFT_PRODUCT_DESCRIPTION_HEADER in headers:
                columns["usoft_product_description"] = headers[
                    _USOFT_PRODUCT_DESCRIPTION_HEADER
                ]
            usolf_header = next(
                (header for header in _UNIT_PRICE_USOLF_HEADERS if header in headers),
                None,
            )
            if usolf_header:
                columns["unit_price_usolf"] = headers[usolf_header]
            if _UOM_HEADER in headers:
                columns["uom"] = headers[_UOM_HEADER]
            if _REMARK_HEADER in headers:
                columns["remark"] = headers[_REMARK_HEADER]
            if _RESOLVE_SKU_HEADER in headers:
                columns["resolve_sku"] = headers[_RESOLVE_SKU_HEADER]
            return row_number, columns
        if row_number >= 50:
            break
    raise ValueError("Shopee Product Listing required headers were not found.")


def _header_text(value: Any) -> str:
    return normalize_whitespace(str(value or "")).casefold()


def _sku_text(value: Any) -> str:
    """Keep identifiers as text: no numeric parsing or case coercion."""
    return "" if value is None else str(value).strip()


def _display_text(value: Any) -> str:
    return normalize_whitespace(str(value or ""))


def _optional_nav(value: Any) -> str | None:
    value = _display_text(value)
    return value or None


def _optional_resolve_sku(value: Any) -> str | None:
    """Preserve the Listing POS instruction exactly when it is nonblank."""

    text = "" if value is None else str(value)
    return text if text.strip() else None


def _resolved_resolve_sku(
    records: tuple[ProductPriceMasterRecord, ...],
) -> tuple[str | None, str | None]:
    """Return a mapping only when all resolved Listing candidates agree."""

    values = {record.resolve_sku for record in records}
    if values == {None}:
        return None, None
    if len(values) == 1:
        return next(iter(values)), None
    return None, "Resolved SKU conflicts across matched Listing candidates."


def _strict_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    match = _STRICT_PRICE.fullmatch(str(value).strip())
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _has_one_price(records: tuple[ProductPriceMasterRecord, ...]) -> bool:
    return len({record.unit_selling_price for record in records}) == 1


def _resolved_usolf_price(
    records: tuple[ProductPriceMasterRecord, ...],
) -> Decimal | None:
    """Return the single positive USOLF price proved by resolved PM evidence."""

    prices = {record.unit_price_usolf for record in records}
    if len(prices) != 1:
        return None
    price = next(iter(prices))
    return price if price is not None and price > 0 else None


def _matches_verified_erp_identity_equivalence(
    candidates: tuple[ProductPriceMasterRecord, ...],
    equivalence: VerifiedErpIdentityEquivalence,
    *,
    product_name: str | None,
    variation_name: str | None,
) -> bool:
    if not candidates:
        return False
    approved_variations = {_match_text(value) for value in equivalence.allowed_variations}
    approved_sku = normalize_sku_text(equivalence.sku)
    return (
        _match_text(product_name) == _match_text(equivalence.product_name)
        and _match_text(variation_name) in approved_variations
        and all(
        _match_text(record.product_id) == _match_text(equivalence.product_id)
        and approved_sku in {
            normalize_sku_text(record.seller_sku),
            normalize_sku_text(record.parent_sku),
        }
        and _match_text(record.product_name) == _match_text(equivalence.product_name)
        and _match_text(record.variation_name) in approved_variations
        and _display_text(record.nav_code) == _display_text(equivalence.nav_code)
        and _match_text(record.usoft_product_description)
        == _match_text(equivalence.usoft_product_description)
        and _match_text(record.uom) == _match_text(equivalence.uom)
        and record.unit_selling_price == equivalence.unit_selling_price
        for record in candidates
        )
    )


def _has_disambiguation_input(
    product_name: str | None,
    variation_name: str | None,
) -> bool:
    return bool(_match_text(product_name) or _match_text(variation_name))


def _match_name_variation(
    records: tuple[ProductPriceMasterRecord, ...],
    product_name: str | None,
    variation_name: str | None,
) -> tuple[ProductPriceMasterRecord, ...]:
    product_key = _match_text(product_name)
    variation_key = _match_text(variation_name)
    if not product_key and not variation_key:
        return ()
    return tuple(
        record
        for record in records
        if (not product_key or _match_text(record.product_name) == product_key)
        and (not variation_key or _match_text(record.variation_name) == variation_key)
    )


def _match_variation(
    records: tuple[ProductPriceMasterRecord, ...],
    variation_name: str | None,
) -> tuple[ProductPriceMasterRecord, ...]:
    variation_key = _match_text(variation_name)
    if not variation_key:
        return ()
    return tuple(
        record for record in records if _match_text(record.variation_name) == variation_key
    )
def _match_text(value: str | None) -> str:
    return normalize_match_text(value)


def _full_order_identity_text(value: str | None) -> str:
    return normalize_whitespace(unicodedata.normalize("NFKC", value or "")).casefold()


def _is_invalid_sku(value: str) -> bool:
    return not value or value.casefold() in {"n/a", "exp"}


def _has_one_identity(records: tuple[ProductPriceMasterRecord, ...]) -> bool:
    return len({_identity_key(record) for record in records}) == 1


def _has_one_company_identity(records: tuple[ProductPriceMasterRecord, ...]) -> bool:
    return len({_company_identity_key(record) for record in records}) == 1


def _identity_key(record: ProductPriceMasterRecord) -> tuple[str, str, str, str, Decimal]:
    return (
        normalize_sku_text(record.seller_sku),
        normalize_sku_text(record.parent_sku),
        _match_text(record.product_name),
        _match_text(record.variation_name),
        record.unit_selling_price,
    )


def _company_identity_key(
    record: ProductPriceMasterRecord,
) -> tuple[str, str, str, Decimal, str]:
    return (
        normalize_sku_text(record.seller_sku),
        normalize_sku_text(record.parent_sku),
        _display_text(record.nav_code),
        record.unit_selling_price,
        _match_text(record.usoft_product_description),
    )


def _is_full_order_match_only(record: ProductPriceMasterRecord) -> bool:
    return _match_text(record.remark) == _FULL_ORDER_MATCH_ONLY_REMARK


def _exact_name_variation(
    records: tuple[ProductPriceMasterRecord, ...],
    product_name: str | None,
    variation_name: str | None,
) -> tuple[ProductPriceMasterRecord, ...]:
    product_key = _match_text(product_name)
    if not product_key:
        return ()
    variation_key = _match_text(variation_name)
    return tuple(
        record
        for record in records
        if _match_text(record.product_name) == product_key
        and _match_text(record.variation_name) == variation_key
    )



def _unique_records(records: Iterable[ProductPriceMasterRecord]) -> tuple[ProductPriceMasterRecord, ...]:
    return tuple(dict.fromkeys(records))


def _matched_status(match_scope: str, disambiguated: bool) -> PriceLookupStatus:
    if match_scope in {"seller_sku", "seller_sku_or_parent_sku"}:
        return (
            PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION
            if disambiguated
            else PriceLookupStatus.MATCHED_BY_SKU
        )
    return (
        PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION
        if disambiguated
        else PriceLookupStatus.MATCHED_BY_PARENT_SKU
    )
