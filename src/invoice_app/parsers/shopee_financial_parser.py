from __future__ import annotations

from decimal import Decimal
import re

from ..utils.normalize import normalize_whitespace, parse_decimal


MONEY_PATTERN = r"[-+]?\s*RM\s*[-+]?\s*[\d,]+(?:\.\d+)?"
MISSING_FINANCIAL_VALUE = "N/A"

INCOME_ALIASES: dict[str, tuple[str, ...]] = {
    "merchandise_subtotal": ("Merchandise Subtotal",),
    "product_price": ("Product Price",),
    "shipping_subtotal": ("Shipping Subtotal", "Estimated Shipping Subtotal"),
    "shipping_fee_paid_by_buyer": ("Shipping Fee Paid by Buyer",),
    "shipping_fee_charged_by_logistic_provider": (
        "Shipping Fee Charged by Logistic Provider",
        "Estimated Shipping Fee Charged by Logistic Provider",
    ),
    "shipping_fee_rebate_from_shopee": (
        "Shipping Fee Rebate From Shopee",
        "Shipping Fee Rebate from Shopee",
        "Estimated Shipping Fee Rebate From Shopee",
        "Estimated Shipping Fee Rebate from Shopee",
    ),
    "seller_paid_shipping_fee_sst": ("Seller Paid Shipping Fee SST",),
    "vouchers_rebates_total": ("Vouchers & Rebates",),
    "fees_charges_total": ("Fees & Charges",),
    "commission_fee": ("Commission Fee",),
    "service_fee": ("Service Fee",),
    "transaction_fee": ("Transaction Fee",),
    "ads_escrow_top_up_fee": ("Ads Escrow Top Up Fee",),
    "estimated_order_income": ("Estimated Order Income",),
    "final_amount": ("Final Amount",),
}

REQUIRED_INCOME_DETAIL_FIELDS = (
    "merchandise_subtotal",
    "product_price",
    "shipping_subtotal",
    "shipping_fee_paid_by_buyer",
    "shipping_fee_charged_by_logistic_provider",
    "seller_paid_shipping_fee_sst",
    "fees_charges_total",
    "commission_fee",
    "service_fee",
    "transaction_fee",
)


def parse_income_details(text: str) -> dict[str, str]:
    section = extract_section(
        text,
        r"^\s*(?:Hide\s+)?Income Details\s*$",
        (
            r"^\s*Buyer Payment\s*$",
            r"^\s*Order History\s*$",
            r"^\s*Home\s+My Orders\s*$",
            r"^\s*[^\n]{0,40}\bAdd a Note\s*$",
        ),
    )
    result = {
        field: extract_alias_money(section, aliases)
        for field, aliases in INCOME_ALIASES.items()
    }
    actual_income = _extract_actual_order_income(section)
    estimated_income = result["estimated_order_income"]
    if not is_missing_financial_value(actual_income):
        result["order_income"] = actual_income
        result["income_type"] = "Final"
    elif not is_missing_financial_value(estimated_income):
        result["order_income"] = estimated_income
        result["income_type"] = "Estimated"
    else:
        result["order_income"] = MISSING_FINANCIAL_VALUE
        result["income_type"] = MISSING_FINANCIAL_VALUE
    return result


def missing_income_detail_fields(text: str, income: dict[str, str]) -> list[str]:
    missing: list[str] = []
    if not re.search(r"(?:Hide\s+)?Income Details", text, flags=re.IGNORECASE):
        missing.append("Income Details section")

    for field in REQUIRED_INCOME_DETAIL_FIELDS:
        if is_missing_financial_value(income.get(field)):
            missing.append(INCOME_ALIASES[field][0])
    if is_missing_financial_value(income.get("order_income")):
        missing.append("Estimated Order Income or Order Income")
    return missing


def parse_buyer_payment(text: str) -> dict[str, str]:
    section = extract_section(
        text,
        r"Buyer Payment",
        (r"Order Adjustment", r"https?://", r"Home\s*My Orders"),
    )
    return {
        "buyer_merchandise_subtotal": extract_alias_money(section, ("Merchandise Subtotal",)),
        "buyer_shipping_fee": extract_alias_money(section, ("Shipping Fee",)),
        "shopee_voucher": extract_alias_money(section, ("Shopee Voucher",)),
        "seller_voucher": extract_alias_money(section, ("Seller Voucher",)),
        "total_buyer_payment": extract_alias_money(section, ("Total Buyer Payment",)),
    }


def parse_voucher_detail(text: str) -> dict[str, str]:
    match = re.search(
        rf"([^\n]*voucher[^\n]*?)\s*-\s*([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\s*({MONEY_PATTERN})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return {
            "voucher_type": MISSING_FINANCIAL_VALUE,
            "voucher_code": MISSING_FINANCIAL_VALUE,
            "voucher_funded_by": MISSING_FINANCIAL_VALUE,
            "voucher_amount": MISSING_FINANCIAL_VALUE,
        }
    voucher_type = normalize_whitespace(match.group(1))
    funded_match = re.search(r"paid by\s+([A-Za-z ]+)", voucher_type, flags=re.IGNORECASE)
    return {
        "voucher_type": voucher_type,
        "voucher_code": match.group(2).strip(),
        "voucher_funded_by": (
            normalize_whitespace(funded_match.group(1)).title()
            if funded_match
            else MISSING_FINANCIAL_VALUE
        ),
        "voucher_amount": money_to_string(match.group(3)),
    }


def calculate_platform_fees(income: dict[str, str]) -> str:
    if not is_missing_financial_value(income.get("fees_charges_total")):
        return income["fees_charges_total"]
    values = [
        income.get("commission_fee", ""),
        income.get("service_fee", ""),
        income.get("transaction_fee", ""),
        income.get("ads_escrow_top_up_fee", ""),
    ]
    present = [value for value in values if not is_missing_financial_value(value)]
    if not present:
        return MISSING_FINANCIAL_VALUE
    total = sum((parse_decimal(value) for value in present), Decimal("0"))
    return str(total.quantize(Decimal("0.01")))


def extract_alias_money(text: str, aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        match = re.search(
            rf"{re.escape(alias)}(?:\s*\([^\n)]*\))?\s*:?\s*({MONEY_PATTERN})",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return money_to_string(match.group(1))
    return MISSING_FINANCIAL_VALUE


def extract_section(text: str, start_pattern: str, end_patterns: tuple[str, ...]) -> str:
    start = re.search(start_pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not start:
        return ""
    tail = text[start.end() :]
    ends = [
        match.start()
        for pattern in end_patterns
        if (match := re.search(pattern, tail, flags=re.IGNORECASE | re.MULTILINE))
    ]
    return tail[: min(ends)].strip() if ends else tail.strip()


def money_to_string(value: str) -> str:
    return str(parse_decimal(value).quantize(Decimal("0.01")))


def is_missing_financial_value(value: str | None) -> bool:
    return value is None or str(value).strip() in ("", MISSING_FINANCIAL_VALUE)


def _extract_actual_order_income(text: str) -> str:
    match = re.search(
        rf"(?<!Estimated )Order Income(?:\s*\([^\n)]*\))?\s*:?\s*({MONEY_PATTERN})",
        text,
        flags=re.IGNORECASE,
    )
    return money_to_string(match.group(1)) if match else MISSING_FINANCIAL_VALUE
