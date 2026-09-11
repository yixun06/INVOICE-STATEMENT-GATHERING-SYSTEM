from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from ..utils.normalize import parse_quantity
from .shopee_financial_parser import NORMAL_ORDER, RETURN_REFUND


MONEY_TOLERANCE = Decimal("0.02")


def validate_product_items(
    items: list[dict[str, Any]],
    *,
    require_sku: bool,
    require_line_total: bool = True,
) -> list[str]:
    errors: list[str] = []
    if not items:
        return ["No product rows were extracted."]

    for index, item in enumerate(items, start=1):
        label = f"Item {index}"
        if not str(item.get("product_name", "")).strip():
            errors.append(f"{label} is missing Product Name.")
        if parse_quantity(item.get("quantity")) <= 0:
            errors.append(f"{label} has missing or invalid Quantity.")
        if (
            require_sku
            and not str(item.get("seller_sku", "")).strip()
            and not bool(item.get("sku_missing_in_source"))
        ):
            errors.append(f"{label} is missing Seller SKU.")
        if (
            require_line_total
            and not _has_decimal_value(item.get("line_total"))
            and not (
                item.get("promotion_group_id")
                and _has_decimal_value(item.get("source_group_total"))
            )
        ):
            errors.append(f"{label} is missing Line Total.")

    return errors


def format_validation_errors(errors: list[str]) -> str:
    return "Deterministic validation failed: " + " ".join(errors)


def count_valid_product_items(
    items: list[dict[str, Any]],
    *,
    require_sku: bool,
) -> int:
    return sum(
        1
        for item in items
        if not validate_product_items([item], require_sku=require_sku)
    )


def count_product_anchor_items(
    items: list[dict[str, Any]],
    *,
    require_sku: bool,
) -> int:
    return sum(
        1
        for item in items
        if parse_quantity(item.get("quantity")) > 0
        and (
            not require_sku
            or str(item.get("seller_sku", "")).strip()
            or bool(item.get("sku_missing_in_source"))
        )
    )


def extract_expected_product_count(text: str) -> int | None:
    match = re.search(r"\bTotal\s+(\d+)\s+products?\b", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def validate_shopee_promotion_evidence(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        if item.get("promotion_metadata_status") == "incomplete":
            reason = str(item.get("promotion_incomplete_reason") or "Promotion source evidence is incomplete.")
            return f"INCOMPLETE_PROMOTION_EVIDENCE: {reason}"
    return None


def validate_shopee_product_amounts(
    items: list[dict[str, Any]],
    merchandise_subtotal: Any,
    refund_amount: Any = None,
    *,
    product_price: Any = None,
) -> str | None:
    totals: list[Decimal] = []
    seen_promotion_groups: set[str] = set()
    for index, item in enumerate(items, start=1):
        group_id = str(item.get("promotion_group_id") or "").strip()
        source_group_total = _decimal_value(item.get("source_group_total"))
        if group_id and source_group_total is not None:
            if group_id not in seen_promotion_groups:
                totals.append(source_group_total)
                seen_promotion_groups.add(group_id)
            # Complete promotion containers reconcile at group level. Individual
            # members intentionally do not have source line subtotals.
            continue

        quantity = parse_quantity(item.get("quantity"))
        unit_price = _decimal_value(item.get("unit_price"))
        line_total = _decimal_value(item.get("line_total"))
        if line_total is None:
            line_total = _decimal_value(item.get("source_line_subtotal"))
        if line_total is None or unit_price is None or quantity <= 0:
            continue
        totals.append(line_total)
        expected = unit_price * Decimal(quantity)
        if abs(expected - line_total) > MONEY_TOLERANCE and not _has_explicit_shopee_promotion(item):
            return (
                "Product Amount Reconciliation Failed: "
                f"Item {index} quantity x unit price is {expected:.2f}, "
                f"but line subtotal is {line_total:.2f}."
            )

    seller_subtotal = _decimal_value(merchandise_subtotal)
    if seller_subtotal is None or not totals:
        return None
    extracted_total = sum(totals, Decimal("0"))
    source_product_price = _decimal_value(product_price)
    if source_product_price is not None and abs(extracted_total - source_product_price) > MONEY_TOLERANCE:
        return (
            "Product Amount Reconciliation Failed: "
            f"extracted source subtotals total {extracted_total:.2f}, "
            f"but seller Product Price is {source_product_price:.2f}."
        )
    signed_refund = _decimal_value(refund_amount)
    product_base = source_product_price if source_product_price is not None else extracted_total
    reconciled_total = product_base + (
        signed_refund if signed_refund is not None else Decimal("0")
    )
    if abs(reconciled_total - seller_subtotal) > MONEY_TOLERANCE:
        refund_detail = (
            f" plus explicit Refund Amount {signed_refund:.2f}"
            if signed_refund is not None
            else ""
        )
        return (
            "Product Amount Reconciliation Failed: "
            f"extracted source subtotals total {extracted_total:.2f}{refund_detail}, "
            f"but seller Merchandise Subtotal is {seller_subtotal:.2f}."
        )
    return None


def validate_shopee_financial_reconciliation(
    income: dict[str, str],
    refund_amount: Any = None,
    *,
    layout: str = NORMAL_ORDER,
) -> str | None:
    required_fields = ["merchandise_subtotal", "product_price", "shipping_subtotal", "order_income"]
    if layout != RETURN_REFUND:
        required_fields.append("fees_charges_total")
    if any(_is_missing_money(income.get(field)) for field in required_fields):
        return None

    required_values = {
        field: _decimal_value(income.get(field))
        for field in required_fields
    }
    if any(value is None for value in required_values.values()):
        return "Financial Reconciliation Failed: a seller financial component is not numeric."

    shipping_fields = (
        "shipping_fee_paid_by_buyer",
        "shipping_fee_charged_by_logistic_provider",
        "shipping_fee_rebate_from_shopee",
        "seller_paid_shipping_fee_sst",
        "reverse_shipping_fee",
        "reverse_shipping_fee_sst",
    )
    shipping_values = _present_decimal_values(income, shipping_fields)
    if isinstance(shipping_values, str):
        return shipping_values
    if shipping_values:
        shipping_components = sum(shipping_values, Decimal("0"))
        if abs(shipping_components - required_values["shipping_subtotal"]) > MONEY_TOLERANCE:
            return (
                "Financial Reconciliation Failed: "
                f"source-present shipping components total {shipping_components:.2f}, "
                f"but Shipping Subtotal is {required_values['shipping_subtotal']:.2f}."
            )

    fee_fields = (
        "commission_fee",
        "service_fee",
        "transaction_fee",
        "ams_commission_fee",
        "ads_escrow_top_up_fee",
    )
    fee_values = _present_decimal_values(income, fee_fields)
    if isinstance(fee_values, str):
        return fee_values
    fees_aggregate = _decimal_value(income.get("fees_charges_total"))
    if fees_aggregate is not None and fee_values:
        fee_components = sum(fee_values, Decimal("0"))
        if abs(fee_components - fees_aggregate) > MONEY_TOLERANCE:
            return (
                "Financial Reconciliation Failed: "
                f"source-present fee components total {fee_components:.2f}, "
                f"but Fees & Charges is {fees_aggregate:.2f}."
            )

    applicable_fees = fees_aggregate
    if applicable_fees is None:
        applicable_fees = sum(fee_values, Decimal("0"))
    expected_income = (
        required_values["merchandise_subtotal"]
        + required_values["shipping_subtotal"]
        + applicable_fees
    )
    vouchers = income.get("vouchers_rebates_total")
    if not _is_missing_money(vouchers):
        voucher_value = _decimal_value(vouchers)
        if voucher_value is None:
            return "Financial Reconciliation Failed: Vouchers & Rebates is not numeric."
        expected_income += voucher_value

    order_income = required_values["order_income"]
    if abs(expected_income - order_income) > MONEY_TOLERANCE:
        return (
            "Financial Reconciliation Failed: "
            f"seller components total {expected_income:.2f}, "
            f"but Order Income is {order_income:.2f}."
        )
    return None


def _present_decimal_values(
    values: dict[str, str], fields: tuple[str, ...]
) -> list[Decimal] | str:
    result: list[Decimal] = []
    for field in fields:
        raw = values.get(field)
        if _is_missing_money(raw):
            continue
        parsed = _decimal_value(raw)
        if parsed is None:
            label = field.replace("_", " ").title()
            return f"Financial Reconciliation Failed: {label} is not numeric."
        result.append(parsed)
    return result


def _has_decimal_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).replace(",", "").replace("RM", "").strip()
    if text == "":
        return False
    try:
        Decimal(text)
    except InvalidOperation:
        return False
    return True


def _decimal_value(value: Any) -> Decimal | None:
    if _is_missing_money(value):
        return None
    text = str(value).replace(",", "").replace("RM", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _is_missing_money(value: Any) -> bool:
    return value is None or str(value).strip() in ("", "N/A")


def _has_explicit_shopee_promotion(item: dict[str, Any]) -> bool:
    return bool(
        re.search(
            r"\bAny\s+\d+\s+(?:at\s+RM\s*[\d,]+(?:\.\d+)?|enjoy\s+[\d,]+(?:\.\d+)?\s*%\s*off)",
            str(item.get("promotion", "")),
            flags=re.IGNORECASE,
        )
    )
