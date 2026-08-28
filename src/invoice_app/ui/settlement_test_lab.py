"""Temporary, removable UI for session-only Shopee settlement testing."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd
import streamlit as st

from ..services.settlement_reporting import build_shopee_settlement_reporting
from ..services.workflow_navigation import begin_workflow_activity, end_workflow_activity, request_navigation
from ..services.shopee_weekly_statement_service import (
    READY_TO_COMMIT,
    StagedShopeeWeeklyStatement,
    stage_shopee_weekly_statement,
)


SETTLEMENT_TEST_LAB_PAGE = "Settlement Test Lab"
_STAGE_KEY = "settlement_test_lab_statement_stage"
_UPLOADER_VERSION_KEY = "settlement_test_lab_uploader_version"
_ACCEPTED_ORDERS_KEY = "settlement_test_lab_accepted_orders"


def initialize_settlement_test_lab_state() -> None:
    """Initialize only the temporary test-lab widget/session keys."""
    st.session_state.setdefault(_UPLOADER_VERSION_KEY, 0)


def reset_settlement_test_lab_state() -> None:
    """Discard the temporary statement when the active session batch is cleared."""
    st.session_state.pop(_STAGE_KEY, None)
    st.session_state.pop(_UPLOADER_VERSION_KEY, None)
    st.session_state.pop(_ACCEPTED_ORDERS_KEY, None)


def sync_accepted_orders_to_test_session(
    orders: Iterable[Mapping[str, Any]],
) -> int:
    """TEMP_TEST_ONLY: copy Accepted order facts into the removable Lab state.

    TODO: delete this session bridge when database-backed reporting replaces the
    temporary Settlement Test Lab. Manual Review records live in ``reviews``
    and are deliberately neither read nor changed here.
    """
    accepted_orders = [
        dict(order)
        for order in orders
        if str(order.get("status") or "").strip() == "Accepted"
    ]
    st.session_state[_ACCEPTED_ORDERS_KEY] = accepted_orders
    return len(accepted_orders)


def render_settlement_test_lab() -> None:
    """Render a source-preserving comparison without persistence or mutation."""
    initialize_settlement_test_lab_state()
    session_orders = tuple(st.session_state.get(_ACCEPTED_ORDERS_KEY, ()))
    st.title("Settlement Test Lab")
    st.caption("Temporary DEVELOPMENT / TESTING workspace. It is session-only and will be removed before the database phase.")
    if st.button("← Back to Data Import", icon=":material/arrow_back:", key="settlement_lab_back_to_data_import"):
        request_navigation(st.session_state, "Data Import")
        st.rerun()
    st.info(
        "This lab builds a reporting projection from Accepted Platform Orders synced to the temporary Test Session and a validated Weekly Statement. "
        "It does not change invoice source facts, parsers, reports, exports, or database records.",
        icon=":material/science:",
    )

    _render_sources(session_orders)
    stage = _current_stage()
    _render_statement_input(session_orders, stage)
    stage = _current_stage()
    if stage is None:
        st.info("Validate a Shopee Weekly Statement in this lab to view the settlement comparison.", icon=":material/info:")
        return
    _render_statement_validation(stage)
    if not stage.eligible_for_future_atomic_commit or stage.statement is None:
        st.warning(
            "Settlement comparison is unavailable until the test statement passes its existing validation. No source data was changed.",
            icon=":material/warning:",
        )
        return

    result = build_shopee_settlement_reporting(session_orders, stage.statement)
    _render_summary(result.summary)
    _render_projection(result.rows, stage.statement.order_rows)


def _render_sources(orders: tuple[Mapping[str, Any], ...]) -> None:
    shopee_order_count = build_shopee_settlement_reporting(orders, None).summary.total_shopee_orders
    with st.container(border=True):
        st.subheader("Current session inputs")
        st.metric("Synced Accepted Shopee Orders", shopee_order_count, border=True)
        st.caption("TEMP_TEST_ONLY — sync Accepted orders from Platform Orders validation. Manual Review records never enter this Test Session. TODO: remove this bridge when database reporting is live.")


def _render_statement_input(
    orders: tuple[Mapping[str, Any], ...], stage: StagedShopeeWeeklyStatement | None
) -> None:
    with st.container(border=True):
        st.subheader("Weekly Statement test input")
        st.caption("Upload one native Shopee Weekly Statement export (.xlsx). This validation result remains in the current session only.")
        version = int(st.session_state.get(_UPLOADER_VERSION_KEY, 0))
        uploaded = st.file_uploader(
            "Shopee Weekly Statement (.xlsx)",
            type=["xlsx"],
            key=f"settlement_test_lab_uploader_{version}",
        )
        has_lab_stage = isinstance(st.session_state.get(_STAGE_KEY), StagedShopeeWeeklyStatement)
        with st.container(horizontal=True):
            validate_clicked = st.button(
                "Validate test statement",
                type="primary",
                icon=":material/fact_check:",
                disabled=uploaded is None,
            )
            clear_clicked = st.button(
                "Clear lab test statement",
                icon=":material/close:",
                disabled=not has_lab_stage and uploaded is None,
            )
        if clear_clicked:
            st.session_state.pop(_STAGE_KEY, None)
            st.session_state[_UPLOADER_VERSION_KEY] = version + 1
            st.rerun()
        if validate_clicked and uploaded is not None:
            begin_workflow_activity(st.session_state, "Validating")
            try:
                with st.status("Validating Shopee Weekly Statement", expanded=True) as status:
                    progress = st.progress(10, text="Reading the native workbook")
                    st.session_state[_STAGE_KEY] = stage_shopee_weekly_statement(
                        uploaded,
                        source_filename=uploaded.name,
                        existing_orders=orders,
                    )
                    progress.progress(100, text="Validation and staging complete")
                    status.update(
                        label="Weekly Statement validation complete",
                        state="complete",
                        expanded=False,
                    )
            finally:
                end_workflow_activity(st.session_state)
            st.rerun()

def _current_stage() -> StagedShopeeWeeklyStatement | None:
    lab_stage = st.session_state.get(_STAGE_KEY)
    if isinstance(lab_stage, StagedShopeeWeeklyStatement):
        return lab_stage
    import_stage = st.session_state.get("weekly_statement_stage")
    return import_stage if isinstance(import_stage, StagedShopeeWeeklyStatement) else None


def _render_statement_validation(stage: StagedShopeeWeeklyStatement) -> None:
    with st.container(border=True):
        st.subheader("Weekly Statement validation")
        statement = stage.statement
        if statement is not None:
            left, right = st.columns(2)
            with left:
                st.metric(
                    "Statement period",
                    f"{statement.statement_period_from:%d/%m/%Y} – {statement.statement_period_to:%d/%m/%Y}",
                    border=True,
                )
                st.metric("Order rows", len(statement.order_rows), border=True)
            with right:
                st.metric("Status", stage.result, border=True)
                st.metric("Total released", f"RM {statement.summary_total_released:.2f}", border=True)
        if stage.result == READY_TO_COMMIT and stage.duplicate_status is None:
            st.success("Passed — the existing statement validation is ready for this session-only test.", icon=":material/check_circle:")
        elif stage.validation_issues or stage.rejection_reasons:
            reasons = [*stage.rejection_reasons, *(issue.message for issue in stage.validation_issues)]
            st.error("Blocking Failure — " + " ".join(reasons[:3]), icon=":material/error:")
        else:
            reasons = [*stage.review_reasons, *( [stage.duplicate_status] if stage.duplicate_status else [])]
            st.warning("Warning — " + " ".join(reasons[:3]), icon=":material/warning:")


def _render_summary(summary: Any) -> None:
    st.subheader("Session reporting merge summary")
    first_row = st.container(horizontal=True, gap="small")
    with first_row:
        st.metric("Total Shopee Orders", summary.total_shopee_orders, border=True)
        st.metric("Statement Matched", summary.statement_matched, border=True)
        st.metric("No Settlement Evidence", summary.no_settlement_evidence, border=True)
        st.metric("Pending → Released", summary.pending_to_released, border=True)
    second_row = st.container(horizontal=True, gap="small")
    with second_row:
        st.metric("Already Released → Released", summary.already_released_to_released, border=True)
        st.metric("Different Amount", summary.different_amount, border=True)
        st.metric("Unmatched Statement Orders", summary.unmatched_statement_orders, border=True)
    st.caption("Different Amount is an amount-only comparison at the existing RM0.02 tolerance. It is not an underpayment decision.")


def _render_projection(rows: tuple[Any, ...], statement_order_rows: tuple[Any, ...]) -> None:
    st.subheader("Invoice source facts and derived settlement reporting")
    st.caption("Before / after comparison. Invoice Payment Signal remains the original invoice `payment_status`; all settlement fields are derived for this temporary display.")
    dataframe = pd.DataFrame(
        [
            {
                "Order ID": row.order_id,
                "Order Created Date": row.order_created_date,
                "Income Type": row.income_type,
                "Final / Estimated Order Income": row.order_income,
                "Invoice Payment Signal": row.invoice_payment_signal,
                "Statement Match": "Matched" if row.statement_match else "No Statement Match",
                "Effective Payment Status": row.effective_payment_status,
                "Payment Evidence Source": row.payment_evidence_source,
                "Settlement Status": row.settlement_status,
                "Payout Completed Date": row.payout_completed_date,
                "Released Amount": row.released_amount,
                "Difference": row.difference,
            }
            for row in rows
        ]
    )
    st.dataframe(
        dataframe,
        column_config={
            "Final / Estimated Order Income": st.column_config.NumberColumn(format="RM %.2f"),
            "Released Amount": st.column_config.NumberColumn(format="RM %.2f"),
            "Difference": st.column_config.NumberColumn(format="RM %.2f"),
        },
        hide_index=True,
        key="settlement_test_lab_projection",
    )
    known_order_ids = {row.order_id for row in rows}
    unmatched_ids = []
    for statement_row in statement_order_rows:
        if statement_row.order_id not in known_order_ids and statement_row.order_id not in unmatched_ids:
            unmatched_ids.append(statement_row.order_id)
    if unmatched_ids:
        st.caption("Representative unmatched statement orders: " + ", ".join(unmatched_ids[:5]))
