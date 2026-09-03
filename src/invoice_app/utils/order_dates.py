from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any


def shopee_order_date_from_id(order_id: Any) -> date | None:
    """Return the date encoded by a valid Shopee YYMMDD Order ID prefix."""
    prefix = str(order_id or "").strip()[:6]
    if not re.fullmatch(r"\d{6}", prefix):
        return None
    try:
        return datetime.strptime(f"20{prefix}", "%Y%m%d").date()
    except ValueError:
        return None


def has_missing_source_date(value: Any) -> bool:
    return value is None or str(value).strip().casefold() in {"", "n/a", "none", "nan", "nat"}
