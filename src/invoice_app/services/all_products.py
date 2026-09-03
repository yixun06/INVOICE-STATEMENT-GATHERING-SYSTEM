from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..review_reason_codes import PRODUCT_SUMMARY_EXCLUSION_REASON_CODES
from ..utils.normalize import normalize_sku_text, normalize_whitespace, parse_quantity
from ..utils.order_dates import has_missing_source_date, shopee_order_date_from_id
from .batch_service import (
    MISSING_VALUE_PLACEHOLDER,
    PLATFORMS,
    canonical_order_identity,
    canonical_platform_label,
    is_manual_review_record,
)
from .product_price_master import PriceLookupStatus, ProductPriceMaster
from .product_pricing import (
    MONEY_QUANTUM,
    PRICING_ANOMALY_TOLERANCE,
    ProductPricingStatus,
    calculate_shopee_product_pricing,
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
    "unit_selling_price",
    "total_quantity",
    "total_selling_price",
    "total_discount_given",
]

CROSS_PLATFORM_SUMMARY_FIELD_LABELS = {
    "seller_sku": "Seller SKU",
    "product_name": "Product Name",
    "unit_selling_price": "Unit Selling Price",
    "total_quantity": "Total Quantity",
    "total_selling_price": "Total Selling Price",
    "total_discount_given": "Total Discount Given",
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
    *,
    price_master: ProductPriceMaster | None = None,
) -> list[dict[str, Any]]:
    """Return eligible All rows enriched with pricing inputs and derived reporting facts."""
    normal_rows, _ = _build_all_product_views(orders, products, reviews, include_reporting=True)
    return _apply_cross_platform_product_pricing(normal_rows, price_master)


def partition_cross_platform_product_summary_rows(
    rows: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep exclusion-coded review orders out of Product Summary only."""
    exclusions: dict[tuple[str, str], dict[str, Any]] = {}
    for review in reviews or []:
        if not is_manual_review_record(review):
            continue
        reason_code = str(review.get("reason_code") or "").strip()
        if reason_code not in PRODUCT_SUMMARY_EXCLUSION_REASON_CODES:
            continue
        order_payload = review.get("order_payload")
        if not isinstance(order_payload, dict):
            order_payload = {}
        identity = _review_identity(review, order_payload)
        if identity is None:
            continue

        platform, order_id = identity
        reason = str(review.get("reason") or reason_code).strip() or reason_code
        source_pdf = str(review.get("source_pdf") or "").strip()
        exclusion = exclusions.setdefault(
            identity,
            {
                "platform": platform,
                "order_id": order_id,
                "reason": reason,
                "reason_code": reason_code,
                "source_pdf": source_pdf,
                "reporting_order_created_date": _preferred_reporting_order_date(
                    platform, order_payload, {}
                ),
                "_reason_codes": [reason_code],
                "_reasons": [reason],
            },
        )
        if reason_code not in exclusion["_reason_codes"]:
            exclusion["_reason_codes"].append(reason_code)
        if reason not in exclusion["_reasons"]:
            exclusion["_reasons"].append(reason)
        if not exclusion["source_pdf"] and source_pdf:
            exclusion["source_pdf"] = source_pdf

    excluded_identities = set(exclusions)
    summary_rows = [
        row
        for row in rows
        if canonical_order_identity(row.get("platform"), row.get("order_id")) not in excluded_identities
    ]
    exclusion_rows: list[dict[str, Any]] = []
    for exclusion in exclusions.values():
        exclusion_rows.append(
            {
                **exclusion,
                "reason_code": ", ".join(exclusion["_reason_codes"]),
                "reason": "\n".join(exclusion["_reasons"]),
            }
        )
    return summary_rows, sorted(
        exclusion_rows,
        key=lambda row: (str(row["platform"]).casefold(), str(row["order_id"]).casefold()),
    )


def build_shopee_product_level_rows(
    products: list[dict[str, Any]],
    *,
    price_master: ProductPriceMaster | None,
) -> list[dict[str, Any]]:
    """Create Shopee Product Level display rows without changing parsed source rows."""
    display_rows = [
        {
            **product,
            "product_name": _product_summary_label(
                {
                    "product_name": product.get("product_name"),
                    "reporting_variation_name": product.get("variation_name") or product.get("variation"),
                }
            ),
        }
        for product in products
    ]
    if price_master is None:
        return [{**row, "unit_price": MISSING_VALUE_PLACEHOLDER} for row in display_rows]

    products_by_order: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, product in enumerate(products):
        order_key = str(product.get("order_id") or "").strip() or f"__row_{index}"
        products_by_order.setdefault(order_key, []).append((index, product))

    for members in products_by_order.values():
        pricing_results = calculate_shopee_product_pricing(
            [product for _, product in members],
            price_master,
        )
        for (index, _), pricing_result in zip(members, pricing_results):
            display_rows[index]["unit_price"] = (
                pricing_result.unit_selling_price
                if pricing_result.unit_selling_price is not None
                else MISSING_VALUE_PLACEHOLDER
            )
    return display_rows


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


def missing_sku_product_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return each current reporting row that cannot join Seller-SKU aggregation."""
    return [
        row
        for row in rows
        if _is_missing(normalize_sku_text(row.get("seller_sku")))
    ]


def summarize_cross_platform_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate pricing rows by SKU plus Product Name/Variation price identity."""
    summaries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        seller_sku = normalize_sku_text(row.get("seller_sku"))
        if _is_missing(seller_sku):
            continue
        identity = (
            seller_sku,
            _normalized_identity_text(row.get("product_name")),
            _normalized_identity_text(row.get("reporting_variation_name")),
        )
        summary = summaries.setdefault(
            identity,
            {
                "seller_sku": seller_sku,
                "product_name": _product_summary_label(row),
                "total_quantity": 0,
                "_unit_prices": set(),
                "_has_unavailable_unit_price": False,
                "_has_non_authoritative_unit_price": False,
                "_selling_total": Decimal("0"),
                "_has_missing_selling_value": False,
            },
        )
        summary["total_quantity"] += parse_quantity(row.get("quantity"))
        unit_price = _decimal_or_none(row.get("reporting_unit_selling_price"))
        if unit_price is None:
            summary["_has_unavailable_unit_price"] = True
        else:
            summary["_unit_prices"].add(unit_price)
        if row.get("reporting_pricing_status") == "platform_source_only":
            summary["_has_non_authoritative_unit_price"] = True
        selling_value = _decimal_or_none(row.get("reporting_actual_selling_value"))
        if selling_value is None:
            summary["_has_missing_selling_value"] = True
        else:
            summary["_selling_total"] += selling_value

    result: list[dict[str, Any]] = []
    for summary in summaries.values():
        unit_selling_price = (
            None
            if summary["_has_unavailable_unit_price"] or len(summary["_unit_prices"]) != 1
            else next(iter(summary["_unit_prices"]))
        )
        total_selling_price = (
            None if summary["_has_missing_selling_value"] else summary["_selling_total"]
        )
        result.append(
            {
                "seller_sku": summary["seller_sku"],
                "product_name": summary["product_name"],
                "unit_selling_price": (
                    MISSING_VALUE_PLACEHOLDER
                    if unit_selling_price is None
                    else unit_selling_price
                ),
                "total_quantity": summary["total_quantity"],
                "total_selling_price": (
                    MISSING_VALUE_PLACEHOLDER
                    if total_selling_price is None
                    else total_selling_price
                ),
                "total_discount_given": (
                    MISSING_VALUE_PLACEHOLDER
                    if (
                        unit_selling_price is None
                        or total_selling_price is None
                        or summary["_has_non_authoritative_unit_price"]
                    )
                    else (unit_selling_price * summary["total_quantity"] - total_selling_price).quantize(
                        MONEY_QUANTUM
                    )
                ),
            }
        )
    return sorted(
        result,
        key=lambda row: (str(row["seller_sku"]).casefold(), str(row["product_name"]).casefold()),
    )
def _apply_cross_platform_product_pricing(
    rows: list[dict[str, Any]],
    price_master: ProductPriceMaster | None,
) -> list[dict[str, Any]]:
    decorated = [dict(row) for row in rows]
    if price_master is None:
        return [_price_master_unavailable(row) for row in decorated]

    shopee_by_order: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(decorated):
        if row.get("platform") == "Shopee":
            order_key = str(row.get("order_id") or "").strip() or f"__row_{index}"
            shopee_by_order.setdefault(order_key, []).append((index, row))
        else:
            decorated[index] = _standard_platform_pricing(row)

    for members in shopee_by_order.values():
        member_rows = [row for _, row in members]
        results = calculate_shopee_product_pricing(
            [_shopee_pricing_input(row) for row in member_rows],
            price_master,
        )
        for (index, row), result in zip(members, results):
            decorated[index] = {
                **row,
                "reporting_unit_selling_price": result.unit_selling_price,
                "reporting_normal_selling_value": result.normal_selling_value,
                "reporting_actual_selling_value": result.actual_selling_value,
                "reporting_discount_given": result.discount_given,
                "reporting_pricing_status": result.pricing_status.value,
                "reporting_price_lookup_status": result.price_lookup_status.value,
                "reporting_pricing_reason": result.reason,
                "reporting_allocation_method": result.allocation_method,
                "reporting_allocation_evidence": result.allocation_evidence,
            }
    return decorated


def _shopee_pricing_input(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "seller_sku": _lookup_input_text(row.get("seller_sku")),
        "parent_sku": _lookup_input_text(row.get("reporting_parent_sku")),
        "product_name": _lookup_input_text(row.get("product_name")),
        "variation_name": _lookup_input_text(row.get("reporting_variation_name")),
        "quantity": row.get("quantity"),
        "source_line_subtotal": _shopee_source_line_subtotal_for_pricing(row),
        "promotion_group_id": row.get("reporting_promotion_group_id"),
        "promotion_label": row.get("reporting_promotion_label"),
        "promotion_group_total": row.get("reporting_promotion_group_total"),
        "source_group_total": row.get("reporting_source_group_total"),
        "promotion_target_qty": row.get("reporting_promotion_target_qty"),
        "promotion_member_qty": row.get("reporting_promotion_member_qty"),
        "promotion_metadata_status": row.get("reporting_promotion_metadata_status"),
    }


def _shopee_source_line_subtotal_for_pricing(row: dict[str, Any]) -> Any:
    """Return a source subtotal only when it is safe to price the individual row.

    Current parser output stores the normal Shopee Subtotal in both the
    source-specific field and the normalized ``line_subtotal`` field. Older
    retained rows can have only the latter, so use it as the same source fact.
    A promotion marker deliberately disables this fallback: its subtotal may
    belong to the group rather than to this SKU.
    """
    source_subtotal = row.get("reporting_source_line_subtotal")
    if _decimal_or_none(source_subtotal) is not None:
        return source_subtotal
    if any(
        _lookup_input_text(row.get(field))
        for field in (
            "reporting_promotion_group_id",
            "reporting_promotion_label",
            "reporting_promotion_metadata_status",
        )
    ):
        return None
    return row.get("reporting_sales_amount")

def _standard_platform_pricing(row: dict[str, Any]) -> dict[str, Any]:
    """Keep non-Shopee reporting strictly on its own invoice/source fields."""
    actual_value = _decimal_or_none(row.get("reporting_sales_amount"))
    unit_price = _decimal_or_none(row.get("unit_price"))
    return {
        **row,
        "reporting_unit_selling_price": unit_price,
        "reporting_normal_selling_value": None,
        "reporting_actual_selling_value": actual_value,
        "reporting_discount_given": None,
        "reporting_pricing_status": "platform_source_only",
        "reporting_price_lookup_status": "not_applicable",
        "reporting_pricing_reason": None,
        "reporting_allocation_method": "platform_source_actual_selling_value",
        "reporting_allocation_evidence": ("platform_source_actual_selling_value",),
    }


def _price_master_unavailable(row: dict[str, Any]) -> dict[str, Any]:
    actual_value = _decimal_or_none(row.get("reporting_sales_amount"))
    return {
        **row,
        "reporting_unit_selling_price": None,
        "reporting_normal_selling_value": None,
        "reporting_actual_selling_value": actual_value if row.get("platform") != "Shopee" else None,
        "reporting_discount_given": None,
        "reporting_pricing_status": "price_master_unavailable",
        "reporting_price_lookup_status": "price_master_unavailable",
        "reporting_pricing_reason": "Shopee Product Master is unavailable.",
        "reporting_allocation_method": None,
        "reporting_allocation_evidence": (),
    }


def _matched_lookup_statuses() -> frozenset[PriceLookupStatus]:
    return frozenset(
        {
            PriceLookupStatus.MATCHED,
            PriceLookupStatus.MATCHED_BY_ALIAS,
            PriceLookupStatus.MATCHED_BY_NAME_VARIATION,
            PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS,
            PriceLookupStatus.MATCHED_BY_SKU,
            PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
            PriceLookupStatus.MATCHED_BY_PARENT_SKU,
            PriceLookupStatus.MATCHED_BY_PARENT_SKU_NAME_VARIATION,
        }
    )


def _lookup_input_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _normalized_identity_text(value: Any) -> str:
    return normalize_whitespace(_lookup_input_text(value)).casefold()


def _product_summary_label(row: dict[str, Any]) -> Any:
    product_name = row.get("product_name", MISSING_VALUE_PLACEHOLDER)
    variation_name = row.get("reporting_variation_name")
    if _is_missing(variation_name):
        return product_name
    return f"{product_name} — {variation_name}"

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
        "seller_sku": normalize_sku_text(product.get("seller_sku")),
        "quantity": _display_value(product.get("quantity")),
        "delivery_fee": _display_value(delivery_fee),
        "platform": _display_value(platform),
        "order_id": order_id,
    }
    if include_reporting:
        row.update(
            {
                "source_pdf": _display_value(product.get("source_pdf")),
                "reporting_order_created_date": reporting_order_created_date,
                "reporting_sales_amount": _display_value(
                    product.get(_SALES_FIELD_BY_PLATFORM.get(platform, ""))
                ),
                "reporting_parent_sku": normalize_sku_text(product.get("parent_sku")),
                "reporting_variation_name": product.get("variation_name") or product.get("variation"),
                "reporting_source_line_subtotal": product.get("source_line_subtotal"),
                "reporting_promotion_group_id": product.get("promotion_group_id"),
                "reporting_promotion_label": product.get("promotion_label"),
                "reporting_promotion_group_total": product.get("promotion_group_total"),
                "reporting_source_group_total": product.get("source_group_total"),
                "reporting_promotion_target_qty": product.get("promotion_target_qty"),
                "reporting_promotion_member_qty": product.get("promotion_member_qty"),
                "reporting_promotion_metadata_status": product.get("promotion_metadata_status"),
            }
        )
    return row

def _canonical_reporting_order_date(platform: str, order: dict[str, Any]) -> date | None:
    source_field = _REPORTING_DATE_FIELD_BY_PLATFORM.get(platform)
    if not source_field:
        return None
    value = order.get(source_field)
    if _is_missing(value):
        if platform == "Shopee" and has_missing_source_date(value):
            return shopee_order_date_from_id(order.get("order_id"))
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
