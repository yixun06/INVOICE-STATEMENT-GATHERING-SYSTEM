from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING

from .shopee_extractor import ShopeeExtractedData
from .shopee_financial_parser import missing_income_detail_fields
from .validation import (
    count_product_anchor_items,
    extract_expected_product_count,
    format_validation_errors,
    validate_product_items,
    validate_shopee_financial_reconciliation,
    validate_shopee_promotion_evidence,
    validate_shopee_product_amounts,
)
from ..review_reason_codes import (
    INCOMPLETE_PROMOTION_EVIDENCE,
    INCOME_DETAILS_REQUIRED_FIELD_MISSING,
    INCOME_EXTRACTION_MISSING,
    INCOME_SOURCE_INCOMPLETE,
    NO_VALID_PRODUCTS,
    PRODUCT_AMOUNT_RECONCILIATION_FAILED,
    PRODUCT_COUNT_MISMATCH,
)

if TYPE_CHECKING:
    from ..pdf_document import PdfDocument


_SOURCE_PAGE_TOTAL = re.compile(
    r"\bpage\s*(\d+)\s*(?:of|/)\s*(\d+)\b|^\s*(\d+)\s*/\s*(\d+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

@dataclass(frozen=True)
class ShopeeReviewIssue:
    order_id: str
    reason: str
    reason_code: str | None = None


def find_shopee_review_issue(
    data: ShopeeExtractedData,
    *,
    source_incomplete_evidence: str | None = None,
) -> ShopeeReviewIssue | None:
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
    product_anchor_count = count_product_anchor_items(product_items, require_sku=True)
    if product_anchor_count == 0:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason="No Valid Product Extracted: no product row had reliable Seller SKU and Quantity anchors.",
            reason_code=NO_VALID_PRODUCTS,
        )

    expected_product_count = extract_expected_product_count(data.normalized_text)
    if expected_product_count is not None and product_anchor_count != expected_product_count:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason=(
                "Product Count Mismatch: "
                f"source declares {expected_product_count} products, "
                f"but {product_anchor_count} product anchors were extracted."
            ),
            reason_code=PRODUCT_COUNT_MISMATCH,
        )

    promotion_evidence_error = validate_shopee_promotion_evidence(product_items)
    if promotion_evidence_error:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason=promotion_evidence_error,
            reason_code=INCOMPLETE_PROMOTION_EVIDENCE,
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
            if source_incomplete_evidence:
                return ShopeeReviewIssue(
                    order_id=data.order_id,
                    reason=(
                        "Income Completion Anchor Missing: Source Document Is Incomplete. "
                        f"{source_incomplete_evidence} Please re-upload the complete order details PDF. "
                        f"Missing: {', '.join(missing_income_fields)}."
                    ),
                    reason_code=INCOME_SOURCE_INCOMPLETE,
                )
            return ShopeeReviewIssue(
                order_id=data.order_id,
                reason=(
                    "Income Completion Anchor Missing: Order Income was not extracted. "
                    "Verify values visible in the original Invoice source before correcting them. "
                    f"Missing: {', '.join(missing_income_fields)}."
                ),
                reason_code=INCOME_EXTRACTION_MISSING,
            )
        else:
            return ShopeeReviewIssue(
                order_id=data.order_id,
                reason=(
                    "Income Details require source review before validation. "
                    f"Missing: {', '.join(missing_income_fields)}."
                ),
                reason_code=INCOME_DETAILS_REQUIRED_FIELD_MISSING,
            )

    product_amount_error = validate_shopee_product_amounts(
        product_items,
        data.income.get("merchandise_subtotal"),
        data.refund_amount,
    )
    if product_amount_error:
        return ShopeeReviewIssue(
            order_id=data.order_id,
            reason=product_amount_error,
            reason_code=PRODUCT_AMOUNT_RECONCILIATION_FAILED,
        )

    financial_error = validate_shopee_financial_reconciliation(
        data.income,
        data.refund_amount,
    )
    if financial_error:
        return ShopeeReviewIssue(order_id=data.order_id, reason=financial_error)

    return None


def source_incomplete_evidence(document: PdfDocument | None) -> str | None:
    """Return explicit PDF pagination evidence only when pages are definitely absent."""
    if document is None:
        return None
    physical_page_count = len(document.pages)
    for page in document.pages:
        for match in _SOURCE_PAGE_TOTAL.finditer(page.text):
            current_page, total_pages = (
                (match.group(1), match.group(2))
                if match.group(1) is not None
                else (match.group(3), match.group(4))
            )
            if int(total_pages) > physical_page_count:
                return (
                    f"Source pagination shows page {int(current_page)} of {int(total_pages)}, "
                    f"but this PDF contains only {physical_page_count} page(s)."
                )
    return None
