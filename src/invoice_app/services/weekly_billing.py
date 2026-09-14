"""Read-only Weekly Billing projection over committed Statement and Invoice data."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.domain.weekly_billing import (
    ActualSellingAmountBasis,
    BillingPeriod,
    BillingSourceItem,
    ProductSummaryRow,
    WeeklyBillingSummary,
)
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
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
)


CENT = Decimal("0.01")


class WeeklyBillingError(RuntimeError):
    """Billing cannot be proven from the committed source snapshot."""


class WeeklyBillingGateway(Protocol):
    def read_tabs(
        self, spreadsheet_id: str, tabs: Sequence[str]
    ) -> Mapping[str, Sequence[Sequence[Any]]]: ...


@dataclass(frozen=True)
class WeeklyBillingDataset:
    periods: tuple[BillingPeriod, ...]
    order_ids_by_batch: Mapping[str, tuple[str, ...]]
    orders: Mapping[str, CanonicalInvoiceOrder]
    items: tuple[CanonicalInvoiceItem, ...]


class GoogleSheetsWeeklyBillingReader:
    """Load the three existing source tabs without mutating UAT2."""

    def __init__(
        self, *, spreadsheet_id: str, gateway: WeeklyBillingGateway
    ) -> None:
        if not spreadsheet_id.strip():
            raise ValueError("spreadsheet_id must not be blank.")
        self._spreadsheet_id = spreadsheet_id.strip()
        self._gateway = gateway

    def load_dataset(self) -> WeeklyBillingDataset:
        tabs = (
            INVOICE_ORDERS_TAB,
            INVOICE_ITEMS_TAB,
            STATEMENT_DATA_TAB,
        )
        try:
            values = self._gateway.read_tabs(self._spreadsheet_id, tabs)
        except HistoricalInvoiceStorageError:
            raise
        except Exception as error:
            raise HistoricalInvoiceStorageError(
                "Weekly Billing source snapshot read failed."
            ) from error
        return build_weekly_billing_dataset(values)


def build_weekly_billing_dataset(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
) -> WeeklyBillingDataset:
    statement_rows = _tab_rows(tabs, STATEMENT_DATA_TAB, STATEMENT_DATA_HEADERS)
    order_rows = _tab_rows(tabs, INVOICE_ORDERS_TAB, INVOICE_ORDERS_HEADERS)
    item_rows = _tab_rows(tabs, INVOICE_ITEMS_TAB, INVOICE_ITEMS_HEADERS)

    periods, order_ids_by_batch = _committed_periods(statement_rows)

    orders: dict[str, CanonicalInvoiceOrder] = {}
    for row_number, row in order_rows:
        order = _deserialize_order(row, row_number)
        if order.platform != "Shopee":
            continue
        if order.order_id in orders:
            raise WeeklyBillingError(
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
            raise WeeklyBillingError(
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
    )


def build_weekly_billing_summary(
    dataset: WeeklyBillingDataset,
    period: BillingPeriod,
) -> WeeklyBillingSummary:
    available = next(
        (candidate for candidate in dataset.periods if candidate == period), None
    )
    if available is None:
        raise WeeklyBillingError(
            "The requested period is not an existing COMMITTED Statement batch."
        )
    order_ids = dataset.order_ids_by_batch.get(period.statement_batch_id, ())
    if not order_ids:
        raise WeeklyBillingError(
            f"Committed Statement batch {period.statement_batch_id} has no ORDER rows."
        )
    missing_orders = sorted(set(order_ids) - set(dataset.orders))
    if missing_orders:
        raise WeeklyBillingError(
            "Committed Statement Orders are missing from Invoice_Orders: "
            + ", ".join(missing_orders[:10])
        )

    wanted_orders = set(order_ids)
    qualifying = tuple(
        item for item in dataset.items if item.order_id in wanted_orders
    )
    by_order = {order_id: 0 for order_id in order_ids}
    for item in qualifying:
        by_order[item.order_id] += 1
    empty_orders = [order_id for order_id, count in by_order.items() if count == 0]
    if empty_orders:
        raise WeeklyBillingError(
            "Committed Statement Orders have no Invoice_Items: "
            + ", ".join(empty_orders[:10])
        )

    identities = [(item.order_id, item.item_index) for item in qualifying]
    if len(identities) != len(set(identities)):
        raise WeeklyBillingError("A qualifying Invoice item would be counted twice.")

    source_items, promotion_counts = _billing_source_items(qualifying)
    product_rows = _aggregate_source_items(source_items)
    _validate_controls(qualifying, source_items, product_rows)

    normal_total = sum(
        (
            item.actual_selling_amount
            for item in source_items
            if item.actual_selling_amount_basis is ActualSellingAmountBasis.DIRECT
        ),
        Decimal("0.00"),
    )
    promotion_total = sum(
        (
            item.actual_selling_amount
            for item in source_items
            if item.actual_selling_amount_basis is not ActualSellingAmountBasis.DIRECT
        ),
        Decimal("0.00"),
    )
    return WeeklyBillingSummary(
        period=period,
        order_count=len(order_ids),
        invoice_item_count=len(qualifying),
        source_items=source_items,
        product_rows=product_rows,
        total_quantity=sum(row.quantity for row in product_rows),
        total_standard_amount=sum(
            (item.standard_amount for item in source_items), Decimal("0.00")
        ),
        total_discount_amount=sum(
            (row.discount_amount for row in product_rows), Decimal("0.00")
        ),
        total_amount=sum((row.amount for row in product_rows), Decimal("0.00")),
        normal_amount_total=normal_total,
        promotion_amount_total=promotion_total,
        promotion_group_count=promotion_counts[0],
        same_price_promotion_count=promotion_counts[1],
        mixed_price_promotion_count=promotion_counts[2],
    )


def _committed_periods(
    statement_rows: Sequence[tuple[int, tuple[Any, ...]]],
) -> tuple[tuple[BillingPeriod, ...], Mapping[str, tuple[str, ...]]]:
    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    batch_facts: dict[str, tuple[str, date, date]] = {}
    order_ids: dict[str, list[str]] = defaultdict(list)
    expected_counts: dict[str, int] = {}

    for row_number, row in statement_rows:
        if _text(row[positions["commit_status"]]) != "COMMITTED":
            continue
        batch_id = _required_text(
            row[positions["statement_batch_id"]], row_number, "statement_batch_id"
        )
        file_hash = _required_text(
            row[positions["statement_file_hash"]], row_number, "statement_file_hash"
        )
        period_from = _iso_date(
            row[positions["statement_period_from"]], row_number, "statement_period_from"
        )
        period_to = _iso_date(
            row[positions["statement_period_to"]], row_number, "statement_period_to"
        )
        facts = (file_hash, period_from, period_to)
        if batch_id in batch_facts and batch_facts[batch_id] != facts:
            raise WeeklyBillingError(
                f"Committed Statement batch {batch_id} has conflicting audit facts."
            )
        batch_facts[batch_id] = facts
        count = _positive_integer(
            row[positions["statement_order_count"]],
            row_number,
            "statement_order_count",
        )
        if batch_id in expected_counts and expected_counts[batch_id] != count:
            raise WeeklyBillingError(
                f"Committed Statement batch {batch_id} has conflicting Order counts."
            )
        expected_counts[batch_id] = count
        if _text(row[positions["record_type"]]) == "ORDER":
            order_id = _required_text(
                row[positions["order_id"]], row_number, "order_id"
            )
            if order_id in order_ids[batch_id]:
                raise WeeklyBillingError(
                    f"Committed Statement batch {batch_id} duplicates ORDER {order_id}."
                )
            order_ids[batch_id].append(order_id)

    periods = tuple(
        BillingPeriod(period_from, period_to, batch_id, file_hash)
        for batch_id, (file_hash, period_from, period_to) in batch_facts.items()
    )
    by_dates: dict[tuple[date, date], list[BillingPeriod]] = defaultdict(list)
    for period in periods:
        by_dates[(period.statement_period_from, period.statement_period_to)].append(
            period
        )
    duplicates = [values for values in by_dates.values() if len(values) > 1]
    if duplicates:
        period = duplicates[0][0]
        raise WeeklyBillingError(
            "BUSINESS DECISION REQUIRED — DUPLICATE STATEMENT PERIOD: "
            f"{period.label} has multiple COMMITTED batches."
        )
    for batch_id, expected in expected_counts.items():
        actual = len(order_ids.get(batch_id, ()))
        if actual != expected:
            raise WeeklyBillingError(
                f"Committed Statement batch {batch_id} declares {expected} Orders "
                f"but contains {actual} committed ORDER rows."
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
        batch_id: tuple(values) for batch_id, values in order_ids.items()
    }


def _billing_source_items(
    items: Sequence[CanonicalInvoiceItem],
) -> tuple[tuple[BillingSourceItem, ...], tuple[int, int, int]]:
    normal: list[CanonicalInvoiceItem] = []
    groups: dict[tuple[str, str], list[CanonicalInvoiceItem]] = defaultdict(list)
    for item in items:
        if item.promotion_group_id:
            groups[(item.order_id, item.promotion_group_id)].append(item)
        elif item.promotion_label or item.source_group_total is not None:
            raise WeeklyBillingError(
                f"{item.order_id}/{item.item_index}: promotion evidence has no group identity."
            )
        else:
            normal.append(item)

    results = [_direct_source_item(item) for item in normal]
    same_price_count = 0
    mixed_price_count = 0
    for (_, group_id), members in sorted(groups.items()):
        allocated, mixed = _promotion_source_items(group_id, members)
        results.extend(allocated)
        if mixed:
            mixed_price_count += 1
        else:
            same_price_count += 1
    return (
        tuple(sorted(results, key=_source_item_sort_key)),
        (len(groups), same_price_count, mixed_price_count),
    )


def _direct_source_item(item: CanonicalInvoiceItem) -> BillingSourceItem:
    facts = _required_item_facts(item)
    if item.line_subtotal is None:
        raise WeeklyBillingError(
            f"{item.order_id}/{item.item_index}: missing required Billing fact line_subtotal."
        )
    return BillingSourceItem(
        **facts,
        actual_selling_amount=_cent_money(
            item.line_subtotal, item.order_id, item.item_index, "line_subtotal"
        ),
        actual_selling_amount_basis=ActualSellingAmountBasis.DIRECT,
    )


def _promotion_source_items(
    group_id: str,
    members: Sequence[CanonicalInvoiceItem],
) -> tuple[tuple[BillingSourceItem, ...], bool]:
    facts = [_required_item_facts(item) for item in members]
    totals = {item.source_group_total for item in members}
    if None in totals or len(totals) != 1:
        first = members[0]
        raise WeeklyBillingError(
            f"{first.order_id}/{group_id}: promotion source_group_total is missing or conflicting."
        )
    group_total = _cent_money(
        next(iter(totals)), members[0].order_id, members[0].item_index,
        "source_group_total",
    )
    prices = {values["historical_pm_unit_price"] for values in facts}
    mixed = len(prices) > 1
    if mixed:
        weights = tuple(
            values["historical_pm_unit_price"] * values["quantity"]
            for values in facts
        )
        basis = ActualSellingAmountBasis.PROMOTION_MIXED_PRICE_WEIGHTED_ALLOCATED
    else:
        weights = tuple(Decimal(values["quantity"]) for values in facts)
        basis = ActualSellingAmountBasis.PROMOTION_SAME_PRICE_ALLOCATED
    tie_breaks = tuple(
        (
            values["nav"],
            values["seller_sku"] or "",
            values["order_id"],
            values["item_index"],
        )
        for values in facts
    )
    allocations = _largest_remainder_allocations(group_total, weights, tie_breaks)
    source_items = tuple(
        BillingSourceItem(
            **values,
            actual_selling_amount=amount,
            actual_selling_amount_basis=basis,
            promotion_group_id=group_id,
            source_group_total=group_total,
        )
        for values, amount in zip(facts, allocations)
    )
    allocated_total = sum(
        (value.actual_selling_amount for value in source_items), Decimal("0")
    )
    if allocated_total != group_total:
        raise WeeklyBillingError(
            f"{members[0].order_id}/{group_id}: promotion allocation control failed."
        )
    return source_items, mixed


def _largest_remainder_allocations(
    total: Decimal,
    weights: Sequence[Decimal],
    tie_breaks: Sequence[tuple[str, str, str, int]],
) -> tuple[Decimal, ...]:
    if not weights or len(weights) != len(tie_breaks):
        raise WeeklyBillingError("Promotion allocation requires aligned members.")
    if any(weight < 0 for weight in weights) or sum(weights) <= 0:
        raise WeeklyBillingError("Promotion allocation requires positive total weight.")
    sign = Decimal("-1") if total < 0 else Decimal("1")
    absolute_cents = int((abs(total) / CENT).to_integral_exact())
    total_weight = sum(weights, Decimal("0"))
    exact = tuple(Decimal(absolute_cents) * weight / total_weight for weight in weights)
    floors = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    remaining = absolute_cents - sum(floors)
    ranking = sorted(
        range(len(weights)),
        key=lambda index: (-(exact[index] - floors[index]), tie_breaks[index]),
    )
    for index in ranking[:remaining]:
        floors[index] += 1
    return tuple(sign * Decimal(value) * CENT for value in floors)


def _required_item_facts(item: CanonicalInvoiceItem) -> dict[str, Any]:
    required_text = {
        "nav": item.nav,
        "product_name": item.product_name,
    }
    for field, value in required_text.items():
        if not _text(value):
            raise WeeklyBillingError(
                f"{item.order_id}/{item.item_index}: missing required Billing fact {field}."
            )
    if item.unit_price is None:
        raise WeeklyBillingError(
            f"{item.order_id}/{item.item_index}: missing required Billing fact historical PM Unit Price."
        )
    if item.quantity is None or item.quantity <= 0:
        raise WeeklyBillingError(
            f"{item.order_id}/{item.item_index}: missing required Billing fact quantity."
        )
    return {
        "order_id": item.order_id,
        "item_index": item.item_index,
        "nav": _text(item.nav),
        "seller_sku": _text(item.seller_sku) or None,
        "product_name": _text(item.product_name),
        "uom": None,
        "historical_pm_unit_price": _cent_money(
            item.unit_price, item.order_id, item.item_index, "historical PM Unit Price"
        ),
        "quantity": item.quantity,
    }


def _aggregate_source_items(
    source_items: Sequence[BillingSourceItem],
) -> tuple[ProductSummaryRow, ...]:
    groups: dict[tuple[str, str, Decimal], list[BillingSourceItem]] = defaultdict(list)
    for item in source_items:
        key = (
            item.nav,
            item.product_name,
            item.historical_pm_unit_price,
        )
        groups[key].append(item)
    unsorted_rows = tuple(
        ProductSummaryRow(
            number=0,
            nav=key[0],
            product_name=key[1],
            uom=None,
            unit_price=key[2],
            quantity=sum(item.quantity for item in members),
            discount_percent=None,
            discount_amount=sum(
                (item.discount_amount for item in members), Decimal("0.00")
            ),
            amount=sum(
                (item.actual_selling_amount for item in members), Decimal("0.00")
            ),
            source_item_count=len(members),
        )
        for key, members in groups.items()
    )
    sorted_rows = sorted(unsorted_rows, key=_summary_row_sort_key)
    return tuple(
        ProductSummaryRow(
            number=number,
            nav=row.nav,
            product_name=row.product_name,
            uom=row.uom,
            unit_price=row.unit_price,
            quantity=row.quantity,
            discount_percent=row.discount_percent,
            discount_amount=row.discount_amount,
            amount=row.amount,
            source_item_count=row.source_item_count,
        )
        for number, row in enumerate(sorted_rows, start=1)
    )


def _validate_controls(
    persisted_items: Sequence[CanonicalInvoiceItem],
    source_items: Sequence[BillingSourceItem],
    rows: Sequence[ProductSummaryRow],
) -> None:
    persisted_ids = {(item.order_id, item.item_index) for item in persisted_items}
    source_ids = {(item.order_id, item.item_index) for item in source_items}
    if len(source_items) != len(source_ids):
        raise WeeklyBillingError("Billing source contains a duplicate Invoice item.")
    if persisted_ids != source_ids:
        missing = sorted(persisted_ids - source_ids)
        extra = sorted(source_ids - persisted_ids)
        raise WeeklyBillingError(
            f"Billing source population control failed; missing={missing[:5]}, extra={extra[:5]}."
        )
    source_quantity = sum(item.quantity for item in source_items)
    summary_quantity = sum(row.quantity for row in rows)
    if source_quantity != summary_quantity:
        raise WeeklyBillingError("Billing Quantity control failed.")
    source_total = sum(
        (item.actual_selling_amount for item in source_items), Decimal("0.00")
    )
    summary_total = sum((row.amount for row in rows), Decimal("0.00"))
    if source_total != summary_total:
        raise WeeklyBillingError("Billing Amount control failed.")
    source_discount = sum(
        (item.discount_amount for item in source_items), Decimal("0.00")
    )
    summary_discount = sum(
        (row.discount_amount for row in rows), Decimal("0.00")
    )
    if source_discount != summary_discount:
        raise WeeklyBillingError("Billing Disc Amt control failed.")
    for row in rows:
        if row.unit_price * row.quantity - row.discount_amount != row.amount:
            raise WeeklyBillingError(
                f"Billing row accounting control failed for row {row.number}."
            )


def _tab_rows(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
    tab: str,
    headers: Sequence[str],
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    if tab not in tabs:
        raise WeeklyBillingError(f"Required Billing source tab {tab} is missing.")
    rows = tuple(tuple(row) for row in tabs[tab])
    if not rows or tuple(_text(value) for value in rows[0]) != tuple(headers):
        raise WeeklyBillingError(f"{tab} header does not match the locked schema.")
    result = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) > len(headers) and any(_text(value) for value in row[len(headers):]):
            raise WeeklyBillingError(f"{tab} row {row_number} exceeds the locked schema.")
        padded = row + (None,) * max(0, len(headers) - len(row))
        if any(_text(value) for value in padded):
            result.append((row_number, padded[:len(headers)]))
    return tuple(result)


def _cent_money(
    value: Decimal, order_id: str, item_index: int, field: str
) -> Decimal:
    quantized = value.quantize(CENT)
    if value != quantized:
        raise WeeklyBillingError(
            f"{order_id}/{item_index}: {field} is not at cent precision."
        )
    return quantized


def _positive_integer(value: Any, row_number: int, field: str) -> int:
    try:
        parsed = int(_text(value))
    except ValueError as error:
        raise WeeklyBillingError(
            f"Statement_Data row {row_number} has invalid {field}."
        ) from error
    if parsed <= 0:
        raise WeeklyBillingError(
            f"Statement_Data row {row_number} has invalid {field}."
        )
    return parsed


def _iso_date(value: Any, row_number: int, field: str) -> date:
    try:
        return date.fromisoformat(_text(value))
    except ValueError as error:
        raise WeeklyBillingError(
            f"Statement_Data row {row_number} has invalid {field}."
        ) from error


def _required_text(value: Any, row_number: int, field: str) -> str:
    text = _text(value)
    if not text:
        raise WeeklyBillingError(
            f"Statement_Data row {row_number} has blank {field}."
        )
    return text


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _source_item_sort_key(item: BillingSourceItem) -> tuple[Any, ...]:
    return (
        item.nav.casefold(),
        item.product_name.casefold(),
        item.historical_pm_unit_price,
        (item.seller_sku or "").casefold(),
        item.order_id,
        item.item_index,
    )


def _summary_row_sort_key(row: ProductSummaryRow) -> tuple[Any, ...]:
    return (
        row.nav.casefold(),
        row.product_name.casefold(),
        row.unit_price,
    )
