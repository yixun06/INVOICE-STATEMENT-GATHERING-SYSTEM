"""Complete deterministic revalidation for corrected Shopee Invoice source facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.invoice_app.parsers.shopee_financial_parser import (
    INCOME_ALIASES,
    NORMAL_ORDER,
    NORMAL_ORDER_REQUIRED_INCOME_DETAIL_FIELDS,
    RETURN_REFUND,
    RETURN_REFUND_REQUIRED_INCOME_DETAIL_FIELDS,
    classify_invoice_financial_layout_from_signals,
    is_missing_financial_value,
)
from src.invoice_app.parsers.shopee_product_parser import resolve_promotion_group_totals
from src.invoice_app.parsers.validation import (
    count_product_anchor_items,
    validate_product_items,
    validate_shopee_financial_reconciliation,
    validate_shopee_product_amounts,
    validate_shopee_promotion_evidence,
)
from src.invoice_app.services.product_price_master import PriceLookupStatus, ProductPriceMaster
from src.invoice_app.services.product_pricing import (
    ProductPricingStatus,
    calculate_shopee_product_pricing,
)


_MATCHED = {
    PriceLookupStatus.MATCHED_BY_SKU,
    PriceLookupStatus.MATCHED,
    PriceLookupStatus.MATCHED_BY_ALIAS,
    PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
    PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
    PriceLookupStatus.MATCHED_BY_PARENT_SKU,
    PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION,
}


@dataclass(frozen=True)
class ShopeeInvoiceRevalidationResult:
    products: tuple[dict[str, Any], ...]
    master_enrichment: tuple[dict[str, Any], ...]
    invoice_financial_layout: str | None = None
    error: str | None = None


def revalidate_shopee_invoice(
    order: Mapping[str, Any],
    products: Sequence[Mapping[str, Any]],
    *,
    price_master: ProductPriceMaster,
    expected_product_count: int | None = None,
) -> ShopeeInvoiceRevalidationResult:
    """Run the complete post-correction validation chain without writing state."""
    prepared = [dict(product) for product in products]
    _refresh_promotion_membership_quantities(prepared)
    working = resolve_promotion_group_totals(
        prepared,
        order.get("product_price"),
    )
    extracted_count = count_product_anchor_items(working, require_sku=True)
    if expected_product_count is not None and extracted_count != expected_product_count:
        return _failed(
            working,
            f"Expected Products: {expected_product_count}; Extracted Products: {extracted_count}.",
        )

    structural = validate_product_items(working, require_sku=True)
    if structural:
        return _failed(working, " ".join(structural))
    if promotion_error := validate_shopee_promotion_evidence(working):
        return _failed(working, promotion_error)

    layout_signals = frozenset(order.get("_financial_layout_signals") or ())
    layout = (
        classify_invoice_financial_layout_from_signals(layout_signals)
        if "_financial_layout_signals" in order
        else str(order.get("invoice_financial_layout") or "").strip()
    )
    if layout not in {NORMAL_ORDER, RETURN_REFUND}:
        return _failed(
            working,
            "Invoice financial layout is unresolved and cannot be Accepted.",
            layout=layout,
        )
    if required_error := _validate_required_financial_source(order, layout):
        return _failed(working, required_error)
    if product_error := validate_shopee_product_amounts(
        working,
        order.get("merchandise_subtotal"),
        order.get("refund_amount"),
        product_price=order.get("product_price"),
    ):
        return _failed(working, product_error)
    if financial_error := validate_shopee_financial_reconciliation(
        dict(order), order.get("refund_amount"), layout=layout
    ):
        return _failed(working, financial_error)

    enrichment: list[dict[str, Any]] = []
    for product in working:
        lookup = price_master.lookup(
            seller_sku=product.get("seller_sku"),
            product_name=product.get("product_name"),
            variation_name=product.get("variation") or product.get("variation_name"),
        )
        if lookup.status not in _MATCHED or lookup.unit_selling_price is None:
            return _failed(working, lookup.reason or "Product Master pricing remains unresolved.")
        if not lookup.nav_code:
            return _failed(working, "Resolved Product Master row has blank NAV CODE.")
        enrichment.append({"unit_price": lookup.unit_selling_price, "nav": lookup.nav_code})
    pricing = calculate_shopee_product_pricing(working, price_master)
    invalid_promotion = next(
        (
            result
            for result in pricing
            if result.promotion_group_id
            and result.pricing_status is not ProductPricingStatus.PROMOTION_ALLOCATED
        ),
        None,
    )
    if invalid_promotion is not None:
        return _failed(
            working,
            invalid_promotion.reason
            or "Promotion allocation remains unresolved after source correction.",
            layout=layout,
        )
    return ShopeeInvoiceRevalidationResult(tuple(working), tuple(enrichment), layout)


def _refresh_promotion_membership_quantities(products: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        group_id = str(product.get("promotion_group_id") or "").strip()
        if group_id:
            groups.setdefault(group_id, []).append(product)
    for members in groups.values():
        participating = sum(int(member.get("quantity", 0) or 0) for member in members)
        for member in members:
            member["promotion_member_qty"] = int(member.get("quantity", 0) or 0)
            member["participating_qty"] = participating


def _validate_required_financial_source(order: Mapping[str, Any], layout: str) -> str | None:
    if order.get("_income_details_present") is False:
        return "Income Details section is missing."
    required = (
        RETURN_REFUND_REQUIRED_INCOME_DETAIL_FIELDS
        if layout == RETURN_REFUND
        else NORMAL_ORDER_REQUIRED_INCOME_DETAIL_FIELDS
    )
    missing = [
        INCOME_ALIASES[field][0]
        for field in required
        if is_missing_financial_value(order.get(field))
    ]
    if layout == RETURN_REFUND:
        if is_missing_financial_value(order.get("refund_amount")):
            missing.append("Refund Amount")
        if (
            is_missing_financial_value(order.get("order_income"))
            or str(order.get("income_type") or "").strip() != "Final"
        ):
            missing.append("Order Income")
    elif is_missing_financial_value(order.get("order_income")):
        missing.append("Estimated Order Income or Order Income")

    visible = set(order.get("_income_label_presence") or ())
    for field in sorted(visible):
        if field in INCOME_ALIASES and is_missing_financial_value(order.get(field)):
            label = INCOME_ALIASES[field][0]
            if label not in missing:
                missing.append(label)
    if missing:
        return "Income Details require source review before validation. Missing: " + ", ".join(missing) + "."
    return None


def _failed(
    products: Sequence[Mapping[str, Any]], error: str, *, layout: str | None = None
) -> ShopeeInvoiceRevalidationResult:
    return ShopeeInvoiceRevalidationResult(
        tuple(dict(product) for product in products), (), layout, error
    )
