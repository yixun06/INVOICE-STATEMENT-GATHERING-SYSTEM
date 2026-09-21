"""Presentation-only grouping for Data Import issues."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .historical_invoice_intake import IntakeStatus, InvoiceIntakeEntry
from .import_result_contract import ImportResult, RecoveryAction, ValidationIssue
from .validation_recovery import REMOVE_SOURCE, recovery_actions_for_source


MISSING_INVOICE_COVERAGE_REASON = (
    "no persisted Invoice order coverage is available."
)
MISSING_INVOICE_ITEMS_REASON = (
    "persisted Invoice order exists but has no Invoice_Items coverage."
)


@dataclass(frozen=True)
class PresentedException:
    category: str
    reason: str
    blocking: bool
    evidence: Mapping[str, Any]
    recovery_actions: tuple[RecoveryAction, ...] = ()


@dataclass(frozen=True)
class StatementReviewGuidance:
    """Plain-language, evidence-backed guidance for one Statement review item.

    This is deliberately presentation-only.  It projects the reconciliation
    result already produced by the authoritative service; it does not choose a
    product, change Product Master data, or reinterpret any financial value.
    """

    category: str
    problem: str
    fact_summary: str
    next_step: str
    resolution_area: str
    evidence_rows: tuple[Mapping[str, Any], ...] = ()
    additional_problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExceptionPresentationItem:
    key: str
    scope: str
    title: str
    summary: str
    issues: tuple[PresentedException, ...]
    blocking: bool
    order_id: str | None = None
    source: str | None = None
    platform: str | None = None
    recovery_actions: tuple[RecoveryAction, ...] = ()
    action_hint: str = "View details"
    statement_guidance: StatementReviewGuidance | None = None

    @property
    def issue_count(self) -> int:
        return len(self.issues)


@dataclass(frozen=True)
class ExceptionWorkQueue:
    items: tuple[ExceptionPresentationItem, ...]
    source_issue_count: int

    @property
    def represented_issue_count(self) -> int:
        return sum(item.issue_count for item in self.items)

    @property
    def blocking_issue_count(self) -> int:
        return sum(issue.blocking for item in self.items for issue in item.issues)


def missing_invoice_order_ids(queue: ExceptionWorkQueue) -> tuple[str, ...]:
    """Project existing missing-coverage blockers into a compact order list."""

    return tuple(
        item.order_id
        for item in queue.items
        if item.order_id
        and any(
            MISSING_INVOICE_COVERAGE_REASON in issue.reason
            for issue in item.issues
        )
    )


def build_exception_work_queue(result: ImportResult) -> ExceptionWorkQueue:
    """Group existing validation issues without changing or dropping them."""

    source_issues = (
        *result.validation.blocking_issues,
        *result.validation.warnings,
    )
    known_order_ids = _known_order_ids(result)
    grouped: OrderedDict[str, list[tuple[ValidationIssue, PresentedException]]] = (
        OrderedDict()
    )
    contexts: dict[str, tuple[str, str | None, str | None, str | None]] = {}

    for index, issue in enumerate(source_issues):
        order_id = _issue_order_id(issue, known_order_ids)
        source = _issue_source(issue, order_id)
        platform = _text(issue.evidence.get("platform"))
        if issue.layer == "duplicate":
            group_key = "category:duplicate"
            scope = "Source"
            title = "Duplicate sources"
        elif order_id:
            group_key = f"order:{order_id}"
            scope = "Order"
            title = order_id
        elif result.source_type == "Shopee Weekly Statement":
            group_key = f"statement:{index}"
            scope = "Statement"
            title = source or "Statement"
        elif source:
            group_key = f"source:{issue.layer}:{source}"
            scope = "Source"
            title = source
        else:
            group_key = f"other:{index}"
            scope = "Other"
            title = "Current batch"

        presented = PresentedException(
            category=issue.layer,
            reason=issue.reason,
            blocking=issue.blocking,
            evidence=issue.evidence,
            recovery_actions=issue.recovery_actions,
        )
        grouped.setdefault(group_key, []).append((issue, presented))
        contexts.setdefault(group_key, (scope, order_id, source, platform))

    items = tuple(
        _build_item(group_key, pairs, contexts[group_key])
        for group_key, pairs in grouped.items()
    )
    if result.source_type == "Shopee Weekly Statement":
        items = tuple(
            replace(
                item,
                statement_guidance=_statement_review_guidance(result, item),
            )
            for item in items
        )
    queue = ExceptionWorkQueue(items=items, source_issue_count=len(source_issues))
    if queue.represented_issue_count != queue.source_issue_count:
        raise ValueError("Exception presentation dropped authoritative issues.")
    return queue


def build_historical_exception_work_queue(
    entries: Sequence[InvoiceIntakeEntry],
) -> ExceptionWorkQueue:
    """Present non-NEW historical classifications as source-level work."""

    items: list[ExceptionPresentationItem] = []
    for index, entry in enumerate(entries):
        if entry.status is IntakeStatus.NEW:
            continue
        actions = recovery_actions_for_source(
            source=entry.source_filename,
            action_type=REMOVE_SOURCE,
            remove_label="Remove source from current batch",
            include_details=False,
        )
        reason = entry.message or f"Historical status: {entry.status.value}."
        issue = PresentedException(
            category="historical_status",
            reason=reason,
            blocking=True,
            evidence={
                "source_filename": entry.source_filename,
                "order_id": entry.order_id,
                "historical_status": entry.status.value,
            },
            recovery_actions=actions,
        )
        items.append(
            ExceptionPresentationItem(
                key=f"historical:{entry.staging_id}:{index}",
                scope="Order" if entry.order_id else "Source",
                title=entry.order_id or entry.source_filename,
                summary=reason,
                issues=(issue,),
                blocking=True,
                order_id=entry.order_id,
                source=entry.source_filename,
                recovery_actions=actions,
                action_hint=actions[0].label if actions else "View details",
            )
        )
    return ExceptionWorkQueue(items=tuple(items), source_issue_count=len(items))


def _build_item(
    group_key: str,
    pairs: list[tuple[ValidationIssue, PresentedException]],
    context: tuple[str, str | None, str | None, str | None],
) -> ExceptionPresentationItem:
    scope, order_id, source, platform = context
    source_issues = tuple(pair[0] for pair in pairs)
    issues = tuple(pair[1] for pair in pairs)
    actions = _unique_actions(
        action
        for issue in source_issues
        for action in issue.recovery_actions
        if action.allowed
    )
    categories = {issue.layer for issue in source_issues}
    first_reason = _trim_order_prefix(source_issues[0].reason, order_id)
    summary = (
        f"{first_reason} +{len(source_issues) - 1} more"
        if len(source_issues) > 1
        else first_reason
    )
    if group_key == "category:duplicate":
        title = "Duplicate sources"
        sources = {
            value
            for issue in source_issues
            if (value := _issue_source(issue, None))
        }
        source = next(iter(sources)) if len(sources) == 1 else None
    else:
        title = order_id or source or "Current batch"
    destructive_action = next(
        (action for action in actions if action.destructive),
        None,
    )
    action_hint = (
        "Review form below"
        if "manual_review" in categories
        else destructive_action.label if destructive_action else "View details"
    )
    return ExceptionPresentationItem(
        key=group_key,
        scope=scope,
        title=title,
        summary=summary,
        issues=issues,
        blocking=any(issue.blocking for issue in source_issues),
        order_id=order_id,
        source=source,
        platform=platform,
        recovery_actions=actions,
        action_hint=action_hint,
    )


def _known_order_ids(result: ImportResult) -> tuple[str, ...]:
    order_ids: list[str] = []
    batch = result.source_specific_details.get("reconciliation_v2")
    for order_result in getattr(batch, "orders", ()):
        value = _text(getattr(order_result.evidence, "order_id", None))
        if value and value not in order_ids:
            order_ids.append(value)
    for exception in result.reconciliation.exceptions:
        value = _text(exception.affected_item)
        if value and value not in order_ids:
            order_ids.append(value)
    return tuple(order_ids)


def _issue_order_id(
    issue: ValidationIssue,
    known_order_ids: tuple[str, ...],
) -> str | None:
    evidence_order_id = _text(issue.evidence.get("order_id"))
    if evidence_order_id:
        return evidence_order_id
    affected_item = _text(issue.affected_item)
    for order_id in known_order_ids:
        if affected_item == order_id or affected_item.startswith(f"{order_id} /"):
            return order_id
        if issue.reason.startswith(f"{order_id}:"):
            return order_id
    return None


def _issue_source(issue: ValidationIssue, order_id: str | None) -> str | None:
    for key in ("source_pdf", "filename", "source_filename"):
        value = _text(issue.evidence.get(key))
        if value:
            return value
    affected_item = _text(issue.affected_item)
    if affected_item and affected_item != order_id and " / source row " not in affected_item:
        return affected_item
    return None


def _unique_actions(actions: Any) -> tuple[RecoveryAction, ...]:
    unique: OrderedDict[str, RecoveryAction] = OrderedDict()
    for action in actions:
        unique.setdefault(action.action_id, action)
    return tuple(unique.values())


def _trim_order_prefix(reason: str, order_id: str | None) -> str:
    prefix = f"{order_id}:" if order_id else None
    if prefix and reason.startswith(prefix):
        return reason[len(prefix):].strip()
    return reason


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _statement_review_guidance(
    result: ImportResult,
    item: ExceptionPresentationItem,
) -> StatementReviewGuidance:
    """Use V2 evidence for the user-facing Statement explanation when present."""

    batch = result.source_specific_details.get("reconciliation_v2")
    statement = result.source_specific_details.get("statement")
    invoice_items = tuple(result.source_specific_details.get("invoice_items", ()))
    order_result = next(
        (
            candidate
            for candidate in getattr(batch, "orders", ())
            if getattr(getattr(candidate, "evidence", None), "order_id", None)
            == item.order_id
        ),
        None,
    )
    if order_result is None:
        return _generic_statement_guidance(item)

    evidence = order_result.evidence
    summary = order_result.summary
    statement_rows = {
        (getattr(row, "source_row_number", None), getattr(row, "sequence_no", None)): row
        for row in getattr(statement, "sku_rows", ())
    }
    order_items = tuple(
        source_item
        for source_item in invoice_items
        if getattr(source_item, "order_id", None) == item.order_id
    )

    if any(
        MISSING_INVOICE_COVERAGE_REASON in issue.reason
        for issue in item.issues
    ):
        return StatementReviewGuidance(
            category="Missing Invoice",
            problem="The matching Invoice has not been imported for this Statement order.",
            fact_summary=(
                "The Statement contains this Order ID, but no persisted Invoice "
                "source is available for comparison."
            ),
            next_step="Upload or persist the matching Invoice, then refresh Statement validation.",
            resolution_area="Invoice import",
            evidence_rows=(
                {
                    "Source": "Statement",
                    "Field": "Order ID",
                    "Value": item.order_id or "Not provided",
                    "Reference": "Statement order coverage",
                },
                {
                    "Source": "Invoice",
                    "Field": "Persisted source",
                    "Value": "Not available",
                    "Reference": "Invoice coverage",
                },
            ),
        )

    if any(
        MISSING_INVOICE_ITEMS_REASON in issue.reason
        for issue in item.issues
    ):
        return StatementReviewGuidance(
            category="Invoice items missing",
            problem="The Invoice order exists, but its item rows are unavailable.",
            fact_summary=(
                "The persisted Invoice_Orders row is present, but no matching "
                "Invoice_Items rows are available for product reconciliation."
            ),
            next_step=(
                "Restore or re-import the authoritative Invoice item rows, then "
                "refresh Statement validation."
            ),
            resolution_area="Invoice data recovery",
            evidence_rows=(
                {
                    "Source": "Invoice_Orders",
                    "Field": "Order ID",
                    "Value": item.order_id or "Not provided",
                    "Reference": "Persisted order coverage",
                },
                {
                    "Source": "Invoice_Items",
                    "Field": "Matching rows",
                    "Value": "0",
                    "Reference": "Persisted item coverage",
                },
            ),
        )

    identity_scope = _enum_text(getattr(summary, "identity_scope", None))
    reasons = {
        _enum_text(reason) for reason in getattr(summary, "reasons", ())
    }
    if identity_scope == "UNRESOLVED":
        identity = next(
            (
                candidate
                for candidate in getattr(evidence, "identities", ())
                if _enum_text(getattr(candidate, "identity_scope", None))
                == "UNRESOLVED"
            ),
            None,
        )
        return _unresolved_identity_guidance(
            identity=identity,
            statement_rows=statement_rows,
            invoice_items=order_items,
            product_family_candidates=getattr(
                getattr(batch, "product_family_snapshot", None), "candidates", ()
            ),
            product_master_gap="PRODUCT_MASTER_IDENTITY_MISSING" in reasons,
        )

    if not getattr(summary, "merchandise_reconciled", True):
        comparison = next(
            (
                candidate
                for candidate in getattr(evidence, "merchandise", ())
                if getattr(candidate, "scope_key", None) == "ORDER_CONTROL"
            ),
            None,
        )
        invoice_value = getattr(comparison, "invoice_value", None)
        statement_value = getattr(comparison, "statement_value", None)
        difference = getattr(comparison, "difference", None)
        return StatementReviewGuidance(
            category="Product Price difference",
            problem="Invoice and Statement Product Price do not reconcile.",
            fact_summary=(
                f"Invoice Product Price: {_money(invoice_value)}; Statement Product "
                f"Price: {_money(statement_value)}; Difference: {_money(difference)}."
            ),
            next_step="Compare the source Product Price evidence, then refresh validation. Do not alter either value in this review.",
            resolution_area="Statement review",
            evidence_rows=(
                {
                    "Source": "Invoice",
                    "Field": "Product Price",
                    "Value": _money(invoice_value),
                    "Reference": "Order control",
                },
                {
                    "Source": "Statement",
                    "Field": "Product Price",
                    "Value": _money(statement_value),
                    "Reference": "Order control",
                },
                {
                    "Source": "Comparison",
                    "Field": "Difference",
                    "Value": _money(difference),
                    "Reference": "RM0.02 tolerance",
                },
            ),
        )

    if _enum_text(getattr(summary, "settlement_basis", None)) == "NONE":
        settlement = getattr(evidence, "settlement", None)
        return StatementReviewGuidance(
            category="Settlement needs review",
            problem="The final seller settlement is not fully explained by the source evidence.",
            fact_summary=(
                f"Invoice basis: {_money(getattr(settlement, 'invoice_basis', None))}; "
                f"Statement settlement: {_money(getattr(settlement, 'statement_total', None))}; "
                f"unexplained residual: {_money(getattr(settlement, 'unexplained_residual', None))}."
            ),
            next_step="Review the Statement components and obtain the required business confirmation. No settlement adjustment is made automatically.",
            resolution_area="Finance review",
            evidence_rows=(
                {
                    "Source": "Invoice",
                    "Field": "Comparison basis",
                    "Value": _money(getattr(settlement, "invoice_basis", None)),
                    "Reference": "Order settlement evidence",
                },
                {
                    "Source": "Statement",
                    "Field": "Released amount",
                    "Value": _money(getattr(settlement, "statement_total", None)),
                    "Reference": "Order settlement evidence",
                },
                {
                    "Source": "Comparison",
                    "Field": "Unexplained residual",
                    "Value": _money(getattr(settlement, "unexplained_residual", None)),
                    "Reference": "After known source components",
                },
            ),
        )

    return _generic_statement_guidance(item)


def _unresolved_identity_guidance(
    *,
    identity: Any,
    statement_rows: Mapping[tuple[Any, Any], Any],
    invoice_items: Sequence[Any],
    product_family_candidates: Sequence[Any],
    product_master_gap: bool,
) -> StatementReviewGuidance:
    statement_members = tuple(getattr(identity, "statement_members", ()))
    product_id = _text(getattr(identity, "product_id", None))
    relevant_statement_rows = tuple(
        statement_rows.get(
            (
                getattr(member, "source_row_number", None),
                getattr(member, "sequence_no", None),
            )
        )
        for member in statement_members
    )
    relevant_statement_rows = tuple(row for row in relevant_statement_rows if row)
    product_master_rows = tuple(
        candidate
        for candidate in product_family_candidates
        if getattr(candidate, "product_id", None) == product_id
    )
    statement_names = _unique_values(
        getattr(row, "product_name", None) for row in relevant_statement_rows
    )
    invoice_names = _unique_values(
        getattr(row, "product_name", None) for row in invoice_items
    )
    evidence_rows: list[Mapping[str, Any]] = []
    for row in relevant_statement_rows:
        evidence_rows.extend(
            (
                {
                    "Source": "Statement",
                    "Field": "Product name",
                    "Value": _display(getattr(row, "product_name", None)),
                    "Reference": f"SKU row {getattr(row, 'source_row_number', 'not provided')}",
                },
                {
                    "Source": "Statement",
                    "Field": "Product ID",
                    "Value": _display(getattr(row, "product_id", None)),
                    "Reference": f"SKU row {getattr(row, 'source_row_number', 'not provided')}",
                },
            )
        )
    for row in invoice_items:
        item_ref = f"Invoice item {getattr(row, 'item_index', 'not provided')}"
        evidence_rows.extend(
            (
                {
                    "Source": "Invoice",
                    "Field": "Product name",
                    "Value": _display(getattr(row, "product_name", None)),
                    "Reference": item_ref,
                },
                {
                    "Source": "Invoice",
                    "Field": "Seller SKU",
                    "Value": _display(
                        getattr(row, "resolved_seller_sku", None)
                        or getattr(row, "seller_sku", None)
                    ),
                    "Reference": item_ref,
                },
            )
        )
    for row in product_master_rows:
        evidence_rows.extend(
            (
                {
                    "Source": "Product Master",
                    "Field": "Product name",
                    "Value": _display(getattr(row, "product_name", None)),
                    "Reference": _display(getattr(row, "seller_sku", None)),
                },
                {
                    "Source": "Product Master",
                    "Field": "Parent SKU",
                    "Value": _display(getattr(row, "parent_sku", None)),
                    "Reference": _display(getattr(row, "seller_sku", None)),
                },
            )
        )
    diagnostic = _display(getattr(identity, "diagnostic", None))
    if product_master_gap:
        problem = "Product Master cannot identify the Statement product."
        next_step = "Check the exact Statement Product ID in Product Master, then refresh validation. The system will not choose a replacement product."
        resolution_area = "Product Master"
    else:
        problem = "The system cannot prove which Invoice product matches the Statement product."
        next_step = "Compare the exact Statement, Invoice, and Product Master evidence. The system will not guess a product match."
        resolution_area = "Statement review"
    return StatementReviewGuidance(
        category=("Product Master needs review" if product_master_gap else "Product identity cannot be proven"),
        problem=problem,
        fact_summary=(
            f"Statement product name: {_display(statement_names)}; "
            f"Invoice product name: {_display(invoice_names)}. {diagnostic}"
        ),
        next_step=next_step,
        resolution_area=resolution_area,
        evidence_rows=tuple(evidence_rows),
    )


def _generic_statement_guidance(
    item: ExceptionPresentationItem,
) -> StatementReviewGuidance:
    return StatementReviewGuidance(
        category="Statement needs review",
        problem="This Statement item needs review before the batch can continue.",
        fact_summary=(
            "The current result does not provide a structured source comparison for "
            "this item. Open the technical details to inspect the original evidence."
        ),
        next_step="Inspect the original evidence and use only the safe recovery action shown there.",
        resolution_area="Statement review",
    )


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _unique_values(values: Any) -> str | None:
    unique = tuple(dict.fromkeys(text for value in values if (text := _text(value))))
    return ", ".join(unique) or None


def _display(value: Any) -> str:
    return _text(value) or "Not provided"


def _money(value: Any) -> str:
    if value is None:
        return "Not provided"
    try:
        return f"RM {value:.2f}"
    except (TypeError, ValueError):
        return _display(value)
