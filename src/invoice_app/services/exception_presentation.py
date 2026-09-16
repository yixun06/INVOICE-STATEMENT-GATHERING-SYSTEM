"""Presentation-only grouping for Data Import issues."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .historical_invoice_intake import IntakeStatus, InvoiceIntakeEntry
from .import_result_contract import ImportResult, RecoveryAction, ValidationIssue
from .validation_recovery import REMOVE_SOURCE, recovery_actions_for_source


@dataclass(frozen=True)
class PresentedException:
    category: str
    reason: str
    blocking: bool
    evidence: Mapping[str, Any]
    recovery_actions: tuple[RecoveryAction, ...] = ()


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
