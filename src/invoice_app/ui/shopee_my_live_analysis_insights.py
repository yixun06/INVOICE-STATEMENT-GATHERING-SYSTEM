"""Deterministic management copy for Shopee MY Live Analysis."""

from __future__ import annotations

from decimal import Decimal

from src.invoice_app.services.shopee_my_live_analysis import KpiValues


_STABLE_THRESHOLD = Decimal("5")


def build_management_insight(current: KpiValues, previous: KpiValues) -> str | None:
    """Summarise KPI relationships without adding new business inference."""

    sales = _percent_change(current.total_sales, previous.total_sales)
    orders = _percent_change(Decimal(current.orders), Decimal(previous.orders))
    aov = _percent_change(current.average_order_value, previous.average_order_value)
    if sales is None or orders is None or aov is None:
        return None

    sales_stable = abs(sales) <= _STABLE_THRESHOLD
    orders_stable = abs(orders) <= _STABLE_THRESHOLD
    orders_down = orders < -_STABLE_THRESHOLD
    orders_up = orders > _STABLE_THRESHOLD
    aov_up = aov > _STABLE_THRESHOLD

    if sales_stable and orders_down and aov_up:
        return (
            f"Sales remained relatively stable ({sales:+.1f}%) despite "
            f"{abs(orders):.1f}% fewer orders, supported by a {aov:.1f}% increase "
            "in average order value."
        )
    if sales > _STABLE_THRESHOLD and orders_down and aov_up:
        return (
            "Sales increased despite lower order volume, supported by a higher "
            "average order value."
        )
    if sales > _STABLE_THRESHOLD and orders_up:
        return "Sales increased alongside higher order volume."
    if sales < -_STABLE_THRESHOLD and orders_down:
        return "Sales declined as order volume decreased."
    if sales_stable and orders_stable:
        return "Sales and order volume were broadly stable versus the previous period."
    return "Sales performance was mixed versus the previous comparable period."


def _percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return (current - previous) / previous * Decimal("100")
