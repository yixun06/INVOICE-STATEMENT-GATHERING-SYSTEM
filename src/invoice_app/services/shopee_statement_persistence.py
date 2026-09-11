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
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    ShippingFeeDiscrepancy,
    ServiceFeeDetail,
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
from src.invoice_app.services.uat2_persistence_schema import (
    STATEMENT_DATA_HEADERS,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
)
from src.invoice_app.services.statement_reconciliation import (
    MISSING_COMPARISON_EVIDENCE,
    UNMATCHED_ORDER,
    compare_statement_order,
)


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


@dataclass(frozen=True)
class InvoiceOrderStatementUpdate:
    order_id: str
    payout_completed_date: date | None
    payment_status: str
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
    financial_component_rows: tuple[tuple[str, ...], ...]
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
    unmatched = [
        comparison.order_id
        for comparison in comparisons
        if comparison.reconciliation_status == UNMATCHED_ORDER
    ]
    if unmatched:
        raise StatementCommitBlocked(
            "Statement target Order ID coverage is incomplete: "
            + ", ".join(unmatched[:5])
        )
    missing_evidence = [
        comparison.order_id
        for comparison in comparisons
        if comparison.reconciliation_status == MISSING_COMPARISON_EVIDENCE
    ]
    if missing_evidence:
        raise StatementCommitBlocked(
            "Invoice comparison evidence is missing: "
            + ", ".join(missing_evidence[:5])
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
    financial_component_rows = _financial_component_rows(statement, audit)

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
            difference=comparison.difference,
        )
        for comparison in comparisons
    )
    return StatementCommitPlan(
        statement=statement,
        audit=audit,
        rows=tuple(rows),
        financial_component_rows=financial_component_rows,
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
            or current.difference != comparison.difference
            or current.reconciliation_status != comparison.reconciliation_status
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
    if row.total_released_amount is None:
        return StatementOrderComparison(
            row.order_id, None, None, None, MISSING_COMPARISON_EVIDENCE,
            row.payout_completed_date,
        )
    decision = compare_statement_order(
        invoice_order_found=order is not None,
        released_amount=row.total_released_amount,
        final_amount=order.final_amount if order else None,
        order_income=order.order_income if order else None,
        income_type=order.income_type if order else None,
    )
    return StatementOrderComparison(
        order_id=row.order_id,
        comparison_source=decision.comparison_source,
        comparison_amount=decision.comparison_amount,
        difference=decision.difference,
        reconciliation_status=decision.reconciliation_status,
        payout_completed_date=row.payout_completed_date,
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


_COMPONENT_RECORD_TYPES = {
    "ORDER",
    "SKU",
    "SERVICE_FEE_DETAIL",
    "SHIPPING_FEE_DISCREPANCY",
}


def _financial_component_rows(
    statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit
) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for income_row in statement.income_rows:
        record_type = {"Order": "ORDER", "Sku": "SKU"}.get(income_row.view_by)
        if record_type is None:
            continue
        for component_name in INCOME_COMPONENT_COLUMNS:
            amount = income_row.financial_components.get(component_name)
            if amount is None:
                continue
            rows.append(_financial_component_row(
                statement=statement,
                audit=audit,
                record_type=record_type,
                source_sheet="Income",
                source_row_number=income_row.source_row_number,
                sequence_no=income_row.sequence_no,
                order_id=income_row.order_id,
                statement_product_id=income_row.product_id,
                component_name=component_name,
                component_amount=amount,
                component_note=None,
                payout_completed_date=income_row.payout_completed_date,
            ))
    for detail in statement.service_fee_details:
        rows.extend(_service_fee_component_rows(statement, audit, detail))
    for discrepancy in statement.shipping_fee_discrepancies:
        rows.extend(_shipping_discrepancy_component_rows(statement, audit, discrepancy))
    _validate_financial_component_rows(rows, statement)
    return tuple(rows)


def _service_fee_component_rows(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    detail: ServiceFeeDetail,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        _financial_component_row(
            statement=statement,
            audit=audit,
            record_type="SERVICE_FEE_DETAIL",
            source_sheet="Service Fee Details",
            source_row_number=detail.source_row_number,
            sequence_no=detail.sequence_no,
            order_id=detail.order_id,
            statement_product_id=None,
            component_name=component_name,
            component_amount=amount,
            component_note=None,
            payout_completed_date=None,
        )
        for component_name, amount in detail.components.items()
        if amount is not None
    )


def _shipping_discrepancy_component_rows(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    discrepancy: ShippingFeeDiscrepancy,
) -> tuple[tuple[str, ...], ...]:
    values: list[tuple[str, ...]] = []
    for component_name, amount in (
        ("Expected Shipping Fee:", discrepancy.expected_shipping_fee),
        (
            "Actual Shipping Fee Charged by Logistic Provider:",
            discrepancy.actual_shipping_fee,
        ),
    ):
        if amount is not None:
            values.append(_financial_component_row(
                statement=statement,
                audit=audit,
                record_type="SHIPPING_FEE_DISCREPANCY",
                source_sheet="Shipping Fee Discrepancy",
                source_row_number=discrepancy.source_row_number,
                sequence_no=None,
                order_id=discrepancy.order_id,
                statement_product_id=None,
                component_name=component_name,
                component_amount=amount,
                component_note=None,
                payout_completed_date=None,
            ))
    if discrepancy.reason:
        values.append(_financial_component_row(
            statement=statement,
            audit=audit,
            record_type="SHIPPING_FEE_DISCREPANCY",
            source_sheet="Shipping Fee Discrepancy",
            source_row_number=discrepancy.source_row_number,
            sequence_no=None,
            order_id=discrepancy.order_id,
            statement_product_id=None,
            component_name="Discrepancy reason",
            component_amount=None,
            component_note=discrepancy.reason,
            payout_completed_date=None,
        ))
    return tuple(values)


def _financial_component_row(
    *,
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    record_type: str,
    source_sheet: str,
    source_row_number: int,
    sequence_no: str | None,
    order_id: str | None,
    statement_product_id: str | None,
    component_name: str,
    component_amount: Decimal | None,
    component_note: str | None,
    payout_completed_date: date | None,
) -> tuple[str, ...]:
    values = {
        "statement_batch_id": audit.statement_batch_id,
        "statement_file_hash": statement.file_hash,
        "record_type": record_type,
        "statement_source_sheet": source_sheet,
        "statement_source_row_number": source_row_number,
        "sequence_no": sequence_no,
        "platform": "Shopee",
        "order_id": order_id,
        "statement_product_id": statement_product_id,
        "component_name": component_name,
        "component_amount": component_amount,
        "component_note": component_note,
        "statement_period_from": statement.statement_period_from,
        "statement_period_to": statement.statement_period_to,
        "payout_completed_date": payout_completed_date,
        "committed_at": audit.committed_at,
        "commit_status": "COMMITTED",
    }
    return tuple(
        _serialize(values.get(header))
        for header in STATEMENT_FINANCIAL_COMPONENT_HEADERS
    )


def _validate_financial_component_rows(
    rows: Sequence[tuple[str, ...]], statement: ParsedShopeeWeeklyStatement
) -> None:
    positions = {
        header: index
        for index, header in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    identities: set[tuple[str, str, str, str]] = set()
    for row in rows:
        if len(row) != len(STATEMENT_FINANCIAL_COMPONENT_HEADERS):
            raise StatementCommitBlocked("Financial component rows do not match the exact ledger schema.")
        record_type = row[positions["record_type"]]
        if record_type not in _COMPONENT_RECORD_TYPES:
            raise StatementCommitBlocked(f"Unsupported financial component record type: {record_type!r}.")
        identity = tuple(
            row[positions[header]]
            for header in (
                "statement_batch_id",
                "statement_source_sheet",
                "statement_source_row_number",
                "component_name",
            )
        )
        if not all(identity):
            raise StatementCommitBlocked("Financial component row has incomplete stable identity.")
        if identity in identities:
            raise StatementCommitBlocked(
                "Financial component plan contains duplicate stable identity: "
                + "/".join(identity)
            )
        identities.add(identity)
        amount = row[positions["component_amount"]]
        note = row[positions["component_note"]]
        if not amount and not note:
            raise StatementCommitBlocked("Financial component row has neither numeric amount nor text evidence.")
        if note and record_type != "SHIPPING_FEE_DISCREPANCY":
            raise StatementCommitBlocked("Text financial component evidence is only supported for Shipping Fee Discrepancy.")
        source_sheet = row[positions["statement_source_sheet"]]
        sequence_no = row[positions["sequence_no"]]
        order_id = row[positions["order_id"]]
        if record_type in {"ORDER", "SKU"}:
            expected_view = "Order" if record_type == "ORDER" else "Sku"
            if source_sheet != "Income" or not sequence_no or not order_id:
                raise StatementCommitBlocked("Income financial component row lacks traceable source identity.")
            if not any(
                source_row.view_by == expected_view
                and source_row.sequence_no == sequence_no
                and str(source_row.source_row_number)
                == row[positions["statement_source_row_number"]]
                for source_row in statement.income_rows
            ):
                raise StatementCommitBlocked("Income financial component row cannot be traced to a Statement source row.")
        elif record_type == "SERVICE_FEE_DETAIL":
            if source_sheet != "Service Fee Details" or not sequence_no or not order_id:
                raise StatementCommitBlocked("Service Fee Detail component row lacks traceable source identity.")
            if not any(
                detail.sequence_no == sequence_no
                and detail.order_id == order_id
                and str(detail.source_row_number)
                == row[positions["statement_source_row_number"]]
                for detail in statement.service_fee_details
            ):
                raise StatementCommitBlocked(
                    "Service Fee Detail component row cannot be traced to a Statement source row."
                )
        elif (
            source_sheet != "Shipping Fee Discrepancy"
            or not order_id
            or not any(
                discrepancy.order_id == order_id
                and str(discrepancy.source_row_number)
                == row[positions["statement_source_row_number"]]
                for discrepancy in statement.shipping_fee_discrepancies
            )
        ):
            raise StatementCommitBlocked(
                "Shipping discrepancy component row cannot be traced to a Statement source row."
            )


def _serialize(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
