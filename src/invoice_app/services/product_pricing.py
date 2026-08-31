from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Any, Iterable, Mapping

from .product_price_master import (
    PriceLookupStatus,
    ProductPriceLookupResult,
    ProductPriceMaster,

)
from src.invoice_app.utils.normalize import normalize_product_identity, normalize_sku_text


MONEY_QUANTUM = Decimal("0.01")
PRICING_ANOMALY_TOLERANCE = Decimal("0.02")
_MONEY_TEXT = re.compile(
    r"^(?:RM\s*)?(-?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?)$",
    re.IGNORECASE,
)
_MATCHED_LOOKUP_STATUSES = frozenset(
    {
        PriceLookupStatus.MATCHED_BY_SKU,
        PriceLookupStatus.MATCHED,
        PriceLookupStatus.MATCHED_BY_ALIAS,
        PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
        PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
        PriceLookupStatus.MATCHED_BY_PARENT_SKU,
        PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION,
    }
)


class ProductPricingStatus(str, Enum):
    NORMAL_PRICED = "normal_priced"
    PROMOTION_ALLOCATED = "promotion_allocated"
    PRICE_NOT_FOUND = "price_not_found"
    PRICING_CONFLICT = "pricing_conflict"
    SOURCE_VALUE_UNAVAILABLE = "source_value_unavailable"
    PROMOTION_EVIDENCE_INCOMPLETE = "promotion_evidence_incomplete"
    PROMOTION_UNSUPPORTED = "promotion_unsupported"
    PROMOTION_UNSUPPORTED_MIXED_PRICE = "promotion_unsupported_mixed_price"
    PRICING_ANOMALY = "pricing_anomaly"


@dataclass(frozen=True)
class ProductPricingResult:
    row_index: int
    unit_selling_price: Decimal | None
    normal_selling_value: Decimal | None
    actual_selling_value: Decimal | None
    discount_given: Decimal | None
    pricing_status: ProductPricingStatus
    price_lookup_status: PriceLookupStatus
    promotion_group_id: str | None
    promotion_label: str | None
    promotion_group_total: Decimal | None
    promotion_target_qty: int | None
    promotion_member_qty: int | None
    source_line_subtotal: Decimal | None
    allocation_method: str | None
    allocation_evidence: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True)
class _PricingCandidate:
    row_index: int
    row: Mapping[str, Any]
    lookup: ProductPriceLookupResult
    source_line_subtotal: Decimal | None
    promotion_group_id: str | None
    promotion_label: str | None
    promotion_group_total: Decimal | None
    promotion_target_qty: int | None
    promotion_member_qty: int | None
    promotion_metadata_status: str | None


def calculate_shopee_product_pricing(
    rows: Iterable[Mapping[str, Any]],
    price_master: ProductPriceMaster,
) -> tuple[ProductPricingResult, ...]:
    """Calculate derived product pricing without changing parsed source rows."""
    candidates = tuple(
        _candidate(row_index, row, price_master)
        for row_index, row in enumerate(rows)
    )
    results: dict[int, ProductPricingResult] = {}
    groups: dict[str, list[_PricingCandidate]] = {}

    for candidate in candidates:
        if candidate.promotion_group_id:
            groups.setdefault(candidate.promotion_group_id, []).append(candidate)
        elif _is_incomplete_promotion(candidate):
            results[candidate.row_index] = _promotion_incomplete(candidate)
        else:
            results[candidate.row_index] = _normal_pricing(candidate)

    for group_id, members in groups.items():
        for result in _promotion_group_pricing(group_id, members):
            results[result.row_index] = result

    return tuple(results[index] for index in range(len(candidates)))


def _candidate(
    row_index: int,
    row: Mapping[str, Any],
    price_master: ProductPriceMaster,
) -> _PricingCandidate:
    product_name, variation_name = normalize_product_identity(
        _text(row.get("product_name")),
        _text(row.get("variation_name")) or _text(row.get("variation")),
    )
    lookup = price_master.lookup(
        seller_sku=normalize_sku_text(row.get("seller_sku")),
        parent_sku=normalize_sku_text(row.get("parent_sku")),
        product_name=product_name,
        variation_name=variation_name,
    )
    return _PricingCandidate(
        row_index=row_index,
        row=row,
        lookup=lookup,
        source_line_subtotal=_decimal_value(row.get("source_line_subtotal")),
        promotion_group_id=_text(row.get("promotion_group_id")) or None,
        promotion_label=_text(row.get("promotion_label")) or None,
        promotion_group_total=(
            _decimal_value(row.get("source_group_total"))
            or _decimal_value(row.get("promotion_group_total"))
        ),
        promotion_target_qty=_positive_integer(row.get("promotion_target_qty")),
        promotion_member_qty=_positive_integer(row.get("promotion_member_qty")),
        promotion_metadata_status=_text(row.get("promotion_metadata_status")) or None,
    )


def _normal_pricing(candidate: _PricingCandidate) -> ProductPricingResult:
    if candidate.lookup.status not in _MATCHED_LOOKUP_STATUSES:
        return _lookup_unavailable(
            candidate,
            actual_selling_value=candidate.source_line_subtotal,
            allocation_method="source_line_subtotal" if candidate.source_line_subtotal is not None else None,
            allocation_evidence=("source_line_subtotal",) if candidate.source_line_subtotal is not None else (),
        )

    quantity = _positive_integer(candidate.row.get("quantity"))
    if quantity is None or candidate.source_line_subtotal is None:
        return _result(
            candidate,
            status=ProductPricingStatus.SOURCE_VALUE_UNAVAILABLE,
            unit_selling_price=candidate.lookup.unit_selling_price,
            reason="Quantity or source line subtotal is unavailable for normal pricing.",
        )

    normal_value = _money(candidate.lookup.unit_selling_price * quantity)
    actual_value = candidate.source_line_subtotal
    discount = _money(normal_value - actual_value)
    return _priced_result(
        candidate,
        normal_value=normal_value,
        actual_value=actual_value,
        discount=discount,
        status=ProductPricingStatus.NORMAL_PRICED,
        allocation_method="source_line_subtotal",
        evidence=("product_price_master", "source_line_subtotal"),
    )


def _promotion_group_pricing(
    group_id: str,
    members: list[_PricingCandidate],
) -> tuple[ProductPricingResult, ...]:
    evidence_reason = _promotion_evidence_reason(group_id, members)
    if evidence_reason:
        return tuple(_promotion_incomplete(member, evidence_reason) for member in members)

    lookup_failure = next(
        (member for member in members if member.lookup.status not in _MATCHED_LOOKUP_STATUSES),
        None,
    )
    if lookup_failure is not None:
        reason = "Promotion pricing is unavailable because every member must have a resolved Unit Selling Price before allocation."
        return tuple(
            _lookup_unavailable(member, group_reason=reason)
            if member.lookup.status not in _MATCHED_LOOKUP_STATUSES
            else _result(
                member,
                status=ProductPricingStatus.PROMOTION_UNSUPPORTED,
                unit_selling_price=member.lookup.unit_selling_price,
                reason=reason,
            )
            for member in members
        )

    prices = {member.lookup.unit_selling_price for member in members}
    if len(prices) != 1:
        return tuple(
            _result(
                member,
                status=ProductPricingStatus.PROMOTION_UNSUPPORTED_MIXED_PRICE,
                unit_selling_price=member.lookup.unit_selling_price,
                reason="Promotion source is complete, but mixed resolved Unit Selling Prices have no confirmed allocation rule.",
            )
            for member in members
        )

    group_total = members[0].promotion_group_total
    total_quantity = sum(member.promotion_member_qty or 0 for member in members)
    allocated = Decimal("0")
    results: list[ProductPricingResult] = []
    for position, member in enumerate(members):
        member_qty = member.promotion_member_qty or 0
        if position == len(members) - 1:
            actual_value = group_total - allocated
            method = "promotion_group_last_member_remainder"
        else:
            actual_value = _money(group_total * Decimal(member_qty) / Decimal(total_quantity))
            allocated += actual_value
            method = "promotion_group_per_unit_with_remainder"
        normal_value = _money(member.lookup.unit_selling_price * member_qty)
        discount = _money(normal_value - actual_value)
        results.append(
            _priced_result(
                member,
                normal_value=normal_value,
                actual_value=actual_value,
                discount=discount,
                status=ProductPricingStatus.PROMOTION_ALLOCATED,
                allocation_method=method,
                evidence=(
                    "product_price_master",
                    "promotion_group_id",
                    "promotion_label",
                    "promotion_group_total",
                    "promotion_target_qty",
                    "promotion_member_qty",
                    f"participating_quantity={total_quantity}",
                ),
            )
        )
    return tuple(results)


def _promotion_actual_allocations(
    members: list[_PricingCandidate],
) -> tuple[tuple[_PricingCandidate, Decimal, str, tuple[str, ...]], ...]:
    group_total = members[0].promotion_group_total
    total_quantity = sum(member.promotion_member_qty or 0 for member in members)
    allocated = Decimal("0")
    results: list[tuple[_PricingCandidate, Decimal, str, tuple[str, ...]]] = []
    for position, member in enumerate(members):
        member_qty = member.promotion_member_qty or 0
        if position == len(members) - 1:
            actual_value = group_total - allocated
            method = "promotion_group_last_member_remainder"
        else:
            actual_value = _money(group_total * Decimal(member_qty) / Decimal(total_quantity))
            allocated += actual_value
            method = "promotion_group_per_unit_with_remainder"
        results.append(
            (
                member,
                actual_value,
                method,
                (
                    "promotion_group_id",
                    "promotion_label",
                    "promotion_group_total",
                    "promotion_target_qty",
                    "promotion_member_qty",
                    f"participating_quantity={total_quantity}",
                ),
            )
        )
    return tuple(results)

def _promotion_evidence_reason(
    group_id: str,
    members: list[_PricingCandidate],
) -> str | None:
    if not members:
        return "Promotion group has no members."
    if any(member.promotion_metadata_status for member in members):
        return "Promotion parser metadata is incomplete or unsupported."
    if any(member.promotion_group_id != group_id for member in members):
        return "Promotion group IDs are inconsistent."
    if any(not member.promotion_label for member in members):
        return "Promotion source label is unavailable."
    totals = {member.promotion_group_total for member in members}
    if None in totals or len(totals) != 1:
        return "Promotion group total is unavailable or inconsistent."
    if any(member.promotion_member_qty is None for member in members):
        return "Promotion member quantity is unavailable."
    # Any N is source metadata, not a membership boundary. The parser has
    # already determined membership from the container layout.
    return None


def _is_incomplete_promotion(candidate: _PricingCandidate) -> bool:
    return bool(candidate.promotion_metadata_status or candidate.promotion_label)


def _promotion_incomplete(
    candidate: _PricingCandidate,
    reason: str | None = None,
) -> ProductPricingResult:
    if candidate.lookup.status not in _MATCHED_LOOKUP_STATUSES:
        return _lookup_unavailable(candidate, group_reason=reason)
    return _result(
        candidate,
        status=ProductPricingStatus.PROMOTION_EVIDENCE_INCOMPLETE,
        unit_selling_price=candidate.lookup.unit_selling_price,
        reason=reason or "Promotion evidence is incomplete or unsupported; no allocation was calculated.",
    )


def _lookup_unavailable(
    candidate: _PricingCandidate,
    group_reason: str | None = None,
    *,
    actual_selling_value: Decimal | None = None,
    allocation_method: str | None = None,
    allocation_evidence: tuple[str, ...] = (),
) -> ProductPricingResult:
    status = (
        ProductPricingStatus.PRICING_CONFLICT
        if candidate.lookup.status is PriceLookupStatus.PRICING_CONFLICT
        else ProductPricingStatus.PRICE_NOT_FOUND
    )
    return _result(
        candidate,
        status=status,
        unit_selling_price=None,
        actual_selling_value=actual_selling_value,
        allocation_method=allocation_method,
        allocation_evidence=allocation_evidence,
        reason=group_reason or candidate.lookup.reason,
    )


def _priced_result(
    candidate: _PricingCandidate,
    *,
    normal_value: Decimal,
    actual_value: Decimal,
    discount: Decimal,
    status: ProductPricingStatus,
    allocation_method: str,
    evidence: tuple[str, ...],
) -> ProductPricingResult:
    if discount < -PRICING_ANOMALY_TOLERANCE:
        status = ProductPricingStatus.PRICING_ANOMALY
        reason = "Discount Given is below -RM0.02; retained without clamping."
    else:
        reason = None
    return _result(
        candidate,
        status=status,
        unit_selling_price=candidate.lookup.unit_selling_price,
        normal_selling_value=normal_value,
        actual_selling_value=actual_value,
        discount_given=discount,
        allocation_method=allocation_method,
        allocation_evidence=evidence,
        reason=reason,
    )


def _result(
    candidate: _PricingCandidate,
    *,
    status: ProductPricingStatus,
    unit_selling_price: Decimal | None,
    normal_selling_value: Decimal | None = None,
    actual_selling_value: Decimal | None = None,
    discount_given: Decimal | None = None,
    allocation_method: str | None = None,
    allocation_evidence: tuple[str, ...] = (),
    reason: str | None = None,
) -> ProductPricingResult:
    return ProductPricingResult(
        row_index=candidate.row_index,
        unit_selling_price=unit_selling_price,
        normal_selling_value=normal_selling_value,
        actual_selling_value=actual_selling_value,
        discount_given=discount_given,
        pricing_status=status,
        price_lookup_status=candidate.lookup.status,
        promotion_group_id=candidate.promotion_group_id,
        promotion_label=candidate.promotion_label,
        promotion_group_total=candidate.promotion_group_total,
        promotion_target_qty=candidate.promotion_target_qty,
        promotion_member_qty=candidate.promotion_member_qty,
        source_line_subtotal=candidate.source_line_subtotal,
        allocation_method=allocation_method,
        allocation_evidence=allocation_evidence,
        reason=reason,
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _positive_integer(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    match = _MONEY_TEXT.fullmatch(text)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM)