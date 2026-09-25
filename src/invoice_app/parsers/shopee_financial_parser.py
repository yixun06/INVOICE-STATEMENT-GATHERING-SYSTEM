from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Iterable, Mapping, TYPE_CHECKING

from ..domain.invoice_adjustment import (
    ADJUSTMENT_EVIDENCE_COMPLETE,
    ADJUSTMENT_EVIDENCE_INCOMPLETE,
    ORDER_ADJUSTMENT,
    InvoiceAdjustmentEvidence,
)
from ..utils.normalize import normalize_whitespace, parse_decimal

if TYPE_CHECKING:
    from ..pdf_document import PdfDocument


MONEY_PATTERN = r"[-+]?\s*RM\s*[-+]?\s*[\d,]+(?:\.\d+)?"
MISSING_FINANCIAL_VALUE = "N/A"
NORMAL_ORDER = "NORMAL_ORDER"
RETURN_REFUND = "RETURN_REFUND"
UNKNOWN_OR_MIXED = "UNKNOWN_OR_MIXED"
RETURN_REFUND_AFTER_ORDER_COMPLETED = "RETURN_REFUND_AFTER_ORDER_COMPLETED"

_ORDER_ADJUSTMENT_HEADER = re.compile(
    r"\bAdjustment\s+Complete\s+Date\s+Adjustment\s+Reason\s+Released\s+Amount\b",
    flags=re.IGNORECASE,
)
_ADJUSTMENT_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_TOTAL_ADJUSTMENT_AMOUNT = re.compile(
    rf"\bTotal\s+Adjustment\s+Amount\b\s*:?[ \t]*({MONEY_PATTERN})",
    flags=re.IGNORECASE,
)
_NO_ADJUSTMENT = re.compile(
    r"\bNo\s+adjustment\s+has\s+been\s+made\s+to\s+this\s+order\s+yet\b",
    flags=re.IGNORECASE,
)
_ADJUSTMENT_SECTION_END = re.compile(
    r"^\s*(?:Buyer\s+Payment|Order\s+History|Home\s+My\s+Orders|Final\s+Amount)\b",
    flags=re.IGNORECASE | re.MULTILINE,
)

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
    "reverse_shipping_fee": ("Reverse Shipping Fee",),
    "reverse_shipping_fee_sst": ("Reverse Shipping Fee SST",),
    "vouchers_rebates_total": ("Vouchers & Rebates",),
    "fees_charges_total": ("Fees & Charges",),
    "commission_fee": ("Commission Fee",),
    "service_fee": ("Service Fee",),
    "transaction_fee": ("Transaction Fee",),
    "ams_commission_fee": ("AMS Commission Fee",),
    "ads_escrow_top_up_fee": ("Ads Escrow Top Up Fee",),
    "estimated_order_income": ("Estimated Order Income",),
    "final_amount": ("Final Amount",),
}

NORMAL_ORDER_REQUIRED_INCOME_DETAIL_FIELDS = (
    "merchandise_subtotal",
    "product_price",
)

CONDITIONAL_TOP_LEVEL_INCOME_FIELDS = (
    "shipping_subtotal",
    "vouchers_rebates_total",
    "fees_charges_total",
)

RETURN_REFUND_REQUIRED_INCOME_DETAIL_FIELDS = (
    "merchandise_subtotal",
    "product_price",
)

# Backwards-compatible public name for callers that only describe normal orders.
REQUIRED_INCOME_DETAIL_FIELDS = NORMAL_ORDER_REQUIRED_INCOME_DETAIL_FIELDS


def parse_income_details(text: str, *, document: PdfDocument | None = None) -> dict[str, str]:
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
    result["final_amount"] = extract_final_amount(text, document=document)
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


def final_amount_label_present(text: str) -> bool:
    return bool(re.search(r"\bFinal\s+Amount\b", text, flags=re.IGNORECASE))


def extract_final_amount(text: str, *, document: PdfDocument | None = None) -> str:
    match = re.search(rf"\bFinal\s+Amount\b(?:\s*\([^\n)]*\))?\s*:?\s*({MONEY_PATTERN})", text, flags=re.IGNORECASE)
    if match:
        return money_to_string(match.group(1))
    if document is None:
        return MISSING_FINANCIAL_VALUE
    for page in document.pages:
        words = page.styled_words
        for index, word in enumerate(words[:-1]):
            if word.text.casefold() != "final" or words[index + 1].text.casefold() != "amount":
                continue
            label = words[index + 1]
            candidates = [candidate for candidate in words if candidate.x0 >= label.x1 and abs(candidate.top - word.top) <= 4 and re.fullmatch(MONEY_PATTERN, candidate.text.replace(" ", ""), flags=re.IGNORECASE)]
            if len(candidates) == 1:
                return money_to_string(candidates[0].text)
    return MISSING_FINANCIAL_VALUE


def extract_refund_amount(text: str) -> Decimal | None:
    """Return only the amount explicitly labelled ``Refund Amount`` in the source."""
    match = re.search(
        rf"\bRefund\s+Amount\b(?:\s*\([^\n)]*\))?\s*:?\s*({MONEY_PATTERN})",
        text,
        flags=re.IGNORECASE,
    )
    return parse_decimal(match.group(1)).quantize(Decimal("0.01")) if match else None


def extract_post_order_return_refund_adjustment(
    text: str,
) -> tuple[str, str, str, Decimal] | None:
    """Return the legacy one-record view of structured Invoice Adjustments.

    New validation consumes :func:`extract_invoice_adjustment_evidence`.  This
    wrapper keeps existing staging and UI contracts stable without restricting
    detection to one source wording.
    """
    return legacy_invoice_adjustment_projection(
        extract_invoice_adjustment_evidence(text)
    )


def legacy_invoice_adjustment_projection(
    events: tuple[InvoiceAdjustmentEvidence, ...],
) -> tuple[str, str, str, Decimal] | None:
    """Expose one event only for legacy non-persistence consumers.

    Multiple or incomplete source events deliberately have no singular view:
    joining source reasons, picking one date, or assigning a refund taxonomy
    would fabricate a business event.  Structured evidence remains the
    validation authority.
    """
    if len(events) != 1 or not events[0].is_complete:
        return None
    event = events[0]
    return (
        ORDER_ADJUSTMENT,
        event.source_reason or "",
        event.adjustment_complete_date or "",
        event.signed_amount,
    )


def extract_invoice_adjustment_evidence(
    text: str,
) -> tuple[InvoiceAdjustmentEvidence, ...]:
    """Extract zero or more rows from source-labelled Order Adjustment tables.

    Rows are recognized from table structure (header, date, released amount,
    and section total), not from an allow-list of Adjustment Reason wording.
    Exact source reasons and signed amounts are retained.
    """
    headers = tuple(_ORDER_ADJUSTMENT_HEADER.finditer(text))
    events: list[InvoiceAdjustmentEvidence] = []
    for section_index, header in enumerate(headers, start=1):
        candidates = [match.start() for match in headers[section_index:]]
        section_end = _ADJUSTMENT_SECTION_END.search(text, header.end())
        if section_end is not None:
            candidates.append(section_end.start())
        end = min(candidates) if candidates else len(text)
        section = text[header.end():end]
        if _is_explicit_no_adjustment_section(section):
            continue

        total_match = _TOTAL_ADJUSTMENT_AMOUNT.search(section)
        total = (
            parse_decimal(total_match.group(1)).quantize(Decimal("0.01"))
            if total_match is not None
            else None
        )
        rows_text = (
            section[: total_match.start()] if total_match is not None else section
        )
        date_matches = list(_ADJUSTMENT_DATE.finditer(rows_text))
        source_label = normalize_whitespace(header.group(0))

        if not date_matches:
            if normalize_whitespace(rows_text):
                events.append(
                    InvoiceAdjustmentEvidence(
                        semantic_type=ORDER_ADJUSTMENT,
                        source_label=source_label,
                        source_reason=_meaningful_adjustment_text(rows_text),
                        adjustment_complete_date=None,
                        signed_amount=None,
                        total_adjustment_amount=total,
                        source_locator=f"Order Adjustment section {section_index}",
                        section_index=section_index,
                        completeness=ADJUSTMENT_EVIDENCE_INCOMPLETE,
                    )
                )
            continue

        for row_index, date_match in enumerate(date_matches, start=1):
            row_end = (
                date_matches[row_index].start()
                if row_index < len(date_matches)
                else len(rows_text)
            )
            row = rows_text[date_match.end():row_end]
            amount_match = re.search(MONEY_PATTERN, row, flags=re.IGNORECASE)
            if amount_match is None:
                reason = _meaningful_adjustment_text(row)
                amount = None
            else:
                before = row[: amount_match.start()]
                after = row[amount_match.end() :]
                reason_parts = [part for part in (
                    _meaningful_adjustment_text(before),
                    _meaningful_adjustment_text(after),
                ) if part]
                reason = normalize_whitespace(" ".join(reason_parts)) or None
                amount = parse_decimal(amount_match.group(0)).quantize(Decimal("0.01"))
            complete = bool(reason) and amount is not None
            events.append(
                InvoiceAdjustmentEvidence(
                    semantic_type=ORDER_ADJUSTMENT,
                    source_label=source_label,
                    source_reason=reason,
                    adjustment_complete_date=normalize_whitespace(date_match.group(0)),
                    signed_amount=amount,
                    total_adjustment_amount=total,
                    source_locator=(
                        f"Order Adjustment section {section_index}, row {row_index}"
                    ),
                    section_index=section_index,
                    completeness=(
                        ADJUSTMENT_EVIDENCE_COMPLETE
                        if complete
                        else ADJUSTMENT_EVIDENCE_INCOMPLETE
                    ),
                )
            )
    return tuple(events)


def _meaningful_adjustment_text(value: str) -> str | None:
    """Keep row wording while excluding known navigation/page artefacts."""
    parts = [
        normalize_whitespace(line)
        for line in value.splitlines()
        if _is_adjustment_reason_line(line)
    ]
    normalized = normalize_whitespace(" ".join(parts))
    return normalized or None


def _is_explicit_no_adjustment_section(section: str) -> bool:
    lines = [normalize_whitespace(line) for line in section.splitlines()]
    meaningful = [line for line in lines if line]
    return bool(meaningful) and all(_NO_ADJUSTMENT.fullmatch(line) for line in meaningful)


def _is_adjustment_reason_line(line: str) -> bool:
    normalized = normalize_whitespace(line)
    if not normalized or not re.search(r"[A-Za-z\u4e00-\u9fff]", normalized):
        return False
    return not bool(
        re.fullmatch(
            r"(?:Home\s+My\s+Orders|Order\s+History|Buyer\s+Payment|"
            r"Page\s+\d+\s+(?:of|/)\s*\d+)",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def income_label_presence(text: str) -> frozenset[str]:
    """Return exact supported labels visible inside the seller Income section."""
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
    return frozenset(
        field
        for field, aliases in INCOME_ALIASES.items()
        if any(_alias_label_visible(section, alias) for alias in aliases)
    )


def classify_invoice_financial_layout(
    text: str,
    *,
    label_presence: frozenset[str] | None = None,
    product_items: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    """Classify only from independent, source-visible refund signals."""
    labels = label_presence if label_presence is not None else income_label_presence(text)
    return classify_invoice_financial_layout_from_signals(
        invoice_financial_layout_signals(
            text,
            label_presence=labels,
            product_items=product_items,
        )
    )


def invoice_financial_layout_signals(
    text: str,
    *,
    label_presence: frozenset[str] | None = None,
    product_items: Iterable[Mapping[str, Any]] | None = None,
) -> frozenset[str]:
    labels = label_presence if label_presence is not None else income_label_presence(text)
    refund_amount = extract_refund_amount(text)
    signals = {
        name
        for name, present in {
            "refund_amount": refund_amount is not None and refund_amount != 0,
            # Original transaction-layout evidence is independent from later
            # Invoice Adjustment events and must never be suppressed by them.
            "return_refund_marker": _has_structured_return_refund_marker(
                product_items or ()
            ),
            "reverse_shipping_fee": "reverse_shipping_fee" in labels,
            "reverse_shipping_fee_sst": "reverse_shipping_fee_sst" in labels,
        }.items()
        if present
    }
    return frozenset(signals)


def classify_invoice_financial_layout_from_signals(signals: frozenset[str]) -> str:
    additional_direct_evidence = {
        "return_refund_marker",
        "reverse_shipping_fee",
        "reverse_shipping_fee_sst",
    }
    if "refund_amount" in signals and signals.intersection(
        additional_direct_evidence
    ):
        return RETURN_REFUND
    if signals:
        return UNKNOWN_OR_MIXED
    return NORMAL_ORDER


def _has_structured_return_refund_marker(
    product_items: Iterable[Mapping[str, Any]],
) -> bool:
    for item in product_items:
        if str(item.get("_source_return_refund_error") or "").strip():
            continue
        quantity = item.get("source_return_refund_quantity")
        if (
            isinstance(quantity, int)
            and not isinstance(quantity, bool)
            and quantity > 0
        ):
            return True
    return False


def missing_income_detail_fields(
    text: str,
    income: dict[str, str],
    *,
    layout: str = NORMAL_ORDER,
    label_presence: frozenset[str] | None = None,
    refund_amount: Decimal | None = None,
) -> list[str]:
    missing: list[str] = []
    if not re.search(r"(?:Hide\s+)?Income Details", text, flags=re.IGNORECASE):
        missing.append("Income Details section")

    required_fields = (
        RETURN_REFUND_REQUIRED_INCOME_DETAIL_FIELDS
        if layout == RETURN_REFUND
        else NORMAL_ORDER_REQUIRED_INCOME_DETAIL_FIELDS
    )
    for field in required_fields:
        if is_missing_financial_value(income.get(field)):
            missing.append(INCOME_ALIASES[field][0])
    if layout == NORMAL_ORDER:
        for field in CONDITIONAL_TOP_LEVEL_INCOME_FIELDS:
            visible = (
                field in label_presence
                if label_presence is not None
                else not is_missing_financial_value(income.get(field))
            )
            if visible and is_missing_financial_value(income.get(field)):
                missing.append(INCOME_ALIASES[field][0])
    if layout == RETURN_REFUND:
        if refund_amount is None:
            missing.append("Refund Amount")
        if (
            is_missing_financial_value(income.get("order_income"))
            or str(income.get("income_type", "")).strip() != "Final"
        ):
            missing.append("Order Income")
    elif is_missing_financial_value(income.get("order_income")):
        missing.append("Estimated Order Income or Order Income")

    if (
        label_presence is not None
        and "final_amount" in label_presence
        and is_missing_financial_value(income.get("final_amount"))
    ):
        missing.append(INCOME_ALIASES["final_amount"][0])
    return missing


def _alias_label_visible(text: str, alias: str) -> bool:
    suffix = r"(?!\s+SST\b)" if alias.casefold() == "reverse shipping fee" else ""
    prefix = r"(?<!AMS\s)" if alias.casefold() == "commission fee" else ""
    return bool(re.search(rf"{prefix}{re.escape(alias)}{suffix}", text, flags=re.IGNORECASE))


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
        income.get("ams_commission_fee", ""),
        income.get("ads_escrow_top_up_fee", ""),
    ]
    present = [value for value in values if not is_missing_financial_value(value)]
    if not present:
        return MISSING_FINANCIAL_VALUE
    total = sum((parse_decimal(value) for value in present), Decimal("0"))
    return str(total.quantize(Decimal("0.01")))


def extract_alias_money(text: str, aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        prefix = r"(?<!AMS\s)" if alias.casefold() == "commission fee" else ""
        match = re.search(
            rf"{prefix}{re.escape(alias)}(?:\s*\([^\n)]*\))?\s*:?\s*({MONEY_PATTERN})",
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
