"""Derived session-state invariants for the Data Import workflow."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

from .workflow_navigation import end_workflow_activity


INVOICE_UPLOAD_ATTEMPT_KEY = "invoice_upload_attempt"
INVOICE_UPLOAD_UNRESOLVED = frozenset(
    {"selected", "processing", "failed", "interrupted"}
)


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


def clear_statement_upload_attempt(state: MutableMapping[str, Any]) -> None:
    """Clear only transient/incomplete Statement upload and review session facts."""

    next_version = int(state.get("weekly_statement_uploader_version", 0)) + 1
    for key in (
        "weekly_statement_stage",
        "weekly_statement_review",
        "weekly_statement_review_stale_reason",
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


def mark_statement_review_stale_after_invoice_commit(
    state: MutableMapping[str, Any],
) -> bool:
    """Require a fresh Statement review after this session writes Invoice facts."""

    if state.get("weekly_statement_review") is None:
        return False
    state["weekly_statement_review_stale_reason"] = (
        "Invoice data changed after this Statement review. "
        "Refresh validation before continuing."
    )
    return True


def replace_statement_review_after_refresh(
    state: MutableMapping[str, Any],
    refreshed_review: Any,
) -> None:
    """Atomically replace stale Statement review and presentation-only state."""

    state["weekly_statement_review"] = refreshed_review
    state["weekly_statement_stage"] = refreshed_review.stage
    state.pop("weekly_statement_review_stale_reason", None)
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
