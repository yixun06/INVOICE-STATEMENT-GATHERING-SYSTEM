from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..utils.normalize import parse_decimal, parse_quantity


INCOME_FIELD_BY_PLATFORM = {
    "Shopee": "order_income",
    "Lazada": "net_paid",
    "ZENXIN": "total",
}


def compute_overall_dashboard(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    pdf_count: int,
) -> dict[str, str | int]:
    return {
        "pdf_count": pdf_count,
        "order_count": len(orders),
        "product_rows": len(products),
        "total_quantity": sum(parse_quantity(row.get("quantity")) for row in products),
        "income": _money(_sum_income(orders)),
    }


def compute_platform_dashboard(
    platform_orders: list[dict[str, Any]],
    platform_products: list[dict[str, Any]],
) -> dict[str, str | int]:
    """Return only the KPI values rendered in a platform dashboard tab."""
    return {
        "orders": len(platform_orders),
        "products": len(platform_products),
        "quantity": sum(parse_quantity(row.get("quantity")) for row in platform_products),
        "income": _money(_sum_income(platform_orders)),
    }


def compute_platform_kpis(
    platform_orders: list[dict[str, Any]],
    platform_products: list[dict[str, Any]],
) -> dict[str, str | int]:
    gross_sales = _sum_money(platform_orders, "gross_sales")
    fees = _sum_money(platform_orders, "platform_fees") + _sum_money(platform_orders, "voucher")
    net_amount = _sum_money(platform_orders, "net_amount")
    return {
        "orders": len(platform_orders),
        "products": len(platform_products),
        "quantity": sum(parse_quantity(row.get("quantity")) for row in platform_products),
        "gross_sales": _money(gross_sales),
        "total_fees": _money(fees),
        "net_amount": _money(net_amount),
    }


def _sum_money(rows: list[dict[str, Any]], field: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        value = str(row.get(field, "")).strip()
        if value == "":
            continue
        total += parse_decimal(value)
    return total


def _sum_income(rows: list[dict[str, Any]]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        field = INCOME_FIELD_BY_PLATFORM.get(str(row.get("platform", "")).strip())
        if field is None:
            continue
        total += parse_decimal(row.get(field))
    return total


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))
