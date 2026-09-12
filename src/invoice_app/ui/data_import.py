"""Single-page import wizard presentation.

This module deliberately orchestrates existing import/staging services only. It
does not own parsing, validation, reconciliation, duplicate, export, or
persistence rules.
"""

from __future__ import annotations

from hashlib import sha256
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
    VIEW_DETAILS,
    execute_current_batch_bulk_recovery,
    execute_current_batch_recovery,
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
    commit_statement_review,
    refresh_statement_review,
    review_statement_upload,
)
from ..services.shopee_statement_persistence import (
    StatementCommitBlocked,
    StatementWriteIntegrityError,
)
from ..services.shopee_statement_item_matching import business_match_method
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
    "weekly_statement_uploader_version",
    "weekly_statement_selected_source",
    "uat2_historical_commit_entries",
    "uat2_historical_commit_refresh_required",
    "uat2_historical_commit_signature",
    "manual_review_correction_drafts",
    "pending_validation_bulk_recovery",
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
    _render_next_step("Continue to reconcile", 4)


def _render_contract_validation(result: ImportResult) -> None:
    validation = result.validation
    _render_recovery_notice()
    visible_warnings = tuple(
        issue for issue in validation.warnings if issue.layer != "manual_review"
    )
    if not validation.blocking_issues and not visible_warnings:
        if result.session_state.applied_to_current_session:
            if not validation.warnings:
                st.success("No validation issues in the current batch.", icon=":material/check_circle:")
        else:
            st.info(result.source_summary.empty_message or "No import result is staged yet.", icon=":material/info:")
    for index, issue in enumerate(validation.blocking_issues):
        _render_validation_issue(issue, index=index, is_blocking=True)
    for index, issue in enumerate(visible_warnings, start=len(validation.blocking_issues)):
        _render_validation_issue(issue, index=index, is_blocking=False)
    for index, issue in enumerate(
        (issue for issue in validation.warnings if issue.layer == "manual_review"),
        start=len(validation.blocking_issues) + len(visible_warnings),
    ):
        _render_validation_issue(issue, index=index, is_blocking=False, show_message=False)


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
    st.dataframe([{"Source PDF": item.get("source_pdf"), "Order ID": item.get("order_id"), "Reason": item.get("reason")} for item in reviews], hide_index=True)
    review_sources = [str(item.get("source_pdf") or "").strip() for item in reviews]
    if st.button(
        "Remove all Manual Review sources from current batch",
        icon=":material/delete_sweep:",
        key="remove_all_manual_review_sources",
    ):
        _queue_bulk_recovery(review_sources, label="Remove all Manual Review sources")
    st.subheader("Manual Review actions")
    for review in reviews:
        plan = resolution_plan(review)
        with st.container(border=True):
            st.write(f"**{review.get('order_id') or 'Unknown order'}**")
            st.caption(f"Source: {review.get('source_pdf') or 'Unavailable'}")
            if plan is None:
                st.info("This issue needs source evidence or Product Master resolution and cannot be force-resolved.")
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
                continue
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
    if readiness.ready:
        st.success("Ready to Commit — current batch review is complete.", icon=":material/check_circle:")
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
            sku_matches=review.sku_matches if review is not None else None,
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
    if review is None or review.stage.statement is None:
        return
    statement = review.stage.statement
    st.subheader("Order ID Coverage and Amount Reconciliation")
    st.caption(
        "Covered means the Statement Order ID exists in Invoice data; it does not mean the amount matched."
    )
    st.dataframe(
        [
            {
                "Order ID": item.order_id,
                "Released Amount": item.released_amount,
                "Comparison Source": item.comparison_source,
                "Comparison Amount": item.comparison_amount,
                "Difference": item.difference,
                "Status": item.status,
            }
            for item in review.stage.order_reconciliations
        ],
        hide_index=True,
    )
    sku_by_row = {row.source_row_number: row for row in statement.sku_rows}
    st.subheader("SKU review")
    st.dataframe(
        [
            {
                "Order ID": match.order_id,
                "Product ID": match.product_id,
                "Product Name": match.product_name,
                "Product Price": (
                    sku_by_row[match.statement_source_row].financial_components.get("Product Price")
                    if match.statement_source_row in sku_by_row
                    else None
                ),
                "Refund": (
                    sku_by_row[match.statement_source_row].financial_components.get("Refund Amount")
                    if match.statement_source_row in sku_by_row
                    else None
                ),
                "Matched Item": match.invoice_item_index,
                "Match Method": business_match_method(match.match_method),
                "Status": match.status.value,
            }
            for match in review.sku_matches.matches
        ],
        hide_index=True,
    )


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
            disabled=not ready or not review.ready,
            key="statement_commit",
        )
    if refresh_clicked:
        if _refresh_statement_review(review):
            st.rerun()
    if not commit_clicked:
        return
    settings = configured_uat2_data_settings()
    try:
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
    return True


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
