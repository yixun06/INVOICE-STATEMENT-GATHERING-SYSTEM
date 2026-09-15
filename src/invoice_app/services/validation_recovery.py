"""Safe, session-only validation recovery for the active import batch."""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .batch_service import apply_batch_rules, is_manual_review_record
from .import_result_contract import RecoveryAction


REMOVE_SOURCE = "remove_source"
REMOVE_DUPLICATE = "remove_duplicate"
REMOVE_STAGED_SOURCE = "remove_staged_source"
REMOVE_INVOICE_STAGING = "remove_invoice_staging"
VIEW_DETAILS = "view_details"
_REMOVABLE_ACTION_TYPES = {
    REMOVE_SOURCE,
    REMOVE_DUPLICATE,
    REMOVE_STAGED_SOURCE,
    REMOVE_INVOICE_STAGING,
}
_SOURCE_BUCKETS = (
    "orders",
    "products",
    "reviews",
    "duplicate_skipped",
    "unsupported_files",
    "processing_errors",
)
_STAGED_STATEMENT_KEYS = (
    "weekly_statement_stage",
    "weekly_statement_review",
    "weekly_statement_review_stale_reason",
    "weekly_statement_issue_order_id",
    "weekly_statement_issue_order_click",
    "weekly_statement_commit_completed",
)
_INVOICE_STAGING_KEYS = (
    "batch_id",
    "pdf_count",
    "upload_notice",
    "upload_result_summary",
    "view_customize_open",
    "manual_review_correction_drafts",
    "manual_resolution_notice",
    "uat2_historical_commit_entries",
    "uat2_historical_commit_refresh_required",
    "uat2_historical_commit_signature",
    "validation_recovery_detail",
    "validation_recovery_notice",
    "invoice_commit_completed",
    "invoice_commit_completed_count",
)
_INVOICE_WIDGET_PREFIXES = (
    "pdf_uploader_",
    "data_import_current_batch_",
    "mr_",
)


@dataclass(frozen=True)
class RecoveryExecution:
    action_id: str
    changed: bool
    revalidated: bool
    removed_counts: dict[str, int]
    message: str


@dataclass(frozen=True)
class DuplicateSourceRemovalPlan:
    """Authoritative current-staging decision for duplicate source cleanup."""

    safe_sources: tuple[str, ...]
    retained_sources: tuple[str, ...]


def source_name(item: dict[str, Any]) -> str | None:
    value = item.get("source_pdf") or item.get("filename") or item.get("source_file")
    text = str(value).strip() if value is not None else ""
    return text or None


def plan_duplicate_source_removal(
    state: MutableMapping[str, Any],
    sources: Iterable[str],
) -> DuplicateSourceRemovalPlan:
    """Allow whole-source removal only for duplicate-only staged sources.

    A PDF may contain both a duplicate order and newly accepted/reviewable
    sibling orders.  The duplicate reducer has already safely excluded the
    duplicate order, so this planner keeps any source that still owns a record
    outside ``duplicate_skipped``.
    """

    candidate_sources = tuple(
        dict.fromkeys(
            source.strip()
            for source in sources
            if isinstance(source, str) and source.strip()
        )
    )
    duplicate_sources = {
        source_name(record)
        for record in state.get("duplicate_skipped", [])
        if isinstance(record, dict) and source_name(record)
    }
    retained_by_other_staging = {
        source_name(record)
        for bucket in _SOURCE_BUCKETS
        if bucket != "duplicate_skipped"
        for record in state.get(bucket, [])
        if isinstance(record, dict) and source_name(record)
    }
    safe_sources = tuple(
        source
        for source in candidate_sources
        if source in duplicate_sources and source not in retained_by_other_staging
    )
    safe_set = frozenset(safe_sources)
    return DuplicateSourceRemovalPlan(
        safe_sources=safe_sources,
        retained_sources=tuple(
            source for source in candidate_sources if source not in safe_set
        ),
    )


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


def plan_current_invoice_staging_exit(
    state: MutableMapping[str, Any],
) -> RecoveryAction | None:
    """Return a guarded exit action only for wholly uncommitted Invoice staging."""

    if state.get("import_source_type") != "Platform Orders":
        return None
    if _has_committed_invoice_evidence(state):
        return None
    staging_token = _invoice_staging_token(state)
    if staging_token is None:
        return None
    return _action(
        REMOVE_INVOICE_STAGING,
        "Exit Invoice Import",
        staging_token,
        destructive=True,
        requires_revalidation=False,
    )


def execute_current_invoice_staging_exit(
    state: MutableMapping[str, Any],
    action: RecoveryAction,
) -> RecoveryExecution:
    """Remove only current uncommitted Invoice staging from this session.

    This operation has no repository dependency and performs no archive or
    remote-persistence deletion. It fails closed if commit evidence exists or
    if the staged facts changed after confirmation was requested.
    """

    if action.action_type != REMOVE_INVOICE_STAGING:
        raise ValueError("Recovery action is not an Invoice staging exit.")
    if state.get("import_source_type") != "Platform Orders":
        raise ValueError("The active source is not an Invoice import.")
    if _has_committed_invoice_evidence(state):
        raise ValueError("Committed Invoice evidence cannot be removed by step exit.")
    current_token = _invoice_staging_token(state)
    if current_token is None:
        raise ValueError("No uncommitted Invoice staging is available.")
    if current_token != action.affected_item:
        raise ValueError("Invoice staging changed after exit confirmation was requested.")

    removed_counts = {
        bucket: len(state.get(bucket) or ())
        for bucket in _SOURCE_BUCKETS
    }
    removed_fields = sum(key in state for key in _INVOICE_STAGING_KEYS)
    widget_keys = tuple(
        key
        for key in state.keys()
        if isinstance(key, str) and key.startswith(_INVOICE_WIDGET_PREFIXES)
    )
    for bucket in _SOURCE_BUCKETS:
        state.pop(bucket, None)
    for key in _INVOICE_STAGING_KEYS:
        state.pop(key, None)
    for key in widget_keys:
        state.pop(key, None)
    state["uploader_version"] = int(state.get("uploader_version", 0)) + 1

    removed_counts["invoice_staging_fields"] = removed_fields
    removed_counts["invoice_widget_state"] = len(widget_keys)
    return RecoveryExecution(
        action_id=action.action_id,
        changed=True,
        revalidated=False,
        removed_counts=removed_counts,
        message=(
            "Removed current uncommitted Invoice staging. Upload Invoice files "
            "to begin again."
        ),
    )


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

    if action.action_type == REMOVE_INVOICE_STAGING:
        return execute_current_invoice_staging_exit(state, action)

    if action.action_type == REMOVE_STAGED_SOURCE:
        changed = state.get("weekly_statement_stage") is not None
        for key in _STAGED_STATEMENT_KEYS:
            state.pop(key, None)
        state["weekly_statement_uploader_version"] = (
            int(state.get("weekly_statement_uploader_version", 0)) + 1
        )
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
    for key in (
        "uat2_historical_commit_entries",
        "uat2_historical_commit_refresh_required",
        "uat2_historical_commit_signature",
    ):
        state.pop(key, None)
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


def execute_current_batch_bulk_recovery(
    state: MutableMapping[str, Any],
    sources: Iterable[str],
) -> RecoveryExecution:
    """Remove known current-batch sources together, then revalidate once."""

    source_names = tuple(dict.fromkeys(
        source.strip() for source in sources if isinstance(source, str) and source.strip()
    ))
    if not source_names:
        raise ValueError("Bulk recovery requires at least one source file.")

    sources_to_remove = frozenset(source_names)
    removed_counts: dict[str, int] = {}
    for bucket in _SOURCE_BUCKETS:
        records = list(state.get(bucket, []))
        retained = [record for record in records if source_name(record) not in sources_to_remove]
        removed_counts[bucket] = len(records) - len(retained)
        state[bucket] = retained

    _revalidate_platform_batch_state(state)
    for key in (
        "uat2_historical_commit_entries",
        "uat2_historical_commit_refresh_required",
        "uat2_historical_commit_signature",
    ):
        state.pop(key, None)
    changed = any(removed_counts.values())
    digest = sha256("\n".join(sorted(source_names)).encode("utf-8")).hexdigest()[:12]
    return RecoveryExecution(
        action_id=f"remove_sources:{digest}",
        changed=changed,
        revalidated=True,
        removed_counts=removed_counts,
        message=(
            f"Removed {len(source_names)} source(s) from current staging and revalidated the remaining batch."
            if changed
            else "No current staging records matched the selected sources; the batch was revalidated."
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


def _has_committed_invoice_evidence(state: MutableMapping[str, Any]) -> bool:
    if state.get("invoice_commit_completed"):
        return True
    for entry in state.get("uat2_historical_commit_entries", ()):
        status = getattr(entry, "status", None)
        if getattr(status, "value", status) == "IMPORTED":
            return True
    return False


def _invoice_staging_token(state: MutableMapping[str, Any]) -> str | None:
    uploader_keys = tuple(
        sorted(
            key
            for key in state.keys()
            if isinstance(key, str) and key.startswith("pdf_uploader_")
            and state.get(key)
        )
    )
    has_staging = bool(
        state.get("batch_id")
        or any(state.get(bucket) for bucket in _SOURCE_BUCKETS)
        or state.get("upload_result_summary")
        or state.get("manual_review_correction_drafts")
        or state.get("uat2_historical_commit_entries")
        or uploader_keys
    )
    if not has_staging:
        return None
    signature_parts = [
        ("import_source_type", repr(state.get("import_source_type"))),
        ("batch_id", repr(state.get("batch_id"))),
        ("uploader_version", repr(state.get("uploader_version", 0))),
    ]
    signature_parts.extend(
        (key, repr(state.get(key)))
        for key in (*_SOURCE_BUCKETS, *_INVOICE_STAGING_KEYS)
    )
    signature_parts.extend((key, repr(state.get(key))) for key in uploader_keys)
    digest = sha256(repr(signature_parts).encode("utf-8")).hexdigest()[:16]
    return f"invoice-staging:{digest}"


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
