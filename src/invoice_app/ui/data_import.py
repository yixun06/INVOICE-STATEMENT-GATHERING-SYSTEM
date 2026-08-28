"""Single-page import wizard presentation.

This module deliberately orchestrates existing import/staging services only. It
does not own parsing, validation, reconciliation, duplicate, export, or
persistence rules.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from ..services.batch_service import create_batch_id
from ..services.import_result_adapters import (
    adapt_platform_orders_import_result,
    adapt_shopee_weekly_statement_import_result,
)
from ..services.import_result_contract import ImportResult, ReconciliationException, RecoveryAction, ValidationIssue
from ..services.validation_recovery import VIEW_DETAILS, execute_current_batch_recovery
from ..services.workflow_navigation import begin_workflow_activity, end_workflow_activity
from ..services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
    stage_shopee_weekly_statement,
)
from .settlement_test_lab import sync_accepted_orders_to_test_session



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
    "weekly_statement_uploader_version",
    "weekly_statement_selected_source",
)


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
        _render_validation_step(render_platform_orders_outcomes)
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
        source_type = st.radio(
            "Data type",
            (PLATFORM_ORDERS, SHOPEE_WEEKLY_STATEMENT),
            key="weekly_statement_selected_source",
            horizontal=True,
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
        begin_workflow_activity(st.session_state, "Checking statement")
        try:
            st.session_state.weekly_statement_stage = stage_shopee_weekly_statement(
                uploaded_file,
                source_filename=uploaded_file.name,
                existing_orders=st.session_state.get("orders", []),
            )
        finally:
            end_workflow_activity(st.session_state)
        _set_step(3)
        st.rerun()


def _render_validation_step(render_platform_orders_outcomes: Callable[[], Any]) -> None:
    st.subheader("Validate")
    result = _current_import_result()
    _render_source_summary(result)
    _render_contract_validation(result)
    if st.session_state.get("pending_validation_recovery_action"):
        _render_recovery_confirmation()
    if result.source_specific_details.get("show_platform_order_outcomes"):
        render_platform_orders_outcomes()
        _render_test_session_sync()
    _render_recovery_area()
    _render_next_step("Continue to reconcile", 4)


def _render_test_session_sync() -> None:
    accepted_count = sum(
        1
        for order in st.session_state.get("orders", [])
        if str(order.get("status") or "").strip() == "Accepted"
    )
    st.subheader("Temporary Settlement Test Session")
    st.caption(
        "TEMP_TEST_ONLY — copy Accepted Platform Orders into the isolated Settlement Test Lab. "
        "Manual Review records are excluded and original source data is unchanged."
    )
    if st.button(
        "Sync Accepted Orders to Test Session",
        icon=":material/sync:",
        disabled=accepted_count == 0,
        key="sync_accepted_orders_to_test_session",
    ):
        synced_count = sync_accepted_orders_to_test_session(st.session_state.get("orders", []))
        st.success(f"Synced {synced_count} Accepted order(s) to Settlement Test Lab.", icon=":material/check_circle:")


def _render_contract_validation(result: ImportResult) -> None:
    validation = result.validation
    _render_recovery_notice()
    if not validation.blocking_issues and not validation.warnings:
        if result.session_state.applied_to_current_session:
            st.success("No validation issues in the current batch.", icon=":material/check_circle:")
        else:
            st.info(result.source_summary.empty_message or "No import result is staged yet.", icon=":material/info:")
    for index, issue in enumerate(validation.blocking_issues):
        _render_validation_issue(issue, index=index, is_blocking=True)
    for index, issue in enumerate(validation.warnings, start=len(validation.blocking_issues)):
        _render_validation_issue(issue, index=index, is_blocking=False)


def _render_validation_issue(issue: ValidationIssue, *, index: int, is_blocking: bool) -> None:
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


@st.dialog("Remove source from current batch?", icon=":material/warning:")
def _render_recovery_confirmation() -> None:
    action = st.session_state.get("pending_validation_recovery_action")
    if not isinstance(action, RecoveryAction):
        st.session_state.pop("pending_validation_recovery_action", None)
        st.rerun()
    st.warning(f"{action.label}: {action.affected_item}. This changes only current staging; archived source files remain unchanged.")
    with st.container(horizontal=True):
        if st.button("Confirm removal and revalidate", type="primary", icon=":material/delete:", key="confirm_validation_recovery"):
            begin_workflow_activity(st.session_state, "Revalidating")
            try:
                execution = execute_current_batch_recovery(st.session_state, action)
            finally:
                end_workflow_activity(st.session_state)
            st.session_state.validation_recovery_notice = execution.message
            st.session_state.validation_recovery_detail = None
            st.session_state.pop("pending_validation_recovery_action", None)
            st.rerun()
        if st.button("Cancel", key="cancel_validation_recovery"):
            st.session_state.pop("pending_validation_recovery_action", None)
            st.rerun()

def _render_recovery_notice() -> None:
    notice = st.session_state.pop("validation_recovery_notice", None)
    if notice:
        st.success(f"Recovery complete — {notice}", icon=":material/check_circle:")

def _render_recovery_area() -> None:
    st.subheader("Available recovery actions")
    st.caption("Use the available action to remove the identified source from the current batch and check it again. Original source files remain unchanged.")

def _render_reconciliation_step() -> None:
    st.subheader("Reconcile")
    reconciliation = _current_import_result().reconciliation
    if not reconciliation.available:
        reason = reconciliation.source_specific_details.get("reason")
        st.info(f"{reconciliation.status} — {reason or 'Reconciliation is not available for this staged result.'}", icon=":material/info:")
        _render_next_step("Continue to review & commit", 5)
        return
    _render_summary_items(reconciliation.summary)
    st.caption("These results are shown for review and do not change the source outcome.")
    _render_representative_contract_exceptions(reconciliation.exceptions)
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
    result = _current_import_result()
    _render_source_summary(result)
    readiness = result.commit_readiness
    if readiness.ready:
        st.success("Ready to Commit — current batch review is complete.", icon=":material/check_circle:")
    else:
        st.warning(f"Items still need attention — {' '.join(readiness.reasons)}", icon=":material/warning:")
    st.caption(
        "Future database commit is disabled in this Self-Test Version. "
        "This review never writes or marks production data as committed."
    )
    st.button(
        "Future Database Commit",
        icon=":material/lock:",
        disabled=True,
        key="future_database_commit_disabled",
    )


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
        return adapt_shopee_weekly_statement_import_result(_weekly_stage(), batch_id=batch_id)
    return adapt_platform_orders_import_result(
        batch_id=batch_id,
        orders=st.session_state.get("orders", []),
        products=st.session_state.get("products", []),
        reviews=st.session_state.get("reviews", []),
        processing_errors=st.session_state.get("processing_errors", []),
        duplicate_skipped=st.session_state.get("duplicate_skipped", []),
        unsupported_files=st.session_state.get("unsupported_files", []),
    )

def _weekly_stage() -> StagedShopeeWeeklyStatement | None:
    stage = st.session_state.get("weekly_statement_stage")
    return stage if isinstance(stage, StagedShopeeWeeklyStatement) else None


def _render_next_step(label: str, step: int) -> None:
    if st.button(label, type="primary", icon=":material/arrow_forward:"):
        _set_step(step)
        st.rerun()
