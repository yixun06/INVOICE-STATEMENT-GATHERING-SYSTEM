"""Presentation-only monetary formatting helpers.

These helpers never change domain or persistence values.  Callers keep numeric
values for calculation, sorting, and export cells, and use the returned formats
only at a presentation boundary.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


_CURRENCY_SYMBOLS = {
    "MYR": "RM",
    "RM": "RM",
    "SGD": "S$",
    "S$": "S$",
}


def currency_symbol(currency: str = "MYR") -> str:
    """Return the approved display symbol for a supported currency."""

    normalized = str(currency).strip().upper()
    try:
        return _CURRENCY_SYMBOLS[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported currency: {currency!r}") from error


def format_currency(
    value: int | float | Decimal | None,
    currency: str = "MYR",
    *,
    missing_value: Any = None,
) -> str | Any:
    """Format a numeric value without converting missing evidence to zero."""

    if value is None or isinstance(value, bool):
        return missing_value
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Currency value must be numeric: {value!r}") from error
    if not amount.is_finite():
        return missing_value
    return f"{currency_symbol(currency)} {amount:,.2f}"


def streamlit_currency_format(currency: str = "MYR") -> str:
    """Return a Streamlit NumberColumn format that keeps source data numeric."""

    return f"{currency_symbol(currency)} %,.2f"


def excel_currency_format(currency: str = "MYR") -> str:
    """Return an Excel numeric format with grouping and two decimals."""

    symbol = currency_symbol(currency).replace('"', '""')
    return f'"{symbol}" #,##0.00;[Red]"{symbol}" -#,##0.00'
