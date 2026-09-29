"""Read-only Monthly Billing adapter over committed full-calendar-month evidence.

This module deliberately adapts Monthly source rows to the stable Weekly Billing
calculation contract.  The public Weekly production path remains unchanged.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from typing import Any, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.domain.weekly_billing import BillingPeriod, WeeklyBillingReport
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    HistoricalInvoiceStorageError,
    _deserialize_item,
    _deserialize_order,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    MONTHLY_STATEMENT_DATA_HEADERS,
    MONTHLY_STATEMENT_DATA_TAB,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
    MONTHLY_STATEMENT_SUMMARY_HEADERS,
    MONTHLY_STATEMENT_SUMMARY_TAB,
)
from src.invoice_app.services.weekly_billing import (
    WeeklyBillingDataset,
    WeeklyBillingError,
    build_weekly_billing_report,
)


MONTHLY_SOURCE_TABS = (
    INVOICE_ORDERS_TAB,
    INVOICE_ITEMS_TAB,
    MONTHLY_STATEMENT_DATA_TAB,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
    MONTHLY_STATEMENT_SUMMARY_TAB,
)


class MonthlyBillingError(WeeklyBillingError):
    """Monthly Billing cannot be proven from committed monthly source facts."""


class MonthlyBillingGateway(Protocol):
    def read_tabs(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, Sequence[Sequence[Any]]]: ...


class GoogleSheetsMonthlyBillingReader:
    """Load exactly the five Monthly Billing source tabs without mutating UAT2."""

    def __init__(self, *, spreadsheet_id: str, gateway: MonthlyBillingGateway) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway

    def load_dataset(self) -> WeeklyBillingDataset:
        try:
            values = self._gateway.read_tabs(self._spreadsheet_id, MONTHLY_SOURCE_TABS)
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "Monthly Billing source snapshot read failed."
            ) from error
        return build_monthly_billing_dataset(values)


def build_monthly_billing_dataset(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> WeeklyBillingDataset:
    """Validate Monthly source tabs and adapt them to the stable report model."""

    statement_rows = _tab_rows(
        tabs, MONTHLY_STATEMENT_DATA_TAB, MONTHLY_STATEMENT_DATA_HEADERS
    )
    order_rows = _tab_rows(tabs, INVOICE_ORDERS_TAB, INVOICE_ORDERS_HEADERS)
    item_rows = _tab_rows(tabs, INVOICE_ITEMS_TAB, INVOICE_ITEMS_HEADERS)
    summary_rows = _tab_rows(
        tabs, MONTHLY_STATEMENT_SUMMARY_TAB, MONTHLY_STATEMENT_SUMMARY_HEADERS
    )
    component_rows = _tab_rows(
        tabs,
        MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
        MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    )
    periods, order_ids_by_batch = _committed_monthly_periods(statement_rows)

    orders: dict[str, CanonicalInvoiceOrder] = {}
    for row_number, row in order_rows:
        order = _deserialize_order(row, row_number)
        if order.platform != "Shopee":
            continue
        if order.order_id in orders:
            raise MonthlyBillingError(
                f"Invoice_Orders duplicates Shopee Order {order.order_id}."
            )
        orders[order.order_id] = order

    items: list[CanonicalInvoiceItem] = []
    identities: set[tuple[str, int]] = set()
    for row_number, row in item_rows:
        item = _deserialize_item(row, row_number)
        if item.platform != "Shopee":
            continue
        identity = (item.order_id, item.item_index)
        if identity in identities:
            raise MonthlyBillingError(
                "Invoice_Items duplicates authoritative item "
                f"{item.order_id}/{item.item_index}."
            )
        identities.add(identity)
        items.append(item)

    return WeeklyBillingDataset(
        periods=periods,
        order_ids_by_batch=order_ids_by_batch,
        orders=orders,
        items=tuple(items),
        statement_summary_rows=summary_rows,
        financial_component_rows=component_rows,
    )


def build_monthly_billing_report(
    dataset: WeeklyBillingDataset, period: BillingPeriod
) -> WeeklyBillingReport:
    """Use the unchanged Billing calculations for one committed calendar month."""

    try:
        return build_weekly_billing_report(dataset, period)
    except WeeklyBillingError as error:
        raise MonthlyBillingError(str(error)) from error


def month_label(period: BillingPeriod) -> str:
    """Return the stable human selector text for one approved calendar month."""

    return period.statement_period_from.strftime("%B %Y")


def _committed_monthly_periods(
    statement_rows: Sequence[tuple[int, tuple[Any, ...]]],
) -> tuple[tuple[BillingPeriod, ...], Mapping[str, tuple[str, ...]]]:
    positions = {
        name: index for index, name in enumerate(MONTHLY_STATEMENT_DATA_HEADERS)
    }
    batch_facts: dict[str, tuple[str, date, date, int, int]] = {}
    order_ids: dict[str, list[str]] = defaultdict(list)
    sku_counts: dict[str, int] = defaultdict(int)
    invalid_batches: set[str] = set()

    for row_number, row in statement_rows:
        if _text(row[positions["commit_status"]]) != "COMMITTED":
            continue
        batch_id = _required_text(
            row[positions["statement_batch_id"]], row_number, "statement_batch_id"
        )
        if _text(row[positions["validation_status"]]) != "PASSED":
            invalid_batches.add(batch_id)
            continue
        if _text(row[positions["platform"]]) != "Shopee":
            invalid_batches.add(batch_id)
            continue
        file_hash = _required_text(
            row[positions["statement_file_hash"]], row_number, "statement_file_hash"
        )
        period_from = _iso_date(
            row[positions["statement_period_from"]], row_number, "statement_period_from"
        )
        period_to = _iso_date(
            row[positions["statement_period_to"]], row_number, "statement_period_to"
        )
        if not _is_full_calendar_month(period_from, period_to):
            raise MonthlyBillingError(
                "Committed Monthly Statement batch "
                f"{batch_id} is not a full calendar month."
            )
        order_count = _positive_integer(
            row[positions["statement_order_count"]], row_number, "statement_order_count"
        )
        sku_count = _positive_integer(
            row[positions["statement_sku_count"]], row_number, "statement_sku_count"
        )
        facts = (file_hash, period_from, period_to, order_count, sku_count)
        prior = batch_facts.get(batch_id)
        if prior is not None and prior != facts:
            raise MonthlyBillingError(
                f"Committed Monthly Statement batch {batch_id} has conflicting audit facts."
            )
        batch_facts[batch_id] = facts

        record_type = _text(row[positions["record_type"]])
        if record_type == "ORDER":
            order_id = _required_text(row[positions["order_id"]], row_number, "order_id")
            if order_id in order_ids[batch_id]:
                raise MonthlyBillingError(
                    f"Committed Monthly Statement batch {batch_id} duplicates ORDER {order_id}."
                )
            order_ids[batch_id].append(order_id)
        elif record_type == "SKU":
            sku_counts[batch_id] += 1

    for batch_id in invalid_batches:
        batch_facts.pop(batch_id, None)
        order_ids.pop(batch_id, None)
        sku_counts.pop(batch_id, None)

    periods = []
    for batch_id, (file_hash, period_from, period_to, expected_orders, expected_skus) in batch_facts.items():
        actual_orders = len(order_ids.get(batch_id, ()))
        if actual_orders != expected_orders:
            raise MonthlyBillingError(
                f"Committed Monthly Statement batch {batch_id} declares {expected_orders} Orders "
                f"but contains {actual_orders} committed ORDER rows."
            )
        actual_skus = sku_counts.get(batch_id, 0)
        if actual_skus != expected_skus:
            raise MonthlyBillingError(
                f"Committed Monthly Statement batch {batch_id} declares {expected_skus} SKUs "
                f"but contains {actual_skus} committed SKU rows."
            )
        periods.append(BillingPeriod(period_from, period_to, batch_id, file_hash))

    by_month: dict[tuple[int, int], list[BillingPeriod]] = defaultdict(list)
    for period in periods:
        by_month[(period.statement_period_from.year, period.statement_period_from.month)].append(
            period
        )
    duplicates = [values for values in by_month.values() if len(values) > 1]
    if duplicates:
        period = duplicates[0][0]
        raise MonthlyBillingError(
            "BUSINESS DECISION REQUIRED â€” DUPLICATE MONTHLY STATEMENT MONTH: "
            f"{month_label(period)} has multiple COMMITTED batches."
        )

    sorted_periods = tuple(
        sorted(
            periods,
            key=lambda value: (
                value.statement_period_to,
                value.statement_period_from,
                value.statement_batch_id,
            ),
            reverse=True,
        )
    )
    return sorted_periods, {
        batch_id: tuple(values) for batch_id, values in order_ids.items() if batch_id in batch_facts
    }


def _tab_rows(
    tabs: Mapping[str, Sequence[Sequence[Any]]], tab: str, headers: Sequence[str]
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    if tab not in tabs:
        raise MonthlyBillingError(f"Required Monthly Billing source tab {tab} is missing.")
    rows = tuple(tuple(row) for row in tabs[tab])
    if not rows or tuple(_text(value) for value in rows[0]) != tuple(headers):
        raise MonthlyBillingError(f"{tab} header does not match the locked schema.")
    result = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) > len(headers) and any(_text(value) for value in row[len(headers) :]):
            raise MonthlyBillingError(f"{tab} row {row_number} exceeds the locked schema.")
        padded = row + (None,) * max(0, len(headers) - len(row))
        if any(_text(value) for value in padded):
            result.append((row_number, padded[: len(headers)]))
    return tuple(result)


def _is_full_calendar_month(period_from: date, period_to: date) -> bool:
    return (
        period_from.day == 1
        and period_from.year == period_to.year
        and period_from.month == period_to.month
        and period_to.day == calendar.monthrange(period_to.year, period_to.month)[1]
    )


def _positive_integer(value: Any, row_number: int, field: str) -> int:
    try:
        parsed = int(_text(value))
    except ValueError as error:
        raise MonthlyBillingError(
            f"Monthly_Statement_Data row {row_number} has invalid {field}."
        ) from error
    if parsed <= 0:
        raise MonthlyBillingError(
            f"Monthly_Statement_Data row {row_number} has invalid {field}."
        )
    return parsed


def _iso_date(value: Any, row_number: int, field: str) -> date:
    try:
        return date.fromisoformat(_text(value))
    except ValueError as error:
        raise MonthlyBillingError(
            f"Monthly_Statement_Data row {row_number} has invalid {field}."
        ) from error


def _required_text(value: Any, row_number: int, field: str) -> str:
    text = _text(value)
    if not text:
        raise MonthlyBillingError(
            f"Monthly_Statement_Data row {row_number} has blank {field}."
        )
    return text


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
