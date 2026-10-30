"""Derived session-state invariants for the Data Import workflow."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
import os
from traceback import extract_stack
from typing import Any

from .workflow_navigation import end_workflow_activity


INVOICE_UPLOAD_ATTEMPT_KEY = "invoice_upload_attempt"
INVOICE_UPLOAD_UNRESOLVED = frozenset(
    {"selected", "processing", "failed", "interrupted"}
)
STATEMENT_WORKFLOW_STEP_KEY = "weekly_statement_workflow_step"
STATEMENT_WORKFLOW_DIAGNOSTICS_KEY = "statement_workflow_transition_log"
STATEMENT_COMMIT_TRACE_PENDING_KEY = "statement_commit_trace_pending"
_STATEMENT_WORKFLOW_DIAGNOSTICS_ENABLED = "INVOICEGATHER_STATEMENT_STATE_DIAGNOSTICS"


def statement_workflow_diagnostics_enabled() -> bool:
    """Return whether the development-only redacted transition ledger is enabled."""

    return os.getenv(_STATEMENT_WORKFLOW_DIAGNOSTICS_ENABLED) == "1"


def record_statement_workflow_transition(
    state: MutableMapping[str, Any],
    *,
    key: str,
    before: Any,
    after: Any,
    reason: str,
) -> None:
    """Capture a bounded, payload-free development trace of Statement state.

    This is intentionally session-local and opt-in. It records no Statement
    rows, source bytes, credentials, or financial amounts.
    """

    if not statement_workflow_diagnostics_enabled() or before == after:
        return
    caller = next(
        (
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
            for frame in reversed(extract_stack(limit=12)[:-1])
            if "data_import_state.py" not in frame.filename.replace("\\", "/")
        ),
        "unknown",
    )
    entries = list(state.get(STATEMENT_WORKFLOW_DIAGNOSTICS_KEY, ()))
    entries.append(
        {
            "key": key,
            "before": _statement_state_value(before),
            "after": _statement_state_value(after),
            "reason": reason,
            "caller": caller,
            "market": str(state.get("active_import_market") or ""),
            "source": str(state.get("import_source_type") or ""),
            "batch": str(state.get("batch_id") or ""),
        }
    )
    state[STATEMENT_WORKFLOW_DIAGNOSTICS_KEY] = entries[-80:]


def _statement_state_value(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    stage = getattr(value, "stage", None)
    if stage is not None:
        return f"{type(value).__name__}(stage={type(stage).__name__})"
    return type(value).__name__


def record_statement_commit_checkpoint(
    state: MutableMapping[str, Any],
    checkpoint: str,
    *,
    outcome: str | None = None,
    reason_codes: tuple[str, ...] = (),
    write_failure_diagnostic: Any | None = None,
) -> None:
    """Capture a redacted Statement commit lifecycle checkpoint when enabled.

    The summary intentionally contains only state presence, typed workflow
    status, and navigation fields. It never serializes Statement rows, money,
    source bytes, filenames, credentials, or commit-plan payloads.
    """

    if not statement_workflow_diagnostics_enabled():
        return
    review = state.get("weekly_statement_review")
    preflight = getattr(review, "commit_preflight", None)
    summary = (
        f"batch={'present' if state.get('batch_id') else 'missing'};"
        f"source={state.get('import_source_type') or 'missing'};"
        f"stage={'present' if state.get('weekly_statement_stage') is not None else 'missing'};"
        f"review={'present' if review is not None else 'missing'};"
        f"plan={'present' if getattr(review, 'plan', None) is not None else 'missing'};"
        f"preflight={getattr(preflight, 'status', None) or 'missing'};"
        f"ready={bool(getattr(review, 'commit_ready', False))};"
        f"stale={'present' if state.get('weekly_statement_review_stale_reason') else 'missing'};"
        f"step={state.get('data_import_step')};"
        f"workflow_step={state.get(STATEMENT_WORKFLOW_STEP_KEY)};"
        f"completed={bool(state.get('weekly_statement_commit_completed'))};"
        f"outcome={outcome or 'pending'};"
        f"reasons={','.join(reason_codes) if reason_codes else 'none'};"
        f"write_diagnostic={_statement_write_diagnostic_value(write_failure_diagnostic)}"
    )
    record_statement_workflow_transition(
        state,
        key="statement_commit_state",
        before=None,
        after=summary,
        reason=f"commit:{checkpoint}",
    )
    if checkpoint == "before_rerun":
        state[STATEMENT_COMMIT_TRACE_PENDING_KEY] = True


def _statement_write_diagnostic_value(value: Any) -> str:
    """Accept only the writer's payload-free diagnostic representation."""

    trace_summary = getattr(value, "trace_summary", None)
    if not callable(trace_summary):
        return "none"
    try:
        return str(trace_summary())[:1500]
    except Exception:
        return "unavailable"


def record_statement_commit_first_render(state: MutableMapping[str, Any]) -> None:
    """Close an opt-in commit trace at the first render after a commit rerun."""

    if not state.pop(STATEMENT_COMMIT_TRACE_PENDING_KEY, False):
        return
    record_statement_commit_checkpoint(state, "first_render_after_rerun")


@dataclass(frozen=True)
class InvoiceUploadEligibility:
    eligible: bool
    state: str
    reason: str | None = None


@dataclass(frozen=True)
class StatementStateConsistency:
    coherent: bool
    state: str
    reason: str | None = None


def invoice_upload_downstream_eligibility(
    state: MutableMapping[str, Any],
) -> InvoiceUploadEligibility:
    """Return whether a newer Invoice upload attempt permits downstream work."""

    attempt = state.get(INVOICE_UPLOAD_ATTEMPT_KEY)
    if state.get("workflow_activity") == "Processing":
        return InvoiceUploadEligibility(
            False,
            "stale_processing",
            "The current Invoice upload did not finish. Return to Upload and recover it before continuing.",
        )
    if attempt in INVOICE_UPLOAD_UNRESOLVED:
        return InvoiceUploadEligibility(
            False,
            str(attempt),
            "The current Invoice upload did not finish. Return to Upload and recover it before continuing.",
        )
    if attempt not in (None, "resolved"):
        return InvoiceUploadEligibility(
            False,
            "unknown",
            "The current Invoice upload state is not recognized. Return to Upload and reset it before continuing.",
        )
    return InvoiceUploadEligibility(
        True,
        "resolved" if attempt == "resolved" else "pristine_or_previously_resolved",
    )


def reset_invoice_upload_attempt(state: MutableMapping[str, Any]) -> None:
    """Clear only transient Invoice upload/widget facts and stale activity."""

    state.pop(INVOICE_UPLOAD_ATTEMPT_KEY, None)
    widget_keys = tuple(
        key
        for key in state
        if isinstance(key, str) and key.startswith("pdf_uploader_")
    )
    for key in widget_keys:
        state.pop(key, None)
    state["uploader_version"] = int(state.get("uploader_version", 0)) + 1
    end_workflow_activity(state)


def statement_stage_review_consistency(
    state: MutableMapping[str, Any],
) -> StatementStateConsistency:
    """Validate the session-only stage/review pair without reconstructing facts."""

    stage = state.get("weekly_statement_stage")
    review = state.get("weekly_statement_review")
    if stage is None and review is None:
        return StatementStateConsistency(True, "empty")
    if stage is None:
        return StatementStateConsistency(
            False,
            "review_only",
            "Statement review exists without its authoritative stage. Check the Statement again.",
        )
    if review is None:
        return StatementStateConsistency(
            False,
            "stage_only",
            "Statement stage exists without a completed review. Check the Statement again.",
        )
    review_stage = getattr(review, "stage", None)
    try:
        same_stage = review_stage is stage or review_stage == stage
    except Exception:
        same_stage = False
    if not same_stage:
        return StatementStateConsistency(
            False,
            "mismatched",
            "Statement stage and review do not describe the same result. Check the Statement again.",
        )
    return StatementStateConsistency(True, "coherent")


def restore_statement_workflow_step(
    state: MutableMapping[str, Any],
    requested_step: int,
) -> int:
    """Restore the highest explicitly completed Statement step on a rerun.

    ``data_import_step`` is a presentation cursor and can be rehydrated by
    Streamlit or older session code. A coherent Statement review has a
    separate, session-local workflow checkpoint so a lower cursor cannot
    silently discard an already reached Review & Commit destination. Explicit
    Back/forward actions update that checkpoint through
    :func:`set_statement_workflow_step`.
    """

    consistency = statement_stage_review_consistency(state)
    if not consistency.coherent or consistency.state != "coherent":
        return requested_step
    try:
        checkpoint = int(state.get(STATEMENT_WORKFLOW_STEP_KEY, requested_step))
    except (TypeError, ValueError):
        checkpoint = requested_step
    checkpoint = min(max(checkpoint, 1), 5)
    # A restored cursor may legitimately be ahead of a checkpoint created by
    # an earlier application version. Capture that progress before protecting
    # it from later low-level cursor resets.
    checkpoint = max(checkpoint, min(max(requested_step, 1), 5))
    record_statement_workflow_transition(
        state,
        key=STATEMENT_WORKFLOW_STEP_KEY,
        before=state.get(STATEMENT_WORKFLOW_STEP_KEY),
        after=checkpoint,
        reason="rerun_normalization",
    )
    state[STATEMENT_WORKFLOW_STEP_KEY] = checkpoint
    return max(requested_step, checkpoint)


def set_statement_workflow_step(
    state: MutableMapping[str, Any],
    step: int,
) -> int:
    """Record an intentional Statement navigation destination."""

    normalized = min(max(int(step), 1), 5)
    record_statement_workflow_transition(
        state,
        key=STATEMENT_WORKFLOW_STEP_KEY,
        before=state.get(STATEMENT_WORKFLOW_STEP_KEY),
        after=normalized,
        reason="intentional_navigation",
    )
    state[STATEMENT_WORKFLOW_STEP_KEY] = normalized
    return normalized


def mark_statement_review_stale(
    state: MutableMapping[str, Any],
    reason: str,
) -> bool:
    """Block the preserved review until a successful authoritative refresh."""

    if state.get("weekly_statement_review") is None:
        return False
    record_statement_workflow_transition(
        state,
        key="weekly_statement_review_stale_reason",
        before=state.get("weekly_statement_review_stale_reason"),
        after="STALE",
        reason="stale_invalidation",
    )
    state["weekly_statement_review_stale_reason"] = reason
    return True


def clear_statement_upload_attempt(state: MutableMapping[str, Any]) -> None:
    """Clear only transient/incomplete Statement upload and review session facts."""

    next_version = int(state.get("weekly_statement_uploader_version", 0)) + 1
    for key in (
        "weekly_statement_stage",
        "weekly_statement_review",
        STATEMENT_WORKFLOW_STEP_KEY,
        "weekly_statement_review_stale_reason",
        "weekly_statement_commit_completed",
        "weekly_statement_commit_noop",
        "weekly_statement_upload_selected",
        "weekly_statement_issue_order_id",
        "weekly_statement_issue_order_click",
    ):
        state.pop(key, None)
    widget_keys = tuple(
        key
        for key in state
        if isinstance(key, str)
        and key.startswith("weekly_statement_uploader_")
        and key != "weekly_statement_uploader_version"
    )
    for key in widget_keys:
        state.pop(key, None)
    state["weekly_statement_uploader_version"] = next_version
    end_workflow_activity(state)


def begin_next_full_order_upload(state: MutableMapping[str, Any]) -> None:
    """Atomically discard only the completed/staged Full Order upload context.

    The selected market and Full Order source mode remain bound, so the next
    native workbook is validated in the same market without retaining a prior
    review, sync result, batch identifier, or uploader widget value.
    """

    next_version = int(state.get("full_order_uploader_version", 0)) + 1
    for key in (
        "full_order_review",
        "full_order_market_mismatch",
        "full_order_sync_result",
        "full_order_pm_refresh_notice",
        "batch_id",
    ):
        record_statement_workflow_transition(
            state,
            key=key,
            before=state.get(key),
            after=None,
            reason="clear_statement_upload_attempt",
        )
        state.pop(key, None)
    widget_keys = tuple(
        key
        for key in state
        if isinstance(key, str)
        and key.startswith("full_order_uploader_")
        and key != "full_order_uploader_version"
    )
    for key in widget_keys:
        state.pop(key, None)
    state["full_order_uploader_version"] = next_version
    state["data_import_step"] = 2
    end_workflow_activity(state)


def mark_statement_review_stale_after_invoice_commit(
    state: MutableMapping[str, Any],
) -> bool:
    """Require a fresh Statement review after this session writes Invoice facts."""

    return mark_statement_review_stale(state, (
        "Invoice data changed after this Statement review. "
        "Refresh validation before continuing."
    ))


def replace_statement_review_after_refresh(
    state: MutableMapping[str, Any],
    refreshed_review: Any,
) -> None:
    """Atomically replace stale Statement review and presentation-only state."""

    record_statement_workflow_transition(
        state,
        key="weekly_statement_review",
        before=state.get("weekly_statement_review"),
        after=refreshed_review,
        reason="refresh_validation_success",
    )
    record_statement_workflow_transition(
        state,
        key="weekly_statement_stage",
        before=state.get("weekly_statement_stage"),
        after=refreshed_review.stage,
        reason="refresh_validation_success",
    )
    record_statement_workflow_transition(
        state,
        key="weekly_statement_review_stale_reason",
        before=state.get("weekly_statement_review_stale_reason"),
        after=None,
        reason="refresh_validation_success",
    )
    state["weekly_statement_review"] = refreshed_review
    state["weekly_statement_stage"] = refreshed_review.stage
    state.pop("weekly_statement_review_stale_reason", None)
    state.pop("weekly_statement_commit_noop", None)
    for key in (
        "statement_exception_queue_click",
        "statement_exception_queue_selected",
        "statement_exception_queue_filter",
        "statement_commit_exception_queue_click",
        "statement_commit_exception_queue_selected",
    ):
        state.pop(key, None)


def has_unfinished_session_work(state: MutableMapping[str, Any]) -> bool:
    """Derive whether Logout would discard meaningful session-only work."""

    if state.get("workflow_activity") or state.get("workflow_navigation_blocked"):
        return True
    if state.get("manual_review_correction_drafts"):
        return True
    if state.get("pending_validation_recovery_action") or state.get(
        "pending_validation_bulk_recovery"
    ):
        return True
    invoice_attempt = state.get(INVOICE_UPLOAD_ATTEMPT_KEY)
    if invoice_attempt is not None and invoice_attempt != "resolved":
        return True
    if state.get("weekly_statement_upload_selected"):
        return True
    if _has_selected_widget(state, "pdf_uploader_") or _has_selected_widget(
        state, "weekly_statement_uploader_"
    ):
        return True
    if not state.get("weekly_statement_commit_completed") and (
        state.get("weekly_statement_stage") is not None
        or state.get("weekly_statement_review") is not None
    ):
        return True
    if state.get("invoice_commit_completed"):
        return False
    return bool(
        state.get("batch_id")
        or state.get("upload_result_summary")
        or any(
            state.get(key)
            for key in (
                "orders",
                "products",
                "reviews",
                "duplicate_skipped",
                "unsupported_files",
                "processing_errors",
            )
        )
    )


def _has_selected_widget(state: MutableMapping[str, Any], prefix: str) -> bool:
    return any(
        bool(value)
        for key, value in state.items()
        if isinstance(key, str)
        and key.startswith(prefix)
        and key not in {"uploader_version", "weekly_statement_uploader_version"}
    )
