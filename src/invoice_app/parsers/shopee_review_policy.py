from __future__ import annotations

from dataclasses import dataclass

from .shopee_extractor import ShopeeExtractedData
from .shopee_financial_parser import missing_income_detail_fields
from .validation import (
    count_valid_product_items,
    extract_expected_product_count,
    format_validation_errors,
    validate_product_items,
    validate_shopee_financial_reconciliation,
    validate_shopee_product_amounts,
)


@dataclass(frozen=True)
class ShopeeReviewIssue:
    order_id: str
    reason: str


def find_shopee_review_issue(data: ShopeeExtractedData) -> ShopeeReviewIssue | None:
    if data.is_courier_only:
        return ShopeeReviewIssue(
            order_id="N/A",
            reason=(
                "Courier-only Shopee document does not include seller order/product "
                "transaction details."
            ),
        )

    if not data.order_id:
        return ShopeeReviewIssue(
            order_id="N/A",
            reason="Order ID could not be extracted from the Shopee order details.",
        )

    product_items = list(data.product_items)
    valid_product_count = count_valid_product_items(product_items, require_sku=True)
    if valid_product_count == 0:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason="No Valid Product Extracted: no product row passed required-field validation.",
        )

    expected_product_count = extract_expected_product_count(data.normalized_text)
    if expected_product_count is not None and valid_product_count != expected_product_count:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason=(
                "Product Count Mismatch: "
                f"source declares {expected_product_count} products, "
                f"but {valid_product_count} valid products were extracted."
            ),
        )

    validation_errors = validate_product_items(product_items, require_sku=True)
    if validation_errors:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason=format_validation_errors(validation_errors),
        )

    missing_income_fields = missing_income_detail_fields(data.normalized_text, data.income)
    if missing_income_fields:
        if "Estimated Order Income or Order Income" in missing_income_fields:
            reason = "Income Completion Anchor Missing: Source Document Appears Incomplete."
        else:
            reason = "Source Document Appears Incomplete: full Income Details are not visible."
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason=(
                f"{reason} "
                "Please re-upload the complete order details PDF. "
                f"Missing: {', '.join(missing_income_fields)}."
            ),
        )

    product_amount_error = validate_shopee_product_amounts(
        product_items,
        data.income.get("merchandise_subtotal"),
    )
    if product_amount_error:
        return ShopeeReviewIssue(order_id=data.order_id, reason=product_amount_error)

    financial_error = validate_shopee_financial_reconciliation(data.income)
    if financial_error:
        return ShopeeReviewIssue(order_id=data.order_id, reason=financial_error)

    return None
