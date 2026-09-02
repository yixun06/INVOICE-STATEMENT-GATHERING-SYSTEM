from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


_VARIATION_MARKER = re.compile(
    r"^(?P<name>.*?)(?:\s+|^)variation\s*:\s*(?P<variation>.+)$",
    re.IGNORECASE,
)

_TRAILING_MONEY = re.compile(
    r"^(?P<value>.*?)(?:\s+)(?P<token>(?:RM\s*)?\d{1,6}(?:,\d{3})*\.\d{2})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProductIdentityMatchText:
    """Source-preserving product identity prepared for deterministic matching."""

    raw_product_name: str
    raw_variation: str
    product_name: str
    variation: str
    removed_variation_token: str = ""
    cleanup_reason: str = ""


def normalize_sku_text(value: Any) -> str:
    """Return a display-safe identifier without changing an existing string SKU."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f").rstrip("0").rstrip(".") if value.as_tuple().exponent < 0 else format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not value.is_integer():
            return format(value, "f").rstrip("0").rstrip(".")
        return format(value, ".0f")
    return str(value).strip()


def normalize_match_text(value: str | None) -> str:
    """Matching-only deterministic key; source text remains unchanged elsewhere."""
    compatibility_text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", normalize_whitespace(compatibility_text)).casefold()


def normalize_product_identity(
    product_name: str | None,
    variation_name: str | None,
) -> tuple[str, str]:
    """Keep product and Variation source fields independent when the marker is embedded."""
    name = normalize_whitespace(product_name or "")
    variation = normalize_whitespace(variation_name or "")
    embedded = _VARIATION_MARKER.fullmatch(name)
    if embedded:
        name = normalize_whitespace(embedded.group("name"))
        embedded_variation = normalize_whitespace(embedded.group("variation"))
        if not variation:
            variation = embedded_variation
    if variation:
        marker = re.fullmatch(r"variation\s*:\s*(.+)", variation, re.IGNORECASE)
        if marker:
            variation = normalize_whitespace(marker.group(1))
    return name, variation

def prepare_product_identity_for_matching(
    product_name: str | None,
    variation_name: str | None,
    *,
    coordinate_confirmed_amount: Decimal | None = None,
) -> ProductIdentityMatchText:
    """Keep raw source text while producing a deterministic lookup identity.

    A trailing money token is removed only when the caller supplies the
    coordinate-confirmed source-group amount and the token equals that amount.
    No arbitrary numeric suffix is removed.
    """
    raw_product_name = "" if product_name is None else str(product_name)
    raw_variation = "" if variation_name is None else str(variation_name)
    structured_name, structured_variation = normalize_product_identity(
        raw_product_name,
        raw_variation,
    )
    variation, removed_token = _remove_coordinate_confirmed_amount(
        structured_variation,
        coordinate_confirmed_amount,
    )
    return ProductIdentityMatchText(
        raw_product_name=raw_product_name,
        raw_variation=raw_variation,
        product_name=structured_name,
        variation=variation,
        removed_variation_token=removed_token,
        cleanup_reason=(
            "COORDINATE_CONFIRMED_AMOUNT_CONTAMINATION" if removed_token else ""
        ),
    )


def _remove_coordinate_confirmed_amount(
    variation: str,
    coordinate_confirmed_amount: Decimal | None,
) -> tuple[str, str]:
    if coordinate_confirmed_amount is None:
        return variation, ""
    match = _TRAILING_MONEY.fullmatch(variation)
    if not match:
        return variation, ""
    try:
        amount = Decimal(
            match.group("token").replace("RM", "").replace(",", "").strip()
        )
    except InvalidOperation:
        return variation, ""
    if amount != coordinate_confirmed_amount:
        return variation, ""

    cleaned = normalize_whitespace(match.group("value"))
    return (cleaned, match.group("token")) if cleaned else (variation, "")

def parse_decimal(value: str | float | int | Decimal | None) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    cleaned = str(value).replace(",", "").replace("RM", "").replace("S$", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if match:
            return Decimal(match.group(0))
        return Decimal("0")


def parse_quantity(value: str | int | float | None) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    cleaned = str(value).replace(",", "").strip()
    digits = re.search(r"\d+", cleaned)
    if not digits:
        return 0
    qty = int(digits.group(0))
    return qty if qty > 0 else 0
