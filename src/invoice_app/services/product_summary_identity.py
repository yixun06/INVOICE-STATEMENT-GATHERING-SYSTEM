"""Approved conservative Product Summary identity policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata


@dataclass(frozen=True)
class ProductSummaryIdentity:
    """Internal, auditable grouping projection; never persisted."""

    nav: str
    seller_sku: str | None
    variation_key: str
    historical_pm_unit_price: Decimal
    title_key: str
    identity_fallback: bool
    display_description: str


@dataclass(frozen=True)
class _ApprovedEquivalence:
    seller_sku: str
    variations: frozenset[str]
    identity_key: str
    display_description: str
    display_variation: str


_APPROVED_EQUIVALENCES = (
    _ApprovedEquivalence(
        seller_sku="9555208107347",
        variations=frozenset({"1KG", "Fresh Raw Honey 1kg"}),
        identity_key="approved:L-02-honey-1kg",
        display_description="Simply Natural Fresh Raw Honey Malaysia [Madu Asli Segar]",
        display_variation="1KG",
    ),
    _ApprovedEquivalence(
        seller_sku="9555208103158",
        variations=frozenset({"", "Sweet Potato Mee Sua"}),
        identity_key="approved:L-06-sweet-potato-mee-sua",
        display_description="Simply Natural Organic Handmade Sweet Potato Mee Sua 200g Malaysia",
        display_variation="Sweet Potato Mee Sua",
    ),
)


def resolve_product_summary_identity(
    *,
    nav: str,
    seller_sku: str | None,
    product_name: str,
    variation: str | None,
    historical_pm_unit_price: Decimal,
) -> ProductSummaryIdentity:
    """Apply only Product Owner-approved Product Summary equivalences.

    Product Name is a collision guard, not a semantic matching algorithm.  It
    can merge clearly technical source-layout noise, but any material title
    difference remains a distinct key unless an explicit equivalence below
    authorizes it.
    """

    clean_nav = _required_text(nav, "nav")
    clean_name = _required_text(product_name, "product_name")
    clean_sku = _optional_text(seller_sku)
    clean_variation = _optional_text(variation) or ""

    equivalence = _approved_equivalence(clean_sku, clean_variation)
    if equivalence is not None:
        return ProductSummaryIdentity(
            nav=clean_nav,
            seller_sku=clean_sku,
            variation_key=equivalence.identity_key,
            historical_pm_unit_price=historical_pm_unit_price,
            title_key=equivalence.identity_key,
            identity_fallback=False,
            display_description=_description_with_variation(
                equivalence.display_description,
                equivalence.display_variation,
            ),
        )

    normalized_title = normalize_technical_product_title(clean_name)
    return ProductSummaryIdentity(
        nav=clean_nav,
        seller_sku=clean_sku,
        variation_key=clean_variation,
        historical_pm_unit_price=historical_pm_unit_price,
        title_key=normalized_title.casefold(),
        identity_fallback=clean_sku is None,
        display_description=_description_with_variation(
            normalized_title,
            clean_variation,
        ),
    )


def product_summary_group_key(
    *,
    identity: ProductSummaryIdentity,
    resolved_sku: str,
) -> tuple[str, str, Decimal]:
    """Return the shared final Product Summary grouping key."""

    return (
        identity.nav,
        _required_text(resolved_sku, "resolved_sku"),
        identity.historical_pm_unit_price,
    )


def normalize_technical_product_title(value: str) -> str:
    """Normalize only approved source-layout noise for Product Summary display."""

    title = unicodedata.normalize("NFKC", value).strip()
    title = re.sub(r"^pre-order\s+", "", title, flags=re.IGNORECASE)
    # These are the reviewed PDF word-break artifacts, not fuzzy corrections.
    for broken, repaired in (
        ("F ree", "Free"),
        ("Ba sed", "Based"),
        ("Be st", "Best"),
    ):
        title = title.replace(broken, repaired)
    # A PDF line break between CJK characters is formatting, not a word boundary.
    title = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _approved_equivalence(
    seller_sku: str | None,
    variation: str,
) -> _ApprovedEquivalence | None:
    if seller_sku is None:
        return None
    return next(
        (
            policy
            for policy in _APPROVED_EQUIVALENCES
            if policy.seller_sku == seller_sku and variation in policy.variations
        ),
        None,
    )


def _required_text(value: str | None, field: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field} must not be blank.")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _description_with_variation(product_name: str, variation: str) -> str:
    """Keep the source Variation visible without changing grouping facts."""

    return f"{product_name} | {variation}" if variation else product_name
