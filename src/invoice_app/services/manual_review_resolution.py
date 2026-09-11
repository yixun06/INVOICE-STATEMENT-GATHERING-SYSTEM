"""Issue-driven, session-only correction of supported Manual Review records."""
from __future__ import annotations

from collections.abc import MutableMapping, Mapping
from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any

from .batch_service import apply_batch_rules, is_manual_review_record
from .product_price_master import PriceLookupStatus, ProductPriceMaster
from ..parsers.validation import count_product_anchor_items, validate_product_items
from ..utils.normalize import parse_quantity

PRODUCT_COUNT_MISMATCH = "PRODUCT_COUNT_MISMATCH"
MISSING_INCOME = "MISSING_INCOME_INFORMATION"
_EXPECTED_COUNT = re.compile(r"source declares\s+(\d+)\s+products?", re.I)
_MATCHED = {PriceLookupStatus.MATCHED_BY_SKU, PriceLookupStatus.MATCHED, PriceLookupStatus.MATCHED_BY_ALIAS, PriceLookupStatus.MATCHED_BY_NAME_VARIATION, PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION, PriceLookupStatus.MATCHED_BY_PARENT_SKU, PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION}

@dataclass(frozen=True)
class ResolutionPlan:
    key: str
    issue_type: str
    expected_products: int | None = None

@dataclass(frozen=True)
class ResolutionOutcome:
    resolved: bool
    reason: str | None

def review_key(review: Mapping[str, Any]) -> str:
    raw = "|".join(str(review.get(name, "")) for name in ("source_pdf", "order_id", "reason", "timestamp"))
    return sha256(raw.encode("utf-8")).hexdigest()[:16]

def resolution_plan(review: Mapping[str, Any]) -> ResolutionPlan | None:
    reason = str(review.get("reason") or "")
    code = str(review.get("reason_code") or "")
    if code == PRODUCT_COUNT_MISMATCH or reason.startswith("Product Count Mismatch:"):
        match = _EXPECTED_COUNT.search(reason)
        return ResolutionPlan(review_key(review), PRODUCT_COUNT_MISMATCH, int(match.group(1)) if match else None)
    if "source document appears incomplete" not in reason.casefold() and (
        "missing income information" in reason.casefold() or "order income" in reason.casefold()
    ):
        return ResolutionPlan(review_key(review), MISSING_INCOME)
    return None

def apply_resolution(state: MutableMapping[str, Any], *, key: str, values: Mapping[str, Any], price_master: ProductPriceMaster) -> ResolutionOutcome:
    reviews = list(state.get("reviews", []))
    index = next((i for i, item in enumerate(reviews) if is_manual_review_record(item) and review_key(item) == key), None)
    if index is None:
        return ResolutionOutcome(False, "The selected Manual Review record is no longer in the current batch.")
    review = reviews[index]
    plan = resolution_plan(review)
    if plan is None:
        return ResolutionOutcome(False, "This source issue cannot be resolved safely from a manual correction.")
    order = dict(review.get("order_payload") or {})
    products = [dict(item) for item in review.get("product_payloads") or []]
    if not order:
        return ResolutionOutcome(False, "This review has no staged order data to correct.")
    if plan.issue_type == PRODUCT_COUNT_MISMATCH:
        added = _missing_product(order, values)
        errors = validate_product_items([added], require_sku=True)
        if errors:
            return ResolutionOutcome(False, " ".join(errors))
        products.append(added)
        extracted = count_product_anchor_items(products, require_sku=True)
        if plan.expected_products is not None and extracted != plan.expected_products:
            return ResolutionOutcome(False, f"Expected Products: {plan.expected_products}; Extracted Products: {extracted}.")
    else:
        income = str(values.get("order_income") or "").strip()
        income_type = str(values.get("income_type") or "").strip()
        if not income or income_type not in {"Estimated", "Final"}:
            return ResolutionOutcome(False, "Order Income and Income Type are required from the original Invoice source.")
        order["order_income"] = income
        order["estimated_order_income"] = income
        order["income_type"] = income_type
        if str(values.get("final_amount") or "").strip():
            order["final_amount"] = str(values["final_amount"]).strip()
        order["net_income"] = order.get("final_amount") or income
        order["net_amount"] = order["net_income"]
    lookup_error = _validate_master(products, price_master)
    if lookup_error:
        return ResolutionOutcome(False, lookup_error)
    remaining = [item for i, item in enumerate(reviews) if i != index]
    order["status"] = "Accepted"
    for product in products:
        product["status"] = "Accepted"
    accepted_orders, accepted_products, updated_reviews = apply_batch_rules(
        [*state.get("orders", []), order], [*state.get("products", []), *products], remaining
    )
    state["orders"], state["products"], state["reviews"] = accepted_orders, accepted_products, updated_reviews
    for stale in ("uat2_historical_commit_entries", "uat2_historical_commit_refresh_required", "uat2_historical_commit_signature"):
        state.pop(stale, None)
    return ResolutionOutcome(True, None)

def _missing_product(order: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    subtotal = str(values.get("line_subtotal") or "").strip()
    return {"batch_id": order.get("batch_id", ""), "source_pdf": order.get("source_pdf", ""), "platform": order.get("platform", "Shopee"), "order_id": order.get("order_id", ""), "seller_sku": str(values.get("seller_sku") or "").strip(), "product_name": str(values.get("product_name") or "").strip(), "variation": str(values.get("variation") or "").strip(), "quantity": values.get("quantity"), "unit_price": str(values.get("actual_selling_unit_price") or "").strip(), "line_total": subtotal, "line_subtotal": subtotal, "source_line_subtotal": subtotal}

def _validate_master(products: list[dict[str, Any]], master: ProductPriceMaster) -> str | None:
    for product in products:
        lookup = master.lookup(seller_sku=product.get("seller_sku"), product_name=product.get("product_name"), variation_name=product.get("variation") or product.get("variation_name"))
        if lookup.status not in _MATCHED or lookup.unit_selling_price is None:
            return lookup.reason or "Product Master pricing remains unresolved."
        if not lookup.nav_code:
            return "Resolved Product Master row has blank NAV CODE."
    return None
