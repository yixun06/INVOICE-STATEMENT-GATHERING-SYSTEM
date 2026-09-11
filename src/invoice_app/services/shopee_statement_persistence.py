"""Persistence-neutral planning for a committed Shopee Weekly Statement batch.

This module deliberately does not write to Google Sheets.  A final adapter may
call its atomic writer boundary only after it can prove the locked pre-commit
contract against authoritative persisted state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceOrder
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    SettlementIncomeRow,
)
from src.invoice_app.services.shopee_statement_item_matching import (
    StatementItemMatch,
    StatementItemMatchBatch,
    StatementItemMatchStatus,
    business_match_method,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.uat2_persistence_schema import STATEMENT_DATA_HEADERS


MONEY_TOLERANCE = Decimal("0.02")


class StatementCommitBlocked(RuntimeError):
    """The staged Statement cannot safely become a committed batch."""


class StatementWriteNotApplied(RuntimeError):
    """An uncertain authoritative write was verified to have applied nothing."""


class StatementWriteIntegrityError(RuntimeError):
    """An authoritative write result is mixed or cannot be proven."""


@dataclass(frozen=True)
class StatementBatchAudit:
    statement_batch_id: str
    uploaded_at: datetime
    uploaded_by: str
    committed_at: datetime


@dataclass(frozen=True)
class StatementOrderComparison:
    order_id: str
    comparison_source: str | None
    comparison_amount: Decimal | None
    difference: Decimal | None
    reconciliation_status: str
    payout_completed_date: date | None
    resulting_income_type: str | None


@dataclass(frozen=True)
class InvoiceOrderStatementUpdate:
    order_id: str
    payout_completed_date: date | None
    payment_status: str
    income_type: str | None
    difference: Decimal


@dataclass(frozen=True)
class InvoiceItemStatementUpdate:
    order_id: str
    item_index: int
    statement_product_price: Decimal
    statement_refund_amount: Decimal
    statement_net_selling_amount: Decimal


@dataclass(frozen=True)
class CommittedStatementReference:
    file_hash: str
    statement_period_from: date
    statement_period_to: date
    commit_status: str = "COMMITTED"


@dataclass(frozen=True)
class StatementCommitPlan:
    statement: ParsedShopeeWeeklyStatement
    audit: StatementBatchAudit
    rows: tuple[tuple[str, ...], ...]
    order_comparisons: tuple[StatementOrderComparison, ...]
    invoice_order_updates: tuple[InvoiceOrderStatementUpdate, ...]
    invoice_item_updates: tuple[InvoiceItemStatementUpdate, ...]


@dataclass(frozen=True)
class StatementCommitState:
    orders: Mapping[str, CanonicalInvoiceOrder]
    committed_statements: Sequence[CommittedStatementReference]


@dataclass(frozen=True)
class StatementCommitAttempt:
    committed: bool
    reasons: tuple[str, ...]


class StatementAtomicWriter(Protocol):
    """Implemented only by a storage adapter with an approved atomic contract."""

    def write_statement_batch(self, plan: StatementCommitPlan) -> None: ...


def prepare_statement_commit_plan(
    statement: ParsedShopeeWeeklyStatement,
    *,
    audit: StatementBatchAudit,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    sku_matches: StatementItemMatchBatch,
    validation_passed: bool,
) -> StatementCommitPlan:
    """Build rows and enrichment updates without performing any persistence write."""

    if not validation_passed:
        raise StatementCommitBlocked("Statement internal validation has not passed.")
    if not sku_matches.eligible_for_commit:
        raise StatementCommitBlocked("Statement SKU matching is not ready for commit.")
    if not audit.statement_batch_id.strip():
        raise ValueError("statement_batch_id is required.")
    _validate_statement_row_identity(statement)

    orders_by_id = {order.order_id: order for order in invoice_orders if order.platform == "Shopee"}
    comparisons = tuple(_compare_order(row, orders_by_id.get(row.order_id)) for row in statement.order_rows)
    missing = [comparison.order_id for comparison in comparisons if comparison.comparison_amount is None]
    if missing:
        raise StatementCommitBlocked(
            "Statement target Order ID coverage or comparison evidence is incomplete: "
            + ", ".join(missing[:5])
        )

    match_by_source_row = {match.statement_source_row: match for match in sku_matches.matches}
    if len(match_by_source_row) != len(statement.sku_rows):
        raise StatementCommitBlocked("Every Statement SKU row requires one matching result.")
    if any(match.status is not StatementItemMatchStatus.MATCHED for match in match_by_source_row.values()):
        raise StatementCommitBlocked("An unresolved Statement SKU row cannot be committed.")

    rows = [
        _order_row(statement, audit, row, comparison)
        for row, comparison in zip(statement.order_rows, comparisons)
    ]
    sku_rows = []
    matched_item_keys: list[tuple[str, int]] = []
    for source_row in statement.sku_rows:
        match = match_by_source_row[source_row.source_row_number]
        if match.invoice_item_index is None:
            raise StatementCommitBlocked("A matched Statement SKU row lacks Invoice item identity.")
        product_price = source_row.financial_components.get("Product Price")
        refund_amount = source_row.financial_components.get("Refund Amount")
        if product_price is None or refund_amount is None:
            raise StatementCommitBlocked(
                f"Statement SKU source row {source_row.source_row_number} lacks Product Price or Refund Amount."
            )
        sku_rows.append(_sku_row(statement, audit, source_row, match, product_price, refund_amount))
        matched_item_keys.append((source_row.order_id, match.invoice_item_index))
    rows.extend(sku_rows)
    rows.extend(_adjustment_row(statement, audit, adjustment) for adjustment in statement.adjustments)

    duplicate_keys = {key for key, count in Counter(matched_item_keys).items() if count > 1}
    item_updates = []
    for source_row in statement.sku_rows:
        match = match_by_source_row[source_row.source_row_number]
        assert match.invoice_item_index is not None
        key = (source_row.order_id, match.invoice_item_index)
        if key in duplicate_keys:
            continue
        product_price = source_row.financial_components["Product Price"]
        refund_amount = source_row.financial_components["Refund Amount"]
        assert product_price is not None and refund_amount is not None
        item_updates.append(InvoiceItemStatementUpdate(
            order_id=source_row.order_id,
            item_index=match.invoice_item_index,
            statement_product_price=product_price,
            statement_refund_amount=refund_amount,
            statement_net_selling_amount=product_price + refund_amount,
        ))

    order_updates = tuple(
        InvoiceOrderStatementUpdate(
            order_id=comparison.order_id,
            payout_completed_date=comparison.payout_completed_date,
            payment_status="RELEASED",
            income_type=comparison.resulting_income_type,
            difference=comparison.difference,
        )
        for comparison in comparisons
    )
    return StatementCommitPlan(
        statement=statement,
        audit=audit,
        rows=tuple(rows),
        order_comparisons=comparisons,
        invoice_order_updates=order_updates,
        invoice_item_updates=tuple(item_updates),
    )


def validate_current_statement_state(
    plan: StatementCommitPlan,
    state: StatementCommitState,
    *,
    sku_matching_is_current: bool,
) -> tuple[str, ...]:
    """Validate a fresh authoritative snapshot immediately before a write."""

    reasons = []
    references = tuple(
        reference
        for reference in state.committed_statements
        if reference.commit_status == "COMMITTED"
    )
    if any(reference.file_hash == plan.statement.file_hash for reference in references):
        reasons.append("ALREADY_IMPORTED")
    elif any(
        reference.statement_period_from == plan.statement.statement_period_from
        and reference.statement_period_to == plan.statement.statement_period_to
        for reference in references
    ):
        reasons.append("POSSIBLE_REVISION")

    for comparison in plan.order_comparisons:
        current_order = state.orders.get(comparison.order_id)
        if current_order is None:
            reasons.append(f"UNMATCHED_ORDER:{comparison.order_id}")
            continue
        current = _compare_order_by_id(
            comparison.order_id,
            plan.statement.order_rows,
            current_order,
        )
        if (
            current.comparison_source != comparison.comparison_source
            or current.comparison_amount != comparison.comparison_amount
        ):
            reasons.append(f"COMPARISON_STATE_CHANGED:{comparison.order_id}")
    if not sku_matching_is_current:
        reasons.append("SKU_MATCHING_STATE_CHANGED")
    return tuple(reasons)


def write_statement_plan_if_current(
    plan: StatementCommitPlan,
    *,
    reload_state: Callable[[], StatementCommitState],
    sku_matching_is_current: Callable[[], bool],
    writer: StatementAtomicWriter,
    commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
) -> StatementCommitAttempt:
    """Commit only while the single-instance lock covers preflight and write."""

    with commit_lock.acquire():
        reasons = validate_current_statement_state(
            plan,
            reload_state(),
            sku_matching_is_current=sku_matching_is_current(),
        )
        if reasons:
            return StatementCommitAttempt(False, reasons)
        try:
            writer.write_statement_batch(plan)
        except StatementWriteNotApplied:
            return StatementCommitAttempt(False, ("WRITE_NOT_APPLIED",))
        return StatementCommitAttempt(True, ())


def _compare_order(
    row: SettlementIncomeRow,
    order: CanonicalInvoiceOrder | None,
) -> StatementOrderComparison:
    if order is None or row.total_released_amount is None:
        return StatementOrderComparison(row.order_id, None, None, None, "", row.payout_completed_date, None)
    if order.final_amount is not None:
        source, amount = "Final Amount", order.final_amount
    elif order.order_income is not None:
        source, amount = "Order Income", order.order_income
    else:
        return StatementOrderComparison(row.order_id, None, None, None, "", row.payout_completed_date, None)
    difference = row.total_released_amount - amount
    income_type = (order.income_type or "").casefold()
    return StatementOrderComparison(
        order_id=row.order_id,
        comparison_source=source,
        comparison_amount=amount,
        difference=difference,
        reconciliation_status="MATCHED" if abs(difference) <= MONEY_TOLERANCE else "DIFFERENT",
        payout_completed_date=row.payout_completed_date,
        resulting_income_type="Final" if income_type in {"estimated", "final"} else order.income_type,
    )


def _validate_statement_row_identity(statement: ParsedShopeeWeeklyStatement) -> None:
    identities = [
        (record_type, row.sequence_no)
        for record_type, rows in (
            ("ORDER", statement.order_rows),
            ("SKU", statement.sku_rows),
            ("ADJUSTMENT", statement.adjustments),
        )
        for row in rows
    ]
    blank = [record_type for record_type, sequence_no in identities if not sequence_no]
    if blank:
        raise StatementCommitBlocked(
            "Statement source sequence is missing for record type(s): "
            + ", ".join(sorted(set(blank)))
        )
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise StatementCommitBlocked(
            "Statement stable row identity is duplicated: " + ", ".join(
                f"{record_type}/{sequence_no}" for record_type, sequence_no in duplicates
            )
        )


def _compare_order_by_id(
    order_id: str,
    rows: Sequence[SettlementIncomeRow],
    order: CanonicalInvoiceOrder,
) -> StatementOrderComparison:
    row = next(row for row in rows if row.order_id == order_id)
    return _compare_order(row, order)


def _common(statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit, record_type: str, sequence_no: str, source_row: int) -> dict[str, object]:
    return {
        "statement_batch_id": audit.statement_batch_id,
        "record_type": record_type,
        "sequence_no": sequence_no,
        "platform": "Shopee",
        "statement_source_filename": statement.source_filename,
        "statement_file_hash": statement.file_hash,
        "statement_period_from": statement.statement_period_from,
        "statement_period_to": statement.statement_period_to,
        "statement_uploaded_at": audit.uploaded_at,
        "statement_uploaded_by": audit.uploaded_by,
        "statement_order_count": len(statement.order_rows),
        "statement_sku_count": len(statement.sku_rows),
        "statement_summary_total_released": statement.summary_total_released,
        "statement_adjustment_control_total": statement.adjustment_control_total,
        "validation_status": "PASSED",
        "commit_status": "COMMITTED",
        "statement_source_row_number": source_row,
        "committed_at": audit.committed_at,
    }


def _order_row(statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit, row: SettlementIncomeRow, comparison: StatementOrderComparison) -> tuple[str, ...]:
    values = _common(statement, audit, "ORDER", row.sequence_no, row.source_row_number)
    values.update({
        "order_id": row.order_id, "order_creation_date": row.order_creation_date,
        "payout_completed_date": row.payout_completed_date, "release_channel": row.release_channel,
        "order_type": row.order_type, "total_released_amount": row.total_released_amount,
        "statement_product_id": row.product_id, "statement_product_name": row.product_name,
        "statement_product_price": row.financial_components.get("Product Price"),
        "statement_refund_amount": row.financial_components.get("Refund Amount"),
        "comparison_source": comparison.comparison_source,
        "comparison_amount": comparison.comparison_amount, "difference": comparison.difference,
        "reconciliation_status": comparison.reconciliation_status,
    })
    return _serialize_statement_row(values)


def _sku_row(statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit, row: SettlementIncomeRow, match: StatementItemMatch, product_price: Decimal, refund_amount: Decimal) -> tuple[str, ...]:
    values = _common(statement, audit, "SKU", row.sequence_no, row.source_row_number)
    values.update({
        "order_id": row.order_id, "order_creation_date": row.order_creation_date,
        "payout_completed_date": row.payout_completed_date, "release_channel": row.release_channel,
        "order_type": row.order_type, "total_released_amount": row.total_released_amount,
        "statement_product_id": row.product_id, "statement_product_name": row.product_name,
        "statement_product_price": product_price, "statement_refund_amount": refund_amount,
        "statement_net_selling_amount": product_price + refund_amount,
        "matched_item_index": match.invoice_item_index, "match_method": _required_business_match_method(match.match_method),
    })
    return _serialize_statement_row(values)


def _adjustment_row(statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit, adjustment: SettlementAdjustment) -> tuple[str, ...]:
    values = _common(statement, audit, "ADJUSTMENT", adjustment.sequence_no, adjustment.source_row_number)
    values.update({
        "linked_order_id": adjustment.linked_order_id,
        "payout_completed_date": adjustment.payout_completed_date,
        "adjustment_complete_date": adjustment.adjustment_complete_date,
        "adjustment_type": adjustment.adjustment_type,
        "adjustment_reason": adjustment.adjustment_reason,
        "adjustment_amount": adjustment.adjustment_amount,
    })
    return _serialize_statement_row(values)


def _required_business_match_method(method: str | None) -> str:
    display = business_match_method(method)
    if display is None:
        raise StatementCommitBlocked(f"Unsupported business match method: {method!r}.")
    return display


def _serialize_statement_row(values: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(_serialize(values.get(header)) for header in STATEMENT_DATA_HEADERS)


def _serialize(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
