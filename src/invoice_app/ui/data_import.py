"""Single-page import wizard presentation.

This module deliberately orchestrates existing import/staging services only. It
does not own parsing, validation, reconciliation, duplicate, export, or
persistence rules.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
import io
from typing import Any, Callable

import streamlit as st

from ..services.batch_service import create_batch_id
from ..services.import_result_adapters import (
    adapt_platform_orders_import_result,
    adapt_shopee_weekly_statement_import_result,
)
from ..services.import_result_contract import ImportResult, ReconciliationException, RecoveryAction, ValidationIssue
from ..services.validation_recovery import (
    REMOVE_SOURCE,
    REMOVE_STAGED_SOURCE,
    VIEW_DETAILS,
    execute_current_batch_bulk_recovery,
    execute_current_batch_recovery,
    plan_duplicate_source_removal,
    recovery_actions_for_source,
)
from ..services.historical_invoice_intake import (
    IntakeStatus, build_current_batch_staging, classify_staging, import_new_staging,
)
from ..services.application_commit_lock import ApplicationCommitInProgress
from ..services.uat2_data_settings import configured_uat2_data_settings
from ..services.product_master_source import ProductMasterSourceError, load_configured_product_price_master
from ..repositories.google_sheets_historical_invoice_repository import HistoricalInvoiceStorageError
from ..repositories.historical_invoice_repository import HistoricalInvoiceBulkImportError
from ..services.workflow_navigation import begin_workflow_activity, end_workflow_activity
from ..services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
)
from ..services.shopee_statement_import import (
    StatementImportReview,
    check_statement_review_currency,
    commit_statement_review,
    refresh_statement_review,
    review_statement_upload,
)
from ..services.shopee_statement_persistence import (
    StatementCommitBlocked,
    StatementWriteIntegrityError,
)
from ..domain.statement_reconciliation_v2 import IdentityScope, SettlementBasis
from ..services.manual_review_resolution import (
    MISSING_INCOME,
    FINAL_AMOUNT,
    PRODUCT_COUNT_MISMATCH,
    PROMOTION_SUBTOTAL,
    add_draft_product,
    apply_product_draft,
    apply_resolution,
    clear_correction_draft,
    draft_products,
    draft_promotion_subtotals,
    draft_summary,
    edit_draft_product,
    promotion_group_options,
    promotion_subtotal_groups,
    remove_draft_product,
    resolution_plan,
    set_draft_promotion_subtotal,
    synchronize_correction_drafts,
)



DATA_IMPORT_PAGE = "Data Import"
PLATFORM_ORDERS = "Platform Orders"
SHOPEE_WEEKLY_STATEMENT = "Shopee Weekly Statement"

WIZARD_STEPS = (
    "Select Source",
    "Upload",
    "Validate",
    "Reconcile",
    "Review & Commit",
)
_WORKFLOW_KEYS = (
    "data_import_step",
    "import_source_type",
    "weekly_statement_stage",
    "weekly_statement_review",
    "weekly_statement_review_stale_reason",
    "weekly_statement_uploader_version",
    "weekly_statement_selected_source",
    "uat2_historical_commit_entries",
    "uat2_historical_commit_refresh_required",
    "uat2_historical_commit_signature",
    "manual_review_correction_drafts",
    "pending_validation_bulk_recovery",
)


@dataclass(frozen=True)
class _StatementOrderIssueGroup:
    order_id: str
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class _StatementIssuePresentation:
    order_groups: tuple[_StatementOrderIssueGroup, ...]
    statement_issues: tuple[ValidationIssue, ...]
    removal_action: RecoveryAction | None


def initialize_data_import_state() -> None:
    """Initialize presentation-only state once per Streamlit session."""
    st.session_state.setdefault("data_import_step", 1)
    st.session_state.setdefault("import_source_type", None)
    st.session_state.setdefault("weekly_statement_uploader_version", 0)


def reset_data_import_state() -> None:
    """Remove UI-only workflow state when the active batch is cleared."""
    for key in _WORKFLOW_KEYS:
        st.session_state.pop(key, None)


def render_data_import(
    *,
    render_platform_orders_upload: Callable[[], Any],
    render_platform_orders_outcomes: Callable[[], Any],
    render_platform_orders_validation_data: Callable[[], Any],
    discard_current_batch: Callable[[], None],
) -> None:
    """Render the sequential import workspace over the existing services."""
    initialize_data_import_state()
    _adopt_legacy_platform_batch()
    step = _current_step()
    st.title("Data Import")
    st.caption("Use the active-batch workflow to stage, validate, reconcile, and review source data.")
    _render_wizard_progress(step)
    if step == 1:
        _render_source_selection(discard_current_batch)
    elif step == 2:
        _render_upload_step(render_platform_orders_upload)
    elif step == 3:
        _render_validation_step(
            render_platform_orders_outcomes,
            render_platform_orders_validation_data,
        )
    elif step == 4:
        _render_reconciliation_step()
    else:
        _render_review_and_commit_step()


def _adopt_legacy_platform_batch() -> None:
    """Present a pre-wizard batch as Platform Orders without changing it."""
    if st.session_state.get("batch_id") and not st.session_state.get("import_source_type"):
        st.session_state.import_source_type = PLATFORM_ORDERS
        st.session_state.data_import_step = max(int(st.session_state.get("data_import_step", 1)), 3)


def _current_step() -> int:
    try:
        return min(max(int(st.session_state.get("data_import_step", 1)), 1), len(WIZARD_STEPS))
    except (TypeError, ValueError):
        return 1


def _set_step(step: int) -> None:
    st.session_state.data_import_step = min(max(step, 1), len(WIZARD_STEPS))


def _render_wizard_progress(current_step: int) -> None:
    st.progress(current_step / len(WIZARD_STEPS), text=f"Step {current_step} of {len(WIZARD_STEPS)} — {WIZARD_STEPS[current_step - 1]}")
    columns = st.columns(len(WIZARD_STEPS), gap="small")
    for index, (column, label) in enumerate(zip(columns, WIZARD_STEPS), start=1):
        with column:
            st.caption(f"Step {index}")
            st.write(label)
            if index < current_step:
                st.badge("Completed", icon=":material/check_circle:", color="green")
            elif index == current_step:
                st.badge("Current", icon=":material/play_circle:", color="blue")
            else:
                st.badge("Pending", icon=":material/schedule:", color="gray")


def _render_source_selection(discard_current_batch: Callable[[], None]) -> None:
    active_source = st.session_state.get("import_source_type") if st.session_state.get("batch_id") else None
    if active_source:
        with st.container(border=True):
            st.subheader("Continue current batch")
            st.write(f"Current batch source: **{active_source}**")
            st.caption("Finish or discard the current batch before choosing another source type.")
            with st.container(horizontal=True):
                if st.button("Continue", type="primary", icon=":material/play_arrow:"):
                    _set_step(2 if active_source == PLATFORM_ORDERS and not st.session_state.get("upload_result_summary") else 3)
                    st.rerun()
                if st.button("Discard current batch", icon=":material/restart_alt:"):
                    discard_current_batch()
                    st.rerun()
        return
    with st.container(border=True):
        st.subheader("Select source")
        source_label = st.segmented_control(
            "Import workflow",
            ("Invoice Import", "Statement Import"),
            key="weekly_statement_selected_source",
            default="Invoice Import",
        )
        source_type = (
            PLATFORM_ORDERS
            if source_label == "Invoice Import"
            else SHOPEE_WEEKLY_STATEMENT
        )
        st.caption(
            "PDF or ZIP order documents for Shopee, Lazada, and ZENXIN."
            if source_type == PLATFORM_ORDERS
            else "Native Shopee Weekly Statement settlement export (.xlsx)."
        )
        if st.button("Continue to upload", type="primary", icon=":material/arrow_forward:"):
            st.session_state.import_source_type = source_type
            _set_step(2)
            st.rerun()
def _render_upload_step(render_platform_orders_upload: Callable[[], Any]) -> None:
    source_type = st.session_state.get("import_source_type")
    if source_type == PLATFORM_ORDERS:
        st.subheader("Upload platform order files")
        st.caption("Upload PDF or ZIP order documents for the active batch.")
        render_platform_orders_upload()
        if st.session_state.get("batch_id") and st.button(
            "Continue to validate", type="primary", icon=":material/arrow_forward:"
        ):
            _set_step(3)
            st.rerun()
        return
    if source_type == SHOPEE_WEEKLY_STATEMENT:
        _render_weekly_statement_upload()
        return
    _set_step(1)
    st.rerun()


def _render_weekly_statement_upload() -> None:
    st.subheader("Upload Shopee Weekly Statement")
    st.caption("Upload one native Shopee Weekly Statement workbook (.xlsx).")
    version = int(st.session_state.get("weekly_statement_uploader_version", 0))
    uploaded_file = st.file_uploader(
        "Shopee Weekly Statement (.xlsx)",
        type=["xlsx"],
        key=f"weekly_statement_uploader_{version}",
    )
    with st.container(horizontal=True):
        stage_clicked = st.button("Check statement", type="primary", icon=":material/upload_file:", disabled=uploaded_file is None)
        clear_clicked = st.button("Clear selected file", icon=":material/close:", disabled=uploaded_file is None)
    if clear_clicked:
        st.session_state.weekly_statement_uploader_version = version + 1
        st.rerun()
    if stage_clicked and uploaded_file is not None:
        st.session_state.batch_id = st.session_state.get("batch_id") or create_batch_id()
        begin_workflow_activity(st.session_state, "Validating")
        try:
            settings = configured_uat2_data_settings()
            master, _label = load_configured_product_price_master()
            review = review_statement_upload(
                uploaded_file,
                source_filename=uploaded_file.name,
                batch_id=st.session_state.batch_id,
                uploaded_by=str(st.session_state.get("authenticated_username") or "Admin"),
                repository=settings.create_repository(),
                writer=settings.create_statement_writer(),
                product_master=master,
            )
            st.session_state.weekly_statement_review = review
            st.session_state.weekly_statement_stage = review.stage
            st.session_state.pop("weekly_statement_review_stale_reason", None)
        except (HistoricalInvoiceStorageError, ProductMasterSourceError, StatementCommitBlocked) as error:
            st.error(f"Statement validation is unavailable: {error}")
        finally:
            end_workflow_activity(st.session_state)
        if _weekly_review() is not None:
            _set_step(3)
            st.rerun()


def _render_validation_step(
    render_platform_orders_outcomes: Callable[[], Any],
    render_platform_orders_validation_data: Callable[[], Any],
) -> None:
    st.subheader("Validate")
    result = _current_import_result()
    _render_source_summary(result)
    if result.source_specific_details.get("show_platform_order_outcomes"):
        render_platform_orders_validation_data()
        _render_manual_review_resolution()
    _render_contract_validation(result)
    if _has_pending_recovery():
        _render_recovery_confirmation()
    if result.source_specific_details.get("show_platform_order_outcomes"):
        render_platform_orders_outcomes()
    else:
        _render_statement_review_tables()
    _render_recovery_area()
    if st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT:
        _render_statement_next_step("Continue to reconcile", 4)
    else:
        _render_next_step("Continue to reconcile", 4)


def _render_contract_validation(result: ImportResult) -> None:
    validation = result.validation
    _render_recovery_notice()
    visible_warnings = tuple(
        issue for issue in validation.warnings if issue.layer != "manual_review"
    )
    duplicate_warnings = tuple(issue for issue in visible_warnings if issue.layer == "duplicate")
    other_warnings = tuple(issue for issue in visible_warnings if issue.layer != "duplicate")

    if not validation.blocking_issues and not other_warnings and not duplicate_warnings:
        if result.session_state.applied_to_current_session:
            if not validation.warnings:
                st.success("No validation issues in the current batch.", icon=":material/check_circle:")
        else:
            st.info(result.source_summary.empty_message or "No import result is staged yet.", icon=":material/info:")
    if (
        result.source_type == SHOPEE_WEEKLY_STATEMENT
        and validation.blocking_issues
    ):
        _render_weekly_statement_needs_attention(
            result,
            (*validation.blocking_issues, *other_warnings),
        )
    else:
        for index, issue in enumerate(validation.blocking_issues):
            _render_validation_issue(issue, index=index, is_blocking=True)
        for index, issue in enumerate(
            other_warnings,
            start=len(validation.blocking_issues),
        ):
            _render_validation_issue(issue, index=index, is_blocking=False)
    if duplicate_warnings:
        _render_consolidated_duplicates(duplicate_warnings)


def _statement_issue_presentation(
    result: ImportResult,
    issues: tuple[ValidationIssue, ...],
) -> _StatementIssuePresentation:
    """Group existing issues for display without changing their semantics."""

    known_order_ids: list[str] = []
    batch = result.source_specific_details.get("reconciliation_v2")
    for order_result in getattr(batch, "orders", ()):
        order_id = str(getattr(order_result.evidence, "order_id", "") or "").strip()
        if order_id and order_id not in known_order_ids:
            known_order_ids.append(order_id)
    for exception in result.reconciliation.exceptions:
        order_id = str(exception.affected_item or "").strip()
        if order_id and order_id not in known_order_ids:
            known_order_ids.append(order_id)

    grouped: dict[str, list[ValidationIssue]] = {}
    statement_issues: list[ValidationIssue] = []
    removal_action: RecoveryAction | None = None
    for issue in issues:
        if removal_action is None:
            removal_action = next(
                (
                    action
                    for action in issue.recovery_actions
                    if action.action_type == REMOVE_STAGED_SOURCE
                ),
                None,
            )
        order_id = _statement_issue_order_id(issue, tuple(known_order_ids))
        if order_id is None:
            statement_issues.append(issue)
            continue
        grouped.setdefault(order_id, []).append(issue)

    return _StatementIssuePresentation(
        order_groups=tuple(
            _StatementOrderIssueGroup(order_id, tuple(order_issues))
            for order_id, order_issues in grouped.items()
        ),
        statement_issues=tuple(statement_issues),
        removal_action=removal_action,
    )


def _statement_issue_order_id(
    issue: ValidationIssue,
    known_order_ids: tuple[str, ...],
) -> str | None:
    evidence_order_id = str(issue.evidence.get("order_id") or "").strip()
    if evidence_order_id:
        return evidence_order_id
    affected_item = str(issue.affected_item or "").strip()
    for order_id in known_order_ids:
        if affected_item == order_id or affected_item.startswith(f"{order_id} /"):
            return order_id
        if issue.reason.startswith(f"{order_id}:"):
            return order_id
    return None


def _render_weekly_statement_needs_attention(
    result: ImportResult,
    issues: tuple[ValidationIssue, ...],
) -> None:
    presentation = _statement_issue_presentation(result, issues)
    affected_count = len(presentation.order_groups)
    st.error(
        "**Statement needs attention**\n\n"
        f"{affected_count} order{'s' if affected_count != 1 else ''} contain "
        "unresolved reconciliation issues. Review the affected orders and "
        "Statement issues before this Statement can be committed.",
        icon=":material/error:",
    )

    action = presentation.removal_action
    if action is not None and st.button(
        action.label,
        icon=":material/delete_outline:",
        key=f"statement_level_{action.action_id}",
        disabled=not action.allowed,
    ):
        st.session_state.pending_validation_recovery_action = action
        st.rerun()

    if presentation.statement_issues:
        st.subheader("Statement issues")
        st.dataframe(
            [
                {
                    "Status": "Needs review" if issue.blocking else "Warning",
                    "Issue": issue.reason,
                }
                for issue in presentation.statement_issues
            ],
            hide_index=True,
            height=min(320, 36 * (len(presentation.statement_issues) + 1)),
        )
        with st.expander("Statement technical details", expanded=False):
            _render_statement_issue_evidence(presentation.statement_issues)

    if not presentation.order_groups:
        return
    st.subheader("Affected orders")
    order_ids = tuple(group.order_id for group in presentation.order_groups)
    rows = [
        {
            "Order ID": group.order_id,
            "Status": (
                "Needs review"
                if any(issue.blocking for issue in group.issues)
                else "Warning"
            ),
            "Issue Count": len(group.issues),
            "Issue Summary": _statement_issue_summary(group),
            "Action": "View details",
        }
        for group in presentation.order_groups
    ]
    st.dataframe(
        rows,
        hide_index=True,
        height=min(420, 36 * (len(rows) + 1)),
        column_config={
            "Order ID": st.column_config.TextColumn("Order ID", pinned=True),
            "Issue Count": st.column_config.NumberColumn("Issue Count", format="%d"),
            "Action": st.column_config.ButtonColumn(
                "Action",
                type="tertiary",
                on_click=_select_statement_issue_order,
                args=(order_ids,),
                key="weekly_statement_issue_order_click",
            ),
        },
    )
    selected_order_id = st.session_state.get("weekly_statement_issue_order_id")
    selected = next(
        (
            group
            for group in presentation.order_groups
            if group.order_id == selected_order_id
        ),
        None,
    )
    if selected is None:
        return
    with st.container(border=True):
        st.write(f"**{selected.order_id}**")
        st.caption(f"{len(selected.issues)} issue{'s' if len(selected.issues) != 1 else ''}")
        st.write("Issues")
        for issue in selected.issues:
            st.markdown(f"- {issue.reason}")
        with st.expander("Technical details", expanded=False):
            _render_statement_issue_evidence(selected.issues)


def _statement_issue_summary(group: _StatementOrderIssueGroup) -> str:
    first = group.issues[0].reason
    prefix = f"{group.order_id}:"
    if first.startswith(prefix):
        first = first[len(prefix):].strip()
    if len(first) > 100:
        first = f"{first[:97].rstrip()}..."
    remaining = len(group.issues) - 1
    return f"{first} +{remaining} more" if remaining else first


def _select_statement_issue_order(order_ids: tuple[str, ...]) -> None:
    click = st.session_state.get("weekly_statement_issue_order_click")
    if click is None:
        return
    try:
        row = int(click["row"])
    except (KeyError, TypeError, ValueError):
        return
    if 0 <= row < len(order_ids):
        st.session_state["weekly_statement_issue_order_id"] = order_ids[row]


def _render_statement_issue_evidence(
    issues: tuple[ValidationIssue, ...],
) -> None:
    for index, issue in enumerate(issues, start=1):
        st.caption(f"Issue {index}: {issue.reason}")
        if issue.evidence:
            st.write(dict(issue.evidence))
        if issue.suggested_action:
            st.caption(f"Suggested action: {issue.suggested_action}")


def _render_consolidated_duplicates(duplicate_warnings: tuple[ValidationIssue, ...]) -> None:
    count = len(duplicate_warnings)
    st.info(
        f"**Duplicate Orders Skipped ({count} file{'s' if count > 1 else ''})** — "
        "These orders were already detected in the batch and safely skipped during import. "
        "They do not affect accepted totals or validation readiness.",
        icon=":material/info:",
    )
    duplicate_sources = [
        str(w.affected_item or "").strip()
        for w in duplicate_warnings
        if w.affected_item
    ]
    if st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT:
        _render_weekly_statement_duplicate_removal(duplicate_sources)
    else:
        removal_plan = plan_duplicate_source_removal(
            st.session_state,
            duplicate_sources,
        )
        with st.container(horizontal=True):
            if removal_plan.safe_sources and st.button(
                f"Remove {len(removal_plan.safe_sources)} safe duplicate source(s)",
                icon=":material/delete_sweep:",
                key="remove_all_duplicate_sources",
            ):
                _queue_bulk_recovery(
                    list(removal_plan.safe_sources),
                    label="Remove safe duplicate sources",
                )
        if removal_plan.retained_sources:
            st.caption(
                "Some duplicate PDFs also contain valid or reviewable orders, so "
                "they are being kept. Duplicate orders are already skipped "
                "automatically."
            )
    with st.expander(f"View skipped duplicate files ({count})", expanded=False):
        st.dataframe(
            [
                {
                    "Affected Source": w.affected_item or "Unavailable",
                    "Reason": w.reason,
                }
                for w in duplicate_warnings
            ],
            hide_index=True,
        )


def _render_weekly_statement_duplicate_removal(
    duplicate_sources: list[str],
) -> None:
    """Use the existing staged-statement recovery path for its duplicate state."""

    stage = _weekly_stage()
    source = stage.source_filename if stage is not None else None
    if not source or source not in duplicate_sources:
        return
    actions = recovery_actions_for_source(
        source=source,
        action_type=REMOVE_STAGED_SOURCE,
        remove_label="Remove staged duplicate statement from current batch",
        include_details=False,
    )
    action = next((item for item in actions if item.destructive), None)
    if action is None:
        return
    if st.button(
        action.label,
        icon=":material/delete_sweep:",
        key="remove_staged_duplicate_statement",
    ):
        st.session_state.pending_validation_recovery_action = action
        st.rerun()


def _render_validation_issue(
    issue: ValidationIssue,
    *,
    index: int,
    is_blocking: bool,
    show_message: bool = True,
) -> None:
    if show_message:
        message = f"{'Needs attention' if is_blocking else 'Warning'} — {issue.reason}"
        if is_blocking:
            st.error(message, icon=":material/error:")
        else:
            st.warning(message, icon=":material/warning:")
    if issue.affected_item:
        st.caption(f"Affected file or order: {issue.affected_item}")
    if not issue.recovery_actions:
        return
    with st.container(horizontal=True):
        for action in issue.recovery_actions:
            if st.button(
                action.label,
                icon=":material/visibility:" if action.action_type == VIEW_DETAILS else ":material/delete_outline:",
                key=f"recovery_action_{index}_{action.action_id}",
                disabled=not action.allowed,
            ):
                if action.action_type == VIEW_DETAILS:
                    st.session_state.validation_recovery_detail = action.action_id
                else:
                    st.session_state.pending_validation_recovery_action = action
                st.rerun()
    if st.session_state.get("validation_recovery_detail") == next(
        (action.action_id for action in issue.recovery_actions if action.action_type == VIEW_DETAILS),
        None,
    ):
        _render_issue_details(issue)


def _render_issue_details(issue: ValidationIssue) -> None:
    st.caption("Details")
    details = {
        key: value
        for key, value in issue.evidence.items()
        if key in {"source_pdf", "filename", "platform", "order_id", "status", "reason", "message", "error", "code"}
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    if details:
        st.json(details)
    else:
        st.write(issue.reason)


def _has_pending_recovery() -> bool:
    return bool(
        st.session_state.get("pending_validation_recovery_action")
        or st.session_state.get("pending_validation_bulk_recovery")
    )


def _queue_bulk_recovery(sources: list[str], *, label: str) -> None:
    source_names = tuple(dict.fromkeys(source.strip() for source in sources if source.strip()))
    if not source_names:
        return
    st.session_state.pending_validation_bulk_recovery = {
        "label": label,
        "sources": source_names,
    }
    st.rerun()


@st.dialog("Remove source(s) from current batch?", icon=":material/warning:")
def _render_recovery_confirmation() -> None:
    action = st.session_state.get("pending_validation_recovery_action")
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    if not isinstance(action, RecoveryAction) and not isinstance(bulk, dict):
        st.session_state.pop("pending_validation_recovery_action", None)
        st.session_state.pop("pending_validation_bulk_recovery", None)
        st.rerun()
    if isinstance(action, RecoveryAction):
        description = f"{action.label}: {action.affected_item}."
    else:
        sources = tuple(bulk.get("sources", ()))
        if not sources:
            st.session_state.pop("pending_validation_bulk_recovery", None)
            st.rerun()
        description = f"{bulk.get('label', 'Remove selected sources')}: {len(sources)} source(s)."
    st.warning(f"{description} This changes only current staging; archived source files remain unchanged.")
    with st.container(horizontal=True):
        if st.button("Confirm removal and revalidate", type="primary", icon=":material/delete:", key="confirm_validation_recovery"):
            begin_workflow_activity(st.session_state, "Revalidating")
            try:
                execution = (
                    execute_current_batch_recovery(st.session_state, action)
                    if isinstance(action, RecoveryAction)
                    else execute_current_batch_bulk_recovery(st.session_state, bulk["sources"])
                )
            finally:
                end_workflow_activity(st.session_state)
            st.session_state.validation_recovery_notice = execution.message
            st.session_state.validation_recovery_detail = None
            st.session_state.pop("pending_validation_recovery_action", None)
            st.session_state.pop("pending_validation_bulk_recovery", None)
            st.rerun()
        if st.button("Cancel", key="cancel_validation_recovery"):
            st.session_state.pop("pending_validation_recovery_action", None)
            st.session_state.pop("pending_validation_bulk_recovery", None)
            st.rerun()

def _render_recovery_notice() -> None:
    notice = st.session_state.pop("validation_recovery_notice", None)
    if notice:
        st.success(f"Recovery complete — {notice}", icon=":material/check_circle:")

def _render_recovery_area() -> None:
    st.subheader("Available recovery actions")
    st.caption("Use the available action to remove the identified source from the current batch and check it again. Original source files remain unchanged.")


def _render_manual_review_resolution() -> None:
    synchronize_correction_drafts(st.session_state)
    reviews = [item for item in st.session_state.get("reviews", []) if str(item.get("status", "")).strip() in {"", "Manual Review"}]
    st.subheader("Manual Review")
    notice = st.session_state.pop("manual_resolution_notice", None)
    if notice:
        (st.success if notice.startswith("Correction applied") else st.warning)(notice)
    if not reviews:
        st.caption("No current-batch sources require Manual Review.")
        return

    review_sources = [str(item.get("source_pdf") or "").strip() for item in reviews]
    unfixable_reviews = [r for r in reviews if resolution_plan(r) is None]
    fixable_reviews = [r for r in reviews if resolution_plan(r) is not None]

    # Download All Manual Reviews (both unfixable and fixable)
    all_output = io.StringIO()
    all_writer = csv.DictWriter(
        all_output,
        fieldnames=["Order ID", "Source PDF", "Platform", "Resolution Type", "Reason"],
    )
    all_writer.writeheader()
    for item in reviews:
        res_type = "Requires Re-upload" if resolution_plan(item) is None else "Online Resolution"
        all_writer.writerow({
            "Order ID": str(item.get("order_id") or ""),
            "Source PDF": str(item.get("source_pdf") or ""),
            "Platform": str(item.get("platform") or "Shopee"),
            "Resolution Type": res_type,
            "Reason": str(item.get("reason") or "Manual Review required"),
        })
    all_csv_data = all_output.getvalue().encode("utf-8-sig")

    with st.container(horizontal=True):
        st.download_button(
            label="📥 Download All Manual Reviews (CSV)",
            data=all_csv_data,
            file_name=f"all_manual_reviews_{st.session_state.get('batch_id', 'batch')}.csv",
            mime="text/csv",
            key="download_all_manual_reviews_csv",
        )

    tab_unfixable, tab_fixable = st.tabs([
        f"⚠️ Requires Re-upload ({len(unfixable_reviews)})",
        f"📝 Online Resolution ({len(fixable_reviews)})",
    ])

    with tab_unfixable:
        if not unfixable_reviews:
            st.success("No sources require re-upload in the current batch.", icon=":material/check_circle:")
        else:
            st.warning(
                "The following invoices have missing product anchors or invalid document structure and cannot be resolved online. "
                "Download this listing to request replacement files, and remove them from the current batch to proceed."
            )
            with st.container(horizontal=True):
                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=["Order ID", "Source PDF", "Platform", "Reason"],
                )
                writer.writeheader()
                for item in unfixable_reviews:
                    writer.writerow({
                        "Order ID": str(item.get("order_id") or ""),
                        "Source PDF": str(item.get("source_pdf") or ""),
                        "Platform": str(item.get("platform") or "Shopee"),
                        "Reason": str(item.get("reason") or "Manual Review required"),
                    })
                st.download_button(
                    label="📥 Download Re-upload Listing (CSV)",
                    data=output.getvalue().encode("utf-8-sig"),
                    file_name=f"manual_review_reupload_{st.session_state.get('batch_id', 'batch')}.csv",
                    mime="text/csv",
                    key="download_manual_review_unfixable_csv",
                )
                if st.button(
                    "Remove all Manual Review sources from current batch",
                    icon=":material/delete_sweep:",
                    key="remove_all_manual_review_sources",
                ):
                    _queue_bulk_recovery(review_sources, label="Remove all Manual Review sources")

            st.dataframe(
                [
                    {
                        "Order ID": item.get("order_id"),
                        "Source PDF": item.get("source_pdf"),
                        "Reason": item.get("reason"),
                    }
                    for item in unfixable_reviews
                ],
                hide_index=True,
            )

            for review in unfixable_reviews:
                with st.expander(f"Technical Details · {review.get('order_id') or 'Unknown'}", expanded=False):
                    source_pdf = review.get("source_pdf")
                    if source_pdf:
                        actions = recovery_actions_for_source(
                            source=source_pdf,
                            action_type=REMOVE_SOURCE,
                            remove_label="Remove source from current batch",
                            include_details=False,
                        )
                        for act in actions:
                            if act.destructive:
                                if st.button(
                                    act.label,
                                    icon=":material/delete_outline:",
                                    key=f"remove_single_mr_unfixable_{id(review)}_{act.action_id}",
                                ):
                                    st.session_state.pending_validation_recovery_action = act
                                    st.rerun()
                    if st.button("View Details", key=f"manual_details_{id(review)}"):
                        _render_issue_details(
                            ValidationIssue(
                                layer="manual_review",
                                severity="warning",
                                blocking=False,
                                reason=str(review.get("reason") or "Manual Review required."),
                                affected_item=(
                                    str(review.get("order_id") or "").strip()
                                    or str(review.get("source_pdf") or "").strip()
                                    or None
                                ),
                                evidence=review,
                            )
                        )

    with tab_fixable:
        if not fixable_reviews:
            st.success("No sources require online form resolution in the current batch.", icon=":material/check_circle:")
        else:
            st.info("The following invoices have missing fields or extracted product count discrepancies. Fill in the forms below to apply corrections.")
            if not unfixable_reviews:
                if st.button(
                    "Remove all Manual Review sources from current batch",
                    icon=":material/delete_sweep:",
                    key="remove_all_manual_review_sources_fixable",
                ):
                    _queue_bulk_recovery(review_sources, label="Remove all Manual Review sources")
            st.subheader("Manual Review actions")
            for review in fixable_reviews:
                plan = resolution_plan(review)
                if plan is None:
                    continue
                with st.container(border=True):
                    with st.container(horizontal=True):
                        st.write(f"**{review.get('order_id') or 'Unknown order'}**")
                        source_pdf = review.get("source_pdf")
                        if source_pdf:
                            actions = recovery_actions_for_source(
                                source=source_pdf,
                                action_type=REMOVE_SOURCE,
                                remove_label="Remove source from current batch",
                                include_details=False,
                            )
                            for act in actions:
                                if act.destructive:
                                    if st.button(
                                        act.label,
                                        icon=":material/delete_outline:",
                                        key=f"remove_single_mr_fixable_{id(review)}_{act.action_id}",
                                    ):
                                        st.session_state.pending_validation_recovery_action = act
                                        st.rerun()
                    st.caption(f"Source: {review.get('source_pdf') or 'Unavailable'}")
                    if plan.issue_type == PRODUCT_COUNT_MISMATCH:
                        if review.get("product_payloads"):
                            st.dataframe([{"Seller SKU": item.get("seller_sku"), "Product Name": item.get("product_name"), "Quantity": item.get("quantity")} for item in review["product_payloads"]], hide_index=True)
                        _render_missing_product_draft(plan.key, review)
                    elif plan.issue_type == PROMOTION_SUBTOTAL:
                        _render_promotion_subtotal_form(plan.key, review)
                    elif plan.issue_type == FINAL_AMOUNT:
                        _render_final_amount_form(plan.key, review)
                    else:
                        _render_income_form(plan.key, review)


def _apply_manual_resolution(key: str, values: dict[str, Any]) -> None:
    try:
        master, _ = load_configured_product_price_master()
        outcome = apply_resolution(st.session_state, key=key, values=values, price_master=master)
    except ProductMasterSourceError as error:
        st.session_state.manual_resolution_notice = f"Product Master validation is unavailable: {error}"
    else:
        st.session_state.manual_resolution_notice = (
            "Correction applied and revalidated. Continue to Reconcile." if outcome.resolved else f"Still needs review — {outcome.reason}"
        )
    st.rerun()


def _render_missing_product_draft(key: str, review: dict[str, Any]) -> None:
    summary = draft_summary(st.session_state, review)
    st.caption(
        f"Extracted Products: {summary.extracted_count} · Manually Added: {summary.manual_count} · "
        f"Source Declared: {summary.declared_count if summary.declared_count is not None else 'Unavailable'} · "
        f"Remaining Missing: {summary.remaining_missing if summary.remaining_missing is not None else 'Unavailable'}"
    )
    manual_products = draft_products(st.session_state, review)
    if manual_products:
        st.write("Manual corrections")
    for index, product in enumerate(manual_products):
        with st.expander(f"Product {index + 1}: {product.get('product_name') or 'Unnamed product'}"):
            _render_draft_product_form(key, review, index=index, existing=product)
            if st.button("Remove product", key=f"mr_remove_{key}_{index}", icon=":material/delete:"):
                outcome = remove_draft_product(st.session_state, key=key, index=index)
                st.session_state.manual_resolution_notice = "Draft product removed." if outcome.resolved else str(outcome.reason)
                st.rerun()
    if summary.remaining_missing is None or summary.remaining_missing > 0:
        with st.expander("Add missing product", expanded=not manual_products):
            _render_draft_product_form(key, review)
    _render_draft_promotion_subtotals(key, review)
    with st.container(horizontal=True):
        if st.button(
            "Apply & Revalidate",
            type="primary",
            key=f"mr_apply_draft_{key}",
            disabled=summary.remaining_missing != 0 or summary.exceeds_declared_count,
        ):
            _apply_product_draft(key)
        if st.button("Cancel corrections", key=f"mr_clear_draft_{key}"):
            clear_correction_draft(st.session_state, key=key)
            st.session_state.manual_resolution_notice = "Correction draft cleared."
            st.rerun()


def _render_draft_promotion_subtotals(key: str, review: dict[str, Any]) -> None:
    saved = draft_promotion_subtotals(st.session_state, review)
    for group in promotion_subtotal_groups(review):
        with st.form(f"draft_promotion_subtotal_{key}_{group.group_id}", border=False):
            st.caption(f"Promotion subtotal source correction: {group.label} · {', '.join(group.member_names)}")
            subtotal = st.text_input(
                "Promotion Subtotal",
                value=saved.get(group.group_id, ""),
                key=f"mr_draft_promo_subtotal_{key}_{group.group_id}",
            )
            confirmed = st.checkbox(
                "I verified this subtotal is visibly printed in the original Invoice source",
                key=f"mr_draft_promo_confirm_{key}_{group.group_id}",
            )
            if st.form_submit_button("Save promotion subtotal"):
                outcome = set_draft_promotion_subtotal(
                    st.session_state,
                    key=key,
                    group_id=group.group_id,
                    value=subtotal,
                    source_confirmed=confirmed,
                )
                st.session_state.manual_resolution_notice = (
                    "Correction draft updated." if outcome.resolved else f"Still needs review — {outcome.reason}"
                )
                st.rerun()


def _render_draft_product_form(
    key: str,
    review: dict[str, Any],
    *,
    index: int | None = None,
    existing: dict[str, Any] | None = None,
) -> None:
    existing = existing or {}
    suffix = f"{key}_{index if index is not None else 'new'}"
    groups = promotion_group_options(review)
    group_labels = ["No Promotion", *[
        f"{group.label} · {', '.join(group.member_names)}"
        + (f" · subtotal RM{group.source_group_total}" if group.source_group_total else "")
        for group in groups
    ]]
    group_ids = ["", *[group.group_id for group in groups]]
    selected_id = str(existing.get("promotion_group_id") or "")
    selected_index = group_ids.index(selected_id) if selected_id in group_ids else 0
    promotion_index = st.selectbox(
        "Promotion membership",
        range(len(group_labels)),
        index=selected_index,
        format_func=lambda value: group_labels[value],
        key=f"mr_promotion_{suffix}",
    )
    has_promotion = bool(group_ids[promotion_index])
    with st.form(f"missing_product_{suffix}", border=False):
        st.caption("Enter only facts visible in the original Invoice. NAV, Product Master price, and promotion allocation are derived.")
        sku_source = st.selectbox(
            "Seller SKU source evidence",
            ("Visible in source", "Not shown in source"),
            index=1 if existing.get("sku_missing_in_source") else 0,
            key=f"mr_sku_source_{suffix}",
        )
        seller_sku = st.text_input("Seller SKU", value=str(existing.get("seller_sku") or ""), key=f"mr_sku_{suffix}")
        product_name = st.text_input("Product Name", value=str(existing.get("product_name") or ""), key=f"mr_name_{suffix}")
        variation = st.text_input("Variation (optional)", value=str(existing.get("variation") or ""), key=f"mr_variation_{suffix}")
        quantity = st.number_input("Quantity", min_value=1, step=1, value=max(1, int(existing.get("quantity") or 1)), key=f"mr_qty_{suffix}")
        if has_promotion:
            st.caption("Actual selling allocation is derived from the selected source promotion group.")
            actual_price = ""
            subtotal = ""
        else:
            actual_price = st.text_input("Source Actual Selling Unit Price", value=str(existing.get("unit_price") or ""), key=f"mr_actual_{suffix}")
            subtotal = st.text_input("Source Line Subtotal", value=str(existing.get("source_line_subtotal") or ""), key=f"mr_subtotal_{suffix}")
        label = "Save changes" if index is not None else "Add missing product"
        if st.form_submit_button(label, type="primary"):
            values = {
                "seller_sku_visible": sku_source == "Visible in source", "seller_sku": seller_sku,
                "product_name": product_name, "variation": variation, "quantity": quantity,
                "promotion_group_id": group_ids[promotion_index],
                "actual_selling_unit_price": actual_price, "line_subtotal": subtotal,
            }
            outcome = (
                edit_draft_product(st.session_state, key=key, index=index, values=values)
                if index is not None
                else add_draft_product(st.session_state, key=key, values=values)
            )
            st.session_state.manual_resolution_notice = (
                "Correction draft updated." if outcome.resolved else f"Still needs review — {outcome.reason}"
            )
            st.rerun()


def _apply_product_draft(key: str) -> None:
    try:
        master, _ = load_configured_product_price_master()
        outcome = apply_product_draft(st.session_state, key=key, price_master=master)
    except ProductMasterSourceError as error:
        st.session_state.manual_resolution_notice = f"Product Master validation is unavailable: {error}"
    else:
        st.session_state.manual_resolution_notice = (
            "Correction applied and revalidated. Continue to Reconcile."
            if outcome.resolved else f"Still needs review — {outcome.reason}"
        )
    st.rerun()


def _render_promotion_subtotal_form(key: str, review: dict[str, Any]) -> None:
    groups = promotion_subtotal_groups(review)
    if not groups:
        st.info("Promotion evidence is ambiguous or the subtotal is absent from the source, so it cannot be safely corrected here.")
        return
    labels = [f"{group.label} · {', '.join(group.member_names)}" for group in groups]
    with st.form(f"promotion_subtotal_{key}", border=False):
        selected = st.selectbox("Promotion group", range(len(groups)), format_func=lambda value: labels[value], key=f"mr_promo_subtotal_group_{key}")
        subtotal = st.text_input("Promotion Subtotal", key=f"mr_promo_subtotal_{key}")
        confirmed = st.checkbox("I verified this subtotal is visibly printed in the original Invoice source", key=f"mr_promo_subtotal_confirm_{key}")
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(key, {"source_confirmed": confirmed, "promotion_group_id": groups[selected].group_id, "source_group_total": subtotal})


def _render_income_form(key: str, review: dict[str, Any]) -> None:
    payload = review.get("order_payload") or {}
    with st.form(f"income_resolution_{key}", border=False):
        st.caption(f"Parser value: {payload.get('order_income') or 'Missing'}. Only enter values visible in the original Invoice source.")
        source_confirmed = st.checkbox(
            "I verified these values are visible in the original Invoice source",
            key=f"mr_income_confirm_{key}",
        )
        income = st.text_input("Order Income", key=f"mr_income_{key}")
        income_type = st.selectbox("Income Type", ("Estimated", "Final"), key=f"mr_income_type_{key}")
        final_amount = st.text_input("Final Amount (optional — only when visible in source)", key=f"mr_final_{key}")
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(key, {"source_confirmed": source_confirmed, "order_income": income, "income_type": income_type, "final_amount": final_amount})


def _render_final_amount_form(key: str, review: dict[str, Any]) -> None:
    with st.form(f"final_amount_resolution_{key}", border=False):
        final_amount = st.text_input("Final Amount", key=f"mr_final_only_{key}")
        source_confirmed = st.checkbox("I confirm this Final Amount is visible in the original Invoice source.", key=f"mr_final_only_confirm_{key}")
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(key, {"source_confirmed": source_confirmed, "final_amount": final_amount})

def _render_reconciliation_step() -> None:
    st.subheader("Reconcile")
    if st.session_state.get("import_source_type") == PLATFORM_ORDERS:
        _reconcile_historical_invoice_staging()
        if _has_pending_recovery():
            _render_recovery_confirmation()
        _render_next_step("Continue to review & commit", 5)
        return
    reconciliation = _current_import_result().reconciliation
    if not reconciliation.available:
        reason = reconciliation.source_specific_details.get("reason")
        st.info(f"{reconciliation.status} — {reason or 'Reconciliation is not available for this staged result.'}", icon=":material/info:")
        _render_next_step("Continue to review & commit", 5)
        return
    _render_summary_items(reconciliation.summary)
    st.caption("These results are shown for review and do not change the source outcome.")
    _render_representative_contract_exceptions(reconciliation.exceptions)
    if st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT:
        _render_statement_review_tables()
        _render_statement_next_step("Continue to review & commit", 5)
    else:
        _render_next_step("Continue to review & commit", 5)


def _render_representative_contract_exceptions(exceptions: tuple[ReconciliationException, ...]) -> None:
    if not exceptions:
        return
    representative = []
    for exception in exceptions[:5]:
        row = {"Item": exception.affected_item or "—", "Status": exception.status}
        row.update(exception.evidence)
        representative.append(row)
    st.caption("Representative exceptions")
    st.dataframe(representative, hide_index=True, height="auto")

def _render_review_and_commit_step() -> None:
    st.subheader("Review & Commit")
    if _has_pending_recovery():
        _render_recovery_confirmation()
    result = _current_import_result()
    _render_source_summary(result)
    readiness = result.commit_readiness
    if st.session_state.get("import_source_type") == PLATFORM_ORDERS:
        entries = tuple(st.session_state.get("uat2_historical_commit_entries", ()))
        historical_ready = _historical_commit_ready(entries)
        if readiness.ready and historical_ready:
            st.success("Ready to Commit — current batch review is complete.", icon=":material/check_circle:")
        elif not readiness.ready:
            st.warning(f"Items still need attention — {' '.join(readiness.reasons)}", icon=":material/warning:")
        else:
            st.warning(
                "Return to Reconcile and resolve/remove all non-NEW sources before Commit.",
                icon=":material/warning:",
            )
        _render_historical_invoice_commit()
        return
    _render_statement_review_tables()
    review = _weekly_review()
    if review is not None and review.ready and not _statement_review_stale():
        st.success(
            "Reconciliation review complete — V2 found no business blockers.",
            icon=":material/check_circle:",
        )
        if not review.commit_ready:
            st.info(
                "Formal V2 Statement persistence is not available in this phase. "
                "No row-to-item allocation has been invented for GROUP evidence.",
                icon=":material/info:",
            )
    elif _statement_review_stale():
        st.warning(
            str(st.session_state.get("weekly_statement_review_stale_reason")),
            icon=":material/refresh:",
        )
    else:
        st.warning(f"Items still need attention — {' '.join(readiness.reasons)}", icon=":material/warning:")
    _render_statement_commit(readiness.ready)


def _render_source_summary(result: ImportResult) -> None:
    st.caption(f"Current batch: {result.session_state.label}")
    _render_summary_items(result.source_summary.items)


def _render_summary_items(items: tuple[Any, ...]) -> None:
    if not items:
        return
    columns = st.columns(len(items), gap="small")
    for column, item in zip(columns, items):
        with column:
            st.metric(item.label, item.value, border=True)


def _current_import_result() -> ImportResult:
    batch_id = st.session_state.get("batch_id")
    if st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT:
        review = _weekly_review()
        return adapt_shopee_weekly_statement_import_result(
            _weekly_stage(),
            batch_id=batch_id,
            review=review,
        )
    return adapt_platform_orders_import_result(
        batch_id=batch_id,
        orders=st.session_state.get("orders", []),
        products=st.session_state.get("products", []),
        reviews=st.session_state.get("reviews", []),
        processing_errors=st.session_state.get("processing_errors", []),
        duplicate_skipped=st.session_state.get("duplicate_skipped", []),
        unsupported_files=st.session_state.get("unsupported_files", []),
    )


def _render_historical_invoice_commit() -> None:
    signature = _historical_commit_signature()
    if st.session_state.get("uat2_historical_commit_signature") not in (None, signature):
        st.session_state.pop("uat2_historical_commit_entries", None)
        st.session_state.pop("uat2_historical_commit_refresh_required", None)
        st.session_state.pop("uat2_historical_commit_signature", None)
        st.info("Current batch changed. Validate again before committing.")
    st.subheader("Historical Invoice Commit")
    st.caption(
        "Only Accepted Shopee invoices are eligible. Lazada and ZENXIN remain outside UAT2 Phase 3 persistence. "
        "Historical status is calculated during Reconcile."
    )
    entries = tuple(st.session_state.get("uat2_historical_commit_entries", ()))
    if not entries:
        st.button("Commit Accepted Shopee Invoices", icon=":material/upload:", disabled=True, key="uat2_historical_commit")
        return
    counts = {status: sum(entry.status is status for entry in entries) for status in IntakeStatus}
    st.caption(
        f"Shopee Sources: {len(entries)} · New: {counts[IntakeStatus.NEW]} · "
        f"Already Imported: {counts[IntakeStatus.ALREADY_IMPORTED]} · Needs Review: {counts[IntakeStatus.NEEDS_REVIEW]} · "
        f"Source Conflict: {counts[IntakeStatus.SOURCE_CONFLICT]}"
    )
    refresh_required = bool(st.session_state.get("uat2_historical_commit_refresh_required", False))
    if refresh_required:
        st.warning("Return to Reconcile and revalidate historical status before retrying.")
    clean_batch = _historical_commit_ready(entries)
    if st.button("Commit Accepted Shopee Invoices", icon=":material/upload:", type="primary", disabled=not clean_batch or refresh_required, key="uat2_historical_commit"):
        try:
            repository = configured_uat2_data_settings().create_repository()
            outcome = import_new_staging(entries, repository)
            st.session_state.uat2_historical_commit_entries = outcome.entries
            actual = outcome.bulk_result.results
            if any(item.status.value != "NEW" for item in actual):
                st.session_state.uat2_historical_commit_refresh_required = True
                st.warning("Historical state changed immediately before commit. No mixed-batch write was started; validate again.")
            else:
                st.success(f"Historical Invoice Commit Complete — Imported: {len(actual)}.")
        except HistoricalInvoiceBulkImportError as error:
            st.session_state.uat2_historical_commit_refresh_required = True
            st.error(
                f"Historical write stopped after {len(error.confirmed_results)} confirmed bundle(s). "
                "Return to Validate and revalidate historical status before retrying."
            )
        except ApplicationCommitInProgress as error:
            st.warning(str(error))
        except HistoricalInvoiceStorageError as error:
            st.error(f"Historical Invoice storage write failed: {error}")


def _reconcile_historical_invoice_staging() -> None:
    signature = _historical_commit_signature()
    refresh_required = st.session_state.get(
        "uat2_historical_commit_refresh_required", False
    )
    if (
        st.session_state.get("uat2_historical_commit_signature") != signature
        or refresh_required
    ):
        try:
            master, _label = load_configured_product_price_master()
            candidates = build_current_batch_staging(
                batch_id=st.session_state.get("batch_id"),
                orders=st.session_state.get("orders", []),
                products=st.session_state.get("products", []),
                reviews=st.session_state.get("reviews", []),
                price_master=master,
            )
            entries = classify_staging(
                candidates,
                configured_uat2_data_settings().create_repository(),
            )
            st.session_state.uat2_historical_commit_entries = entries
            st.session_state.uat2_historical_commit_refresh_required = False
            st.session_state.uat2_historical_commit_signature = signature
        except (HistoricalInvoiceStorageError, ProductMasterSourceError) as error:
            st.error(f"Historical Invoice validation is unavailable: {error}")
            return
    entries = tuple(st.session_state.get("uat2_historical_commit_entries", ()))
    _render_historical_status_details(
        entries,
        allow_removal=True,
        key_prefix="reconcile_historical",
    )


def _render_historical_status_details(
    entries: tuple[Any, ...],
    *,
    allow_removal: bool,
    key_prefix: str,
) -> None:
    st.subheader("Historical Invoice Status")
    if not entries:
        st.caption("No target Shopee Invoice source is ready for historical classification.")
        return
    st.dataframe(
        [
            {
                "Source PDF": entry.source_filename,
                "Order ID": entry.order_id or "N/A",
                "Historical Status": entry.status.value,
                "Reason / Message": entry.message or "Ready for Review & Commit.",
            }
            for entry in entries
        ],
        hide_index=True,
    )
    if not allow_removal:
        return
    needs_review_sources = [
        entry.source_filename
        for entry in entries
        if entry.status is IntakeStatus.NEEDS_REVIEW and entry.source_filename
    ]
    if needs_review_sources and st.button(
        "Remove all NEEDS_REVIEW sources from current batch",
        icon=":material/delete_sweep:",
        key=f"{key_prefix}_remove_all_needs_review",
    ):
        _queue_bulk_recovery(needs_review_sources, label="Remove all NEEDS_REVIEW sources")
    for entry in entries:
        if entry.status is IntakeStatus.NEW:
            continue
        actions = recovery_actions_for_source(
            source=entry.source_filename,
            action_type=REMOVE_SOURCE,
            remove_label="Remove source from current batch",
            include_details=False,
        )
        if actions and st.button(
            f"Remove {entry.source_filename} from current batch",
            icon=":material/delete_outline:",
            key=f"{key_prefix}_{entry.staging_id}",
        ):
            st.session_state.pending_validation_recovery_action = actions[0]
            st.rerun()


def _historical_commit_ready(entries: tuple[Any, ...]) -> bool:
    return bool(entries) and (
        st.session_state.get("uat2_historical_commit_signature")
        == _historical_commit_signature()
        and not st.session_state.get("uat2_historical_commit_refresh_required", False)
        and all(
            entry.status is IntakeStatus.NEW and entry.bundle is not None
            for entry in entries
        )
    )


def _weekly_stage() -> StagedShopeeWeeklyStatement | None:
    stage = st.session_state.get("weekly_statement_stage")
    return stage if isinstance(stage, StagedShopeeWeeklyStatement) else None


def _weekly_review() -> StatementImportReview | None:
    review = st.session_state.get("weekly_statement_review")
    return review if isinstance(review, StatementImportReview) else None


def _render_statement_review_tables() -> None:
    review = _weekly_review()
    if (
        review is None
        or review.stage.statement is None
        or review.reconciliation_v2 is None
    ):
        return
    batch = review.reconciliation_v2
    st.subheader("Reconciliation V2 review")
    stale_reason = st.session_state.get("weekly_statement_review_stale_reason")
    if stale_reason:
        st.warning(str(stale_reason), icon=":material/refresh:")
    st.caption(
        "Identity, merchandise, and seller settlement are evaluated separately. "
        "GROUP is valid source-level reconciliation, not a failed item match."
    )
    st.dataframe(
        [
            {
                "Order ID": result.evidence.order_id,
                "Identity": result.summary.identity_scope.value,
                "Merchandise": (
                    "Reconciled"
                    if result.summary.merchandise_reconciled
                    else "Unresolved"
                ),
                "Invoice Product Price": _order_merchandise_value(
                    result, "invoice_value"
                ),
                "Statement Product Price": _order_merchandise_value(
                    result, "statement_value"
                ),
                "Settlement": result.summary.settlement_basis.value.title(),
                "Unexplained Residual": (
                    result.evidence.settlement.unexplained_residual
                ),
                "Allocation": (
                    "Resolved"
                    if result.summary.allocation_resolved
                    else (
                        "Group only"
                        if result.summary.identity_scope is IdentityScope.GROUP
                        else "Unresolved"
                    )
                ),
                "Source note": _order_review_note(result),
            }
            for result in batch.orders
        ],
        hide_index=True,
    )
    for limitation in review.limitations:
        st.warning(limitation, icon=":material/info:")
    st.caption(
        "Quantity evidence — Invoice quantity: available where captured. "
        "Statement quantity: not provided by source. No quantity-match claim is made."
    )
    with st.container(border=True):
        st.write("**Statement Adjustment evidence (separate)**")
        st.write(f"RM {batch.statement_adjustment_total:.2f}")
        st.caption(
            "This is not included in original merchandise or seller-settlement reconciliation."
        )
    with st.expander("Technical reconciliation evidence"):
        st.caption(
            f"Rule {batch.rule_version} · Product Master snapshot "
            f"{batch.product_family_snapshot.sha256}"
        )
        st.dataframe(_identity_evidence_rows(batch), hide_index=True)
        settlement_rows = _settlement_evidence_rows(batch)
        if settlement_rows:
            st.dataframe(settlement_rows, hide_index=True)


def _render_statement_commit(ready: bool) -> None:
    review = _weekly_review()
    if review is None:
        st.button("Commit Statement", disabled=True, key="statement_commit")
        return
    with st.container(horizontal=True):
        refresh_clicked = st.button(
            "Refresh validation",
            icon=":material/refresh:",
            key="statement_refresh_validation",
        )
        commit_clicked = st.button(
            "Commit Statement",
            type="primary",
            icon=":material/upload:",
            disabled=(
                not ready
                or not review.commit_ready
                or _statement_review_stale()
            ),
            key="statement_commit",
        )
    if refresh_clicked:
        if _refresh_statement_review(review):
            st.rerun()
    if not commit_clicked:
        return
    settings = configured_uat2_data_settings()
    try:
        master, _label = load_configured_product_price_master()
        currency = check_statement_review_currency(
            review,
            repository=settings.create_repository(),
            writer=settings.create_statement_writer(),
            product_master=master,
        )
        if not currency.is_current:
            st.warning(
                "Statement review evidence changed and must be evaluated again: "
                + ", ".join(currency.changed_evidence)
            )
            if _refresh_statement_review(review):
                _set_step(3)
                st.rerun()
            return
        attempt = commit_statement_review(
            review,
            repository=settings.create_repository(),
            writer=settings.create_statement_writer(),
            load_product_master=lambda: load_configured_product_price_master()[0],
        )
    except ApplicationCommitInProgress as error:
        st.warning(str(error))
        return
    except StatementWriteIntegrityError as error:
        st.error(f"Statement write requires manual integrity recovery: {error}")
        return
    except (HistoricalInvoiceStorageError, ProductMasterSourceError, StatementCommitBlocked) as error:
        st.error(f"Statement commit failed before a safe write could be confirmed: {error}")
        return
    if attempt.committed:
        st.success("Statement Commit Complete.", icon=":material/check_circle:")
        return
    st.warning(
        "Statement state changed or the write was not applied. No new commit was confirmed; validation has been refreshed."
    )
    if not _refresh_statement_review(review):
        return
    _set_step(3)
    st.rerun()


def _refresh_statement_review(review: StatementImportReview) -> bool:
    try:
        settings = configured_uat2_data_settings()
        master, _label = load_configured_product_price_master()
        refreshed = refresh_statement_review(
            review,
            repository=settings.create_repository(),
            writer=settings.create_statement_writer(),
            product_master=master,
        )
    except (HistoricalInvoiceStorageError, ProductMasterSourceError, StatementCommitBlocked) as error:
        st.error(f"Statement validation refresh failed: {error}")
        return False
    st.session_state.weekly_statement_review = refreshed
    st.session_state.weekly_statement_stage = refreshed.stage
    st.session_state.pop("weekly_statement_review_stale_reason", None)
    return True


def _render_statement_next_step(label: str, step: int) -> None:
    """Re-evaluate current evidence before moving to the next review step."""

    if not st.button(label, type="primary", icon=":material/arrow_forward:"):
        return
    review = _weekly_review()
    if review is None:
        st.warning("Validate the Statement before continuing.")
        return
    if _refresh_statement_review(review):
        _set_step(step)
        st.rerun()


def _statement_review_stale() -> bool:
    return bool(st.session_state.get("weekly_statement_review_stale_reason"))


def _order_merchandise_value(result: Any, field: str) -> Any:
    control = next(
        (
            evidence
            for evidence in result.evidence.merchandise
            if evidence.scope_key == "ORDER_CONTROL"
        ),
        None,
    )
    return getattr(control, field, None) if control is not None else None


def _order_review_note(result: Any) -> str:
    summary = result.summary
    if summary.identity_scope is IdentityScope.UNRESOLVED:
        return "Product identity requires authoritative source evidence."
    if summary.identity_scope is IdentityScope.GROUP:
        return (
            "Product group reconciled; individual Statement row allocation "
            "cannot be proven from source evidence."
        )
    if "LATE_STATEMENT_REFUND_EFFECT" in result.evidence.settlement.internal_effects:
        return (
            "Later authoritative Statement refund explains settlement; the "
            "earlier Invoice source remains unchanged."
        )
    if summary.settlement_basis is SettlementBasis.EXPLAINED:
        return "Statement components fully explain the final settlement difference."
    return "Item identity and financial evidence reconcile."


def _identity_evidence_rows(batch: Any) -> list[dict[str, Any]]:
    rows = []
    for result in batch.orders:
        for identity in result.evidence.identities:
            selected = (
                ", ".join(
                    f"row {statement.source_row_number} → item {invoice.item_index}"
                    for statement, invoice in identity.selected_pairs
                )
                if identity.identity_scope is IdentityScope.ITEM
                else "Not assigned"
            )
            rows.append(
                {
                    "Order ID": result.evidence.order_id,
                    "Product ID": identity.product_id or "Not provided",
                    "Identity scope": identity.identity_scope.value,
                    "Statement members": len(identity.statement_members),
                    "Invoice members": len(identity.invoice_members),
                    "Physical allocation": selected,
                    "Evidence": identity.diagnostic,
                }
            )
    return rows


def _settlement_evidence_rows(batch: Any) -> list[dict[str, Any]]:
    rows = []
    for result in batch.orders:
        settlement = result.evidence.settlement
        if settlement.basis is SettlementBasis.EXACT and not settlement.internal_effects:
            continue
        material_components = tuple(
            component
            for component in settlement.component_deltas
            if component.delta not in (None, 0)
            or component.treatment
            in {"LATE_STATEMENT_REFUND_EFFECT", "INVOICE_SOURCE_EVIDENCE_MISSING"}
        )
        for component in material_components or (None,):
            rows.append(
                {
                    "Order ID": result.evidence.order_id,
                    "Basis": settlement.basis.value,
                    "Component": component.component if component else "Residual control",
                    "Invoice component": component.invoice_value if component else None,
                    "Statement component": component.statement_value if component else None,
                    "Component delta": component.delta if component else None,
                    "Treatment": (
                        component.treatment
                        if component
                        else ", ".join(settlement.internal_effects) or "CONTROL"
                    ),
                    "Invoice basis": settlement.invoice_basis,
                    "Statement settlement": settlement.statement_total,
                    "Unexplained residual": settlement.unexplained_residual,
                }
            )
    return rows


def _historical_commit_signature() -> str:
    rows = []
    for bucket in ("orders", "products", "reviews", "processing_errors", "duplicate_skipped", "unsupported_files"):
        for record in st.session_state.get(bucket, []):
            if isinstance(record, dict):
                rows.append((bucket, tuple(sorted((str(key), repr(value)) for key, value in record.items()))))
    return sha256(repr((st.session_state.get("batch_id"), tuple(rows))).encode("utf-8")).hexdigest()


def _render_next_step(label: str, step: int) -> None:
    if st.button(label, type="primary", icon=":material/arrow_forward:"):
        _set_step(step)
        st.rerun()
