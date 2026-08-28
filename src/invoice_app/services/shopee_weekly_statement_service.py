from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping

from ..parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
    WeeklyStatementParseError,
    parse_shopee_weekly_statement,
)
from .batch_service import canonical_order_identity


READY_TO_COMMIT = "Ready to Commit"
NEEDS_REVIEW = "Needs Review"
REJECTED = "Rejected"
MONEY_TOLERANCE = Decimal("0.02")
ORDER_RECONCILIATION_STATUSES = (
    "Matched", "Different", "Estimated Only", "Unmatched Order"
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class StatementReference:
    file_hash: str
    statement_period_from: date
    statement_period_to: date


@dataclass(frozen=True)
class OrderReconciliation:
    order_id: str
    status: str
    released_amount: Decimal
    order_income: Decimal | None
    difference: Decimal | None


@dataclass(frozen=True)
class AdjustmentReconciliation:
    linked_order_id: str
    status: str
    adjustment_amount: Decimal


@dataclass(frozen=True)
class StagedShopeeWeeklyStatement:
    result: str
    source_filename: str
    file_hash: str
    statement: ParsedShopeeWeeklyStatement | None
    validation_issues: tuple[ValidationIssue, ...]
    review_reasons: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    duplicate_status: str | None
    order_reconciliations: tuple[OrderReconciliation, ...]
    adjustment_reconciliations: tuple[AdjustmentReconciliation, ...]
    whole_statement_atomic: bool = True

    @property
    def eligible_for_future_atomic_commit(self) -> bool:
        return self.result == READY_TO_COMMIT and self.duplicate_status is None

    @property
    def reconciliation_counts(self) -> Mapping[str, int]:
        counts = Counter(item.status for item in self.order_reconciliations)
        return {status: counts.get(status, 0) for status in ORDER_RECONCILIATION_STATUSES}


def stage_shopee_weekly_statement(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str | None = None,
    existing_orders: Iterable[Mapping[str, Any]] = (),
    existing_statements: Iterable[StatementReference | Mapping[str, Any]] = (),
) -> StagedShopeeWeeklyStatement:
    try:
        statement = parse_shopee_weekly_statement(
            source, source_filename=source_filename
        )
    except (WeeklyStatementParseError, OSError) as exc:
        return StagedShopeeWeeklyStatement(
            result=REJECTED,
            source_filename=getattr(exc, "source_filename", source_filename or ""),
            file_hash=getattr(exc, "file_hash", ""),
            statement=None,
            validation_issues=(),
            review_reasons=(),
            rejection_reasons=(str(exc),),
            duplicate_status=None,
            order_reconciliations=(),
            adjustment_reconciliations=(),
        )
    return stage_parsed_shopee_weekly_statement(
        statement,
        existing_orders=existing_orders,
        existing_statements=existing_statements,
    )


def stage_parsed_shopee_weekly_statement(
    statement: ParsedShopeeWeeklyStatement,
    *,
    existing_orders: Iterable[Mapping[str, Any]] = (),
    existing_statements: Iterable[StatementReference | Mapping[str, Any]] = (),
) -> StagedShopeeWeeklyStatement:
    orders = tuple(existing_orders)
    validation_issues = validate_shopee_weekly_statement(statement)
    result = NEEDS_REVIEW if validation_issues else READY_TO_COMMIT
    duplicate_status = None
    review_reasons: list[str] = []
    references = tuple(existing_statements)
    if any(_reference_value(item, "file_hash") == statement.file_hash for item in references):
        duplicate_status = "Exact Duplicate"
    elif any(_same_period(statement, item) for item in references):
        duplicate_status = "Same Period Different File"
        result = NEEDS_REVIEW
        review_reasons.append(
            "A different file already exists for the same Payout Completed Date period."
        )

    order_reconciliations: tuple[OrderReconciliation, ...] = ()
    adjustment_reconciliations: tuple[AdjustmentReconciliation, ...] = ()
    if not validation_issues:
        order_reconciliations = reconcile_statement_orders(statement, orders)
        adjustment_reconciliations = reconcile_statement_adjustments(statement, orders)

    return StagedShopeeWeeklyStatement(
        result=result,
        source_filename=statement.source_filename,
        file_hash=statement.file_hash,
        statement=statement,
        validation_issues=validation_issues,
        review_reasons=tuple(review_reasons),
        rejection_reasons=(),
        duplicate_status=duplicate_status,
        order_reconciliations=order_reconciliations,
        adjustment_reconciliations=adjustment_reconciliations,
    )

def validate_shopee_weekly_statement(
    statement: ParsedShopeeWeeklyStatement,
) -> tuple[ValidationIssue, ...]:
    issues = [
        ValidationIssue(code=f"source_{item.code}", message=item.message)
        for item in statement.source_value_issues
    ]
    period_from = statement.statement_period_from
    period_to = statement.statement_period_to
    if period_from > period_to:
        issues.append(ValidationIssue(
            "invalid_statement_period",
            f"Statement period From {period_from} is after To {period_to}.",
        ))

    order_rows = statement.order_rows
    sku_rows = statement.sku_rows
    if not order_rows:
        issues.append(ValidationIssue(
            "missing_order_view", "Income contains no authoritative Order View rows."
        ))
    if not sku_rows:
        issues.append(ValidationIssue(
            "missing_sku_view", "Income contains no SKU allocation rows."
        ))

    out_of_period = [
        row for row in statement.income_rows
        if row.payout_completed_date is not None
        and not (period_from <= row.payout_completed_date <= period_to)
    ]
    if out_of_period:
        issues.append(ValidationIssue(
            "payout_date_outside_statement_period",
            f"{len(out_of_period)} Income row(s) have Payout Completed Date outside the statement period.",
        ))
    adjustment_out_of_period = [
        row for row in statement.adjustments
        if row.payout_completed_date is not None
        and not (period_from <= row.payout_completed_date <= period_to)
    ]
    if adjustment_out_of_period:
        issues.append(ValidationIssue(
            "adjustment_payout_date_outside_statement_period",
            f"{len(adjustment_out_of_period)} Adjustment row(s) have Payout Completed Date outside the statement period.",
        ))

    if order_rows and all(row.total_released_amount is not None for row in order_rows):
        order_total = sum(
            (row.total_released_amount for row in order_rows if row.total_released_amount is not None),
            Decimal("0"),
        )
        if _amount_differs(order_total, statement.summary_total_released):
            issues.append(ValidationIssue(
                "order_total_vs_summary_mismatch",
                f"Order View total {order_total:.2f} does not match Summary Total Released {statement.summary_total_released:.2f}.",
            ))

    component_mismatches: list[str] = []
    for row in order_rows:
        values = tuple(row.financial_components.get(name) for name in INCOME_COMPONENT_COLUMNS)
        if row.total_released_amount is None or any(value is None for value in values):
            continue
        component_total = sum(
            (value for value in values if value is not None), Decimal("0")
        )
        if _amount_differs(component_total, row.total_released_amount):
            component_mismatches.append(row.order_id)
    if component_mismatches:
        issues.append(ValidationIssue(
            "order_component_mismatch",
            f"{len(component_mismatches)} Order View row(s) do not reconcile from primary financial components: "
            + ", ".join(component_mismatches[:5]),
        ))

    order_groups: dict[str, list[SettlementIncomeRow]] = defaultdict(list)
    for row in order_rows:
        order_groups[row.order_id].append(row)
    conflicting = [
        order_id for order_id, rows in order_groups.items()
        if len(rows) > 1 and len({_order_fingerprint(row) for row in rows}) > 1
    ]
    if conflicting:
        issues.append(ValidationIssue(
            "conflicting_order_view_duplicates",
            "Conflicting authoritative Order View records found for: "
            + ", ".join(conflicting[:5]),
        ))

    authoritative_totals = {
        order_id: rows[0].total_released_amount
        for order_id, rows in order_groups.items() if rows
    }
    sku_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    incomplete_sku_orders: set[str] = set()
    for row in sku_rows:
        if row.total_released_amount is None:
            incomplete_sku_orders.add(row.order_id)
        else:
            sku_totals[row.order_id] += row.total_released_amount
    sku_mismatches = []
    for order_id in sorted(set(authoritative_totals) | set(sku_totals)):
        authoritative = authoritative_totals.get(order_id)
        if authoritative is None or order_id in incomplete_sku_orders:
            continue
        if _amount_differs(sku_totals.get(order_id, Decimal("0")), authoritative):
            sku_mismatches.append(order_id)
    if sku_mismatches:
        issues.append(ValidationIssue(
            "sku_total_vs_order_mismatch",
            f"{len(sku_mismatches)} order(s) have SKU Total Released allocations that do not match Order View: "
            + ", ".join(sku_mismatches[:5]),
        ))

    service_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    incomplete_service_orders: set[str] = set()
    for detail in statement.service_fee_details:
        values = tuple(detail.components.values())
        if any(value is None for value in values):
            incomplete_service_orders.add(detail.order_id)
        else:
            service_totals[detail.order_id] += sum(
                (value for value in values if value is not None), Decimal("0")
            )
    service_mismatches = []
    for order_id, rows in order_groups.items():
        income_service = rows[0].financial_components.get("Service Fee (Incl. SST)")
        if income_service is None or order_id in incomplete_service_orders:
            continue
        if _amount_differs(service_totals.get(order_id, Decimal("0")), income_service):
            service_mismatches.append(order_id)
    extra_service_orders = sorted(set(service_totals) - set(order_groups))
    if service_mismatches or extra_service_orders:
        issues.append(ValidationIssue(
            "service_fee_detail_mismatch",
            f"Service Fee Details mismatch {len(service_mismatches)} Income order(s); "
            f"{len(extra_service_orders)} detail order(s) are not in Order View.",
        ))

    if all(row.adjustment_amount is not None for row in statement.adjustments):
        adjustment_total = sum(
            (row.adjustment_amount for row in statement.adjustments if row.adjustment_amount is not None),
            Decimal("0"),
        )
        if _amount_differs(adjustment_total, statement.adjustment_control_total):
            issues.append(ValidationIssue(
                "adjustment_detail_vs_control_mismatch",
                f"Adjustment details total {adjustment_total:.2f} does not match control total {statement.adjustment_control_total:.2f}.",
            ))
        if (
            statement.adjustment_footer_total is not None
            and _amount_differs(adjustment_total, statement.adjustment_footer_total)
        ):
            issues.append(ValidationIssue(
                "adjustment_detail_vs_footer_mismatch",
                f"Adjustment details total {adjustment_total:.2f} does not match footer total {statement.adjustment_footer_total:.2f}.",
            ))
    return tuple(issues)

def reconcile_statement_orders(
    statement: ParsedShopeeWeeklyStatement,
    existing_orders: Iterable[Mapping[str, Any]],
) -> tuple[OrderReconciliation, ...]:
    existing_by_order: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for order in existing_orders:
        identity = canonical_order_identity(order.get("platform"), order.get("order_id"))
        if identity is not None and identity[0] == "Shopee":
            existing_by_order[identity[1]].append(order)

    reconciliations: list[OrderReconciliation] = []
    for row in statement.order_rows:
        if row.total_released_amount is None:
            continue
        candidates = existing_by_order.get(row.order_id, [])
        final_candidates = [
            item for item in candidates
            if str(item.get("income_type") or "").strip().casefold() == "final"
        ]
        if final_candidates:
            order_income = _to_decimal(final_candidates[0].get("order_income"))
            difference = (
                row.total_released_amount - order_income
                if order_income is not None else None
            )
            status = (
                "Matched"
                if difference is not None and abs(difference) <= MONEY_TOLERANCE
                else "Different"
            )
        elif candidates:
            estimated_candidates = [
                item for item in candidates
                if str(item.get("income_type") or "").strip().casefold() == "estimated"
            ]
            selected = estimated_candidates[0] if estimated_candidates else candidates[0]
            order_income = _to_decimal(selected.get("order_income"))
            difference = (
                row.total_released_amount - order_income
                if order_income is not None else None
            )
            status = "Estimated Only"
        else:
            order_income = None
            difference = None
            status = "Unmatched Order"
        reconciliations.append(OrderReconciliation(
            order_id=row.order_id,
            status=status,
            released_amount=row.total_released_amount,
            order_income=order_income,
            difference=difference,
        ))
    return tuple(reconciliations)


def reconcile_statement_adjustments(
    statement: ParsedShopeeWeeklyStatement,
    existing_orders: Iterable[Mapping[str, Any]],
) -> tuple[AdjustmentReconciliation, ...]:
    known_order_ids = {
        identity[1]
        for order in existing_orders
        if (identity := canonical_order_identity(order.get("platform"), order.get("order_id")))
        is not None and identity[0] == "Shopee"
    }
    return tuple(
        AdjustmentReconciliation(
            linked_order_id=row.linked_order_id,
            status=(
                "Matched" if row.linked_order_id in known_order_ids
                else "Unmatched Adjustment"
            ),
            adjustment_amount=row.adjustment_amount,
        )
        for row in statement.adjustments
        if row.adjustment_amount is not None
    )


def _amount_differs(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) > MONEY_TOLERANCE


def _order_fingerprint(row: SettlementIncomeRow) -> tuple[Any, ...]:
    return (
        row.total_released_amount,
        *(row.financial_components.get(name) for name in INCOME_COMPONENT_COLUMNS),
    )


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return Decimal(str(value).strip().replace("RM", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _reference_value(reference: StatementReference | Mapping[str, Any], key: str) -> Any:
    if isinstance(reference, Mapping):
        return reference.get(key)
    return getattr(reference, key)


def _same_period(
    statement: ParsedShopeeWeeklyStatement,
    reference: StatementReference | Mapping[str, Any],
) -> bool:
    return (
        _reference_value(reference, "statement_period_from")
        == statement.statement_period_from
        and _reference_value(reference, "statement_period_to")
        == statement.statement_period_to
        and _reference_value(reference, "file_hash") != statement.file_hash
    )