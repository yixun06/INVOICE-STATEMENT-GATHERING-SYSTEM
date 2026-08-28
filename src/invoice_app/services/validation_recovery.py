"""Safe, session-only validation recovery for the active import batch."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .batch_service import apply_batch_rules, is_manual_review_record
from .import_result_contract import RecoveryAction


REMOVE_SOURCE = "remove_source"
REMOVE_DUPLICATE = "remove_duplicate"
REMOVE_STAGED_SOURCE = "remove_staged_source"
VIEW_DETAILS = "view_details"
_REMOVABLE_ACTION_TYPES = {REMOVE_SOURCE, REMOVE_DUPLICATE, REMOVE_STAGED_SOURCE}
_SOURCE_BUCKETS = (
    "orders",
    "products",
    "reviews",
    "duplicate_skipped",
    "unsupported_files",
    "processing_errors",
)


@dataclass(frozen=True)
class RecoveryExecution:
    action_id: str
    changed: bool
    revalidated: bool
    removed_counts: dict[str, int]
    message: str


def source_name(item: dict[str, Any]) -> str | None:
    value = item.get("source_pdf") or item.get("filename") or item.get("source_file")
    text = str(value).strip() if value is not None else ""
    return text or None


def recovery_actions_for_source(
    *,
    source: str | None,
    action_type: str,
    remove_label: str,
    include_details: bool = True,
) -> tuple[RecoveryAction, ...]:
    """Build UI actions for a known staged source without changing it."""

    if not source:
        return ()
    actions: list[RecoveryAction] = []
    if include_details:
        actions.append(_action(VIEW_DETAILS, "View details", source, destructive=False, requires_revalidation=False))
    actions.append(_action(action_type, remove_label, source, destructive=True, requires_revalidation=True))
    return tuple(actions)


def execute_current_batch_recovery(
    state: MutableMapping[str, Any],
    action: RecoveryAction,
) -> RecoveryExecution:
    """Apply an approved session-only recovery action and revalidate staging.

    This never removes archive/source files, writes a database record, or
    accepts an arbitrary action type such as Force Pass or Ignore Error.
    """

    if not action.allowed:
        raise ValueError("Recovery action is not allowed.")
    if action.action_type not in _REMOVABLE_ACTION_TYPES:
        raise ValueError(f"Unsupported recovery action: {action.action_type}")
    if not action.affected_item:
        raise ValueError("Recovery action does not identify a source file.")

    if action.action_type == REMOVE_STAGED_SOURCE:
        changed = state.pop("weekly_statement_stage", None) is not None
        return RecoveryExecution(
            action_id=action.action_id,
            changed=changed,
            revalidated=True,
            removed_counts={"weekly_statement_stage": int(changed)},
            message="Removed the staged Weekly Statement source. Upload another statement to validate it.",
        )

    source = action.affected_item
    removed_counts: dict[str, int] = {}
    for bucket in _SOURCE_BUCKETS:
        records = list(state.get(bucket, []))
        retained = [record for record in records if source_name(record) != source]
        removed_counts[bucket] = len(records) - len(retained)
        state[bucket] = retained

    _revalidate_platform_batch_state(state)
    changed = any(removed_counts.values())
    return RecoveryExecution(
        action_id=action.action_id,
        changed=changed,
        revalidated=True,
        removed_counts=removed_counts,
        message=(
            f"Removed {source} from current staging and revalidated the remaining batch."
            if changed
            else f"No current staging records matched {source}; the batch was revalidated."
        ),
    )


def _revalidate_platform_batch_state(state: MutableMapping[str, Any]) -> None:
    orders, products, reviews = apply_batch_rules(
        list(state.get("orders", [])),
        list(state.get("products", [])),
        list(state.get("reviews", [])),
    )
    state["orders"] = orders
    state["products"] = products
    state["reviews"] = reviews
    summary = dict(state.get("upload_result_summary", {}))
    if summary:
        summary.update(
            orders_imported=len(orders),
            manual_reviews=sum(is_manual_review_record(item) for item in reviews),
            duplicate_orders=len(state.get("duplicate_skipped", [])),
            unsupported_files=len(state.get("unsupported_files", [])),
            processing_errors=len(state.get("processing_errors", [])),
        )
        state["upload_result_summary"] = summary


def _action(
    action_type: str,
    label: str,
    affected_item: str,
    *,
    destructive: bool,
    requires_revalidation: bool,
) -> RecoveryAction:
    digest = sha256(f"{action_type}:{affected_item}".encode("utf-8")).hexdigest()[:12]
    return RecoveryAction(
        action_id=f"{action_type}:{digest}",
        action_type=action_type,
        label=label,
        affected_item=affected_item,
        allowed=True,
        destructive=destructive,
        requires_revalidation=requires_revalidation,
    )
