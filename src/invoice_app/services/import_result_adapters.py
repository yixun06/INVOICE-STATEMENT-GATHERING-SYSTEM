"""Adapters from stable import-service outputs to the Data Import contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .batch_service import is_manual_review_record
from .validation_recovery import (
    REMOVE_DUPLICATE,
    REMOVE_SOURCE,
    REMOVE_STAGED_SOURCE,
    recovery_actions_for_source,
    source_name,
)
from .import_result_contract import (
    CommitReadiness,
    ImportResult,
    ReconciliationException,
    ReconciliationResult,
    SessionState,
    SourceSummary,
    SummaryItem,
    ValidationIssue,
    ValidationResult,
)
from .shopee_weekly_statement_service import StagedShopeeWeeklyStatement


PLATFORM_ORDERS = "Platform Orders"
SHOPEE_WEEKLY_STATEMENT = "Shopee Weekly Statement"


def adapt_platform_orders_import_result(
    *,
    batch_id: str | None,
    orders: Sequence[Mapping[str, Any]],
    products: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
    processing_errors: Sequence[Mapping[str, Any]],
    duplicate_skipped: Sequence[Mapping[str, Any]],
    unsupported_files: Sequence[Mapping[str, Any]],
) -> ImportResult:
    """Project the existing Platform Order session payload without mutating it."""

    manual_reviews = tuple(item for item in reviews if is_manual_review_record(item))
    blocking_issues = tuple(
        ValidationIssue(
            layer="ingestion",
            severity="error",
            blocking=True,
            reason=str(item.get("reason") or item.get("error") or item.get("message") or "Processing error"),
            affected_item=source_name(dict(item)),
            evidence=dict(item),
            suggested_action="Remove this failed source from current staging, then review the revalidated batch.",
            recovery_actions=recovery_actions_for_source(
                source=source_name(dict(item)),
                action_type=REMOVE_SOURCE,
                remove_label="Remove source from current batch",
            ),
        )
        for item in processing_errors
    )
    warnings: list[ValidationIssue] = [
        ValidationIssue(
            layer="manual_review",
            severity="warning",
            blocking=False,
            reason=str(item.get("reason") or "Manual Review is required."),
            affected_item=source_name(dict(item)),
            evidence=dict(item),
            suggested_action="Use the existing Manual Review workflow or remove this source from current staging.",
            recovery_actions=recovery_actions_for_source(
                source=source_name(dict(item)),
                action_type=REMOVE_SOURCE,
                remove_label="Remove source from current batch",
            ),
        )
        for item in manual_reviews
    ]
    warnings.extend(
        ValidationIssue(
            layer="duplicate",
            severity="warning",
            blocking=False,
            reason=str(item.get("reason") or item.get("message") or "Duplicate source in current batch."),
            affected_item=source_name(dict(item)),
            evidence=dict(item),
            suggested_action="Remove the duplicate source from current staging, then revalidate.",
            recovery_actions=recovery_actions_for_source(
                source=source_name(dict(item)),
                action_type=REMOVE_DUPLICATE,
                remove_label="Remove Duplicate",
            ),
            status="Open",
        )
        for item in duplicate_skipped
    )
    warnings.extend(
        ValidationIssue(
            layer="ingestion",
            severity="warning",
            blocking=False,
            reason=str(item.get("reason") or item.get("message") or "Unsupported file."),
            affected_item=source_name(dict(item)),
            evidence=dict(item),
            suggested_action="Remove this unsupported source from current staging, then revalidate.",
            recovery_actions=recovery_actions_for_source(
                source=source_name(dict(item)),
                action_type=REMOVE_SOURCE,
                remove_label="Remove source from current batch",
            ),
            status="Open",
        )
        for item in unsupported_files
    )
    readiness_reasons: list[str] = []
    if not orders:
        readiness_reasons.append("No accepted orders are available.")
    if manual_reviews:
        readiness_reasons.append("Manual Review records remain.")
    if processing_errors:
        readiness_reasons.append("Processing errors remain.")
    ready = not readiness_reasons
    reconciliation = ReconciliationResult(
        available=False,
        status="Not Applicable",
        source_specific_details={"reason": "Platform Orders have no cross-source reconciliation data yet."},
    )
    source_details = {
        "orders": tuple(orders),
        "products": tuple(products),
        "manual_review": tuple(reviews),
        "processing_errors": tuple(processing_errors),
        "duplicate_skipped": tuple(duplicate_skipped),
        "unsupported_files": tuple(unsupported_files),
        "temp_test_only_sync_supported": True,
        "show_platform_order_outcomes": True,
    }
    return ImportResult(
        source_type=PLATFORM_ORDERS,
        batch_status="Ready to Commit" if ready else ("Not Ready" if batch_id else "No Active Batch"),
        source_summary=SourceSummary(
            title="Platform Orders result",
            items=(
                SummaryItem("Accepted Orders", len(orders)),
                SummaryItem("Product Rows", len(products)),
                SummaryItem("Manual Review", len(manual_reviews)),
                SummaryItem("Processing Errors", len(processing_errors)),
            ),
            empty_message="Upload and process Platform Orders to begin validation.",
        ),
        validation=ValidationResult(blocking_issues=blocking_issues, warnings=tuple(warnings)),
        reconciliation=reconciliation,
        commit_readiness=CommitReadiness(
            ready=ready,
            status="Ready to Commit" if ready else "Not Ready",
            reasons=tuple(readiness_reasons),
        ),
        session_state=SessionState(
            applied_to_current_session=bool(batch_id),
            label="Applied to Current Session" if batch_id else "No Active Session Batch",
            batch_id=batch_id,
        ),
        source_specific_details=source_details,
    )


def adapt_shopee_weekly_statement_import_result(
    stage: StagedShopeeWeeklyStatement | None,
    *,
    batch_id: str | None,
) -> ImportResult:
    """Project weekly-statement staging output without changing its semantics."""

    validation_blockers: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    statement = stage.statement if stage else None
    stage_source = stage.source_filename if stage else None
    stage_recovery_actions = recovery_actions_for_source(
        source=stage_source,
        action_type=REMOVE_STAGED_SOURCE,
        remove_label="Remove staged source",
    )
    if stage:
        for issue in stage.validation_issues:
            validation_blockers.append(
                ValidationIssue(
                    layer="statement_validation",
                    severity="error",
                    blocking=True,
                    reason=issue.message,
                    affected_item=stage_source,
                    evidence={"code": issue.code, "source_filename": stage_source},
                    suggested_action="View the validation detail or remove the staged source before uploading a replacement.",
                    recovery_actions=stage_recovery_actions,
                )
            )
        for reason in stage.rejection_reasons:
            validation_blockers.append(
                ValidationIssue(
                    layer="statement_validation",
                    severity="error",
                    blocking=True,
                    reason=str(reason),
                    affected_item=stage_source,
                    evidence={"source_filename": stage_source},
                    suggested_action="View the rejection detail or remove the staged source before uploading a replacement.",
                    recovery_actions=stage_recovery_actions,
                )
            )
        for reason in stage.review_reasons:
            warnings.append(
                ValidationIssue(
                    layer="statement_review",
                    severity="warning",
                    blocking=False,
                    reason=str(reason),
                    affected_item=stage_source,
                    evidence={"source_filename": stage_source},
                    suggested_action="Review this statement in the existing workflow.",
                    recovery_actions=stage_recovery_actions,
                )
            )
        if stage.duplicate_status:
            warnings.append(
                ValidationIssue(
                    layer="duplicate",
                    severity="warning",
                    blocking=False,
                    reason="This statement matches an existing session statement.",
                    affected_item=stage_source,
                    evidence={"duplicate_status": stage.duplicate_status, "source_filename": stage_source},
                    recovery_actions=stage_recovery_actions,
                    status="Open",
                )
            )
    reconciliation_exceptions: list[ReconciliationException] = []
    reconciliation_summary: tuple[SummaryItem, ...] = ()
    reconciliation_available = bool(stage and statement)
    if stage and statement:
        order_reconciliations = tuple(stage.order_reconciliations)
        adjustment_reconciliations = tuple(stage.adjustment_reconciliations)
        shipping_exceptions = tuple(statement.shipping_fee_discrepancies)
        for item in order_reconciliations:
            if item.status != "Matched":
                reconciliation_exceptions.append(
                    ReconciliationException(
                        status=item.status,
                        affected_item=item.order_id,
                        evidence={"difference": item.difference, "order_id": item.order_id},
                    )
                )
        for item in adjustment_reconciliations:
            if item.status == "Unmatched Adjustment":
                reconciliation_exceptions.append(
                    ReconciliationException(
                        status=item.status,
                        affected_item=item.linked_order_id,
                        evidence={"amount": getattr(item, "amount", None)},
                    )
                )
        reconciliation_summary = (
            SummaryItem("Matched", sum(item.status == "Matched" for item in order_reconciliations)),
            SummaryItem("Different", sum(item.status == "Different" for item in order_reconciliations)),
            SummaryItem("Estimated Only", sum(item.status == "Estimated Only" for item in order_reconciliations)),
            SummaryItem("Unmatched Orders", sum(item.status == "Unmatched Order" for item in order_reconciliations)),
            SummaryItem("Unmatched Adjustments", sum(item.status == "Unmatched Adjustment" for item in adjustment_reconciliations)),
            SummaryItem("Shipping exceptions", len(shipping_exceptions)),
        )

    readiness_reasons: list[str] = []
    if not stage:
        readiness_reasons.append("No staged weekly statement is available.")
    elif not stage.eligible_for_future_atomic_commit:
        readiness_reasons.append("The staged statement is not eligible for future atomic commit.")
    if validation_blockers:
        readiness_reasons.append("Blocking statement validation issues remain.")
    ready = not readiness_reasons
    source_details = {
        "stage": stage,
        "statement": statement,
        "order_reconciliations": tuple(stage.order_reconciliations) if stage else (),
        "adjustment_reconciliations": tuple(stage.adjustment_reconciliations) if stage else (),
        "shipping_fee_discrepancies": tuple(statement.shipping_fee_discrepancies) if statement else (),
    }
    summary_items = (
        SummaryItem("Statement Period", (f"{statement.statement_period_from:%d/%m/%Y} – {statement.statement_period_to:%d/%m/%Y}" if statement is not None else "—")),
        SummaryItem("Status", stage.result if stage else "—"),
        SummaryItem("Order Rows", len(getattr(statement, "order_rows", ()) or ())),
        SummaryItem("SKU Rows", len(getattr(statement, "sku_rows", ()) or ())),
        SummaryItem("Total Released", getattr(statement, "summary_total_released", "—")),
        SummaryItem("Adjustment Total", getattr(statement, "adjustment_control_total", "—")),
    )
    return ImportResult(
        source_type=SHOPEE_WEEKLY_STATEMENT,
        batch_status="Ready to Commit" if ready else ("Not Ready" if stage else "No Active Batch"),
        source_summary=SourceSummary(
            title="Shopee Weekly Statement result",
            items=summary_items,
            empty_message="Upload a native Shopee Weekly Statement .xlsx file to begin validation.",
        ),
        validation=ValidationResult(blocking_issues=tuple(validation_blockers), warnings=tuple(warnings)),
        reconciliation=ReconciliationResult(
            available=reconciliation_available,
            status="Available" if reconciliation_available else "Not Available",
            summary=reconciliation_summary,
            exceptions=tuple(reconciliation_exceptions),
            source_specific_details=source_details,
        ),
        commit_readiness=CommitReadiness(
            ready=ready,
            status="Ready to Commit" if ready else "Not Ready",
            reasons=tuple(dict.fromkeys(readiness_reasons)),
        ),
        session_state=SessionState(
            applied_to_current_session=bool(stage and batch_id),
            label="Applied to Current Session" if stage and batch_id else "No Active Session Batch",
            batch_id=batch_id,
        ),
        source_specific_details=source_details,
    )
