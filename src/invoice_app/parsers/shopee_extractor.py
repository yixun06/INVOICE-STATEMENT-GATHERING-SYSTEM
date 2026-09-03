from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from ..utils.normalize import normalize_whitespace
from ..utils.order_dates import shopee_order_date_from_id
from .shopee_financial_parser import (
    parse_buyer_payment,
    parse_income_details,
    parse_voucher_detail,
)
from .shopee_product_parser import parse_text_products, reconcile_product_candidates, resolve_promotion_group_totals


SHOPEE_ORDER_STATUSES = (
    "Order Received",
    "Ready to Ship",
    "To Ship",
    "To Receive",
    "Delivered",
    "Shipped",
    "Shipping",
    "Completed",
    "Cancelled",
    "New Order",
)


@dataclass(frozen=True)
class ShopeeExtractedData:
    """Source facts extracted from one Shopee order-details document."""

    source_pdf: str
    normalized_text: str
    is_courier_only: bool
    order_id: str
    order_status: str
    order_created_date: str
    delivered_date: str
    completed_date: str
    fund_transfer_date: str
    product_items: tuple[dict[str, Any], ...]
    income: dict[str, str]
    buyer_payment: dict[str, str]
    voucher: dict[str, str]


def extract_shopee_data(
    text: str,
    source_pdf: str,
    positioned_items: list[dict[str, Any]] | None = None,
) -> ShopeeExtractedData:
    normalized_text = normalize_pdf_text(text)
    order_id = extract_order_id(normalized_text, source_pdf)
    income = parse_income_details(normalized_text)
    product_items = reconcile_product_candidates(
        positioned_items or [],
        parse_text_products(normalized_text),
    )

    product_items = resolve_promotion_group_totals(product_items, income.get("merchandise_subtotal"))
    return ShopeeExtractedData(
        source_pdf=source_pdf,
        normalized_text=normalized_text,
        is_courier_only=bool(
            re.search(
                r"SPX Express Website Order Details",
                normalized_text,
                flags=re.IGNORECASE,
            )
        ),
        order_id=order_id,
        order_status=extract_order_status(normalized_text),
        order_created_date=extract_order_date(normalized_text, order_id),
        delivered_date=extract_delivered_date(normalized_text),
        completed_date=extract_completed_date(normalized_text),
        fund_transfer_date=extract_fund_transfer_date(normalized_text),
        product_items=tuple(product_items),
        income=income,
        buyer_payment=parse_buyer_payment(normalized_text),
        voucher=parse_voucher_detail(normalized_text),
    )


def normalize_pdf_text(text: str) -> str:
    text = (text or "").replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_order_id(text: str, source_pdf: str = "") -> str:
    explicit = re.search(
        r"\bOrder ID[^\S\r\n]*:?[^\S\r\n]*([A-Z0-9-]*\d[A-Z0-9-]*)\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        return explicit.group(1).strip()

    candidate_pattern = (
        r"(?<![A-Z0-9-])(?=[A-Z0-9-]{10,}\b)(?=[A-Z0-9-]*\d)"
        r"(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]+"
    )
    order_label = re.search(r"Order ID\s*:?", text, flags=re.IGNORECASE)
    if order_label:
        nearby = text[order_label.end() : order_label.end() + 500]
        candidate = re.search(candidate_pattern, nearby)
        if candidate:
            return candidate.group(0)

    dated = re.search(rf"({candidate_pattern})\s+\d{{1,2}}/\d{{1,2}}/\d{{4}}", text)
    if dated:
        return dated.group(1)

    source_stem = Path(source_pdf).stem.upper()
    if re.fullmatch(candidate_pattern, source_stem) and re.search(
        rf"\b{re.escape(source_stem)}\b", text, flags=re.IGNORECASE
    ):
        return source_stem
    return ""


def extract_order_date(text: str, order_id: str) -> str:
    patterns = (
        rf"\b{re.escape(order_id)}\s+(\d{{1,2}}/\d{{1,2}}/\d{{4}}(?:\s+\d{{2}}:\d{{2}})?)",
        r"New Order.{0,180}?(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{2}:\d{2})?)",
        r"Order (?:Created|Creation) Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{2}:\d{2})?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_whitespace(match.group(1))
    return _order_date_from_order_id_prefix(order_id)


def _order_date_from_order_id_prefix(order_id: str) -> str:
    """Derive a Shopee order date only from a valid YYMMDD order-ID prefix."""
    derived_date = shopee_order_date_from_id(order_id)
    return derived_date.strftime("%d/%m/%Y") if derived_date else ""


def extract_order_status(text: str) -> str:
    prefix = text.split("Order ID", 1)[0][:1800]
    alternatives = "|".join(re.escape(status) for status in SHOPEE_ORDER_STATUSES)
    labelled = re.search(
        rf"(?:^|\n)\s*({alternatives})\s+Add a Note",
        prefix,
        flags=re.IGNORECASE,
    )
    if labelled:
        value = normalize_whitespace(labelled.group(1)).lower()
        return next(status for status in SHOPEE_ORDER_STATUSES if status.lower() == value)
    for status in SHOPEE_ORDER_STATUSES:
        if status == "New Order":
            continue
        if re.search(rf"(?<![A-Za-z]){re.escape(status)}(?![A-Za-z])", prefix, flags=re.IGNORECASE):
            return status
    return ""


def extract_delivered_date(text: str) -> str:
    return extract_event_datetime(text, r"Parcel has been delivered to buyer")


def extract_completed_date(text: str) -> str:
    match = re.search(
        r"(?:^|\n)[^\n]{0,80}\bCompleted\s*(?:\n|\s).{0,120}?"
        r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalize_whitespace(match.group(1)) if match else ""


def extract_fund_transfer_date(text: str) -> str:
    match = re.search(
        r"Fund transfer has.{0,260}?(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalize_whitespace(match.group(1)) if match else ""


def extract_event_datetime(text: str, label_pattern: str) -> str:
    match = re.search(
        rf"{label_pattern}.{{0,160}}?(\d{{2}}/\d{{2}}/\d{{4}}\s+\d{{2}}:\d{{2}})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return normalize_whitespace(match.group(1)) if match else ""
