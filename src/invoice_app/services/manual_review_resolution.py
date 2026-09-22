"""Issue-driven, session-only correction of supported Manual Review records."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any

from .batch_service import apply_batch_rules, is_manual_review_record
from .product_price_master import ProductPriceMaster
from .shopee_invoice_revalidation import revalidate_shopee_invoice
from ..parsers.shopee_mapper import resolve_shopee_payment_status
from ..parsers.shopee_financial_parser import INCOME_ALIASES, is_missing_financial_value
from ..parsers.validation import (
    financial_reconciliation_evidence_notes,
    validate_product_items,
)
from ..review_reason_codes import FINAL_AMOUNT_EXTRACTION_MISSING, INCOME_COMPLETION_ANCHOR_MISSING, INCOME_EXTRACTION_MISSING, INCOMPLETE_PROMOTION_EVIDENCE, SKU_RESOLUTION_REQUIRED

PRODUCT_COUNT_MISMATCH = "PRODUCT_COUNT_MISMATCH"
MISSING_INCOME = "MISSING_INCOME_INFORMATION"
FINAL_AMOUNT = "FINAL_AMOUNT_EXTRACTION"
PROMOTION_SUBTOTAL = "PROMOTION_SUBTOTAL"
SKU_RESOLUTION = "SKU_RESOLUTION"
CORRECTION_DRAFTS_KEY = "manual_review_correction_drafts"
_EXPECTED_COUNT = re.compile(r"source declares\s+(\d+)\s+products?", re.I)
_FINANCIAL_ENRICHMENT_FIELDS = tuple(
    field for field in INCOME_ALIASES if field != "estimated_order_income"
)

@dataclass(frozen=True)
class ResolutionPlan:
    key: str
    issue_type: str
    expected_products: int | None = None

@dataclass(frozen=True)
class ResolutionOutcome:
    resolved: bool
    reason: str | None

@dataclass(frozen=True)
class CorrectionDraftSummary:
    extracted_count: int
    manual_count: int
    declared_count: int | None
    remaining_missing: int | None
    exceeds_declared_count: bool

@dataclass(frozen=True)
class PromotionGroupOption:
    group_id: str
    label: str
    member_names: tuple[str, ...]
    member_quantities: tuple[int, ...]
    source_group_total: str | None
    advertised_amount: str | None
    target_quantity: int | None
    subtotal_correction_allowed: bool = False


@dataclass(frozen=True)
class PromotionSubtotalResolutionEligibility:
    eligible: bool
    reason: str
    groups: tuple[PromotionGroupOption, ...] = ()
    missing_fields: tuple[str, ...] = ()

def review_key(review: Mapping[str, Any]) -> str:
    raw = "|".join(str(review.get(name, "")) for name in ("source_pdf", "order_id", "reason", "timestamp"))
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def review_presentation_key(review: Mapping[str, Any]) -> str:
    """Return the stable, session-only identity used to restore UI focus.

    The audit key intentionally includes the review reason and timestamp.  A
    revalidation can legitimately change either of those, so the presentation
    layer uses only the stable source/order identity and never writes it to
    staging or persistence.
    """
    raw = "|".join(
        str(review.get(name, ""))
        for name in ("platform", "source_pdf", "order_id")
    )
    return sha256(raw.encode("utf-8")).hexdigest()[:16]


def resolution_plan(review: Mapping[str, Any]) -> ResolutionPlan | None:
    reason = str(review.get("reason") or "")
    code = str(review.get("reason_code") or "")
    if code == PRODUCT_COUNT_MISMATCH or reason.startswith("Product Count Mismatch:"):
        match = _EXPECTED_COUNT.search(reason)
        return ResolutionPlan(review_key(review), PRODUCT_COUNT_MISMATCH, int(match.group(1)) if match else None)
    if code in {INCOME_COMPLETION_ANCHOR_MISSING, INCOME_EXTRACTION_MISSING}:
        return ResolutionPlan(review_key(review), MISSING_INCOME)
    if code == FINAL_AMOUNT_EXTRACTION_MISSING:
        return ResolutionPlan(review_key(review), FINAL_AMOUNT)
    if code == SKU_RESOLUTION_REQUIRED:
        return ResolutionPlan(review_key(review), SKU_RESOLUTION)
    if code == INCOMPLETE_PROMOTION_EVIDENCE:
        eligibility = promotion_subtotal_resolution_eligibility(review)
        if eligibility.eligible:
            return ResolutionPlan(review_key(review), PROMOTION_SUBTOTAL)
    if _has_source_missing_sku(review):
        return ResolutionPlan(review_key(review), SKU_RESOLUTION)
    if promotion_subtotal_resolution_eligibility(review).eligible:
        return ResolutionPlan(review_key(review), PROMOTION_SUBTOTAL)
    return None


def resolution_capabilities(review: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every independently safe correction supported by structured evidence."""
    capabilities: list[str] = []
    plan = resolution_plan(review)
    if plan is not None:
        capabilities.append(plan.issue_type)
    if _has_source_missing_sku(review) and SKU_RESOLUTION not in capabilities:
        capabilities.append(SKU_RESOLUTION)
    if (
        promotion_subtotal_resolution_eligibility(review).eligible
        and PROMOTION_SUBTOTAL not in capabilities
    ):
        capabilities.append(PROMOTION_SUBTOTAL)
    return tuple(capabilities)


def _has_source_missing_sku(review: Mapping[str, Any]) -> bool:
    return any(
        bool(product.get("sku_missing_in_source"))
        and not str(product.get("seller_sku") or "").strip()
        for product in review.get("product_payloads") or []
        if isinstance(product, Mapping)
    )

def synchronize_correction_drafts(state: MutableMapping[str, Any]) -> None:
    """Discard drafts that no longer belong to a current source/order/review."""
    drafts = state.get(CORRECTION_DRAFTS_KEY)
    if not isinstance(drafts, dict):
        state[CORRECTION_DRAFTS_KEY] = {}
        return
    active = {review_key(review): _draft_identity(review) for review in state.get("reviews", []) if is_manual_review_record(review)}
    state[CORRECTION_DRAFTS_KEY] = {key: draft for key, draft in drafts.items() if key in active and isinstance(draft, dict) and draft.get("identity") == active[key]}

def draft_products(state: MutableMapping[str, Any], review: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    synchronize_correction_drafts(state)
    return tuple(dict(product) for product in _ensure_draft(state, review)["products"])

def draft_promotion_subtotals(state: MutableMapping[str, Any], review: Mapping[str, Any]) -> dict[str, str]:
    synchronize_correction_drafts(state)
    return dict(_ensure_draft(state, review)["promotion_subtotals"])


def draft_financial_enrichment(
    state: MutableMapping[str, Any], review: Mapping[str, Any]
) -> dict[str, str]:
    """Return the session-only optional financial corrections for one review."""
    synchronize_correction_drafts(state)
    return dict(_ensure_draft(state, review)["financial_enrichment"])


def financial_enrichment_fields(
    review: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Expose only parser-recognized monetary fields that are currently missing."""
    order = review.get("order_payload") or {}
    return tuple(
        (field, INCOME_ALIASES[field][0])
        for field in _FINANCIAL_ENRICHMENT_FIELDS
        if is_missing_financial_value(order.get(field))
    )


def set_financial_enrichment(
    state: MutableMapping[str, Any],
    *,
    key: str,
    values: Mapping[str, Any],
    source_confirmed: bool,
) -> ResolutionOutcome:
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    if resolution_plan(review) is None:
        return ResolutionOutcome(False, "This source issue cannot be resolved safely from a manual correction.")
    if not source_confirmed:
        return ResolutionOutcome(False, "Confirm that these optional values are visible in the original Invoice source.")
    normalized, error = _normalize_financial_enrichment(review, values)
    if error:
        return ResolutionOutcome(False, error)
    _ensure_draft(state, review)["financial_enrichment"].update(normalized)
    return ResolutionOutcome(True, None)

def draft_summary(state: MutableMapping[str, Any], review: Mapping[str, Any]) -> CorrectionDraftSummary:
    plan = resolution_plan(review)
    manual_count = len(draft_products(state, review))
    extracted_count = len(review.get("product_payloads") or [])
    declared = plan.expected_products if plan and plan.issue_type == PRODUCT_COUNT_MISMATCH else None
    remaining = None if declared is None else max(0, declared - extracted_count - manual_count)
    return CorrectionDraftSummary(extracted_count, manual_count, declared, remaining, declared is not None and extracted_count + manual_count > declared)

def add_draft_product(state: MutableMapping[str, Any], *, key: str, values: Mapping[str, Any]) -> ResolutionOutcome:
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    plan = resolution_plan(review)
    if plan is None or plan.issue_type != PRODUCT_COUNT_MISMATCH:
        return ResolutionOutcome(False, "This source issue does not support a missing-product draft.")
    product, error = _missing_product(review.get("order_payload") or {}, values, review)
    if error:
        return ResolutionOutcome(False, error)
    draft = _ensure_draft(state, review)
    if plan.expected_products is not None and len(review.get("product_payloads") or []) + len(draft["products"]) + 1 > plan.expected_products:
        return ResolutionOutcome(False, "Manual products would exceed the source-declared product count.")
    draft["products"].append(product)
    return ResolutionOutcome(True, None)

def edit_draft_product(state: MutableMapping[str, Any], *, key: str, index: int, values: Mapping[str, Any]) -> ResolutionOutcome:
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    draft = _ensure_draft(state, review)
    if index < 0 or index >= len(draft["products"]):
        return ResolutionOutcome(False, "The selected draft product no longer exists.")
    product, error = _missing_product(review.get("order_payload") or {}, values, review)
    if error:
        return ResolutionOutcome(False, error)
    draft["products"][index] = product
    return ResolutionOutcome(True, None)

def remove_draft_product(state: MutableMapping[str, Any], *, key: str, index: int) -> ResolutionOutcome:
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    draft = _ensure_draft(state, review)
    if index < 0 or index >= len(draft["products"]):
        return ResolutionOutcome(False, "The selected draft product no longer exists.")
    draft["products"].pop(index)
    return ResolutionOutcome(True, None)

def clear_correction_draft(state: MutableMapping[str, Any], *, key: str) -> None:
    drafts = state.get(CORRECTION_DRAFTS_KEY)
    if isinstance(drafts, dict):
        drafts.pop(key, None)

def set_draft_promotion_subtotal(
    state: MutableMapping[str, Any],
    *,
    key: str,
    group_id: str,
    value: Any,
    source_confirmed: bool,
) -> ResolutionOutcome:
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    if not source_confirmed:
        return ResolutionOutcome(False, "Confirm that the subtotal is visible in the original Invoice source.")
    subtotal, error = _validated_promotion_subtotal(review, group_id, value)
    if error:
        return ResolutionOutcome(False, error)
    _ensure_draft(state, review)["promotion_subtotals"][group_id] = subtotal
    return ResolutionOutcome(True, None)

def promotion_group_options(review: Mapping[str, Any]) -> tuple[PromotionGroupOption, ...]:
    """Return only reliable source-owned groups; IDs are never user-authored."""
    return _promotion_groups(review, subtotal_only=False)

def promotion_subtotal_groups(review: Mapping[str, Any]) -> tuple[PromotionGroupOption, ...]:
    """Return only deterministic groups with a source-visible subtotal amount."""
    return promotion_subtotal_resolution_eligibility(review).groups


def promotion_subtotal_resolution_eligibility(
    review: Mapping[str, Any],
) -> PromotionSubtotalResolutionEligibility:
    """Classify promotion-subtotal correction from structured source evidence.

    A coordinate-visible unresolved subtotal keeps the established Case 1 path.
    A missing subtotal is also eligible when the reliable promotion label carries
    one exact advertised RM amount and every owned member remains deterministic.
    The advertised amount is evidence for user confirmation, never an automatic
    correction.
    """
    grouped = _grouped_promotion_members(review)
    if not grouped:
        return PromotionSubtotalResolutionEligibility(
            False,
            "No structured promotion container and members are available.",
            missing_fields=("promotion_group_id", "promotion_members"),
        )

    options: list[PromotionGroupOption] = []
    rejection_reasons: list[str] = []
    missing_fields: set[str] = set()
    for group_id, members in grouped.items():
        option, reason, missing = _promotion_subtotal_option(group_id, members)
        if option is not None:
            options.append(option)
        else:
            rejection_reasons.append(reason)
            missing_fields.update(missing)
    if options:
        return PromotionSubtotalResolutionEligibility(
            True,
            "Reliable promotion members and a source-visible subtotal amount are available.",
            tuple(options),
            ("source_group_total",),
        )
    return PromotionSubtotalResolutionEligibility(
        False,
        "; ".join(dict.fromkeys(rejection_reasons))
        or "No promotion group is eligible for safe subtotal correction.",
        missing_fields=tuple(sorted(missing_fields)),
    )

def apply_product_draft(state: MutableMapping[str, Any], *, key: str, price_master: ProductPriceMaster) -> ResolutionOutcome:
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    plan = resolution_plan(review)
    if plan is None or plan.issue_type != PRODUCT_COUNT_MISMATCH:
        return ResolutionOutcome(False, "This source issue does not support a missing-product draft.")
    draft = _ensure_draft(state, review)
    extracted = len(review.get("product_payloads") or [])
    total = extracted + len(draft["products"])
    if plan.expected_products is None:
        return ResolutionOutcome(False, "The source-declared product count is unavailable.")
    if total < plan.expected_products:
        return ResolutionOutcome(False, f"Add {plan.expected_products - total} more source-visible product(s) before applying.")
    if total > plan.expected_products:
        return ResolutionOutcome(False, "Manual products exceed the source-declared product count.")
    products = [dict(product) for product in [*(review.get("product_payloads") or []), *draft["products"]]]
    for group_id, subtotal in draft["promotion_subtotals"].items():
        _apply_confirmed_promotion_subtotal(products, group_id, subtotal)
    order = dict(review.get("order_payload") or {})
    order.update(draft["financial_enrichment"])
    return _revalidate_and_accept(
        state,
        review,
        products,
        price_master,
        expected_product_count=plan.expected_products,
        corrected_order=order,
    )

def apply_resolution(state: MutableMapping[str, Any], *, key: str, values: Mapping[str, Any], price_master: ProductPriceMaster) -> ResolutionOutcome:
    """Apply income/subtotal corrections; retain legacy one-product compatibility."""
    review = _find_review(state, key)
    if review is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    plan = resolution_plan(review)
    if plan is None:
        return ResolutionOutcome(False, "This source issue cannot be resolved safely from a manual correction.")
    order = dict(review.get("order_payload") or {})
    products = [dict(item) for item in review.get("product_payloads") or []]
    if not order:
        return ResolutionOutcome(False, "This review has no staged order data to correct.")
    capabilities = set(resolution_capabilities(review))
    if SKU_RESOLUTION in capabilities:
        if values.get("sku_source_confirmed", values.get("source_confirmed")) is not True:
            return ResolutionOutcome(False, "Confirm that every resolved Seller SKU was verified from an authoritative source outside the Invoice PDF.")
        candidates = values.get("resolved_seller_skus")
        if not isinstance(candidates, Mapping):
            return ResolutionOutcome(False, "Provide a resolved Seller SKU for every source-missing SKU product.")
        for index, product in enumerate(products):
            if not bool(product.get("sku_missing_in_source")) or str(product.get("seller_sku") or "").strip():
                continue
            candidate = str(candidates.get(str(index)) or "").strip()
            if not candidate:
                return ResolutionOutcome(False, "Provide a resolved Seller SKU for every source-missing SKU product.")
            product["resolved_seller_sku"] = candidate
    if PROMOTION_SUBTOTAL in capabilities:
        if values.get("promotion_source_confirmed", values.get("source_confirmed")) is not True:
            return ResolutionOutcome(False, "Confirm that the subtotal is visible in the original Invoice source.")
        options = {option.group_id: option for option in promotion_subtotal_groups(review)}
        submitted = values.get("promotion_subtotals")
        if submitted is None:
            submitted = {
                str(values.get("promotion_group_id") or "").strip(): values.get("source_group_total")
            }
        if not isinstance(submitted, Mapping):
            return ResolutionOutcome(False, "Provide one source-visible subtotal for each eligible promotion group.")
        normalized: dict[str, str] = {}
        for group_id in options:
            subtotal, error = _validated_promotion_subtotal(review, group_id, submitted.get(group_id))
            if error:
                return ResolutionOutcome(False, error)
            normalized[group_id] = subtotal
        if set(submitted) - set(options):
            return ResolutionOutcome(False, "A submitted promotion group is no longer eligible for safe subtotal correction.")
        _ensure_draft(state, review)["promotion_subtotals"].update(normalized)
        for group_id, subtotal in normalized.items():
            _apply_confirmed_promotion_subtotal(products, group_id, subtotal)
    if plan.issue_type == PRODUCT_COUNT_MISMATCH:
        added, error = _missing_product(order, values, review)
        if error:
            return ResolutionOutcome(False, error)
        products.append(added)
    elif plan.issue_type in {SKU_RESOLUTION, PROMOTION_SUBTOTAL}:
        pass
    elif plan.issue_type == FINAL_AMOUNT:
        if values.get("source_confirmed") is not True:
            return ResolutionOutcome(False, "Confirm that this Final Amount is visible in the original Invoice source.")
        final_amount = _source_money(values.get("final_amount"))
        if final_amount is None:
            return ResolutionOutcome(False, "Final Amount must be a source-visible numeric amount.")
        order["final_amount"] = final_amount
        order["net_income"] = final_amount
        order["net_amount"] = final_amount
    else:
        if values.get("source_confirmed") is not True:
            return ResolutionOutcome(False, "Confirm that these values are visible in the original Invoice source before applying them.")
        income = _source_money(values.get("order_income"))
        income_type = str(values.get("income_type") or "").strip()
        final_amount = _source_money(values.get("final_amount"), optional=True)
        if income is None or income_type not in {"Estimated", "Final"} or final_amount is None:
            return ResolutionOutcome(False, "Order Income and Income Type are required from the original Invoice source.")
        order["order_income"] = income
        order["income_type"] = income_type
        order["estimated_order_income"] = income if income_type == "Estimated" else "N/A"
        if final_amount != "":
            order["final_amount"] = final_amount
        order["payment_status"] = resolve_shopee_payment_status(order.get("fund_transfer_date"), income_type)
        order["net_income"] = final_amount or income
        order["net_amount"] = order["net_income"]
    direct_enrichment = values.get("financial_enrichment")
    if direct_enrichment is not None:
        if values.get("source_confirmed") is not True:
            return ResolutionOutcome(False, "Confirm that these optional values are visible in the original Invoice source.")
        normalized, error = _normalize_financial_enrichment(review, direct_enrichment)
        if error:
            return ResolutionOutcome(False, error)
        _ensure_draft(state, review)["financial_enrichment"].update(normalized)
    order.update(_ensure_draft(state, review)["financial_enrichment"])
    return _revalidate_and_accept(
        state,
        review,
        products,
        price_master,
        expected_product_count=plan.expected_products,
        corrected_order=order,
    )

def _revalidate_and_accept(
    state: MutableMapping[str, Any],
    review: Mapping[str, Any],
    products: list[Mapping[str, Any]],
    price_master: ProductPriceMaster,
    *,
    expected_product_count: int | None,
    corrected_order: Mapping[str, Any] | None = None,
) -> ResolutionOutcome:
    order = dict(corrected_order or review.get("order_payload") or {})
    revalidated = revalidate_shopee_invoice(order, products, price_master=price_master, expected_product_count=expected_product_count)
    if revalidated.error:
        return ResolutionOutcome(False, revalidated.error)
    order["invoice_financial_layout"] = revalidated.invoice_financial_layout
    order["_financial_evidence_notes"] = financial_reconciliation_evidence_notes(
        order,
        order.get("refund_amount"),
        layout=revalidated.invoice_financial_layout or "",
    )
    accepted_products = [dict(product) for product in revalidated.products]
    for product, enrichment in zip(accepted_products, revalidated.master_enrichment):
        product["master_unit_price"] = enrichment["unit_price"]
        product["nav"] = enrichment["nav"]
    key = review_key(review)
    reviews = list(state.get("reviews", []))
    index = next((i for i, item in enumerate(reviews) if is_manual_review_record(item) and review_key(item) == key), None)
    if index is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    remaining = [item for i, item in enumerate(reviews) if i != index]
    order["status"] = "Accepted"
    for product in accepted_products:
        product["status"] = "Accepted"
    accepted_orders, accepted_products, updated_reviews = apply_batch_rules([*state.get("orders", []), order], [*state.get("products", []), *accepted_products], remaining)
    state["orders"], state["products"], state["reviews"] = accepted_orders, accepted_products, updated_reviews
    clear_correction_draft(state, key=key)
    for stale in ("uat2_historical_commit_entries", "uat2_historical_commit_refresh_required", "uat2_historical_commit_signature"):
        state.pop(stale, None)
    return ResolutionOutcome(True, None)

def _missing_product(order: Mapping[str, Any], values: Mapping[str, Any], review: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    sku_visible = values.get("seller_sku_visible") is not False
    seller_sku = str(values.get("seller_sku") or "").strip()
    if sku_visible and not seller_sku:
        return {}, "Seller SKU is required when it is visible in the source."
    if not sku_visible and seller_sku:
        return {}, "Leave Seller SKU blank when confirming that the source does not show one."
    subtotal = str(values.get("line_subtotal") or "").strip()
    actual_price = str(values.get("actual_selling_unit_price") or "").strip()
    product: dict[str, Any] = {
        "batch_id": order.get("batch_id", ""), "source_pdf": order.get("source_pdf", ""), "platform": order.get("platform", "Shopee"), "order_id": order.get("order_id", ""),
        "seller_sku": seller_sku, "sku_missing_in_source": not sku_visible, "product_name": str(values.get("product_name") or "").strip(),
        "variation": str(values.get("variation") or "").strip(), "quantity": values.get("quantity"), "unit_price": actual_price,
        "line_total": subtotal, "line_subtotal": subtotal, "source_line_subtotal": subtotal,
    }
    selected_group = str(values.get("promotion_group_id") or "").strip()
    if selected_group:
        options = {option.group_id: option for option in promotion_group_options(review)}
        if selected_group not in options:
            return {}, "The selected promotion group is not reliable source evidence."
        source_member = next(item for item in review.get("product_payloads") or [] if str(item.get("promotion_group_id") or "").strip() == selected_group)
        for field in ("promotion_group_id", "promotion_label", "promotion_advertised_amount", "promotion_discount_percent", "source_group_total", "promotion_group_total", "promotion_target_qty", "_promotion_boundary_status", "_promotion_member_ownership_status", "_promotion_subtotal_source_status", "_promotion_subtotal_resolution"):
            if field in source_member:
                product[field] = source_member[field]
        product["promotion"] = product.get("promotion_label", "")
        product["source_line_subtotal"] = product["line_total"] = product["line_subtotal"] = None
    # A reliable Case 1 group may receive its confirmed subtotal elsewhere in
    # this same draft. Structural product facts can be staged before that value.
    errors = validate_product_items(
        [product], require_sku=True, require_line_total=not bool(selected_group)
    )
    return (product, None) if not errors else ({}, " ".join(errors))

def _promotion_groups(review: Mapping[str, Any], *, subtotal_only: bool) -> tuple[PromotionGroupOption, ...]:
    if subtotal_only:
        return promotion_subtotal_resolution_eligibility(review).groups
    grouped = _grouped_promotion_members(review)
    options: list[PromotionGroupOption] = []
    for group_id, members in grouped.items():
        leading = members[0]
        reliable = all(
            member.get("_promotion_boundary_status") == "reliable"
            and member.get("_promotion_member_ownership_status") == "reliable"
            for member in members
        )
        complete = bool(str(leading.get("source_group_total") or "").strip()) and leading.get("promotion_metadata_status") != "incomplete"
        case_one = reliable and leading.get("_promotion_subtotal_source_status") == "visible_unresolved"
        if not reliable or not (complete or case_one):
            continue
        options.append(_promotion_group_option(group_id, members, case_one))
    return tuple(options)


def _grouped_promotion_members(
    review: Mapping[str, Any],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in review.get("product_payloads") or []:
        group_id = str(item.get("promotion_group_id") or "").strip()
        if group_id:
            grouped.setdefault(group_id, []).append(item)
    return grouped


def _promotion_subtotal_option(
    group_id: str,
    members: list[Mapping[str, Any]],
) -> tuple[PromotionGroupOption | None, str, tuple[str, ...]]:
    missing: list[str] = []
    if not members:
        return None, "Promotion members are unavailable.", ("promotion_members",)
    if not all(
        member.get("_promotion_boundary_status") == "reliable"
        for member in members
    ):
        missing.append("promotion_boundary")
    if not all(
        member.get("_promotion_member_ownership_status") == "reliable"
        for member in members
    ):
        missing.append("promotion_member_ownership")

    member_labels = tuple(
        str(member.get("promotion_label") or member.get("promotion") or "").strip()
        for member in members
    )
    labels = set(member_labels)
    labels.discard("")
    if len(labels) != 1 or any(not label for label in member_labels):
        missing.append("promotion_label")

    quantities = tuple(_positive_int(member.get("quantity")) for member in members)
    if any(quantity is None for quantity in quantities) or any(
        not str(member.get("product_name") or "").strip() for member in members
    ):
        missing.append("promotion_members")

    member_targets = tuple(
        _positive_int(member.get("promotion_target_qty")) for member in members
    )
    targets = set(member_targets)
    targets.discard(None)
    target = next(iter(targets)) if len(targets) == 1 else None
    if target is None or any(member_target is None for member_target in member_targets):
        missing.append("promotion_target_qty")

    source_totals = {
        _source_money(member.get("source_group_total"), optional=True)
        for member in members
    }
    source_totals.discard("")
    source_totals.discard(None)
    if source_totals:
        missing.append("source_group_total_not_missing")

    statuses = {
        str(member.get("_promotion_subtotal_source_status") or "").strip()
        for member in members
    }
    member_advertised = tuple(
        _source_money(member.get("promotion_advertised_amount"), optional=True)
        for member in members
    )
    advertised = set(member_advertised)
    advertised.discard("")
    advertised.discard(None)
    has_coordinate_visible_subtotal = statuses == {"visible_unresolved"}
    has_label_visible_subtotal = (
        statuses.issubset({"absent", "unusable"})
        and bool(statuses)
        and len(advertised) == 1
        and all(value not in (None, "") for value in member_advertised)
    )
    if not has_coordinate_visible_subtotal and not has_label_visible_subtotal:
        missing.append("source_visible_promotion_amount")

    if missing:
        return (
            None,
            f"Promotion group {group_id} lacks deterministic source evidence: "
            + ", ".join(dict.fromkeys(missing))
            + ".",
            tuple(dict.fromkeys(missing)),
        )
    return (
        _promotion_group_option(group_id, members, True),
        "Eligible for source-confirmed promotion subtotal correction.",
        ("source_group_total",),
    )


def _promotion_group_option(
    group_id: str,
    members: list[Mapping[str, Any]],
    correction_allowed: bool,
) -> PromotionGroupOption:
    leading = members[0]
    return PromotionGroupOption(
        group_id=group_id,
        label=str(
            leading.get("promotion_label")
            or leading.get("promotion")
            or "Promotion"
        ),
        member_names=tuple(
            str(member.get("product_name") or "Unnamed product") for member in members
        ),
        member_quantities=tuple(
            int(member.get("quantity", 0) or 0) for member in members
        ),
        source_group_total=(
            str(leading.get("source_group_total"))
            if leading.get("source_group_total") not in (None, "")
            else None
        ),
        advertised_amount=(
            str(leading.get("promotion_advertised_amount"))
            if leading.get("promotion_advertised_amount") not in (None, "")
            else None
        ),
        target_quantity=(
            int(leading.get("promotion_target_qty"))
            if leading.get("promotion_target_qty") not in (None, "")
            else None
        ),
        subtotal_correction_allowed=correction_allowed,
    )


def _validated_promotion_subtotal(
    review: Mapping[str, Any], group_id: str, value: Any
) -> tuple[str, str | None]:
    options = {
        option.group_id: option for option in promotion_subtotal_groups(review)
    }
    option = options.get(group_id)
    if option is None:
        return "", "This promotion group is not eligible for safe subtotal correction."
    subtotal = _source_money(value)
    if subtotal is None:
        return "", "Promotion Subtotal must be a source-visible numeric amount."
    advertised = _source_money(option.advertised_amount, optional=True)
    if advertised not in (None, "") and Decimal(subtotal) != Decimal(advertised):
        return (
            "",
            f"Promotion Subtotal must match the source-visible amount RM{advertised}.",
        )
    return subtotal, None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

def _find_review(state: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    return next((item for item in state.get("reviews", []) if is_manual_review_record(item) and review_key(item) == key), None)

def _apply_confirmed_promotion_subtotal(products: list[dict[str, Any]], group_id: str, subtotal: str) -> None:
    for product in products:
        if str(product.get("promotion_group_id") or "").strip() == group_id:
            product["source_group_total"] = subtotal
            product["promotion_group_total"] = subtotal
            product["_promotion_subtotal_resolution"] = "source_confirmed_manual"
            product["_promotion_subtotal_source_status"] = "manual_confirmed"
            product.pop("promotion_metadata_status", None)
            product.pop("promotion_incomplete_reason", None)

def _draft_identity(review: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(review.get(field) or "").strip() for field in ("batch_id", "source_pdf", "order_id", "timestamp"))

def _ensure_draft(state: MutableMapping[str, Any], review: Mapping[str, Any]) -> dict[str, Any]:
    drafts = state.setdefault(CORRECTION_DRAFTS_KEY, {})
    key, identity = review_key(review), _draft_identity(review)
    draft = drafts.get(key)
    if not isinstance(draft, dict) or draft.get("identity") != identity:
        draft = {
            "identity": identity,
            "products": [],
            "promotion_subtotals": {},
            "financial_enrichment": {},
        }
        drafts[key] = draft
    draft.setdefault("promotion_subtotals", {})
    draft.setdefault("financial_enrichment", {})
    return draft


def _normalize_financial_enrichment(
    review: Mapping[str, Any], values: Any
) -> tuple[dict[str, str], str | None]:
    if not isinstance(values, Mapping):
        return {}, "Optional financial details must be entered as source-visible monetary values."
    available = {field for field, _ in financial_enrichment_fields(review)}
    normalized: dict[str, str] = {}
    for field, raw in values.items():
        if field not in available:
            return {}, "Only currently missing parser-recognized financial fields may be enriched."
        amount = _source_money(raw, optional=True)
        if amount is None:
            return {}, f"{INCOME_ALIASES[field][0]} must be a source-visible numeric amount."
        if amount != "":
            normalized[field] = amount
    return normalized, None

def _source_money(value: Any, *, optional: bool = False) -> str | None:
    text = str(value or "").replace(",", "").replace("RM", "").strip()
    if not text:
        return "" if optional else None
    try:
        return str(Decimal(text).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None
