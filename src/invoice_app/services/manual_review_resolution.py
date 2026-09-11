"""Issue-driven, session-only correction of supported Manual Review records."""
from __future__ import annotations

from collections.abc import MutableMapping, Mapping
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
from ..review_reason_codes import (
    INCOME_COMPLETION_ANCHOR_MISSING,
    INCOME_EXTRACTION_MISSING,
)

PRODUCT_COUNT_MISMATCH = "PRODUCT_COUNT_MISMATCH"
MISSING_INCOME = "MISSING_INCOME_INFORMATION"
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
    else:
        if values.get("source_confirmed") is not True:
            return ResolutionOutcome(
                False,
                "Confirm that these values are visible in the original Invoice source before applying them.",
            )
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
        order["payment_status"] = resolve_shopee_payment_status(
            order.get("fund_transfer_date"), income_type
        )
        order["net_income"] = final_amount or income
        order["net_amount"] = order["net_income"]
    revalidated = revalidate_shopee_invoice(
        order,
        products,
        price_master=price_master,
        expected_product_count=plan.expected_products,
    )
    if revalidated.error:
        return ResolutionOutcome(False, revalidated.error)
    order["invoice_financial_layout"] = revalidated.invoice_financial_layout
    products = [dict(product) for product in revalidated.products]
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

def _source_money(value: Any, *, optional: bool = False) -> str | None:
    text = str(value or "").replace(",", "").replace("RM", "").strip()
    if not text:
        return "" if optional else None
    try:
        return str(Decimal(text).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None
