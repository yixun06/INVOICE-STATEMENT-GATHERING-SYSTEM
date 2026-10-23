"""Platform-neutral parser for Listing ``Resolved SKU`` POS instructions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from src.invoice_app.services.weekly_billing_barcode_export import is_valid_ean13


MAX_UNITS_PER_LISTING_ITEM = 999
_EXPLICIT_NAV = re.compile(r"^NAV\s*:\s*(.+?)(?:-(\d+))?$", re.IGNORECASE)
_EAN_WITH_UNITS = re.compile(r"^(\d{13})-(\d+)$")
_EAN_WITH_TEXT_SUFFIX = re.compile(r"^(\d{13})-(.+)$")


class ResolveSkuIdentifierType(str, Enum):
    EAN13 = "EAN13"
    NAV = "NAV"
    MANUAL_CODE = "MANUAL_CODE"


class ResolveSkuParseStatus(str, Enum):
    READY = "READY"
    MANUAL_ENTRY_REQUIRED = "MANUAL_ENTRY_REQUIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class ResolveSkuComponent:
    """One Listing-prescribed physical POS component, in Listing order."""

    original_text: str
    identifier: str
    identifier_type: ResolveSkuIdentifierType
    units_per_listing_item: int | None
    barcode: str | None
    status: ResolveSkuParseStatus
    warning: str | None = None

    def scan_quantity(self, source_quantity: int) -> int | None:
        if source_quantity <= 0 or self.units_per_listing_item is None:
            return None
        return source_quantity * self.units_per_listing_item


def parse_resolve_sku(value: str | None) -> tuple[ResolveSkuComponent, ...]:
    """Parse one raw Listing value without changing its financial meaning.

    Only an explicit ``NAV:`` entry or a checksum-valid EAN-13 may carry a
    numeric ``-quantity`` suffix.  This prevents product text or an embedded
    barcode from being misread as a bundle multiplier.
    """

    if value is None or not value.strip():
        return ()
    components: list[ResolveSkuComponent] = []
    for raw_component in value.split(";"):
        original = raw_component.strip()
        if not original:
            components.append(
                ResolveSkuComponent(
                    original_text="",
                    identifier="",
                    identifier_type=ResolveSkuIdentifierType.MANUAL_CODE,
                    units_per_listing_item=None,
                    barcode=None,
                    status=ResolveSkuParseStatus.REVIEW_REQUIRED,
                    warning="Empty component between semicolon separators.",
                )
            )
            continue
        components.append(_parse_component(original))
    return tuple(components)


def _parse_component(original: str) -> ResolveSkuComponent:
    nav_match = _EXPLICIT_NAV.fullmatch(original)
    if nav_match:
        identifier = nav_match.group(1).strip()
        units = _validated_units(nav_match.group(2))
        if not identifier or units is None:
            return _review(original, "Explicit NAV entry has an invalid identifier or quantity.")
        return ResolveSkuComponent(
            original_text=original,
            identifier=identifier,
            identifier_type=ResolveSkuIdentifierType.NAV,
            units_per_listing_item=units,
            barcode=None,
            status=ResolveSkuParseStatus.MANUAL_ENTRY_REQUIRED,
        )

    ean_quantity_match = _EAN_WITH_UNITS.fullmatch(original)
    if ean_quantity_match:
        barcode, raw_units = ean_quantity_match.groups()
        units = _validated_units(raw_units)
        if is_valid_ean13(barcode) and units is not None:
            return ResolveSkuComponent(
                original_text=original,
                identifier=barcode,
                identifier_type=ResolveSkuIdentifierType.EAN13,
                units_per_listing_item=units,
                barcode=barcode,
                status=ResolveSkuParseStatus.READY,
            )
        if is_valid_ean13(barcode):
            return _review(original, "EAN-13 component has an unsupported unit multiplier.")

    if is_valid_ean13(original):
        return ResolveSkuComponent(
            original_text=original,
            identifier=original,
            identifier_type=ResolveSkuIdentifierType.EAN13,
            units_per_listing_item=1,
            barcode=original,
            status=ResolveSkuParseStatus.READY,
        )

    text_suffix_match = _EAN_WITH_TEXT_SUFFIX.fullmatch(original)
    if text_suffix_match and is_valid_ean13(text_suffix_match.group(1)):
        barcode = text_suffix_match.group(1)
        return ResolveSkuComponent(
            original_text=original,
            identifier=barcode,
            identifier_type=ResolveSkuIdentifierType.EAN13,
            units_per_listing_item=1,
            barcode=barcode,
            status=ResolveSkuParseStatus.READY,
            warning="Non-numeric Listing suffix retained as source context.",
        )

    return ResolveSkuComponent(
        original_text=original,
        identifier=original,
        identifier_type=ResolveSkuIdentifierType.MANUAL_CODE,
        units_per_listing_item=1,
        barcode=None,
        status=ResolveSkuParseStatus.MANUAL_ENTRY_REQUIRED,
    )


def _validated_units(raw_units: str | None) -> int | None:
    if raw_units is None:
        return 1
    units = int(raw_units)
    return units if 0 < units <= MAX_UNITS_PER_LISTING_ITEM else None


def _review(original: str, warning: str) -> ResolveSkuComponent:
    return ResolveSkuComponent(
        original_text=original,
        identifier=original,
        identifier_type=ResolveSkuIdentifierType.MANUAL_CODE,
        units_per_listing_item=None,
        barcode=None,
        status=ResolveSkuParseStatus.REVIEW_REQUIRED,
        warning=warning,
    )
