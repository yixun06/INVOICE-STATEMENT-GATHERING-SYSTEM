from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..utils.normalize import parse_quantity
from .batch_service import (
    MISSING_VALUE_PLACEHOLDER,
    PLATFORMS,
    canonical_order_identity,
    canonical_platform_label,
    is_manual_review_record,
)


ALL_PRODUCT_COLUMNS = [
    "product_name",
    "unit_price",
    "seller_sku",
    "quantity",
    "platform",
]

ALL_PRODUCT_FIELD_LABELS = {
    "product_name": "Product Name",
    "unit_price": "Product Price",
    "seller_sku": "Seller SKU #",
    "quantity": "Qty",
    "platform": "Platform",
}

ALL_PRODUCT_DISPLAY_COLUMNS = [
    "product_name",
    "unit_price",
    "seller_sku",
    "quantity",
    "platform",
]

ALL_PRODUCT_DISPLAY_FIELD_LABELS = {
    "product_name": "Product Name",
    "unit_price": "Product Price",
    "seller_sku": "Seller SKU #",
    "quantity": "Qty",
    "platform": "Platform",
}


ALL_PRODUCT_REVIEW_COLUMNS = [
    "product_name",
    "unit_price",
    "seller_sku",
    "quantity",
    "delivery_fee",
    "platform",
    "all_review_reason",
]
ALL_PRODUCT_REVIEW_FIELD_LABELS = {
    **ALL_PRODUCT_FIELD_LABELS,
    "delivery_fee": "Delivery Fee",
    "all_review_reason": "All Review Reason",
}

CROSS_PLATFORM_PRODUCT_DISPLAY_COLUMNS = [
    "reporting_order_created_date",
    *ALL_PRODUCT_DISPLAY_COLUMNS,
]

CROSS_PLATFORM_PRODUCT_DISPLAY_FIELD_LABELS = {
    "reporting_order_created_date": "Order Created Date",
    **ALL_PRODUCT_DISPLAY_FIELD_LABELS,
}
CROSS_PLATFORM_SUMMARY_COLUMNS = [
    "seller_sku",
    "product_name",
    "total_quantity",
    "total_sales_amount",
]

CROSS_PLATFORM_SUMMARY_FIELD_LABELS = {
    "seller_sku": "Seller SKU",
    "product_name": "Product Name",
    "total_quantity": "Total Quantity",
    "total_sales_amount": "Total Sales Amount",
}

_REPORTING_DATE_FIELD_BY_PLATFORM = {
    "Shopee": "order_created_date",
    "Lazada": "order_date",
    "ZENXIN": "invoice_date",
}

_SALES_FIELD_BY_PLATFORM = {
    "Shopee": "line_subtotal",
    "Lazada": "paid_price",
    "ZENXIN": "line_total_inc_tax",
}


def build_all_product_views(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive normal and review-only All product rows without changing batch data."""
    return _build_all_product_views(orders, products, reviews, include_reporting=False)


def build_cross_platform_product_rows(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return eligible All rows enriched only with reporting-layer date and sales facts."""
    normal_rows, _ = _build_all_product_views(orders, products, reviews, include_reporting=True)
    return normal_rows


def filter_cross_platform_product_rows(
    rows: list[dict[str, Any]],
    *,
    platform: str = "All",
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Apply the shared Cross Platform Summary filters without altering source rows."""
    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        if platform != "All" and row.get("platform") != platform:
            continue
        reporting_date = row.get("reporting_order_created_date")
        if start_date is not None or end_date is not None:
            if not isinstance(reporting_date, date):
                continue
            if start_date is not None and reporting_date < start_date:
                continue
            if end_date is not None and reporting_date > end_date:
                continue
        filtered_rows.append(row)
    return filtered_rows


def summarize_cross_platform_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate eligible reporting rows by Seller SKU, never by Product Name."""
    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        seller_sku = str(row.get("seller_sku", "")).strip()
        if _is_missing(seller_sku):
            continue
        summary = summaries.setdefault(
            seller_sku,
            {
                "seller_sku": seller_sku,
                "product_name": row.get("product_name", MISSING_VALUE_PLACEHOLDER),
                "total_quantity": 0,
                "_sales_total": Decimal("0"),
                "_has_missing_sales_amount": False,
            },
        )
        summary["total_quantity"] += parse_quantity(row.get("quantity"))
        sales_amount = _decimal_or_none(row.get("reporting_sales_amount"))
        if sales_amount is None:
            summary["_has_missing_sales_amount"] = True
        else:
            summary["_sales_total"] += sales_amount

    result: list[dict[str, Any]] = []
    for summary in summaries.values():
        result.append(
            {
                "seller_sku": summary["seller_sku"],
                "product_name": summary["product_name"],
                "total_quantity": summary["total_quantity"],
                "total_sales_amount": (
                    MISSING_VALUE_PLACEHOLDER
                    if summary["_has_missing_sales_amount"]
                    else summary["_sales_total"]
                ),
            }
        )
    return sorted(result, key=lambda row: str(row["seller_sku"]).casefold())


def _build_all_product_views(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None,
    *,
    include_reporting: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    delivery_fee_by_order: dict[tuple[str, str], Any] = {}
    reporting_date_by_order: dict[tuple[str, str], date | None] = {}
    accepted_identities: set[tuple[str, str]] = set()
    for order in orders:
        identity = canonical_order_identity(order.get("platform"), order.get("order_id"))
        if identity is not None:
            accepted_identities.add(identity)
            delivery_fee_by_order[identity] = order.get("delivery_fee", "")
            reporting_date_by_order[identity] = _canonical_reporting_order_date(identity[0], order)

    normal_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    for product in products:
        platform = canonical_platform_label(product.get("platform"))
        order_id = _clean_order_id(product.get("order_id"))
        identity = canonical_order_identity(platform, order_id)
        row = _all_product_row(
            product,
            platform=platform,
            order_id=order_id,
            delivery_fee=delivery_fee_by_order.get(identity, MISSING_VALUE_PLACEHOLDER),
            reporting_order_created_date=(
                reporting_date_by_order.get(identity)
                or _canonical_reporting_order_date(platform, product)
            ),
            include_reporting=include_reporting,
        )
        _append_by_row_eligibility(row, normal_rows, review_rows)

    seen_review_identities: set[tuple[str, str]] = set()
    for review in reviews or []:
        if not is_manual_review_record(review):
            continue
        order_payload = review.get("order_payload")
        if not isinstance(order_payload, dict):
            order_payload = {}
        review_identity = _review_identity(review, order_payload)
        if review_identity is not None:
            if review_identity in accepted_identities or review_identity in seen_review_identities:
                continue
            seen_review_identities.add(review_identity)

        product_payloads = review.get("product_payloads")
        if not isinstance(product_payloads, list):
            continue

        review_platform = review_identity[0] if review_identity else canonical_platform_label(review.get("platform"))
        review_order_id = review_identity[1] if review_identity else _clean_order_id(review.get("order_id"))
        for product_payload in product_payloads:
            if not isinstance(product_payload, dict):
                continue
            product_platform = canonical_platform_label(product_payload.get("platform")) or review_platform
            product_order_id = _clean_order_id(product_payload.get("order_id")) or review_order_id
            row = _all_product_row(
                product_payload,
                platform=product_platform,
                order_id=product_order_id,
                delivery_fee=order_payload.get("delivery_fee", MISSING_VALUE_PLACEHOLDER),
                reporting_order_created_date=_preferred_reporting_order_date(
                    product_platform,
                    order_payload,
                    product_payload,
                ),
                include_reporting=include_reporting,
            )
            _append_by_row_eligibility(
                row,
                normal_rows,
                review_rows,
                identity_reason=_payload_identity_reason(product_payload, review_identity),
            )

    return normal_rows, review_rows


def build_all_product_rows(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return only eligible All product rows for existing callers and exports."""
    normal_rows, _ = build_all_product_views(orders, products, reviews)
    return normal_rows


def _review_identity(
    review: dict[str, Any], order_payload: dict[str, Any]
) -> tuple[str, str] | None:
    return canonical_order_identity(review.get("platform"), review.get("order_id")) or canonical_order_identity(
        order_payload.get("platform"), order_payload.get("order_id")
    )


def _payload_identity_reason(
    product_payload: dict[str, Any], review_identity: tuple[str, str] | None
) -> str | None:
    if review_identity is None:
        return None
    product_identity = canonical_order_identity(
        product_payload.get("platform"), product_payload.get("order_id")
    )
    if product_identity is not None and product_identity != review_identity:
        return "Payload identity does not match review"
    return None


def _append_by_row_eligibility(
    row: dict[str, Any],
    normal_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    *,
    identity_reason: str | None = None,
) -> None:
    reasons = _all_row_review_reasons(row)
    if identity_reason:
        reasons.append(identity_reason)
    if reasons:
        review_rows.append({**row, "all_review_reason": "; ".join(reasons)})
    else:
        normal_rows.append(row)


def _all_row_review_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if _is_missing(row.get("product_name")):
        reasons.append("Missing Product Name")
    if not _has_valid_price(row.get("unit_price")):
        reasons.append("Missing or invalid Product Price")
    if parse_quantity(row.get("quantity")) <= 0:
        reasons.append("Missing or invalid Qty")
    if row.get("platform") not in PLATFORMS:
        reasons.append("Missing or invalid Platform")
    return reasons


def _has_valid_price(value: Any) -> bool:
    if _is_missing(value):
        return False
    try:
        amount = Decimal(str(value).replace(",", "").replace("RM", "").replace("S$", "").strip())
    except (InvalidOperation, ValueError):
        return False
    return amount.is_finite()


def _all_product_row(
    product: dict[str, Any],
    *,
    platform: str,
    order_id: str,
    delivery_fee: Any,
    reporting_order_created_date: date | None = None,
    include_reporting: bool = False,
) -> dict[str, Any]:
    row = {
        "product_name": _display_value(product.get("product_name")),
        "unit_price": _display_value(product.get("unit_price")),
        "seller_sku": _display_value(product.get("seller_sku")),
        "quantity": _display_value(product.get("quantity")),
        "delivery_fee": _display_value(delivery_fee),
        "platform": _display_value(platform),
        "order_id": order_id,
    }
    if include_reporting:
        row["reporting_order_created_date"] = reporting_order_created_date
        row["reporting_sales_amount"] = _display_value(
            product.get(_SALES_FIELD_BY_PLATFORM.get(platform, ""))
        )
    return row


def _canonical_reporting_order_date(platform: str, order: dict[str, Any]) -> date | None:
    source_field = _REPORTING_DATE_FIELD_BY_PLATFORM.get(platform)
    if not source_field:
        return None
    value = order.get(source_field)
    if _is_missing(value):
        return None

    text = " ".join(str(value).split())
    formats = {
        "Shopee": ("%d/%m/%Y %H:%M", "%d/%m/%Y"),
        "Lazada": ("%d %m %Y",),
        "ZENXIN": ("%d/%m/%Y",),
    }.get(platform, ())
    for value_format in formats:
        try:
            return datetime.strptime(text, value_format).date()
        except ValueError:
            continue
    return None



def _preferred_reporting_order_date(
    platform: str,
    order_payload: dict[str, Any],
    product_payload: dict[str, Any],
) -> date | None:
    return _canonical_reporting_order_date(platform, order_payload) or _canonical_reporting_order_date(
        platform, product_payload
    )
def _decimal_or_none(value: Any) -> Decimal | None:
    if _is_missing(value):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").replace("RM", "").replace("S$", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _clean_order_id(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_missing(value: Any) -> bool:
    return value is None or not str(value).strip() or str(value).strip().casefold() == MISSING_VALUE_PLACEHOLDER.casefold()


def _display_value(value: Any) -> Any:
    if _is_missing(value):
        return MISSING_VALUE_PLACEHOLDER
    return value
