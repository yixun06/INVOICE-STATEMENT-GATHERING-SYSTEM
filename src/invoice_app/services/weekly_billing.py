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
    FinancialControl,
    FinancialSummaryRow,
    ProductSummaryRow,
    WeeklyBillingFinancialSummary,
    WeeklyBillingReport,
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
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
    STATEMENT_SUMMARY_HEADERS,
    STATEMENT_SUMMARY_TAB,
)
from src.invoice_app.services.product_summary_identity import (
    resolve_product_summary_identity,
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
    statement_summary_rows: tuple[tuple[int, tuple[Any, ...]], ...] = ()
    financial_component_rows: tuple[tuple[int, tuple[Any, ...]], ...] = ()


class GoogleSheetsWeeklyBillingReader:
    """Load Billing facts from existing source tabs without mutating UAT2."""

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
            STATEMENT_FINANCIAL_COMPONENTS_TAB,
            STATEMENT_SUMMARY_TAB,
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
    summary_rows = _optional_tab_rows(
        tabs, STATEMENT_SUMMARY_TAB, STATEMENT_SUMMARY_HEADERS
    )
    component_rows = _optional_tab_rows(
        tabs,
        STATEMENT_FINANCIAL_COMPONENTS_TAB,
        STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    )

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
        statement_summary_rows=summary_rows,
        financial_component_rows=component_rows,
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


_CONTROL_COMPONENTS = {
    "Product Price": ("Product Price",),
    "Refund": ("Refund Amount",),
    "Voucher & Rebates": (
        "Rebate Provided by Shopee",
        "Voucher Sponsored by Seller",
        "Cofund Voucher Sponsored by Seller",
        "Coin Cashback Sponsored by Seller",
        "Cofund Coin Cashback Sponsored by Seller",
    ),
    "Shipping Subtotal": (
        "Shipping Fee Paid by Buyer (excl. SST)",
        "Shipping Fee Charged by Logistic Provider",
        "Seller Paid Shipping Fee SST",
        "Shipping Rebate From Shopee",
        "Reverse Shipping Fee",
        "Reverse Shipping Fee SST",
        "Saver Programme Shipping Fee Savings",
        "Return to Seller Fee",
    ),
    "Fees & Charges": (
        "Commission Fee (incl. SST)",
        "Service Fee (Incl. SST)",
        "Transaction Fee (Incl. SST)",
        "AMS Commission Fee",
        "Saver Programme Fee (Incl. SST)",
        "Ads Escrow Top Up Fee",
    ),
}

_NATIVE_CONTROL_LABELS = {
    "Merchandise Subtotal": "Merchandise Subtotal",
    "Total Revenue": "1. Total Revenue",
    "Total Expenses": "2. Total Expenses",
    "Total Released Amount": "3. Total Released Amount",
}


def build_weekly_billing_financial_summary(
    dataset: WeeklyBillingDataset,
    period: BillingPeriod,
) -> WeeklyBillingFinancialSummary:
    """Build a fail-closed Billing view from native Summary plus ORDER controls."""

    _require_available_period(dataset, period)
    rows = _native_summary_rows(dataset.statement_summary_rows, period)
    components = _order_components(dataset.financial_component_rows, period)
    native_by_label = {row.native_label: row.amount for row in rows}
    product_price = _component_total(components, "Product Price")
    refund = _component_total(components, "Refund")
    vouchers = _component_total(components, "Voucher & Rebates")
    shipping = _component_total(components, "Shipping Subtotal")
    fees = _component_total(components, "Fees & Charges")
    controls = (
        _financial_control(
            "Merchandise Subtotal",
            product_price + refund,
            native_by_label,
        ),
        _financial_control(
            "Total Revenue",
            product_price + refund + vouchers,
            native_by_label,
        ),
        _financial_control(
            "Total Expenses", shipping + fees, native_by_label
        ),
        _financial_control(
            "Total Released Amount",
            product_price + refund + vouchers + shipping + fees,
            native_by_label,
        ),
    )
    failures = tuple(
        f"{control.name} control failed: derived {control.derived_amount:.2f} "
        f"does not equal native {control.native_amount:.2f}."
        for control in controls
        if not control.passed
    )
    return WeeklyBillingFinancialSummary(
        period=period,
        currency=rows[0].currency,
        rows=rows,
        controls=controls,
        export_ready=not failures,
        validation_failures=failures,
    )


def build_weekly_billing_report(
    dataset: WeeklyBillingDataset,
    period: BillingPeriod,
) -> WeeklyBillingReport:
    """Build preview/export inputs once and prove their shared batch identity."""

    product_summary = build_weekly_billing_summary(dataset, period)
    financial_summary = build_weekly_billing_financial_summary(dataset, period)
    if product_summary.period != financial_summary.period:
        raise WeeklyBillingError("Billing Product and Financial Summary batches differ.")
    if not financial_summary.export_ready:
        raise WeeklyBillingError("Financial Summary is not export-ready: " + " ".join(
            financial_summary.validation_failures
        ))
    return WeeklyBillingReport(product_summary, financial_summary)


def _require_available_period(dataset: WeeklyBillingDataset, period: BillingPeriod) -> None:
    if period not in dataset.periods:
        raise WeeklyBillingError(
            "The requested period is not an existing COMMITTED Statement batch."
        )


def _native_summary_rows(
    source_rows: Sequence[tuple[int, tuple[Any, ...]]], period: BillingPeriod
) -> tuple[FinancialSummaryRow, ...]:
    positions = {name: index for index, name in enumerate(STATEMENT_SUMMARY_HEADERS)}
    selected: list[FinancialSummaryRow] = []
    for row_number, row in source_rows:
        if _text(row[positions["commit_status"]]) != "COMMITTED":
            continue
        if _text(row[positions["statement_batch_id"]]) != period.statement_batch_id:
            continue
        if _text(row[positions["statement_file_hash"]]) != period.statement_file_hash:
            raise WeeklyBillingError("Statement_Summary batch hash does not match selected period.")
        if _iso_date(row[positions["statement_period_from"]], row_number, "statement_period_from") != period.statement_period_from or _iso_date(row[positions["statement_period_to"]], row_number, "statement_period_to") != period.statement_period_to:
            raise WeeklyBillingError("Statement_Summary period does not match selected batch.")
        line_type = _required_text(row[positions["line_type"]], row_number, "line_type")
        if line_type not in {"TOTAL", "SUBTOTAL", "DETAIL", "REFERENCE", "SECTION_HEADER"}:
            raise WeeklyBillingError(f"Statement_Summary row {row_number} has unsupported line_type.")
        amount_text = _text(row[positions["component_amount"]])
        amount = None if not amount_text else _decimal_money(amount_text, "Statement_Summary", row_number)
        if (line_type == "SECTION_HEADER") != (amount is None):
            raise WeeklyBillingError(
                f"Statement_Summary row {row_number} has invalid amount for {line_type}."
            )
        parent_text = _text(row[positions["parent_source_row_number"]])
        try:
            source_row_number = int(_required_text(
                row[positions["statement_source_row_number"]],
                row_number,
                "statement_source_row_number",
            ))
            parent = int(parent_text) if parent_text else None
        except ValueError as error:
            raise WeeklyBillingError(
                f"Statement_Summary row {row_number} has invalid source hierarchy."
            ) from error
        selected.append(FinancialSummaryRow(
            statement_source_row_number=source_row_number,
            native_label=_required_text(row[positions["native_label"]], row_number, "native_label"),
            line_type=line_type,
            parent_source_row_number=parent,
            amount=amount,
            currency=_required_text(row[positions["currency"]], row_number, "currency"),
        ))
    if len(selected) != 32:
        raise WeeklyBillingError(
            f"Statement_Summary is incomplete for selected batch: expected 32 native lines, found {len(selected)}."
        )
    ordered = tuple(sorted(selected, key=lambda value: value.statement_source_row_number))
    source_rows_seen = {row.statement_source_row_number for row in ordered}
    if len(source_rows_seen) != len(ordered):
        raise WeeklyBillingError("Statement_Summary has duplicate source row identities.")
    if len({row.native_label for row in ordered}) != len(ordered):
        raise WeeklyBillingError("Statement_Summary has duplicate native labels.")
    if len({row.currency for row in ordered}) != 1:
        raise WeeklyBillingError("Statement_Summary has conflicting currencies.")
    for row in ordered:
        if row.parent_source_row_number is not None and row.parent_source_row_number not in source_rows_seen:
            raise WeeklyBillingError("Statement_Summary parent hierarchy is incomplete.")
    return ordered


def _order_components(
    source_rows: Sequence[tuple[int, tuple[Any, ...]]], period: BillingPeriod
) -> Mapping[str, Decimal]:
    positions = {name: index for index, name in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)}
    results: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    seen: set[tuple[str, str]] = set()
    for row_number, row in source_rows:
        if _text(row[positions["commit_status"]]) != "COMMITTED":
            continue
        if _text(row[positions["statement_batch_id"]]) != period.statement_batch_id:
            continue
        if _text(row[positions["statement_file_hash"]]) != period.statement_file_hash:
            raise WeeklyBillingError("Statement_Financial_Components batch hash does not match selected period.")
        if _iso_date(row[positions["statement_period_from"]], row_number, "statement_period_from") != period.statement_period_from or _iso_date(row[positions["statement_period_to"]], row_number, "statement_period_to") != period.statement_period_to:
            raise WeeklyBillingError("Statement_Financial_Components period does not match selected batch.")
        if _text(row[positions["record_type"]]) != "ORDER":
            continue
        source_row = _required_text(row[positions["statement_source_row_number"]], row_number, "statement_source_row_number")
        name = _required_text(row[positions["component_name"]], row_number, "component_name")
        identity = (source_row, name)
        if identity in seen:
            raise WeeklyBillingError("ORDER financial component has duplicate source identity.")
        seen.add(identity)
        results[name] += _decimal_money(row[positions["component_amount"]], STATEMENT_FINANCIAL_COMPONENTS_TAB, row_number)
    if not seen:
        raise WeeklyBillingError("Selected batch has no ORDER financial component evidence.")
    return results


def _component_total(components: Mapping[str, Decimal], control_name: str) -> Decimal:
    names = _CONTROL_COMPONENTS[control_name]
    missing = [name for name in names if name not in components]
    if missing:
        raise WeeklyBillingError(
            "ORDER financial controls are missing component(s): " + ", ".join(missing)
        )
    return sum((components[name] for name in names), Decimal("0.00"))


def _financial_control(
    name: str, derived: Decimal, native_by_label: Mapping[str, Decimal | None]
) -> FinancialControl:
    label = _NATIVE_CONTROL_LABELS[name]
    native = native_by_label.get(label)
    if native is None:
        raise WeeklyBillingError(f"Statement_Summary is missing native {name}.")
    return FinancialControl(name, derived, native, derived == native)


def _decimal_money(value: Any, tab: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(_text(value))
    except Exception as error:
        raise WeeklyBillingError(f"{tab} row {row_number} has invalid money value.") from error
    if parsed != parsed.quantize(CENT):
        raise WeeklyBillingError(f"{tab} row {row_number} is not at cent precision.")
    return parsed


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
    sku_code = _text(item.resolved_seller_sku) or _text(item.seller_sku)
    if not sku_code:
        raise WeeklyBillingError(
            "BUSINESS DECISION REQUIRED — WEEKLY BILLING SKU MISSING: "
            f"{item.order_id}/{item.item_index}."
        )
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
        "sku_code": sku_code,
        "product_name": _text(item.product_name),
        "variation": _text(item.variation),
        "uom": "EA",
        "historical_pm_unit_price": _cent_money(
            item.unit_price, item.order_id, item.item_index, "historical PM Unit Price"
        ),
        "quantity": item.quantity,
    }


def _aggregate_source_items(
    source_items: Sequence[BillingSourceItem],
) -> tuple[ProductSummaryRow, ...]:
    groups: dict[tuple[str, str, Decimal], list[BillingSourceItem]] = defaultdict(list)
    descriptions: dict[tuple[str, str, Decimal], list[str]] = defaultdict(list)
    for item in source_items:
        identity = resolve_product_summary_identity(
            nav=item.nav,
            seller_sku=item.sku_code,
            product_name=item.product_name,
            variation=item.variation,
            historical_pm_unit_price=item.historical_pm_unit_price,
        )
        key = (
            identity.nav,
            item.sku_code,
            identity.historical_pm_unit_price,
        )
        groups[key].append(item)
        if identity.display_description not in descriptions[key]:
            descriptions[key].append(identity.display_description)
    unsorted_rows = tuple(
        ProductSummaryRow(
            number=0,
            sku_code=key[1],
            nav=key[0],
            product_name=" | ".join(descriptions[key]),
            uom="EA",
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
            sku_code=row.sku_code,
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


def _optional_tab_rows(
    tabs: Mapping[str, Sequence[Sequence[Any]]],
    tab: str,
    headers: Sequence[str],
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    """Keep Product Summary backward-compatible; Finance fails closed at use."""
    if tab not in tabs:
        return ()
    return _tab_rows(tabs, tab, headers)


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
        item.sku_code,
        item.historical_pm_unit_price,
        item.order_id,
        item.item_index,
        item.product_name.casefold(),
        item.variation,
    )


def _summary_row_sort_key(row: ProductSummaryRow) -> tuple[Any, ...]:
    return (
        row.nav.casefold(),
        row.sku_code,
        row.unit_price,
        row.product_name.casefold(),
    )
