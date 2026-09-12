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
from ..parsers.validation import validate_product_items
from ..review_reason_codes import INCOME_COMPLETION_ANCHOR_MISSING, INCOME_EXTRACTION_MISSING, INCOMPLETE_PROMOTION_EVIDENCE, FINAL_AMOUNT_EXTRACTION_MISSING

PRODUCT_COUNT_MISMATCH = "PRODUCT_COUNT_MISMATCH"
MISSING_INCOME = "MISSING_INCOME_INFORMATION"
FINAL_AMOUNT = "FINAL_AMOUNT_EXTRACTION"
PROMOTION_SUBTOTAL = "PROMOTION_SUBTOTAL"
CORRECTION_DRAFTS_KEY = "manual_review_correction_drafts"
_EXPECTED_COUNT = re.compile(r"source declares\s+(\d+)\s+products?", re.I)

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
    source_group_total: str | None
    advertised_amount: str | None
    target_quantity: int | None
    subtotal_correction_allowed: bool = False

def review_key(review: Mapping[str, Any]) -> str:
    raw = "|".join(str(review.get(name, "")) for name in ("source_pdf", "order_id", "reason", "timestamp"))
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
    if code == INCOMPLETE_PROMOTION_EVIDENCE and promotion_subtotal_groups(review):
        return ResolutionPlan(review_key(review), PROMOTION_SUBTOTAL)
    return None

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
    if group_id not in {option.group_id for option in promotion_subtotal_groups(review)}:
        return ResolutionOutcome(False, "This promotion group is not eligible for safe subtotal correction.")
    subtotal = _source_money(value)
    if subtotal is None:
        return ResolutionOutcome(False, "Promotion Subtotal must be a source-visible numeric amount.")
    _ensure_draft(state, review)["promotion_subtotals"][group_id] = subtotal
    return ResolutionOutcome(True, None)

def promotion_group_options(review: Mapping[str, Any]) -> tuple[PromotionGroupOption, ...]:
    """Return only reliable source-owned groups; IDs are never user-authored."""
    return _promotion_groups(review, subtotal_only=False)

def promotion_subtotal_groups(review: Mapping[str, Any]) -> tuple[PromotionGroupOption, ...]:
    """Return only Case 1 groups: reliable ownership and visible unresolved subtotal."""
    return _promotion_groups(review, subtotal_only=True)

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
    return _revalidate_and_accept(state, review, products, price_master, expected_product_count=plan.expected_products)

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
    if plan.issue_type == PRODUCT_COUNT_MISMATCH:
        added, error = _missing_product(order, values, review)
        if error:
            return ResolutionOutcome(False, error)
        products.append(added)
    elif plan.issue_type == PROMOTION_SUBTOTAL:
        if values.get("source_confirmed") is not True:
            return ResolutionOutcome(False, "Confirm that the subtotal is visible in the original Invoice source.")
        group_id = str(values.get("promotion_group_id") or "").strip()
        if group_id not in {option.group_id for option in promotion_subtotal_groups(review)}:
            return ResolutionOutcome(False, "This promotion group is not eligible for safe subtotal correction.")
        subtotal = _source_money(values.get("source_group_total"))
        if subtotal is None:
            return ResolutionOutcome(False, "Promotion Subtotal must be a source-visible numeric amount.")
        _apply_confirmed_promotion_subtotal(products, group_id, subtotal)
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
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in review.get("product_payloads") or []:
        group_id = str(item.get("promotion_group_id") or "").strip()
        if group_id:
            grouped.setdefault(group_id, []).append(item)
    options: list[PromotionGroupOption] = []
    for group_id, members in grouped.items():
        leading = members[0]
        reliable = leading.get("_promotion_boundary_status") == "reliable" and leading.get("_promotion_member_ownership_status") == "reliable"
        complete = bool(str(leading.get("source_group_total") or "").strip()) and leading.get("promotion_metadata_status") != "incomplete"
        case_one = reliable and leading.get("_promotion_subtotal_source_status") == "visible_unresolved"
        if not reliable or (subtotal_only and not case_one) or (not subtotal_only and not (complete or case_one)):
            continue
        options.append(PromotionGroupOption(group_id, str(leading.get("promotion_label") or leading.get("promotion") or "Promotion"), tuple(str(member.get("product_name") or "Unnamed product") for member in members), str(leading.get("source_group_total")) if leading.get("source_group_total") not in (None, "") else None, str(leading.get("promotion_advertised_amount")) if leading.get("promotion_advertised_amount") not in (None, "") else None, int(leading.get("promotion_target_qty")) if leading.get("promotion_target_qty") not in (None, "") else None, case_one))
    return tuple(options)

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
        draft = {"identity": identity, "products": [], "promotion_subtotals": {}}
        drafts[key] = draft
    draft.setdefault("promotion_subtotals", {})
    return draft

def _source_money(value: Any, *, optional: bool = False) -> str | None:
    text = str(value or "").replace(",", "").replace("RM", "").strip()
    if not text:
        return "" if optional else None
    try:
        return str(Decimal(text).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None
