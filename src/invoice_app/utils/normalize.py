from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def parse_decimal(value: str | float | int | Decimal | None) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    cleaned = str(value).replace(",", "").replace("RM", "").replace("S$", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if match:
            return Decimal(match.group(0))
        return Decimal("0")


def parse_quantity(value: str | int | float | None) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    cleaned = str(value).replace(",", "").strip()
    digits = re.search(r"\d+", cleaned)
    if not digits:
        return 0
    qty = int(digits.group(0))
    return qty if qty > 0 else 0
