"""Single-page import wizard presentation.

This module deliberately orchestrates existing import/staging services only. It
does not own parsing, validation, reconciliation, duplicate, export, or
persistence rules.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import io
import re
from typing import Any, Callable, Mapping, MutableMapping

import streamlit as st

from ..utils.monetary import format_currency

from .data_import_components import (
    render_authoritative_status,
    render_summary_items,
    render_workflow_stepper,
)
from .cross_platform_product_summary import (
    clear_cross_platform_reporting_snapshot_cache,
)

from ..services.batch_service import create_batch_id
from ..services.market_context import (
    ACTIVE_IMPORT_MARKET_KEY,
    MarketContext,
    MarketConfigurationUnavailable,
    MarketKey,
    MarketStateIsolationError,
    SHOPEE_MY,
    SHOPEE_SG,
    TIKTOK,
    bind_active_import_market,
    has_active_import_batch,
)
from ..services.import_result_adapters import (
    adapt_platform_orders_import_result,
    adapt_shopee_weekly_statement_import_result,
)
from ..services.import_result_contract import ImportResult, ReconciliationException, RecoveryAction, ValidationIssue
from ..services.exception_presentation import (
    ExceptionPresentationItem,
    ExceptionWorkQueue,
    build_exception_work_queue,
    build_historical_exception_work_queue,
    missing_invoice_order_ids,
)
from ..services.validation_recovery import (
    REMOVE_INVOICE_STAGING,
    REMOVE_SOURCE,
    REMOVE_STAGED_SOURCE,
    execute_current_batch_bulk_recovery,
    execute_current_batch_recovery,
    plan_already_imported_source_removal,
    plan_current_invoice_staging_exit,
    plan_duplicate_source_removal,
    recovery_actions_for_source,
)
from ..services.historical_invoice_intake import (
    IntakeStatus, build_current_batch_staging, classify_staging, import_new_staging,
)
from ..services.application_commit_lock import ApplicationCommitInProgress
from ..services.uat2_data_settings import configured_uat2_data_settings
from ..services.product_master_source import (
    ProductMasterSourceError,
    clear_product_master_source_cache,
    load_configured_product_price_master,
)
from ..services.invoice_product_master_revalidation import (
    has_product_master_dependent_blocker,
    revalidate_current_invoice_batch,
)
from ..repositories.google_sheets_historical_invoice_repository import HistoricalInvoiceStorageError
from ..repositories.historical_invoice_repository import HistoricalInvoiceBulkImportError
from ..services.workflow_navigation import (
    begin_workflow_activity,
    end_workflow_activity,
    request_navigation,
)
from ..services.data_import_state import (
    INVOICE_UPLOAD_ATTEMPT_KEY as _INVOICE_UPLOAD_ATTEMPT_KEY,
    INVOICE_UPLOAD_UNRESOLVED as _UNRESOLVED_INVOICE_UPLOAD_ATTEMPTS,
    begin_next_full_order_upload,
    clear_statement_upload_attempt,
    invoice_upload_downstream_eligibility,
    mark_statement_review_stale_after_invoice_commit,
    replace_statement_review_after_refresh,
    reset_invoice_upload_attempt,
    statement_stage_review_consistency,
)
from ..services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
)
from ..services.shopee_statement_import import (
    StatementImportReview,
    check_statement_review_currency,
    commit_statement_review,
    refresh_statement_review,
    review_statement_upload,
    statement_review_requires_proof_rule_refresh,
)
from ..services.shopee_statement_persistence import (
    StatementCommitBlocked,
    StatementWriteIntegrityError,
)
from ..services.shopee_full_order_import import (
    ShopeeFullOrderImportReview,
    ShopeeFullOrderMarketMismatch,
    commit_shopee_full_order_review,
    refresh_shopee_full_order_review,
    inspect_shopee_full_order_upload,
    review_shopee_full_order_upload,
)
from ..services.shopee_monthly_statement_service import (
    StagedShopeeMonthlyStatement,
)
from ..services.shopee_monthly_statement_import import (
    MonthlyStatementReview,
    commit_monthly_statement_review,
    refresh_monthly_statement_review,
    review_monthly_statement_upload,
)
from ..domain.statement_reconciliation_v2 import IdentityScope, SettlementBasis
from ..services.manual_review_resolution import (
    ADJUSTMENT_CORRECTION,
    MISSING_INCOME,
    FINAL_AMOUNT,
    FINANCIAL_CORRECTION,
    PRODUCT_COUNT_MISMATCH,
    PRODUCT_IDENTITY_CORRECTION,
    SKU_RESOLUTION,
    PROMOTION_SUBTOTAL,
    add_draft_product,
    apply_product_draft,
    apply_resolution,
    clear_correction_draft,
    draft_financial_enrichment,
    draft_products,
    draft_promotion_subtotals,
    draft_summary,
    edit_draft_product,
    financial_enrichment_fields,
    promotion_group_options,
    promotion_subtotal_groups,
    remove_draft_product,
    review_presentation_key,
    resolution_capabilities,
    resolution_plan,
    set_financial_enrichment,
    set_draft_promotion_subtotal,
    surface_product_identity_reviews,
    synchronize_correction_drafts,
)
from ..review_reason_codes import (
    POST_ORDER_ADJUSTMENT_AMOUNT_MISSING,
    POST_ORDER_ADJUSTMENT_EVIDENCE_CONFLICT,
    POST_ORDER_ADJUSTMENT_SOURCE_MISSING,
)



DATA_IMPORT_PAGE = "Data Import"
PLATFORM_ORDERS = "Platform Orders"
SHOPEE_WEEKLY_STATEMENT = "Shopee Weekly Statement"
SHOPEE_MONTHLY_STATEMENT = "Shopee Monthly Statement"
SHOPEE_FULL_ORDER_LIST = "Shopee Full Order List"


@dataclass(frozen=True)
class DataImportPlatform:
    key: str
    display_name: str
    subtitle: str
    expected_platform: str | None
    supported_source_types: frozenset[str]
    market_context: MarketContext | None = None


_ALL_SOURCE_TYPES = frozenset(
    {
        PLATFORM_ORDERS,
        SHOPEE_FULL_ORDER_LIST,
        SHOPEE_WEEKLY_STATEMENT,
        SHOPEE_MONTHLY_STATEMENT,
    }
)
DATA_IMPORT_PLATFORMS = (
    DataImportPlatform(
        key="shopee_my",
        display_name="Shopee MY",
        subtitle="Import Malaysian marketplace invoices and statements.",
        expected_platform="Shopee",
        supported_source_types=_ALL_SOURCE_TYPES,
        market_context=SHOPEE_MY,
    ),
    DataImportPlatform(
        key="shopee_sg",
        display_name="Shopee SG",
        subtitle="Singapore marketplace import workspace.",
        expected_platform="Shopee",
        supported_source_types=frozenset({
            PLATFORM_ORDERS, SHOPEE_FULL_ORDER_LIST, SHOPEE_WEEKLY_STATEMENT,
        }),
        market_context=SHOPEE_SG,
    ),
    DataImportPlatform(
        key="lazada",
        display_name="Lazada",
        subtitle="Import Lazada marketplace invoices.",
        expected_platform="Lazada",
        supported_source_types=frozenset({PLATFORM_ORDERS}),
    ),
    DataImportPlatform(
        key="tiktok",
        display_name="TikTok",
        subtitle="TikTok marketplace import workspace.",
        expected_platform=None,
        supported_source_types=frozenset(),
        market_context=TIKTOK,
    ),
)
_DATA_IMPORT_PLATFORM_BY_KEY = {platform.key: platform for platform in DATA_IMPORT_PLATFORMS}
ACTIVE_IMPORT_PLATFORM_KEY = "data_import.active_platform"
SHOW_PLATFORM_SELECTOR_KEY = "data_import.show_platform_selector"
PENDING_PLATFORM_SWITCH_KEY = "data_import.pending_platform_switch"
POST_DISCARD_PLATFORM_SWITCH_KEY = "data_import.platform_after_discard"
SHOPEE_SG_PRODUCT_RESOLUTION_EVALUATED_KEY = (
    "data_import.shopee_sg.product_resolution_evaluated"
)

WIZARD_STEPS = (
    "Select Source",
    "Upload",
    "Validate",
    "Reconcile",
    "Review & Commit",
)
MONTHLY_WIZARD_STEPS = WIZARD_STEPS
_WORKFLOW_KEYS = (
    ACTIVE_IMPORT_PLATFORM_KEY,
    SHOW_PLATFORM_SELECTOR_KEY,
    PENDING_PLATFORM_SWITCH_KEY,
    SHOPEE_SG_PRODUCT_RESOLUTION_EVALUATED_KEY,
    "data_import_step",
    "import_source_type",
    "invoice_upload_attempt",
    "weekly_statement_stage",
    "weekly_statement_review",
    "weekly_statement_review_stale_reason",
    "weekly_statement_upload_selected",
    "weekly_statement_commit_completed",
    "invoice_commit_completed",
    "invoice_commit_completed_count",
    "weekly_statement_uploader_version",
    "weekly_statement_selected_source",
    "monthly_statement_review",
    "monthly_statement_commit_result",
    "monthly_statement_uploader_version",
    "full_order_review",
    "full_order_market_mismatch",
    "full_order_sync_result",
    "full_order_uploader_version",
    "uat2_historical_commit_entries",
    "uat2_historical_commit_refresh_required",
    "uat2_historical_commit_signature",
    "historical_validation_blocker",
    "historical_partial_import_failure",
    "product_master_revalidation_notice",
    "product_master_revalidation_error",
    "manual_review_correction_drafts",
    "pending_validation_recovery_action",
    "pending_validation_bulk_recovery",
    "pending_validation_recovery_context",
    "validation_recovery_detail",
    "validation_recovery_notice",
    "weekly_statement_issue_order_id",
    "weekly_statement_issue_order_click",
    "invoice_exception_queue_click",
    "invoice_exception_queue_selected",
    "statement_exception_queue_click",
    "statement_exception_queue_selected",
    "statement_exception_queue_filter",
    "historical_exception_queue_click",
    "historical_exception_queue_selected",
    "historical_commit_exception_queue_click",
    "historical_commit_exception_queue_selected",
    "statement_commit_exception_queue_click",
    "statement_commit_exception_queue_selected",
)

_INVOICE_UPLOAD_PRISTINE = "pristine"
_INVOICE_UPLOAD_SELECTED = "selected"
_INVOICE_UPLOAD_NEEDS_ATTENTION = "needs_attention"
_INVOICE_UPLOAD_RESOLVED = "resolved"
_MANUAL_REVIEW_ONLINE = "online_resolution"
_MANUAL_REVIEW_REUPLOAD = "requires_reupload"
_MANUAL_REVIEW_ACTIVE_SECTION = "manual_review_active_section"
_MANUAL_REVIEW_ACTIVE_KEY = "manual_review_active_key"
_MANUAL_REVIEW_TAB_WIDGET = "manual_review_tab_widget"
_FINANCIAL_FORMULA_MISMATCH = re.compile(
    r"seller components total\s+(?P<calculated>-?[\d,]+(?:\.\d{1,2})?),\s*"
    r"but Order Income is\s+(?P<invoice>-?[\d,]+(?:\.\d{1,2})?)",
    flags=re.IGNORECASE,
)
def initialize_data_import_state() -> None:
    """Initialize presentation-only state once per Streamlit session."""
    st.session_state.setdefault("data_import_step", 1)
    st.session_state.setdefault("import_source_type", None)
    st.session_state.setdefault("weekly_statement_uploader_version", 0)
    st.session_state.setdefault("monthly_statement_uploader_version", 0)
    st.session_state.setdefault("full_order_uploader_version", 0)
    st.session_state.setdefault(
        SHOW_PLATFORM_SELECTOR_KEY,
        not bool(
            st.session_state.get(ACTIVE_IMPORT_PLATFORM_KEY)
            or st.session_state.get("batch_id")
        ),
    )


def reset_data_import_state() -> None:
    """Remove UI-only workflow state when the active batch is cleared."""
    for key in _WORKFLOW_KEYS:
        st.session_state.pop(key, None)
    st.session_state.pop(ACTIVE_IMPORT_MARKET_KEY, None)
    for key in tuple(st.session_state):
        if isinstance(key, str) and (
            key.startswith("weekly_statement_uploader_")
            or key.startswith("monthly_statement_uploader_")
            or key.startswith("full_order_uploader_")
        ):
            st.session_state.pop(key, None)


def mark_invoice_upload_attempt(state: MutableMapping[str, Any], status: str) -> None:
    """Record the lifecycle fact for the currently selected Invoice upload."""

    if status not in {*_UNRESOLVED_INVOICE_UPLOAD_ATTEMPTS, "resolved"}:
        raise ValueError(f"Unsupported Invoice upload attempt state: {status}")
    state[_INVOICE_UPLOAD_ATTEMPT_KEY] = status


def clear_invoice_upload_attempt(state: MutableMapping[str, Any]) -> None:
    """Clear only the current selected-upload attempt after the uploader is reset."""

    if _INVOICE_UPLOAD_ATTEMPT_KEY in state:
        del state[_INVOICE_UPLOAD_ATTEMPT_KEY]


def invoice_upload_is_resolved(state: MutableMapping[str, Any]) -> bool:
    """Return whether the current Invoice Upload step is complete enough to advance.

    Existing staged rows are deliberately not evidence of completion: a newer
    selected or interrupted upload must be resolved first.
    """

    return invoice_upload_presentation_state(state) == _INVOICE_UPLOAD_RESOLVED


def invoice_upload_presentation_state(state: MutableMapping[str, Any]) -> str:
    """Classify the current technical Upload lifecycle for presentation only.

    This derives from the authoritative attempt and activity facts; it does not
    introduce a second workflow state. A visible ``processing`` attempt is no
    longer actively executing the synchronous uploader, so it needs recovery.
    """

    attempt = state.get(_INVOICE_UPLOAD_ATTEMPT_KEY)
    if state.get("workflow_activity") == "Processing":
        return _INVOICE_UPLOAD_NEEDS_ATTENTION
    if attempt == "selected":
        return _INVOICE_UPLOAD_SELECTED
    if attempt in _UNRESOLVED_INVOICE_UPLOAD_ATTEMPTS:
        return _INVOICE_UPLOAD_NEEDS_ATTENTION
    if attempt == "resolved" or (
        attempt is None and bool(state.get("upload_result_summary"))
    ):
        return _INVOICE_UPLOAD_RESOLVED
    if attempt is None:
        return _INVOICE_UPLOAD_PRISTINE
    return _INVOICE_UPLOAD_NEEDS_ATTENTION


def render_data_import(
    *,
    render_platform_orders_upload: Callable[[DataImportPlatform], Any],
    render_platform_orders_outcomes: Callable[[], Any],
    render_platform_orders_summary: Callable[[], Any],
    render_platform_orders_validation_data: Callable[[], Any],
    discard_current_batch: Callable[[], None],
) -> None:
    """Render the sequential import workspace over the existing services."""
    initialize_data_import_state()
    _adopt_legacy_platform_batch()
    _complete_platform_switch_after_discard()
    platform = active_data_import_platform(st.session_state)
    if st.session_state.get(SHOW_PLATFORM_SELECTOR_KEY, platform is None) or platform is None:
        _render_platform_selection(discard_current_batch)
        return
    step = _current_step()
    if st.button("Back", icon=":material/arrow_back:", key="data_import_platform_back"):
        st.session_state[SHOW_PLATFORM_SELECTOR_KEY] = True
        st.rerun()
    st.title(f"{platform.display_name} · Data Import")
    st.caption("Use the active-batch workflow to stage, validate, reconcile, and review source data.")
    _render_wizard_progress(step)
    source_type = st.session_state.get("import_source_type")
    if source_type and not platform_supports_source(platform, source_type):
        _render_unavailable_capability(platform, source_type)
        return
    if (
        step >= 3
        and st.session_state.get("import_source_type") == PLATFORM_ORDERS
        and not invoice_upload_downstream_eligibility(st.session_state).eligible
    ):
        _render_blocked_invoice_destination(step)
        return
    if (
        step >= 3
        and st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT
    ):
        statement_consistency = statement_stage_review_consistency(st.session_state)
        can_render_stage_recovery = (
            step == 3
            and statement_consistency.state == "stage_only"
            and _weekly_stage() is not None
        )
        if not statement_consistency.coherent and not can_render_stage_recovery:
            _render_inconsistent_statement_destination(step)
            return
    if step == 1:
        _render_source_selection(discard_current_batch)
    elif step == 2:
        _render_upload_step(render_platform_orders_upload, platform)
    elif step == 3:
        _render_validation_step(
            render_platform_orders_outcomes,
            render_platform_orders_summary,
            render_platform_orders_validation_data,
        )
    elif step == 4:
        if st.session_state.get("import_source_type") == SHOPEE_MONTHLY_STATEMENT:
            _set_step(5)
            st.rerun()
            return
        _render_reconciliation_step()
    else:
        _render_review_and_commit_step()


def _adopt_legacy_platform_batch() -> None:
    """Present a pre-wizard batch as Platform Orders without changing it."""
    if has_active_import_batch(st.session_state) and not st.session_state.get("import_source_type"):
        st.session_state.import_source_type = PLATFORM_ORDERS
        st.session_state.data_import_step = max(int(st.session_state.get("data_import_step", 1)), 3)
    if has_active_import_batch(st.session_state):
        market_platform = _platform_for_active_market(st.session_state)
        if market_platform is not None:
            # The market binding is the existing batch owner.  Repair only the
            # UI platform key to that owner; never bind a newly selected target
            # or override an already-rendered Back-to-chooser request.
            st.session_state[ACTIVE_IMPORT_PLATFORM_KEY] = market_platform.key
        elif (
            st.session_state.get("import_source_type")
            and active_data_import_platform(st.session_state) is None
        ):
            st.session_state[ACTIVE_IMPORT_PLATFORM_KEY] = SHOPEE_MY.key.value
            st.session_state[SHOW_PLATFORM_SELECTOR_KEY] = False
    _normalize_step_for_source(st.session_state)


def _complete_platform_switch_after_discard() -> None:
    """Enter the requested platform only after the shared discard lifecycle cleared state."""
    if has_active_import_batch(st.session_state):
        return
    pending_key = st.session_state.pop(POST_DISCARD_PLATFORM_SWITCH_KEY, None)
    pending = _DATA_IMPORT_PLATFORM_BY_KEY.get(str(pending_key))
    if pending is not None:
        bind_active_import_platform(st.session_state, pending)
        st.session_state[SHOW_PLATFORM_SELECTOR_KEY] = False


def active_data_import_platform(state: Mapping[str, Any]) -> DataImportPlatform | None:
    value = state.get(ACTIVE_IMPORT_PLATFORM_KEY)
    if value in (None, ""):
        return None
    return _DATA_IMPORT_PLATFORM_BY_KEY.get(str(value))


def _active_invoice_market_context() -> MarketContext:
    platform = active_data_import_platform(st.session_state)
    return platform.market_context if platform and platform.market_context else SHOPEE_MY


def _required_persistence_market_context() -> MarketContext:
    """Return the UI-bound market or block a market-capable persistence path."""

    platform = active_data_import_platform(st.session_state)
    if platform is None or platform.market_context is None:
        raise MarketConfigurationUnavailable(
            "MISSING_MARKET_CONTEXT: choose a market before accessing persistence."
        )
    return platform.market_context


def _active_invoice_currency() -> str:
    return _active_invoice_market_context().currency_code


def _platform_for_active_market(state: Mapping[str, Any]) -> DataImportPlatform | None:
    """Return the platform owned by a real active batch, if any."""
    if not has_active_import_batch(state):
        return None
    active_market = state.get(ACTIVE_IMPORT_MARKET_KEY)
    if active_market in (None, ""):
        return None
    return next(
        (
            platform
            for platform in DATA_IMPORT_PLATFORMS
            if platform.market_context is not None
            and platform.market_context.key.value == str(active_market)
        ),
        None,
    )


def bind_active_import_platform(
    state: MutableMapping[str, Any], platform: DataImportPlatform
) -> DataImportPlatform:
    current = active_data_import_platform(state)
    if current is not None and current.key != platform.key and has_active_import_batch(state):
        raise MarketStateIsolationError(
            "The active import batch belongs to another platform; finish or discard it before switching."
        )
    if platform.market_context is not None:
        bind_active_import_market(state, platform.market_context)
    else:
        state.pop(ACTIVE_IMPORT_MARKET_KEY, None)
    # Bind the market first so a rejected real-batch switch cannot leave the UI
    # platform key pointing at a target the batch never entered.
    state[ACTIVE_IMPORT_PLATFORM_KEY] = platform.key
    return platform


def platform_supports_source(platform: DataImportPlatform, source_type: str) -> bool:
    return source_type in platform.supported_source_types


def _render_platform_selection(discard_current_batch: Callable[[], None]) -> None:
    st.title("Choose a platform")
    st.caption("Select the source workspace for this import.")
    pending_key = st.session_state.get(PENDING_PLATFORM_SWITCH_KEY)
    # A market binding owns the chooser only while a real batch exists.  A
    # leftover binding alone is stale selection state, not an active batch.
    current = _platform_for_active_market(st.session_state) or active_data_import_platform(
        st.session_state
    )
    if pending_key:
        pending = _DATA_IMPORT_PLATFORM_BY_KEY.get(str(pending_key))
        if pending is not None and current is not None:
            with st.container(border=True):
                st.subheader("Current import is still active")
                st.write(
                    f"Finish or discard the current **{current.display_name}** import before entering "
                    f"**{pending.display_name}**."
                )
                with st.container(horizontal=True):
                    if st.button(
                        f"Continue {current.display_name} import",
                        type="primary",
                        icon=":material/play_arrow:",
                    ):
                        st.session_state[SHOW_PLATFORM_SELECTOR_KEY] = False
                        st.session_state.pop(PENDING_PLATFORM_SWITCH_KEY, None)
                        st.rerun()
                    if st.button(
                        f"Discard current import and switch to {pending.display_name}",
                        icon=":material/restart_alt:",
                    ):
                        st.session_state[POST_DISCARD_PLATFORM_SWITCH_KEY] = pending.key
                        discard_current_batch()
                        st.session_state.pop(PENDING_PLATFORM_SWITCH_KEY, None)
                        st.rerun()
            return
        st.session_state.pop(PENDING_PLATFORM_SWITCH_KEY, None)

    for row in (DATA_IMPORT_PLATFORMS[:2], DATA_IMPORT_PLATFORMS[2:]):
        columns = st.columns(2)
        for column, platform in zip(columns, row):
            with column:
                with st.container(border=True):
                    st.subheader(platform.display_name)
                    st.caption(platform.subtitle)
                    if st.button(
                        f"Enter {platform.display_name}",
                        key=f"data_import_enter_{platform.key}",
                        icon=":material/arrow_forward:",
                        width="stretch",
                    ):
                        if (
                            current is not None
                            and current.key != platform.key
                            and has_active_import_batch(st.session_state)
                        ):
                            st.session_state[PENDING_PLATFORM_SWITCH_KEY] = platform.key
                        else:
                            try:
                                bind_active_import_platform(st.session_state, platform)
                            except MarketStateIsolationError:
                                # The normal live-batch route above renders the
                                # safe-discard panel.  Keep an unexpected
                                # domain conflict at this UI boundary friendly.
                                owner = _platform_for_active_market(st.session_state) or current
                                if owner is not None:
                                    st.warning(
                                        f"An import batch is still active for {owner.display_name}. "
                                        f"Finish or discard it before switching to {platform.display_name}."
                                    )
                                else:
                                    st.warning(
                                        "An import batch is still active. Finish or discard it before switching platforms."
                                    )
                                return
                            st.session_state[SHOW_PLATFORM_SELECTOR_KEY] = False
                        st.rerun()


def _workflow_steps_for_source(source_type: Any) -> tuple[str, ...]:
    return WIZARD_STEPS


def _max_internal_step_for_source(source_type: Any) -> int:
    """Monthly Statement retains its established internal Review step five."""
    return len(WIZARD_STEPS)


def _normalize_step_for_source(state: MutableMapping[str, Any]) -> int:
    """Clamp legacy wizard state without changing staged source data."""
    try:
        requested_step = int(state.get("data_import_step", 1))
    except (TypeError, ValueError):
        requested_step = 1
    step = min(
        max(requested_step, 1),
        _max_internal_step_for_source(state.get("import_source_type")),
    )
    if (
        state.get("import_source_type") == SHOPEE_FULL_ORDER_LIST
        and step >= 3
        and not isinstance(state.get("full_order_review"), ShopeeFullOrderImportReview)
    ):
        # A presentation step alone is never proof that a Full Order upload was
        # validated.  Return to Upload rather than showing a stale completion.
        step = 2
    state["data_import_step"] = step
    return step


def _current_step() -> int:
    return _normalize_step_for_source(st.session_state)


def _set_step(step: int) -> None:
    st.session_state.data_import_step = min(
        max(step, 1),
        _max_internal_step_for_source(st.session_state.get("import_source_type")),
    )


def _render_wizard_progress(current_step: int) -> None:
    render_workflow_stepper(
        _workflow_steps_for_source(st.session_state.get("import_source_type")),
        current_step,
    )


def _render_source_selection(discard_current_batch: Callable[[], None]) -> None:
    platform = active_data_import_platform(st.session_state)
    if platform is None:
        st.session_state[SHOW_PLATFORM_SELECTOR_KEY] = True
        st.rerun()
        return
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
            (
                "Invoice Import",
                "Full Order Import",
                "Statement",
                "Monthly Statement",
            ),
            key="weekly_statement_selected_source",
            default="Invoice Import",
        )
        source_type = {
            "Invoice Import": PLATFORM_ORDERS,
            "Full Order Import": SHOPEE_FULL_ORDER_LIST,
            "Statement": SHOPEE_WEEKLY_STATEMENT,
            "Monthly Statement": SHOPEE_MONTHLY_STATEMENT,
        }[source_label]
        st.caption(
            f"PDF or ZIP order documents for {platform.display_name}."
            if source_type == PLATFORM_ORDERS
            else (
                "Native Shopee Full Order List export (.xlsx)."
                if source_type == SHOPEE_FULL_ORDER_LIST
            else (
                "Native Shopee Statement settlement export for its source period (.xlsx)."
                if source_type == SHOPEE_WEEKLY_STATEMENT
                else "Native Shopee full-calendar-month Statement export (.xlsx)."
            )
            )
        )
        available = platform_supports_source(platform, source_type)
        if not available:
            _render_unavailable_capability(platform, source_type)
        if st.button(
            "Continue to upload",
            type="primary",
            icon=":material/arrow_forward:",
            disabled=not available,
        ):
            st.session_state.import_source_type = source_type
            _set_step(2)
            st.rerun()


def _render_unavailable_capability(platform: DataImportPlatform, source_type: str) -> None:
    source_label = {
        PLATFORM_ORDERS: "Invoice Import",
        SHOPEE_FULL_ORDER_LIST: "Full Order Import",
        SHOPEE_WEEKLY_STATEMENT: "Statement",
        SHOPEE_MONTHLY_STATEMENT: "Monthly Statement",
    }[source_type]
    if platform.key == "shopee_sg":
        st.info(f"This import workflow is not available for {platform.display_name} yet.")
    else:
        st.info(f"{source_label} import is not available for {platform.display_name} yet.")


def _render_upload_step(
    render_platform_orders_upload: Callable[[DataImportPlatform], Any],
    platform: DataImportPlatform,
) -> None:
    _render_recovery_notice()
    if _has_pending_recovery():
        _render_recovery_confirmation()
    source_type = st.session_state.get("import_source_type")
    if source_type == PLATFORM_ORDERS:
        st.subheader("Upload platform order files")
        st.caption("Upload PDF or ZIP order documents for the active batch.")
        render_platform_orders_upload(platform)
        presentation_state = invoice_upload_presentation_state(st.session_state)
        if presentation_state == _INVOICE_UPLOAD_RESOLVED:
            _render_next_step(
                "Continue to validate",
                3,
                back_step=1,
                include_invoice_exit=True,
            )
        elif presentation_state == _INVOICE_UPLOAD_NEEDS_ATTENTION:
            _render_unresolved_invoice_upload_actions(back_step=1)
        else:
            _render_invoice_upload_back_actions(back_step=1)
        return
    if source_type == SHOPEE_WEEKLY_STATEMENT:
        _render_weekly_statement_upload()
        return
    if source_type == SHOPEE_FULL_ORDER_LIST:
        _render_full_order_upload(platform)
        return
    if source_type == SHOPEE_MONTHLY_STATEMENT:
        _render_monthly_statement_upload()
        return
    _set_step(1)
    st.rerun()


def _render_full_order_upload(platform: DataImportPlatform) -> None:
    st.subheader("Upload Shopee Full Order List")
    st.caption("Upload one native Shopee Full Order List workbook (.xlsx).")
    refresh_notice = st.session_state.pop("full_order_pm_refresh_notice", None)
    if refresh_notice:
        st.success(refresh_notice, icon=":material/check_circle:")
    review = _full_order_review()
    mismatch = _full_order_market_mismatch()
    if mismatch is not None:
        _render_full_order_market_mismatch(platform, mismatch)
        return
    if review is not None:
        st.info(
            f"Validated Full Order source: {review.parsed.source_filename}.",
            icon=":material/info:",
        )
        with st.container(horizontal=True):
            if st.button(
                "Continue to validate",
                type="primary",
                icon=":material/arrow_forward:",
                key="full_order_resume_validation",
            ):
                _set_step(3)
                st.rerun()
            if st.button(
                "Refresh PM & Revalidate",
                icon=":material/refresh:",
                key="full_order_refresh_pm",
            ):
                begin_workflow_activity(st.session_state, "Revalidating")
                try:
                    context = review.parsed.market
                    clear_product_master_source_cache()
                    settings = configured_uat2_data_settings(
                        context, require_explicit_context=True
                    )
                    master, source_label = load_configured_product_price_master(context)
                    refreshed = refresh_shopee_full_order_review(
                        review,
                        product_master=master,
                        repository=settings.create_full_order_current_repository(),
                    )
                except (
                    ValueError,
                    HistoricalInvoiceStorageError,
                    MarketConfigurationUnavailable,
                    ProductMasterSourceError,
                ) as error:
                    st.error(
                        f"Full Order Product Master refresh failed: {error}. "
                        "The previous validation was kept.",
                        icon=":material/error:",
                    )
                else:
                    st.session_state.full_order_review = refreshed
                    st.session_state.pop("full_order_sync_result", None)
                    st.session_state.full_order_pm_refresh_notice = (
                        f"Revalidated Full Order source from {source_label}."
                    )
                    st.rerun()
                finally:
                    end_workflow_activity(st.session_state)
            if st.button(
                "Clear staged source",
                icon=":material/close:",
                key="full_order_clear_staged",
            ):
                begin_next_full_order_upload(st.session_state)
                st.rerun()
        return
    version = int(st.session_state.get("full_order_uploader_version", 0))
    uploaded_file = st.file_uploader(
        "Shopee Full Order List (.xlsx)",
        type=["xlsx"],
        key=f"full_order_uploader_{version}",
    )
    with st.container(horizontal=True):
        validate_clicked = st.button(
            "Validate Full Order",
            type="primary" if uploaded_file is not None else "secondary",
            icon=":material/fact_check:",
            disabled=uploaded_file is None,
            key="full_order_validate",
        )
        clear_clicked = st.button(
            "Clear selected file",
            icon=":material/close:",
            disabled=uploaded_file is None,
            key="full_order_clear_selected",
        )
    if clear_clicked:
        st.session_state.full_order_uploader_version = version + 1
        st.rerun()
    if validate_clicked and uploaded_file is not None:
        if platform.market_context is None:
            st.error("Shopee market context is unavailable.")
            return
        begin_workflow_activity(st.session_state, "Validating")
        try:
            review = _review_full_order_source(
                uploaded_file, uploaded_file.name, platform.market_context
            )
            st.session_state.batch_id = st.session_state.get("batch_id") or create_batch_id()
            st.session_state.full_order_review = review
            st.session_state.pop("full_order_sync_result", None)
        except ShopeeFullOrderMarketMismatch as error:
            st.session_state["full_order_market_mismatch"] = error
        except (
            ValueError,
            HistoricalInvoiceStorageError,
            MarketConfigurationUnavailable,
            ProductMasterSourceError,
        ) as error:
            st.error(f"Full Order validation is unavailable: {error}")
        finally:
            end_workflow_activity(st.session_state)
        if _full_order_review() is not None:
            _set_step(3)
            st.rerun()
    if st.button("Back", icon=":material/arrow_back:", key="full_order_upload_back"):
        _set_step(1)
        st.rerun()


def _review_full_order_source(
    source: Any, source_filename: str, context: MarketContext
) -> ShopeeFullOrderImportReview:
    """Validate one source against only its explicitly selected market resources."""

    source_bytes, parsed = inspect_shopee_full_order_upload(
        source, source_filename=source_filename
    )
    if parsed.market != context:
        raise ShopeeFullOrderMarketMismatch(
            source_bytes=source_bytes, parsed=parsed, current_market=context
        )
    settings = configured_uat2_data_settings(context, require_explicit_context=True)
    master, _source_label = load_configured_product_price_master(context)
    return review_shopee_full_order_upload(
        source,
        source_filename=source_filename,
        market=context,
        product_master=master,
        repository=settings.create_full_order_current_repository(),
    )


def _full_order_market_mismatch() -> ShopeeFullOrderMarketMismatch | None:
    mismatch = st.session_state.get("full_order_market_mismatch")
    return mismatch if isinstance(mismatch, ShopeeFullOrderMarketMismatch) else None


def _render_full_order_market_mismatch(
    platform: DataImportPlatform, mismatch: ShopeeFullOrderMarketMismatch
) -> None:
    """Require an explicit market switch before the staged bytes can be reviewed."""

    detected = mismatch.parsed.market
    st.error(
        "MARKET_MISMATCH: current market "
        f"{platform.display_name}; detected source {detected.display_name}."
    )
    target = next(
        (
            candidate
            for candidate in DATA_IMPORT_PLATFORMS
            if candidate.market_context == detected
        ),
        None,
    )
    if target is None:
        st.error("Detected Full Order market is not available in Data Import.")
        return
    with st.container(horizontal=True):
        switch_clicked = st.button(
            f"Switch to {target.display_name} and continue",
            type="primary",
            key="full_order_market_mismatch_switch",
        )
        cancel_clicked = st.button(
            "Cancel", key="full_order_market_mismatch_cancel"
        )
    if cancel_clicked:
        st.session_state.pop("full_order_market_mismatch", None)
        st.session_state.full_order_uploader_version = int(
            st.session_state.get("full_order_uploader_version", 0)
        ) + 1
        st.rerun()
    if not switch_clicked:
        return
    begin_workflow_activity(st.session_state, "Validating")
    try:
        review = _review_full_order_source(
            mismatch.source_bytes,
            mismatch.parsed.source_filename,
            detected,
        )
        # There is no active batch before this point, so binding the source-owned
        # target cannot reinterpret an existing MY/SG batch.
        bind_active_import_platform(st.session_state, target)
        st.session_state.batch_id = create_batch_id()
        st.session_state.full_order_review = review
        st.session_state.pop("full_order_market_mismatch", None)
        st.session_state.pop("full_order_sync_result", None)
    except (
        ValueError,
        HistoricalInvoiceStorageError,
        MarketConfigurationUnavailable,
        ProductMasterSourceError,
    ) as error:
        st.error(f"Full Order validation is unavailable: {error}")
    finally:
        end_workflow_activity(st.session_state)
    if _full_order_review() is not None:
        _set_step(3)
        st.rerun()


def _render_weekly_statement_upload() -> None:
    st.subheader("Upload Shopee Statement")
    st.caption("Upload one native Shopee Statement workbook for its actual source period (.xlsx).")
    consistency = statement_stage_review_consistency(st.session_state)
    if not consistency.coherent:
        _render_inconsistent_statement_destination(2)
        return
    review = _weekly_review()
    stage = _weekly_stage()
    if stage is not None:
        st.info(
            f"Staged Statement: {stage.source_filename}. Return to validation to "
            "continue the existing review.",
            icon=":material/info:",
        )
        _render_statement_resume_actions(back_step=1)
        return
    version = int(st.session_state.get("weekly_statement_uploader_version", 0))
    uploaded_file = st.file_uploader(
        "Shopee Statement (.xlsx)",
        type=["xlsx"],
        key=f"weekly_statement_uploader_{version}",
    )
    if uploaded_file is not None:
        st.session_state.weekly_statement_upload_selected = True
    else:
        st.session_state.pop("weekly_statement_upload_selected", None)
    with st.container(horizontal=True):
        stage_clicked = st.button(
            "Check statement",
            type="primary" if uploaded_file is not None else "secondary",
            icon=":material/upload_file:",
            disabled=uploaded_file is None,
        )
        clear_clicked = st.button("Clear selected file", icon=":material/close:", disabled=uploaded_file is None)
    if clear_clicked:
        clear_statement_upload_attempt(st.session_state)
        st.rerun()
    if stage_clicked and uploaded_file is not None:
        st.session_state.batch_id = st.session_state.get("batch_id") or create_batch_id()
        begin_workflow_activity(st.session_state, "Validating")
        try:
            context = _required_persistence_market_context()
            settings = configured_uat2_data_settings(
                context, require_explicit_context=True
            )
            master = (
                None
                if context.statement_order_existence_gate
                else load_configured_product_price_master(context)[0]
            )
            review = review_statement_upload(
                uploaded_file,
                source_filename=uploaded_file.name,
                batch_id=st.session_state.batch_id,
                uploaded_by=str(st.session_state.get("authenticated_username") or "Admin"),
                writer=settings.create_statement_writer(),
                product_master=master,
                full_order_repository=settings.create_full_order_current_repository(),
                market_context=context,
            )
            st.session_state.weekly_statement_review = review
            st.session_state.weekly_statement_stage = review.stage
            st.session_state.pop("weekly_statement_review_stale_reason", None)
            st.session_state.pop("weekly_statement_upload_selected", None)
        except (
            HistoricalInvoiceStorageError,
            MarketConfigurationUnavailable,
            ProductMasterSourceError,
            StatementCommitBlocked,
        ) as error:
            st.error(f"Statement validation is unavailable: {error}")
        finally:
            end_workflow_activity(st.session_state)
        if _weekly_review() is not None:
            _set_step(3)
            st.rerun()
    if st.button("Back", icon=":material/arrow_back:", key="statement_upload_back"):
        if uploaded_file is not None:
            clear_statement_upload_attempt(st.session_state)
        _set_step(1)
        st.rerun()


def _render_monthly_statement_upload() -> None:
    st.subheader("Upload Shopee Monthly Statement")
    st.caption("Upload one native full-calendar-month Shopee Statement workbook (.xlsx).")
    review = _monthly_review()
    if review is not None:
        st.info(
            f"Staged Monthly Statement: {review.stage.source_filename}. Return to validation to continue.",
            icon=":material/info:",
        )
        with st.container(horizontal=True):
            if st.button(
                "Continue to validate",
                type="primary",
                icon=":material/arrow_forward:",
                key="monthly_statement_resume_validation",
            ):
                _set_step(3)
                st.rerun()
            if st.button(
                "Back", icon=":material/arrow_back:", key="monthly_statement_resume_back"
            ):
                _set_step(1)
                st.rerun()
        return
    version = int(st.session_state.get("monthly_statement_uploader_version", 0))
    uploaded_file = st.file_uploader(
        "Shopee Monthly Statement (.xlsx)",
        type=["xlsx"],
        key=f"monthly_statement_uploader_{version}",
    )
    with st.container(horizontal=True):
        check_clicked = st.button(
            "Check monthly statement",
            type="primary" if uploaded_file is not None else "secondary",
            icon=":material/upload_file:",
            disabled=uploaded_file is None,
            key="monthly_statement_check",
        )
        clear_clicked = st.button(
            "Clear selected file",
            icon=":material/close:",
            disabled=uploaded_file is None,
            key="monthly_statement_clear",
        )
    if clear_clicked:
        st.session_state.monthly_statement_uploader_version = version + 1
        st.rerun()
    if check_clicked and uploaded_file is not None:
        st.session_state.batch_id = st.session_state.get("batch_id") or create_batch_id()
        begin_workflow_activity(st.session_state, "Validating")
        try:
            writer = configured_uat2_data_settings().create_monthly_statement_writer()
            writer.ensure_schema()
            review = review_monthly_statement_upload(
                uploaded_file,
                source_filename=uploaded_file.name,
                batch_id=st.session_state.batch_id,
                uploaded_by=str(st.session_state.get("authenticated_username") or "Admin"),
                writer=writer,
            )
            st.session_state.monthly_statement_review = review
        except (HistoricalInvoiceStorageError, StatementCommitBlocked, StatementWriteIntegrityError) as error:
            st.error(f"Monthly Statement validation is unavailable: {error}")
        finally:
            end_workflow_activity(st.session_state)
        if _monthly_review() is not None:
            _set_step(3)
            st.rerun()
    if st.button("Back", icon=":material/arrow_back:", key="monthly_statement_upload_back"):
        _set_step(1)
        st.rerun()


def _render_validation_step(
    render_platform_orders_outcomes: Callable[[], Any],
    render_platform_orders_summary: Callable[[], Any],
    render_platform_orders_validation_data: Callable[[], Any],
) -> None:
    st.subheader("Validate")
    if st.session_state.get("import_source_type") == SHOPEE_MONTHLY_STATEMENT:
        _render_monthly_statement_validation()
        return
    if st.session_state.get("import_source_type") == SHOPEE_FULL_ORDER_LIST:
        _render_full_order_validation()
        return
    if st.session_state.get("import_source_type") == PLATFORM_ORDERS:
        presentation_state = invoice_upload_presentation_state(st.session_state)
        if presentation_state != _INVOICE_UPLOAD_RESOLVED:
            if presentation_state == _INVOICE_UPLOAD_NEEDS_ATTENTION:
                _render_unresolved_invoice_upload_actions(back_step=2)
            else:
                _render_invoice_upload_back_actions(back_step=2)
            return
    if _has_pending_recovery():
        _render_recovery_confirmation()
    if (
        st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT
        and _render_stale_statement_refresh("statement_validation_stale_refresh")
    ):
        return
    if _render_statement_already_imported():
        return
    result = _current_import_result()
    is_platform_orders = bool(
        result.source_specific_details.get("show_platform_order_outcomes")
    )
    if _render_statement_source_error(result):
        return
    if is_platform_orders:
        _render_platform_invoice_validation_status(result)
    else:
        _render_validation_status(result)

    if st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT:
        _render_statement_refresh_action(
            _weekly_review(),
            key="statement_validation_refresh",
        )
        _render_statement_next_step("Continue to reconcile", 4, back_step=2)
    else:
        _render_next_step(
            "Continue to reconcile",
            4,
            back_step=2,
            allowed=result.commit_readiness.ready,
            include_invoice_exit=True,
            disabled_reason=_commit_readiness_reason(result),
        )

    if is_platform_orders:
        _render_platform_invoice_non_manual_issues(result)
        _render_manual_review_resolution()
        st.subheader("Current batch summary")
        render_platform_orders_summary()
        render_platform_orders_validation_data()
        render_platform_orders_outcomes()
    else:
        if _statement_invoice_coverage(result) is not None:
            _render_statement_invoice_coverage_details(result)
            _render_source_summary(result)
            return
        _render_contract_validation(result)
        _render_source_summary(result)
        _render_statement_review_tables(collapsed=not result.commit_readiness.ready)


def _render_full_order_validation() -> None:
    review = _full_order_review()
    if review is None:
        st.warning("Upload and validate a Shopee Full Order List first.")
        _render_back_button(2, key="full_order_validation_missing_back")
        return
    preview = review.sync_preview
    issues = review.current_batch.issues
    ready = review.ready
    render_authoritative_status(
        title="Ready" if ready else "Needs Attention",
        message=(
            "Full Order source, Product Master identity, and current-store preview passed."
            if ready
            else "Resolve every Full Order Product Master or source issue before sync."
        ),
        state="ready" if ready else "blocked",
    )
    with st.container(horizontal=True):
        st.metric("Orders", len(review.parsed.order_ids), border=True)
        st.metric("New", preview.new_count if preview else 0, border=True)
        st.metric("Updated", preview.updated_count if preview else 0, border=True)
        st.metric("Unchanged", preview.unchanged_count if preview else 0, border=True)
        st.metric("Need Review", len({issue.order_id for issue in issues}), border=True)
    st.caption(
        "Only Order IDs present in this workbook are inserted or replaced. "
        "Current orders absent from the workbook remain untouched."
    )
    if review.current_batch.items:
        st.caption(
            "Resolve SKU is the Listing-maintained POS instruction. Source Seller SKU "
            "and resolved Seller SKU remain unchanged."
        )
        st.dataframe(
            [
                {
                    "Order ID": item.order_id,
                    "Item": item.item_index + 1,
                    "Source Seller SKU": item.source_seller_sku,
                    "Resolved Seller SKU": item.resolved_seller_sku,
                    "Resolve SKU": item.resolve_sku or "",
                    "POS mapping": item.resolve_sku_warning or (
                        "Listing value unavailable" if not item.resolve_sku else "Ready"
                    ),
                }
                for item in review.current_batch.items
            ],
            hide_index=True,
        )
    if review.parsed.dimension_fallback_used:
        st.caption("Malformed worksheet dimensions were recovered from populated cell bounds.")
    if issues:
        st.dataframe(
            [
                {
                    "Order ID": issue.order_id,
                    "Source row": issue.source_row_number,
                    "Status": issue.code,
                    "Reason": issue.message,
                }
                for issue in issues
            ],
            hide_index=True,
        )
    _render_next_step(
        "Continue to review sync",
        4,
        back_step=2,
        allowed=ready,
        disabled_reason=(
            None if ready else "All Full Order rows require deterministic Product Master resolution."
        ),
    )


def _render_monthly_statement_validation() -> None:
    review = _monthly_review()
    if review is None:
        st.warning("Upload and check a Shopee Monthly Statement first.")
        _render_back_button(2, key="monthly_validation_missing_back")
        return
    stage = review.stage
    statement = stage.statement
    if stage.source_error is not None:
        st.error(stage.source_error.technical_message, icon=":material/error:")
        _render_back_button(2, key="monthly_validation_source_error_back")
        return
    if statement is None:
        st.error("Monthly Statement source could not be read.", icon=":material/error:")
        _render_back_button(2, key="monthly_validation_unreadable_back")
        return
    _render_monthly_metrics(statement)
    if stage.already_imported:
        st.success("Monthly Statement already imported", icon=":material/check_circle:")
        st.caption("No additional data was written.")
        _render_monthly_upload_another_action("monthly_duplicate_upload_another")
        return
    if stage.duplicate_status == "POSSIBLE_REVISION":
        st.warning(stage.review_reasons[0], icon=":material/warning:")
        st.caption("The committed Monthly Statement was not overwritten. No data was written.")
        _render_monthly_upload_another_action("monthly_revision_upload_another")
        return
    if stage.validation_issues:
        st.error("Monthly Statement source validation failed.", icon=":material/error:")
        for issue in stage.validation_issues:
            st.write(f"- {issue.message}")
        _render_back_button(2, key="monthly_validation_failed_back")
        return
    render_authoritative_status(
        title="Ready to Commit",
        message="Monthly source and internal controls passed.",
        state="ready",
    )
    with st.container(horizontal=True):
        if st.button(
            "Continue to review",
            type="primary",
            icon=":material/arrow_forward:",
            key="monthly_validation_continue",
        ):
            _set_step(5)
            st.rerun()
        if st.button(
            "Back", icon=":material/arrow_back:", key="monthly_validation_back"
        ):
            _set_step(2)
            st.rerun()


def _render_monthly_metrics(statement: Any) -> None:
    adjustment_total = sum(
        (
            row.adjustment_amount or Decimal("0.00")
            for row in statement.adjustments
        ),
        Decimal("0.00"),
    )
    st.write(
        f"**Statement Period:** {statement.statement_period_from:%d/%m/%Y} – "
        f"{statement.statement_period_to:%d/%m/%Y}"
    )
    with st.container(horizontal=True):
        st.metric("Order rows", len(statement.order_rows))
        st.metric("SKU rows", len(statement.sku_rows))
        st.metric("Total released", format_currency(statement.summary_total_released))
        st.metric(
            "Adjustments",
            len(statement.adjustments),
            help=f"Source total: {format_currency(adjustment_total)}",
        )


def _render_monthly_upload_another_action(key: str) -> None:
    if st.button("Upload another Monthly Statement", icon=":material/upload_file:", key=key):
        _reset_monthly_statement_workflow()
        _set_step(2)
        st.rerun()


def _render_validation_status(result: ImportResult) -> None:
    """Present existing readiness truth before any detailed validation output."""

    if not result.session_state.applied_to_current_session:
        render_authoritative_status(
            title="No active batch",
            message=result.source_summary.empty_message or "Upload a source to begin.",
            state="empty",
        )
        return
    if result.commit_readiness.ready:
        accepted_orders = next(
            (
                item.value
                for item in result.source_summary.items
                if item.label in {"Accepted Orders", "Statement Orders"}
            ),
            None,
        )
        message = (
            f"{accepted_orders} accepted order{'s' if accepted_orders != 1 else ''} "
            "validated successfully."
            if accepted_orders is not None
            else "The current batch passed the existing readiness checks."
        )
        render_authoritative_status(title="Ready", message=message, state="ready")
        return

    if result.source_type == SHOPEE_WEEKLY_STATEMENT:
        if _render_statement_invoice_coverage_status(result):
            return
        affected_orders, issue_count = _statement_blocker_counts(result)
        message = (
            f"{affected_orders} affected order{'s' if affected_orders != 1 else ''}; "
            f"{issue_count} unresolved issue{'s' if issue_count != 1 else ''} "
            "must be resolved before continuing."
            if issue_count
            else _commit_readiness_reason(result)
        )
    else:
        review_count = len(result.source_specific_details.get("manual_review", ()))
        error_count = len(result.source_specific_details.get("processing_errors", ()))
        attention_count = review_count + error_count
        message = (
            f"{attention_count} current-batch item{'s' if attention_count != 1 else ''} "
            "require review. Resolve the blocking items before continuing."
            if attention_count
            else _commit_readiness_reason(result)
        )
    render_authoritative_status(title="Needs Attention", message=message, state="blocked")


def _platform_invoice_blocker_identity(
    *, platform: Any, order_id: Any, source: Any
) -> tuple[str, str]:
    normalized_platform = str(platform or "Shopee").strip().casefold()
    normalized_order = str(order_id or "").strip()
    if normalized_order:
        return normalized_platform, normalized_order.casefold()
    return "source", str(source or "").strip().casefold()


def _platform_invoice_primary_blockers(
    result: ImportResult,
    *,
    historical_entries: tuple[Any, ...] = (),
    historical_blocker: Any = None,
) -> tuple[dict[str, Any], ...]:
    """Project one primary Validate owner per current source identity.

    This is presentation-only: it does not alter Manual Review, repository, or
    historical classification semantics.
    """
    owned: dict[tuple[str, str], dict[str, Any]] = {}

    def register(
        *, owner: str, platform: Any, order_id: Any, source: Any, reason: Any
    ) -> None:
        identity = _platform_invoice_blocker_identity(
            platform=platform, order_id=order_id, source=source
        )
        if identity[1] and identity not in owned:
            owned[identity] = {
                "owner": owner,
                "platform": str(platform or "Shopee"),
                "order_id": str(order_id or "").strip() or None,
                "source": str(source or "").strip() or None,
                "reason": str(reason or "Requires review."),
            }

    for review in result.source_specific_details.get("manual_review", ()):
        if isinstance(review, Mapping):
            register(
                owner="manual_review",
                platform=review.get("platform"),
                order_id=review.get("order_id"),
                source=review.get("source_pdf"),
                reason=review.get("reason"),
            )
    for error in result.source_specific_details.get("processing_errors", ()):
        if isinstance(error, Mapping):
            register(
                owner="ingestion",
                platform=error.get("platform"),
                order_id=error.get("order_id"),
                source=error.get("source_pdf") or error.get("filename"),
                reason=error.get("reason") or error.get("error") or error.get("message"),
            )
    return tuple(owned.values())


def _render_platform_invoice_validation_status(
    result: ImportResult,
    *,
    historical_entries: tuple[Any, ...] = (),
    historical_blocker: Any = None,
) -> None:
    blockers = _platform_invoice_primary_blockers(
        result,
        historical_entries=historical_entries,
        historical_blocker=historical_blocker,
    )
    if blockers:
        count = len(blockers)
        render_authoritative_status(
            title="Needs Attention",
            message=(
                f"{count} unique source{'s' if count != 1 else ''} "
                f"{'requires' if count == 1 else 'require'} action "
                "before validation can complete."
            ),
            state="blocked",
        )
        return
    if result.commit_readiness.ready:
        render_authoritative_status(
            title="Ready",
            message="The current Platform Invoice batch passed validation.",
            state="ready",
        )
        return
    render_authoritative_status(
        title="Needs Attention",
        message=_commit_readiness_reason(result),
        state="blocked",
    )


def _render_platform_invoice_non_manual_issues(result: ImportResult) -> None:
    """Keep real ingestion/duplicate issues visible without duplicating Manual Review."""
    queue = build_exception_work_queue(result)
    manual_identities = {
        _platform_invoice_blocker_identity(
            platform=review.get("platform"),
            order_id=review.get("order_id"),
            source=review.get("source_pdf"),
        )
        for review in result.source_specific_details.get("manual_review", ())
        if isinstance(review, Mapping)
    }

    # A Manual Review / Online Resolution owns the primary action for its
    # source.  Keep unrelated ingestion and duplicate work visible, but do not
    # present a second generic queue card for that same source.
    def is_owned_by_manual_review(item: ExceptionPresentationItem) -> bool:
        return (
            _platform_invoice_blocker_identity(
                platform=item.platform,
                order_id=item.order_id,
                source=item.source,
            )
            in manual_identities
        )

    items = tuple(
        replace(
            item,
            issues=tuple(
                issue for issue in item.issues if issue.category != "manual_review"
            ),
        )
        for item in queue.items
        if any(issue.category != "manual_review" for issue in item.issues)
        and not is_owned_by_manual_review(item)
    )
    filtered = ExceptionWorkQueue(
        items=items,
        source_issue_count=sum(item.issue_count for item in items),
    )
    blockers, notes = _partition_exception_work_queue(filtered)
    _render_actionable_blockers_and_notes(
        blockers,
        notes,
        key_prefix="invoice_non_manual_exception_queue",
        secondary_heading="Skipped / Non-blocking information",
    )


def _render_statement_source_error(result: ImportResult) -> bool:
    """Render workbook-level failure without creating an order-level work item."""

    error = result.source_error
    if result.source_type != SHOPEE_WEEKLY_STATEMENT or error is None:
        return False
    render_authoritative_status(
        title=error.title,
        message=error.message,
        state="blocked",
    )
    st.write(f"**What to do:** {error.next_step}")
    with st.container(horizontal=True):
        if st.button(
            "Upload another Statement",
            type="primary",
            icon=":material/upload_file:",
            key="replace_invalid_statement",
        ):
            actions = recovery_actions_for_source(
                source=error.source_filename,
                action_type=REMOVE_STAGED_SOURCE,
                remove_label="Upload another Statement",
                include_details=False,
            )
            action = next((item for item in actions if item.allowed), None)
            if action is not None:
                st.session_state.pending_validation_recovery_action = action
                st.session_state.pending_validation_recovery_context = "remove_statement"
                st.rerun()
    with st.expander("Technical details (for audit)", expanded=False):
        st.json(dict(error.technical_details))
    return True


def _statement_invoice_coverage(result: ImportResult) -> Any | None:
    """Return active Full Order Current coverage (legacy function name only)."""
    coverage = result.source_specific_details.get("statement_evidence_coverage")
    if coverage is None or coverage.complete:
        return None
    return coverage


def _render_statement_invoice_coverage_status(result: ImportResult) -> bool:
    coverage = _statement_invoice_coverage(result)
    if coverage is None:
        return False
    render_authoritative_status(
        title="Full Order evidence required",
        message=(
            f"{len(coverage.additional_evidence)} order"
            f"{'s require' if len(coverage.additional_evidence) != 1 else ' requires'} "
            "Full Order evidence before reconciliation can continue."
        ),
        state="blocked",
    )
    with st.container(horizontal=True, gap="small"):
        st.metric("Statement Orders", len(coverage.statement_order_ids), border=True)
        st.metric("Previously Reviewed", len(coverage.already_verified_order_ids), border=True)
        st.metric(
            "Ready from Full Order",
            len(coverage.ready_from_full_order_order_ids),
            border=True,
        )
        st.metric(
            "Need Full Order Evidence", len(coverage.additional_evidence), border=True
        )
    return True


def _render_statement_invoice_coverage_details(result: ImportResult) -> None:
    coverage = _statement_invoice_coverage(result)
    if coverage is None:
        return
    rows = [
        {
            "Order ID": item.order_id,
            "Order Status": item.order_status or "Not available",
            "Reason": item.reason,
        }
        for item in coverage.additional_evidence
    ]
    st.subheader("Full Order evidence required")
    st.dataframe(
        rows,
        hide_index=True,
        height=min(420, 36 * (len(rows) + 1)),
        column_config={
            "Order ID": st.column_config.TextColumn("Order ID", pinned=True),
        },
    )


def _render_contract_validation(result: ImportResult) -> None:
    _render_recovery_notice()
    queue = build_exception_work_queue(result)
    blockers, notes = _partition_exception_work_queue(queue)
    _render_actionable_blockers_and_notes(
        blockers,
        notes,
        key_prefix=(
            "statement_exception_queue"
            if result.source_type == SHOPEE_WEEKLY_STATEMENT
            else "invoice_exception_queue"
        ),
        humanize_statement=result.source_type == SHOPEE_WEEKLY_STATEMENT,
    )


def _render_actionable_blockers_and_notes(
    blockers: ExceptionWorkQueue,
    notes: ExceptionWorkQueue,
    *,
    key_prefix: str,
    allow_recovery: bool = True,
    humanize_statement: bool = False,
    secondary_heading: str | None = None,
) -> None:
    _render_needs_attention_queue(
        blockers,
        key_prefix=key_prefix,
        allow_recovery=allow_recovery,
        humanize_statement=humanize_statement,
    )
    if secondary_heading is None:
        _render_exception_reconciliation_notes(
            notes,
            key_prefix=f"{key_prefix}_non_blocking",
            allow_recovery=allow_recovery,
        )
    else:
        _render_non_blocking_exception_queue(
            notes,
            key_prefix=f"{key_prefix}_non_blocking",
            heading=secondary_heading,
            allow_recovery=allow_recovery,
        )


def _partition_exception_work_queue(
    queue: ExceptionWorkQueue,
) -> tuple[ExceptionWorkQueue, ExceptionWorkQueue]:
    """Split blockers from non-blocking facts without dropping safe actions."""

    def project(*, blocking: bool) -> ExceptionWorkQueue:
        items: list[ExceptionPresentationItem] = []
        for item in queue.items:
            issues = tuple(
                issue for issue in item.issues if issue.blocking is blocking
            )
            if not issues:
                continue
            actions = tuple(
                dict.fromkeys(
                    action
                    for issue in issues
                    for action in issue.recovery_actions
                    if action.allowed
                )
            )
            destructive_action = next(
                (action for action in actions if action.destructive),
                None,
            )
            first_reason = issues[0].reason
            if item.order_id and first_reason.startswith(f"{item.order_id}:"):
                first_reason = first_reason[len(item.order_id) + 1 :].strip()
            summary = (
                f"{first_reason} +{len(issues) - 1} more"
                if len(issues) > 1
                else first_reason
            )
            items.append(
                replace(
                    item,
                    summary=summary,
                    issues=issues,
                    blocking=any(issue.blocking for issue in issues),
                    recovery_actions=actions,
                    action_hint=(
                        "Review form below"
                        if any(issue.category == "manual_review" for issue in issues)
                        else destructive_action.label
                        if destructive_action is not None
                        else "View details"
                    ),
                )
            )
        return ExceptionWorkQueue(
            items=tuple(items),
            source_issue_count=sum(item.issue_count for item in items),
        )

    return project(blocking=True), project(blocking=False)


def _render_non_blocking_exception_queue(
    queue: ExceptionWorkQueue,
    *,
    key_prefix: str,
    heading: str,
    allow_recovery: bool,
) -> None:
    """Show preserved non-blocking evidence and any optional safe action."""

    _render_needs_attention_queue(
        queue,
        key_prefix=key_prefix,
        allow_recovery=allow_recovery,
        heading=heading,
    )


def _render_needs_attention_queue(
    queue: ExceptionWorkQueue,
    *,
    key_prefix: str,
    allow_recovery: bool = True,
    heading: str = "Needs Attention",
    show_count: bool = True,
    auto_select_single: bool = True,
    humanize_statement: bool = False,
) -> None:
    """Render one compact entry point while retaining every original issue."""

    if not queue.items:
        return
    displayed_items = (
        _filter_statement_review_items(queue.items, key_prefix=key_prefix)
        if humanize_statement
        else queue.items
    )
    if not displayed_items:
        st.info(
            "No items match the selected problem type.",
            icon=":material/filter_alt_off:",
        )
        return
    st.subheader(heading)
    if show_count:
        if humanize_statement:
            st.caption(
                f"{len(displayed_items)} order"
                f"{'s' if len(displayed_items) != 1 else ''} shown. Each row states "
                "the source facts, the safe next step, and where to resolve it."
            )
        else:
            st.caption(
                f"{queue.source_issue_count} issue"
                f"{'s' if queue.source_issue_count != 1 else ''} across "
                f"{len(queue.items)} work item{'s' if len(queue.items) != 1 else ''}. "
                "Select an item to inspect its original evidence and available action."
            )
    item_keys = tuple(item.key for item in displayed_items)
    click_key = f"{key_prefix}_click"
    st.dataframe(
        [
            (
                _statement_review_queue_row(item)
                if humanize_statement
                else {
                    "Status": "Blocking" if item.blocking else "Review",
                    "Scope": item.scope,
                    "Order ID": item.order_id or "—",
                    "Source": item.source or "—",
                    "Platform": item.platform or "—",
                    "Issues": item.issue_count,
                    "Summary": item.summary,
                    "Available action": item.action_hint,
                    "Action": "View details",
                }
            )
            for item in displayed_items
        ],
        hide_index=True,
        height=min(420, 36 * (len(displayed_items) + 1)),
        column_config={
            "Order ID": st.column_config.TextColumn("Order ID", pinned=True),
            "Issues": st.column_config.NumberColumn("Issues", format="%d"),
            "Action": st.column_config.ButtonColumn(
                "Action",
                type="tertiary",
                on_click=_select_exception_queue_item,
                args=(item_keys, click_key, f"{key_prefix}_selected"),
                key=click_key,
            ),
        },
    )
    selected_key = st.session_state.get(f"{key_prefix}_selected")
    selected = next(
        (item for item in displayed_items if item.key == selected_key),
        None,
    )
    if selected is None and auto_select_single and len(displayed_items) == 1:
        selected = displayed_items[0]
    if selected is not None:
        _render_exception_queue_detail(
            selected,
            key_prefix=key_prefix,
            allow_recovery=allow_recovery,
            statement_context=humanize_statement,
        )


def _filter_statement_review_items(
    items: tuple[ExceptionPresentationItem, ...],
    *,
    key_prefix: str,
) -> tuple[ExceptionPresentationItem, ...]:
    """Keep the Statement queue focused without hiding any underlying issue."""

    categories = tuple(
        dict.fromkeys(
            item.statement_guidance.category
            if item.statement_guidance is not None
            else "Statement needs review"
            for item in items
        )
    )
    counts = {
        category: sum(
            (
                item.statement_guidance.category
                if item.statement_guidance is not None
                else "Statement needs review"
            )
            == category
            for item in items
        )
        for category in categories
    }
    options = ("All problems", *categories)
    filter_key = f"{key_prefix}_filter"
    if st.session_state.get(filter_key) not in options:
        st.session_state.pop(filter_key, None)
    pills = getattr(st, "pills", None)
    selected = (
        pills(
            "Filter by problem",
            options,
            default="All problems",
            key=filter_key,
            format_func=lambda option: (
                option
                if option == "All problems"
                else f"{option} ({counts[option]})"
            ),
            width="stretch",
        )
        if pills is not None
        else "All problems"
    )
    if not selected or selected == "All problems":
        return items
    return tuple(
        item
        for item in items
        if (
            item.statement_guidance.category
            if item.statement_guidance is not None
            else "Statement needs review"
        )
        == selected
    )


def _statement_review_queue_row(item: ExceptionPresentationItem) -> dict[str, Any]:
    guidance = item.statement_guidance
    if guidance is None:
        raise ValueError("Statement review item is missing presentation guidance.")
    return {
        "Order ID": item.order_id or "—",
        "Problem": guidance.problem,
        "What differs": guidance.fact_summary,
        "Next step": guidance.next_step,
        "Resolve in": guidance.resolution_area,
        "Action": "View evidence",
    }


def _render_missing_invoice_status(result: ImportResult) -> bool:
    order_ids = missing_invoice_order_ids(build_exception_work_queue(result))
    if not order_ids:
        return False
    label = (
        "Missing Invoice Order"
        if len(order_ids) == 1
        else f"Missing Invoice Orders · {len(order_ids)}"
    )
    order_line = f"\n\n`{order_ids[0]}`" if len(order_ids) == 1 else ""
    render_authoritative_status(
        title="Statement blocked",
        message=(
            f"**{label}**{order_line}\n\n"
            "Upload the missing invoice, then check the Statement again."
        ),
        state="blocked",
    )
    if len(order_ids) > 1:
        st.dataframe(
            [{"Missing Invoice Order ID": order_id} for order_id in order_ids],
            hide_index=True,
            height=min(280, 36 * (len(order_ids) + 1)),
        )
    return True


def _render_missing_invoice_exception_details(
    blockers: ExceptionWorkQueue,
    notes: ExceptionWorkQueue,
    *,
    key_prefix: str,
) -> None:
    missing_ids = frozenset(missing_invoice_order_ids(blockers))
    missing_items = tuple(
        item for item in blockers.items if item.order_id in missing_ids
    )
    other_blockers = tuple(
        item
        for item in blockers.items
        if item.order_id not in missing_ids
    )

    if other_blockers:
        _render_needs_attention_queue(
            ExceptionWorkQueue(
                items=other_blockers,
                source_issue_count=sum(item.issue_count for item in other_blockers),
            ),
            key_prefix=f"{key_prefix}_other",
            auto_select_single=False,
            humanize_statement=True,
        )

    with st.expander("Missing Invoice details", expanded=False):
        _render_secondary_exception_items(missing_items)
    _render_exception_reconciliation_notes(
        notes,
        key_prefix=f"{key_prefix}_notes",
    )


def _render_exception_reconciliation_notes(
    queue: ExceptionWorkQueue,
    *,
    key_prefix: str = "exception_reconciliation_notes",
    allow_recovery: bool = True,
) -> None:
    if not queue.items:
        return
    with st.expander("Reconciliation notes", expanded=False):
        _render_secondary_exception_items(queue.items)
        if not allow_recovery:
            return
        for item in queue.items:
            categories = {issue.category for issue in item.issues}
            if categories == {"duplicate"}:
                _render_duplicate_queue_actions(item, key_prefix=key_prefix)
            else:
                _render_queue_recovery_actions(item, key_prefix=key_prefix)


def _render_secondary_exception_items(
    items: tuple[ExceptionPresentationItem, ...],
) -> None:
    for item in items:
        st.write(f"**{item.order_id or item.title}**")
        for issue in item.issues:
            st.markdown(f"- {issue.reason}")
            if issue.evidence:
                st.write(dict(issue.evidence))


def _select_exception_queue_item(
    item_keys: tuple[str, ...],
    click_key: str,
    selected_key: str,
) -> None:
    click = st.session_state.get(click_key)
    if click is None:
        return
    try:
        row = int(click["row"])
    except (KeyError, TypeError, ValueError):
        return
    if 0 <= row < len(item_keys):
        st.session_state[selected_key] = item_keys[row]


def _render_exception_queue_detail(
    item: ExceptionPresentationItem,
    *,
    key_prefix: str,
    allow_recovery: bool,
    statement_context: bool = False,
) -> None:
    with st.container(border=True):
        if statement_context and item.statement_guidance is not None:
            guidance = item.statement_guidance
            st.write(f"**Order {item.order_id or item.title}**")
            st.write(f"**{guidance.problem}**")
            st.caption(f"Resolve in: {guidance.resolution_area}")
            st.write(guidance.fact_summary)
            st.write("**Next step**")
            st.write(guidance.next_step)
            if guidance.evidence_rows:
                st.write("**What the system compared**")
                st.dataframe(guidance.evidence_rows, hide_index=True)
            if guidance.additional_problems:
                st.caption(
                    "Also needs review: " + "; ".join(guidance.additional_problems)
                )
            with st.expander("Technical details (for audit)", expanded=False):
                for issue in item.issues:
                    st.markdown(f"- {issue.reason}")
                    if issue.evidence:
                        st.write(dict(issue.evidence))
        else:
            st.write(f"**{item.title}**")
            context = [item.scope]
            if item.source:
                context.append(f"Source: {item.source}")
            if item.platform:
                context.append(f"Platform: {item.platform}")
            st.caption(" · ".join(context))
            for issue in item.issues:
                st.markdown(f"- {issue.reason}")
            with st.expander("Original issue evidence", expanded=False):
                for index, issue in enumerate(item.issues, start=1):
                    st.caption(f"Issue {index}: {issue.reason}")
                    if issue.evidence:
                        st.write(dict(issue.evidence))
        categories = {issue.category for issue in item.issues}
        if "manual_review" in categories:
            st.caption("Use the existing Manual Review form below to resolve this item.")
        if not allow_recovery:
            return
        if categories == {"duplicate"}:
            _render_duplicate_queue_actions(item, key_prefix=key_prefix)
        else:
            _render_queue_recovery_actions(
                item,
                key_prefix=key_prefix,
                statement_context=statement_context,
            )


def _render_queue_recovery_actions(
    item: ExceptionPresentationItem,
    *,
    key_prefix: str,
    statement_context: bool = False,
) -> None:
    actions = tuple(action for action in item.recovery_actions if action.destructive)
    if not actions:
        return
    if statement_context:
        with st.expander("Batch option — does not fix this order", expanded=False):
            st.caption(
                "Removing the staged source only discards this current Statement "
                "batch. It does not repair the Invoice, product identity, Product "
                "Master, or settlement evidence."
            )
            _render_destructive_recovery_buttons(actions, key_prefix=key_prefix, item=item)
        return
    st.caption("Source recovery")
    _render_destructive_recovery_buttons(actions, key_prefix=key_prefix, item=item)


def _render_destructive_recovery_buttons(
    actions: tuple[RecoveryAction, ...],
    *,
    key_prefix: str,
    item: ExceptionPresentationItem,
) -> None:
    with st.container(horizontal=True):
        for action in actions:
            if st.button(
                action.label,
                icon=":material/delete_outline:",
                key=f"{key_prefix}_{item.key}_{action.action_id}",
                disabled=not action.allowed,
            ):
                st.session_state.pending_validation_recovery_action = action
                st.rerun()


def _render_duplicate_queue_actions(
    item: ExceptionPresentationItem,
    *,
    key_prefix: str,
) -> None:
    duplicate_sources = tuple(
        dict.fromkeys(
            str(action.affected_item).strip()
            for action in item.recovery_actions
            if action.affected_item
        )
    )
    if st.session_state.get("import_source_type") == SHOPEE_WEEKLY_STATEMENT:
        _render_weekly_statement_duplicate_removal(list(duplicate_sources))
        return
    removal_plan = plan_duplicate_source_removal(st.session_state, duplicate_sources)
    if removal_plan.safe_sources and st.button(
        f"Remove {len(removal_plan.safe_sources)} safe duplicate source(s)",
        icon=":material/delete_sweep:",
        key=f"{key_prefix}_remove_safe_duplicates",
    ):
        _queue_bulk_recovery(
            list(removal_plan.safe_sources),
            label="Remove safe duplicate sources",
            confirmation_kind="duplicate_sources",
        )
    if removal_plan.retained_sources:
        st.caption(
            "Some duplicate PDFs also contain valid or reviewable orders, so "
            "they are being kept. Duplicate orders are already skipped automatically."
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
        st.session_state.pending_validation_recovery_context = "remove_statement"
        st.rerun()


def _render_issue_details(issue: ValidationIssue) -> None:
    st.caption("Details")
    details = {
        key: value
        for key, value in issue.evidence.items()
        if key in {"source_pdf", "filename", "platform", "order_id", "status", "reason", "reason_code", "message", "error", "code"}
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


def _queue_bulk_recovery(
    sources: list[str],
    *,
    label: str,
    confirmation_kind: str = "remove_sources",
) -> None:
    source_names = tuple(dict.fromkeys(source.strip() for source in sources if source.strip()))
    if not source_names:
        return
    st.session_state.pending_validation_bulk_recovery = {
        "label": label,
        "sources": source_names,
        "confirmation_kind": confirmation_kind,
    }
    st.rerun()


def _render_recovery_confirmation() -> None:
    """Route each pending destructive action to a concise native dialog."""

    action = st.session_state.get("pending_validation_recovery_action")
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    if not isinstance(action, RecoveryAction) and not isinstance(bulk, dict):
        _clear_pending_recovery()
        st.rerun()

    context = st.session_state.get("pending_validation_recovery_context")
    if isinstance(action, RecoveryAction) and context == "exit_statement":
        _render_statement_exit_confirmation()
    elif isinstance(action, RecoveryAction) and context == "exit_invoice":
        _render_invoice_exit_confirmation()
    elif isinstance(action, RecoveryAction) and action.action_type == REMOVE_STAGED_SOURCE:
        _render_staged_statement_removal_confirmation()
    elif isinstance(bulk, dict) and bulk.get("confirmation_kind") == "duplicate_sources":
        _render_duplicate_removal_confirmation()
    elif isinstance(bulk, dict) and bulk.get("confirmation_kind") == "already_imported_sources":
        _render_already_imported_removal_confirmation()
    elif isinstance(bulk, dict) and bulk.get("confirmation_kind") == "non_new_sources":
        _render_non_new_removal_confirmation()
    else:
        _render_source_removal_confirmation()


@st.dialog("Leave Statement Review?", icon=":material/warning:")
def _render_statement_exit_confirmation() -> None:
    _render_pending_recovery_dialog(
        body=(
            "The staged Weekly Statement will be removed. Previously completed "
            "Invoice data will remain unchanged."
        ),
        confirm_label="Leave Statement Review",
        confirm_key="confirm_exit_statement_review",
        cancel_label="Cancel",
        cancel_key="cancel_exit_statement_review",
        failure_message=(
            "Unable to remove the staged Statement. Your current work has been kept."
        ),
    )


@st.dialog("Leave Invoice Import?", icon=":material/warning:")
def _render_invoice_exit_confirmation() -> None:
    _render_pending_recovery_dialog(
        body=(
            "All uncommitted data for this Invoice import will be cleared from "
            "this session. Previously committed data and archived source files "
            "will remain unchanged."
        ),
        confirm_label="Leave Invoice Import",
        confirm_key="confirm_exit_invoice_import",
        cancel_label="Cancel",
        cancel_key="cancel_exit_invoice_import",
        failure_message=(
            "Unable to remove the current Invoice staging. Your current work "
            "has been kept."
        ),
    )


@st.dialog("Remove Staged Statement?", icon=":material/warning:")
def _render_staged_statement_removal_confirmation() -> None:
    _render_pending_recovery_dialog(
        body=(
            "This Statement will be removed from the current session. Previously "
            "completed Invoice data will remain unchanged."
        ),
        confirm_label="Remove Statement",
        confirm_key="confirm_remove_staged_statement",
        cancel_label="Cancel",
        cancel_key="cancel_remove_staged_statement",
        failure_message=(
            "Unable to remove the staged Statement. Your current work has been kept."
        ),
    )


@st.dialog("Remove Duplicate Sources?", icon=":material/warning:")
def _render_duplicate_removal_confirmation() -> None:
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    sources = tuple(bulk.get("sources", ())) if isinstance(bulk, dict) else ()
    if not sources:
        _clear_pending_recovery()
        st.rerun()
    count = len(sources)
    _render_pending_recovery_dialog(
        body=(
            f"{count} duplicate-only source file{'s' if count != 1 else ''} will "
            "be removed from the current batch. Valid orders will not be affected."
        ),
        confirm_label=f"Remove {count} Source{'s' if count != 1 else ''}",
        confirm_key="confirm_remove_duplicate_sources",
        cancel_label="Cancel",
        cancel_key="cancel_remove_duplicate_sources",
        failure_message=(
            "Unable to remove the duplicate sources. Your current work has been kept."
        ),
    )


@st.dialog("Remove Current Source?", icon=":material/warning:")
def _render_source_removal_confirmation() -> None:
    action = st.session_state.get("pending_validation_recovery_action")
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    if isinstance(action, RecoveryAction):
        body = (
            f"{action.affected_item} will be removed from the current batch. "
            "Other current-batch sources will remain."
        )
        confirm_label = "Remove Source"
    elif isinstance(bulk, dict):
        sources = tuple(bulk.get("sources", ()))
        if not sources:
            _clear_pending_recovery()
            st.rerun()
        count = len(sources)
        body = (
            f"{count} selected source file{'s' if count != 1 else ''} will be "
            "removed from the current batch. Other current-batch sources will remain."
        )
        confirm_label = f"Remove {count} Source{'s' if count != 1 else ''}"
    else:
        _clear_pending_recovery()
        st.rerun()
    _render_pending_recovery_dialog(
        body=body,
        confirm_label=confirm_label,
        confirm_key="confirm_remove_current_sources",
        cancel_label="Cancel",
        cancel_key="cancel_remove_current_sources",
        failure_message=(
            "Unable to remove the selected source. Your current work has been kept."
        ),
    )


def _render_pending_recovery_dialog(
    *,
    body: str,
    confirm_label: str,
    confirm_key: str,
    cancel_label: str,
    cancel_key: str,
    failure_message: str,
) -> None:
    st.warning(body)
    with st.container(horizontal=True):
        if st.button(
            confirm_label,
            type="primary",
            icon=":material/delete:",
            key=confirm_key,
        ):
            _execute_pending_recovery(failure_message)
        if st.button(cancel_label, key=cancel_key):
            _clear_pending_recovery()
            st.rerun()


def _execute_pending_recovery(failure_message: str) -> None:
    action = st.session_state.get("pending_validation_recovery_action")
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    begin_workflow_activity(st.session_state, "Revalidating")
    try:
        if isinstance(action, RecoveryAction):
            execution = execute_current_batch_recovery(st.session_state, action)
        elif isinstance(bulk, dict):
            execution = execute_current_batch_bulk_recovery(
                st.session_state,
                bulk.get("sources", ()),
            )
        else:
            raise ValueError("No pending recovery action is available.")
    except Exception:
        st.error(failure_message, icon=":material/error:")
        return
    finally:
        end_workflow_activity(st.session_state)
    if not execution.changed:
        st.error(failure_message, icon=":material/error:")
        return
    st.session_state.validation_recovery_notice = execution.message
    st.session_state.validation_recovery_detail = None
    if isinstance(action, RecoveryAction) and action.action_type == REMOVE_STAGED_SOURCE:
        _set_step(2)
    elif isinstance(action, RecoveryAction) and action.action_type == REMOVE_INVOICE_STAGING:
        clear_invoice_upload_attempt(st.session_state)
        _set_step(2)
    _clear_pending_recovery()
    st.rerun()


def _clear_pending_recovery() -> None:
    st.session_state.pop("pending_validation_recovery_action", None)
    st.session_state.pop("pending_validation_bulk_recovery", None)
    st.session_state.pop("pending_validation_recovery_context", None)

def _render_recovery_notice() -> None:
    notice = st.session_state.pop("validation_recovery_notice", None)
    if notice:
        st.success(f"Recovery complete — {notice}", icon=":material/check_circle:")

def _render_manual_review_resolution() -> None:
    synchronize_correction_drafts(st.session_state)
    reviews = [item for item in st.session_state.get("reviews", []) if str(item.get("status", "")).strip() in {"", "Manual Review"}]
    unfixable_reviews = [r for r in reviews if resolution_plan(r) is None]
    fixable_reviews = [r for r in reviews if resolution_plan(r) is not None]
    unfixable_reviews, fixable_reviews = _synchronize_manual_review_focus(
        unfixable_reviews,
        fixable_reviews,
    )
    notice = st.session_state.pop("manual_resolution_notice", None)
    if notice:
        (st.success if notice.startswith("Correction applied") else st.warning)(notice)
    if not reviews:
        return
    st.subheader("Resolve Manual Review")

    review_sources = [str(item.get("source_pdf") or "").strip() for item in reviews]

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

    st.caption(
        f"⚠️ Requires Re-upload: {len(unfixable_reviews)}    "
        f"📝 Online Resolution: {len(fixable_reviews)}"
    )
    reupload_tab_label = "Requires Re-upload"
    online_tab_label = "Online Resolution"
    active_section = st.session_state.get(_MANUAL_REVIEW_ACTIVE_SECTION)
    tab_unfixable, tab_fixable = st.tabs(
        [reupload_tab_label, online_tab_label],
        default=(online_tab_label if active_section == _MANUAL_REVIEW_ONLINE else reupload_tab_label),
        key=_MANUAL_REVIEW_TAB_WIDGET,
        on_change="rerun",
    )

    with tab_unfixable:
        if not unfixable_reviews:
            st.success("No sources require re-upload in the current batch.", icon=":material/check_circle:")
        else:
            st.warning(
                "The following invoices need a replacement file before they can continue. "
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

            for review in unfixable_reviews:
                presentation = _unfixable_financial_presentation(review)
                if presentation is None:
                    continue
                with st.container(border=True):
                    st.write(f"**{presentation['title']}**")
                    st.write(presentation["reason"])
                    st.caption(presentation["action"])

            st.dataframe(
                [
                    {
                        "Order ID": item.get("order_id"),
                        "Source PDF": item.get("source_pdf"),
                        "Reason": _manual_review_primary_reason(item),
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
            st.info("The following invoices need your review. Check the details below and correct the information where possible.")
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
                    st.caption("Required to resolve")
                    if plan.issue_type == PRODUCT_COUNT_MISMATCH:
                        if review.get("product_payloads"):
                            st.dataframe([{"Seller SKU": item.get("seller_sku"), "Product Name": item.get("product_name"), "Quantity": item.get("quantity")} for item in review["product_payloads"]], hide_index=True)
                        _render_missing_product_draft(plan.key, review)
                    elif plan.issue_type in {SKU_RESOLUTION, PROMOTION_SUBTOTAL}:
                        _render_combined_resolution_form(plan.key, review)
                    elif plan.issue_type == FINAL_AMOUNT:
                        _render_final_amount_form(plan.key, review)
                    elif plan.issue_type in {FINANCIAL_CORRECTION, ADJUSTMENT_CORRECTION}:
                        _render_financial_correction_form(plan.key, review)
                    elif plan.issue_type == PRODUCT_IDENTITY_CORRECTION:
                        _render_product_identity_resolution_form(plan.key, review)
                    else:
                        _render_income_form(plan.key, review)
                    if plan.issue_type not in {
                        FINANCIAL_CORRECTION,
                        ADJUSTMENT_CORRECTION,
                        PRODUCT_IDENTITY_CORRECTION,
                    }:
                        _render_financial_enrichment_draft(plan.key, review)
                    if plan.issue_type == ADJUSTMENT_CORRECTION:
                        with st.expander("Technical Details", expanded=False):
                            st.json(
                                {
                                    "reason_code": review.get("reason_code"),
                                    "reason": review.get("reason"),
                                }
                            )


def _synchronize_manual_review_focus(
    unfixable_reviews: list[dict[str, Any]],
    fixable_reviews: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Restore session-only Manual Review focus against freshly rebuilt reviews."""
    state = st.session_state
    active_section = state.get(_MANUAL_REVIEW_ACTIVE_SECTION)
    if active_section not in {_MANUAL_REVIEW_ONLINE, _MANUAL_REVIEW_REUPLOAD}:
        state[_MANUAL_REVIEW_ACTIVE_SECTION] = (
            _MANUAL_REVIEW_ONLINE if fixable_reviews else _MANUAL_REVIEW_REUPLOAD
        )

    active_key = state.get(_MANUAL_REVIEW_ACTIVE_KEY)
    if active_key:
        matching_fixable = [
            review for review in fixable_reviews
            if review_presentation_key(review) == active_key
        ]
        matching_unfixable = [
            review for review in unfixable_reviews
            if review_presentation_key(review) == active_key
        ]
        if matching_fixable:
            state[_MANUAL_REVIEW_ACTIVE_SECTION] = _MANUAL_REVIEW_ONLINE
            fixable_reviews = matching_fixable + [
                review for review in fixable_reviews if review not in matching_fixable
            ]
        elif matching_unfixable:
            if state.get(_MANUAL_REVIEW_ACTIVE_SECTION) == _MANUAL_REVIEW_ONLINE:
                state["manual_resolution_notice"] = (
                    "After revalidation, this Invoice still has a source-structure issue "
                    "and now requires re-upload. "
                    f"Reason: {matching_unfixable[0].get('reason') or 'Manual Review required.'}"
                )
            state[_MANUAL_REVIEW_ACTIVE_SECTION] = _MANUAL_REVIEW_REUPLOAD
            unfixable_reviews = matching_unfixable + [
                review for review in unfixable_reviews if review not in matching_unfixable
            ]
        else:
            # The resolved review must not be retained merely for presentation.
            state.pop(_MANUAL_REVIEW_ACTIVE_KEY, None)

    return unfixable_reviews, fixable_reviews


def _preserve_manual_review_resolution_context(key: str, *, resolved: bool, reason: str | None) -> None:
    """Set only presentation state before a Streamlit rerun after an action."""
    st.session_state[_MANUAL_REVIEW_ACTIVE_SECTION] = _MANUAL_REVIEW_ONLINE
    if resolved:
        st.session_state.pop(_MANUAL_REVIEW_ACTIVE_KEY, None)
        st.session_state.manual_resolution_notice = "Correction applied and the Invoice passed revalidation."
    else:
        st.session_state[_MANUAL_REVIEW_ACTIVE_KEY] = key
        st.session_state.manual_resolution_notice = (
            f"Still needs review — {reason or 'Correct the source-visible values and try again.'}"
        )


def _apply_manual_resolution(key: str, values: dict[str, Any]) -> None:
    try:
        master, _ = load_configured_product_price_master(_active_invoice_market_context())
        outcome = apply_resolution(st.session_state, key=key, values=values, price_master=master)
    except (ProductMasterSourceError, MarketConfigurationUnavailable) as error:
        st.session_state[_MANUAL_REVIEW_ACTIVE_SECTION] = _MANUAL_REVIEW_ONLINE
        st.session_state[_MANUAL_REVIEW_ACTIVE_KEY] = key
        st.session_state.manual_resolution_notice = f"Product Master validation is unavailable: {error}"
    else:
        if outcome.resolved and values.get("product_identity_fields") is not None:
            _reconcile_historical_invoice_staging()
        _preserve_manual_review_resolution_context(
            key,
            resolved=outcome.resolved,
            reason=outcome.reason,
        )
    st.rerun()


def _surface_reconciliation_product_identity_reviews() -> int:
    """Surface Product Master identity ambiguity during Reconcile.

    This is a read-only Product Master lookup and changes current session
    staging only.  It intentionally does not turn a missing/unavailable master
    into a Manual Review record.
    """
    try:
        context = _active_invoice_market_context()
        master, _ = load_configured_product_price_master(context)
    except (ProductMasterSourceError, MarketConfigurationUnavailable):
        return 0
    created = surface_product_identity_reviews(
        st.session_state,
        price_master=master,
        platform=context.persisted_platform,
        include_not_found=context.key is MarketKey.SHOPEE_SG,
        enrich_resolved=context.key is MarketKey.SHOPEE_SG,
        review_price_mismatch=context.key is MarketKey.SHOPEE_SG,
    )
    return created


def _return_product_identity_reviews_to_validate(created: int) -> None:
    """Return Reconcile-discovered identity work to its Validate owner.

    ``surface_product_identity_reviews`` appends exactly the newly created
    records to the current session review list.  Keep the selected tab/key as
    presentation state only; the review list remains the sole workflow
    authority.
    """
    new_reviews = list(st.session_state.get("reviews", ()))[-created:]
    online_review = next(
        (
            review
            for review in new_reviews
            if isinstance(review, Mapping) and resolution_plan(review) is not None
        ),
        None,
    )
    st.session_state[_MANUAL_REVIEW_ACTIVE_SECTION] = _MANUAL_REVIEW_ONLINE
    if online_review is not None:
        st.session_state[_MANUAL_REVIEW_ACTIVE_KEY] = review_presentation_key(
            online_review
        )
    else:
        st.session_state.pop(_MANUAL_REVIEW_ACTIVE_KEY, None)
    st.session_state.pop(_MANUAL_REVIEW_TAB_WIDGET, None)
    st.session_state.manual_resolution_notice = (
        f"Reconcile found {created} Product Master identity issue(s). "
        "Resolve them in Validate, then return to Reconcile."
    )
    _set_step(3)
    st.rerun()


def _render_product_identity_resolution_form(key: str, review: dict[str, Any]) -> None:
    """Edit only source-derived identity facts for the affected staged items."""
    products = review.get("product_payloads") or []
    affected = review.get("product_identity_affected") or []
    st.write("**Product identity needs source correction**")
    st.write(str(review.get("reason") or "Product Master identity remains unresolved."))
    st.caption("Correct only values visible in the original Invoice. Product Master price and NAV are re-derived after revalidation.")
    evidence_rows = []
    for item in affected:
        if not isinstance(item, Mapping):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(products):
            continue
        product = products[index]
        evidence = {
            "Affected item": index + 1,
            "Seller SKU": product.get("seller_sku") or "",
            "Product Name": product.get("product_name") or "",
            "Variation": product.get("variation") or product.get("variation_name") or "",
            "Product Master candidates": item.get("candidate_count"),
        }
        if item.get("issue_type") == "pricing_reference_mismatch":
            evidence["Invoice Unit Price"] = format_currency(
                item.get("source_unit_price"), _active_invoice_currency()
            )
            evidence["Product Listing Price"] = format_currency(
                item.get("reference_unit_price"), _active_invoice_currency()
            )
        evidence_rows.append(evidence)
    if evidence_rows:
        st.write("Original source evidence / current parsed values")
        st.dataframe(evidence_rows, hide_index=True)
    with st.form(f"product_identity_resolution_{key}", border=False):
        submitted: dict[str, dict[str, str]] = {}
        for item in affected:
            if not isinstance(item, Mapping):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= index < len(products):
                continue
            product = products[index]
            st.write(f"Affected item {index + 1}")
            sku_column, name_column, variation_column = st.columns((1, 3, 2))
            with sku_column:
                seller_sku = st.text_input(
                    "Seller SKU",
                    value=str(product.get("seller_sku") or ""),
                    key=f"mr_identity_sku_{key}_{index}",
                )
            with name_column:
                product_name = st.text_input(
                    "Product Name",
                    value=str(product.get("product_name") or ""),
                    key=f"mr_identity_name_{key}_{index}",
                )
            with variation_column:
                variation = st.text_input(
                    "Variation",
                    value=str(product.get("variation") or product.get("variation_name") or ""),
                    key=f"mr_identity_variation_{key}_{index}",
                )
            submitted[str(index)] = {
                "seller_sku": seller_sku,
                "product_name": product_name,
                "variation": variation,
            }
        confirmed = st.checkbox(
            "I confirm these product identity values are visible in the original Shopee Invoice.",
            key=f"mr_identity_confirm_{key}",
        )
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(
                key,
                {
                    "source_confirmed": confirmed,
                    "product_identity_fields": submitted,
                },
            )


def _render_combined_resolution_form(key: str, review: dict[str, Any]) -> None:
    """Render all structured SKU/promotion corrections for one atomic revalidation."""
    capabilities = set(resolution_capabilities(review))
    sku_products = [
        (index, item)
        for index, item in enumerate(review.get("product_payloads") or [])
        if bool(item.get("sku_missing_in_source"))
        and not str(item.get("seller_sku") or "").strip()
    ]
    groups = promotion_subtotal_groups(review) if PROMOTION_SUBTOTAL in capabilities else ()
    saved = draft_promotion_subtotals(st.session_state, review)
    with st.form(f"mr_combined_resolution_{key}"):
        candidates: dict[str, str] = {}
        sku_confirmed = SKU_RESOLUTION not in capabilities
        promotion_confirmed = PROMOTION_SUBTOTAL not in capabilities
        if SKU_RESOLUTION in capabilities:
            st.write("**Product / SKU correction**")
            st.caption("Source Seller SKU: Not provided. Enter only a SKU verified from an authoritative source outside this PDF.")
            for index, item in sku_products:
                st.write(str(item.get("product_name") or "Product"))
                candidates[str(index)] = st.text_input(
                    "Resolved Seller SKU",
                    key=f"mr_combined_sku_{key}_{index}",
                )
            sku_confirmed = st.checkbox(
                "I confirm these SKUs were verified from an authoritative source and are not shown in this PDF.",
                key=f"mr_combined_sku_confirm_{key}",
            )
        submitted: dict[str, str] = {}
        if PROMOTION_SUBTOTAL in capabilities:
            st.write("**Promotion subtotal**")
            for group in groups:
                st.caption(f"Promotion: {group.label}")
                st.caption(
                    "Products: "
                    + ", ".join(
                        f"{name} (Qty {quantity})"
                        for name, quantity in zip(group.member_names, group.member_quantities)
                    )
                )
                st.caption(
                    "Source subtotal: "
                    + format_currency(
                        group.source_group_total,
                        _active_invoice_currency(),
                        missing_value="Not extracted",
                    )
                )
                st.caption(
                    "Source-visible promotion amount: "
                    + format_currency(
                        group.advertised_amount,
                        _active_invoice_currency(),
                        missing_value="Visible in the original Invoice",
                    )
                )
                submitted[group.group_id] = st.text_input(
                    "Promotion Subtotal",
                    value=saved.get(group.group_id, ""),
                    key=f"mr_combined_promo_{key}_{group.group_id}",
                )
            promotion_confirmed = st.checkbox(
                "I confirmed this subtotal from the original Invoice.",
                key=f"mr_combined_promo_confirm_{key}",
            )
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(
                key,
                {
                    "sku_source_confirmed": sku_confirmed,
                    "resolved_seller_skus": candidates,
                    "promotion_source_confirmed": promotion_confirmed,
                    "promotion_subtotals": submitted,
                },
            )


def _render_financial_enrichment_draft(key: str, review: dict[str, Any]) -> None:
    fields = financial_enrichment_fields(review)
    if not fields:
        return
    saved = draft_financial_enrichment(st.session_state, review)
    with st.expander("Additional source-visible financial details (optional)"):
        st.caption(
            "These values are optional enrichment only. Leave a source-absent value blank; "
            "enter 0.00 only when the Invoice visibly shows 0.00."
        )
        with st.form(f"financial_enrichment_{key}", border=False):
            values = {
                field: st.text_input(
                    label,
                    value=saved.get(field, ""),
                    key=f"mr_financial_{key}_{field}",
                )
                for field, label in fields
            }
            source_confirmed = st.checkbox(
                "I verified these optional values are visible in the original Invoice source",
                key=f"mr_financial_confirm_{key}",
            )
            if st.form_submit_button("Save optional financial details"):
                outcome = set_financial_enrichment(
                    st.session_state,
                    key=key,
                    values=values,
                    source_confirmed=source_confirmed,
                )
                st.session_state.manual_resolution_notice = (
                    "Optional financial details saved for Apply & Revalidate."
                    if outcome.resolved
                    else f"Still needs review â€” {outcome.reason}"
                )
                st.rerun()


def _render_financial_correction_form(key: str, review: dict[str, Any]) -> None:
    fields = financial_enrichment_fields(review)
    payload = review.get("order_payload") or {}
    saved = draft_financial_enrichment(st.session_state, review)
    presentation = _financial_review_presentation(review, fields)
    st.write(f"**{presentation['title']}**")
    st.write(presentation["reason"])
    if summary := presentation.get("summary"):
        calculated, invoice, difference = summary
        calculated_column, invoice_column, difference_column = st.columns(3)
        calculated_column.metric("Calculated", calculated)
        invoice_column.metric("Invoice", invoice)
        difference_column.metric("Difference", difference)
    if summary := presentation.get("adjustment_summary"):
        expected, invoice, difference = summary
        expected_column, invoice_column, difference_column = st.columns(3)
        expected_column.metric("Expected Final Amount", expected)
        invoice_column.metric("Invoice Final Amount", invoice)
        difference_column.metric("Difference", difference)
    st.caption(presentation["action"])
    with st.form(f"financial_correction_{key}", border=False):
        values: dict[str, str] = {}
        def render_field(field: str, label: str) -> None:
            current = payload.get(field)
            prefilled = "" if current in (None, "", "N/A") else str(current)
            values[field] = st.text_input(
                label,
                value=saved.get(field, prefilled),
                key=f"mr_financial_correction_{key}_{field}",
            )

        if presentation["kind"] in {"formula_mismatch", "adjustment_conflict"}:
            input_columns = st.columns(
                3 if presentation["kind"] == "adjustment_conflict" else 2
            )
            for index, (field, label) in enumerate(fields):
                with input_columns[index % len(input_columns)]:
                    render_field(field, label)
        else:
            for field, label in fields:
                render_field(field, label)
        source_confirmed = st.checkbox(
            "Confirmed with the original Shopee Invoice",
            key=f"mr_financial_correction_confirm_{key}",
        )
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(
                key,
                {
                    "source_confirmed": source_confirmed,
                    "financial_enrichment": values,
                },
            )


def _financial_review_presentation(
    review: Mapping[str, Any], fields: tuple[tuple[str, str], ...]
) -> dict[str, Any]:
    code = str(review.get("reason_code") or "")
    if code == POST_ORDER_ADJUSTMENT_AMOUNT_MISSING:
        return {
            "kind": "adjustment_missing",
            "title": "Missing adjustment amount",
            "reason": "The adjustment amount could not be read from the invoice.",
            "action": "Enter the adjustment amount exactly as shown on the invoice.",
        }
    if code == POST_ORDER_ADJUSTMENT_EVIDENCE_CONFLICT:
        return {
            "kind": "adjustment_conflict",
            "title": "Check adjustment amounts",
            "reason": "Final Amount does not match Order Income plus the adjustment.",
            "action": "Check the amounts below and correct any value that was read incorrectly.",
            "adjustment_summary": _adjustment_conflict_summary(review),
        }
    summary = _formula_mismatch_summary(str(review.get("reason") or ""))
    if summary is not None:
        return {
            "kind": "formula_mismatch",
            "title": "Check invoice amounts",
            "reason": "Order Income does not match the other invoice amounts.",
            "action": "Check the amounts below and correct any value that was read incorrectly.",
            "summary": summary,
        }
    labels = [label for _, label in fields]
    amount = labels[0] if len(labels) == 1 else "Some amounts"
    return {
        "kind": "missing_amount",
        "title": "Missing amount",
        "reason": f"{amount} could not be read from the invoice.",
        "action": "Enter the amount exactly as shown on the invoice.",
    }


def _formula_mismatch_summary(reason: str) -> tuple[str, str, str] | None:
    match = _FINANCIAL_FORMULA_MISMATCH.search(reason)
    if match is None:
        return None
    try:
        calculated = Decimal(match.group("calculated").replace(",", ""))
        invoice = Decimal(match.group("invoice").replace(",", ""))
    except ArithmeticError:
        return None
    return (
        format_currency(calculated, _active_invoice_currency()),
        format_currency(invoice, _active_invoice_currency()),
        format_currency(abs(invoice - calculated), _active_invoice_currency()),
    )


def _adjustment_conflict_summary(
    review: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    payload = review.get("order_payload") or {}
    try:
        order_income = Decimal(str(payload.get("order_income")).replace(",", ""))
        adjustment = Decimal(
            str(payload.get("post_order_adjustment_amount")).replace(",", "")
        )
        final_amount = Decimal(str(payload.get("final_amount")).replace(",", ""))
    except (ArithmeticError, ValueError):
        return None
    expected = order_income + adjustment
    return (
        format_currency(expected, _active_invoice_currency()),
        format_currency(final_amount, _active_invoice_currency()),
        format_currency(abs(final_amount - expected), _active_invoice_currency()),
    )


def _unfixable_financial_presentation(
    review: Mapping[str, Any],
) -> dict[str, str] | None:
    if str(review.get("reason_code") or "") != POST_ORDER_ADJUSTMENT_SOURCE_MISSING:
        return None
    return {
        "title": "Adjustment evidence missing",
        "reason": "Final Amount differs from Order Income, but no adjustment is shown in the source.",
        "action": "Upload an invoice or source that shows the adjustment to continue.",
    }


def _manual_review_primary_reason(review: Mapping[str, Any]) -> str:
    presentation = _unfixable_financial_presentation(review)
    return presentation["reason"] if presentation is not None else str(
        review.get("reason") or "Manual Review required"
    )
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
        + (
            f" · subtotal {format_currency(group.source_group_total, _active_invoice_currency())}"
            if group.source_group_total is not None
            else ""
        )
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
        master, _ = load_configured_product_price_master(_active_invoice_market_context())
        outcome = apply_product_draft(st.session_state, key=key, price_master=master)
    except (ProductMasterSourceError, MarketConfigurationUnavailable) as error:
        st.session_state[_MANUAL_REVIEW_ACTIVE_SECTION] = _MANUAL_REVIEW_ONLINE
        st.session_state[_MANUAL_REVIEW_ACTIVE_KEY] = key
        st.session_state.manual_resolution_notice = f"Product Master validation is unavailable: {error}"
    else:
        _preserve_manual_review_resolution_context(
            key,
            resolved=outcome.resolved,
            reason=outcome.reason,
        )
    st.rerun()


def _render_promotion_subtotal_form(key: str, review: dict[str, Any]) -> None:
    groups = promotion_subtotal_groups(review)
    if not groups:
        st.info("Promotion evidence is ambiguous or the subtotal is absent from the source, so it cannot be safely corrected here.")
        return
    saved = draft_promotion_subtotals(st.session_state, review)
    with st.form(f"promotion_subtotal_{key}", border=False):
        submitted: dict[str, str] = {}
        for group in groups:
            st.write("**Promotion subtotal missing**")
            st.caption(f"Promotion: {group.label}")
            st.caption(
                "Products: "
                + ", ".join(
                    f"{name} (Qty {quantity})"
                    for name, quantity in zip(
                        group.member_names, group.member_quantities
                    )
                )
            )
            st.caption(
                "Source subtotal: "
                + (
                    format_currency(
                        group.source_group_total,
                        _active_invoice_currency(),
                        missing_value="Not extracted",
                    )
                )
            )
            st.caption(
                "Source-visible promotion amount: "
                + (
                    format_currency(
                        group.advertised_amount,
                        _active_invoice_currency(),
                        missing_value="Visible in the original Invoice",
                    )
                )
            )
            submitted[group.group_id] = st.text_input(
                "Promotion Subtotal",
                value=saved.get(group.group_id, ""),
                key=f"mr_promo_subtotal_{key}_{group.group_id}",
            )
        confirmed = st.checkbox(
            "I confirmed this subtotal from the original Invoice.",
            key=f"mr_promo_subtotal_confirm_{key}",
        )
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(
                key,
                {
                    "source_confirmed": confirmed,
                    "promotion_subtotals": submitted,
                },
            )


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
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(key, {"source_confirmed": source_confirmed, "order_income": income, "income_type": income_type, "final_amount": ""})


def _render_final_amount_form(key: str, review: dict[str, Any]) -> None:
    with st.form(f"final_amount_resolution_{key}", border=False):
        final_amount = st.text_input("Final Amount", key=f"mr_final_only_{key}")
        source_confirmed = st.checkbox("I confirm this Final Amount is visible in the original Invoice source.", key=f"mr_final_only_confirm_{key}")
        if st.form_submit_button("Apply & Revalidate", type="primary"):
            _apply_manual_resolution(key, {"source_confirmed": source_confirmed, "final_amount": final_amount})

def _render_reconciliation_step() -> None:
    st.subheader("Reconcile")
    if _has_pending_recovery():
        _render_recovery_confirmation()
    if st.session_state.get("import_source_type") == SHOPEE_FULL_ORDER_LIST:
        review = _full_order_review()
        ready = review is not None and review.ready
        render_authoritative_status(
            title="Ready" if ready else "Needs Attention",
            message=(
                "Current snapshot replacement preview is complete. Statement proof remains a separate fail-closed step."
                if ready
                else "Return to Validate and resolve the Full Order review."
            ),
            state="ready" if ready else "blocked",
        )
        st.caption(
            "This sync changes only mutable Full Order current data. It does not overwrite Invoice_Orders or Invoice_Items."
        )
        _render_next_step(
            "Continue to review & sync",
            5,
            back_step=3,
            allowed=ready,
            disabled_reason=None if ready else "Full Order validation is not ready.",
        )
        return
    if st.session_state.get("import_source_type") == PLATFORM_ORDERS:
        eligibility = invoice_upload_downstream_eligibility(st.session_state)
        if not eligibility.eligible:
            _render_blocked_invoice_destination(5)
            return
        if _active_invoice_market_context().key is MarketKey.SHOPEE_SG:
            _render_shopee_sg_test_reconciliation()
            return
        created_identity_reviews = _surface_reconciliation_product_identity_reviews()
        if created_identity_reviews:
            _return_product_identity_reviews_to_validate(created_identity_reviews)
            return
        entries = _reconcile_historical_invoice_staging()
        historical_validation_blocker = st.session_state.get(
            "historical_validation_blocker"
        )
        if historical_validation_blocker:
            _render_historical_validation_blocker(historical_validation_blocker)
            _render_next_step(
                "Continue to review & commit",
                5,
                back_step=3,
                allowed=False,
                include_invoice_exit=True,
            )
            return
        historical_ready = _historical_commit_ready(entries)
        render_authoritative_status(
            title="Ready" if historical_ready else "Needs Attention",
            message=(
                "Historical status checks passed for the current Invoice batch."
                if historical_ready
                else _historical_forward_reason(entries)
            ),
            state="ready" if historical_ready else "blocked",
        )
        _render_product_master_revalidation(entries)
        _render_next_step(
            "Continue to review & commit",
            5,
            back_step=3,
            allowed=(
                historical_ready
                or bool(st.session_state.get("invoice_commit_completed"))
            ),
            include_invoice_exit=True,
            disabled_reason=(
                None
                if historical_ready
                else _historical_forward_reason(entries)
            ),
        )
        _render_needs_attention_queue(
            build_historical_exception_work_queue(entries),
            key_prefix="historical_exception_queue",
        )
        _render_historical_status_details(
            entries,
            allow_removal=True,
            key_prefix="reconcile_historical",
            suppress_manual_review_duplicates=True,
        )
        return
    if _render_statement_already_imported():
        return
    if _render_stale_statement_refresh("statement_reconciliation_stale_refresh"):
        return
    result = _current_import_result()
    if _render_statement_source_error(result):
        return
    if _statement_invoice_coverage(result) is not None:
        _render_statement_invoice_coverage_status(result)
        _render_statement_refresh_action(
            _weekly_review(),
            key="statement_reconciliation_coverage_refresh",
        )
        _render_statement_next_step(
            "Continue to review & commit",
            5,
            back_step=3,
        )
        _render_statement_invoice_coverage_details(result)
        _render_source_summary(result)
        return
    reconciliation = result.reconciliation
    if not reconciliation.available:
        reason = reconciliation.source_specific_details.get("reason")
        st.info(f"{reconciliation.status} — {reason or 'Reconciliation is not available for this staged result.'}", icon=":material/info:")
        _render_next_step(
            "Continue to review & commit",
            5,
            back_step=3,
            allowed=False,
            disabled_reason=(
                reason or "Reconciliation must be available before continuing."
            ),
        )
        return
    allowed, disabled_reason = _statement_forward_gate()
    if allowed:
        render_authoritative_status(
            title="Ready",
            message="Statement reconciliation passed the existing readiness checks.",
            state="ready",
        )
    elif not _render_missing_invoice_status(result):
        render_authoritative_status(
            title="Needs Attention",
            message=disabled_reason or "Resolve the current reconciliation blockers.",
            state="blocked",
        )
    _render_statement_next_step(
        "Continue to review & commit",
        5,
        back_step=3,
    )
    _render_statement_refresh_action(
        _weekly_review(),
        key="statement_reconciliation_refresh",
    )
    if not allowed:
        _render_contract_validation(result)
    _render_summary_items(reconciliation.summary)
    st.caption("These results are shown for review and do not change the source outcome.")
    _render_representative_contract_exceptions(reconciliation.exceptions)
    _render_statement_review_tables(collapsed=not allowed)


def _render_shopee_sg_test_reconciliation() -> None:
    """Resolve SG listing identity without opening a persistence dependency."""

    st.session_state[SHOPEE_SG_PRODUCT_RESOLUTION_EVALUATED_KEY] = False
    try:
        master, source_label = load_configured_product_price_master(SHOPEE_SG)
    except (ProductMasterSourceError, MarketConfigurationUnavailable) as error:
        render_authoritative_status(
            title="SG Product Listing unavailable",
            message=(
                "Shopee SG remains fail-closed. Configure the SHOPEE PRODUCT "
                "LISTING 'shopee sg' tab before product resolution can continue."
            ),
            state="blocked",
        )
        with st.expander("View technical details", expanded=False):
            st.code(str(error), language=None)
        _render_back_button(3, key="shopee_sg_listing_unavailable_back")
        return

    st.session_state[SHOPEE_SG_PRODUCT_RESOLUTION_EVALUATED_KEY] = True
    created = surface_product_identity_reviews(
        st.session_state,
        price_master=master,
        platform=SHOPEE_SG.persisted_platform,
        include_not_found=True,
        enrich_resolved=True,
        review_price_mismatch=True,
    )
    if created:
        _return_product_identity_reviews_to_validate(created)
        return

    accepted_orders = [
        order
        for order in st.session_state.get("orders", [])
        if str(order.get("platform") or "").strip() == SHOPEE_SG.persisted_platform
        and str(order.get("status") or "").strip() == "Accepted"
    ]
    resolved_products = [
        product
        for product in st.session_state.get("products", [])
        if str(product.get("platform") or "").strip() == SHOPEE_SG.persisted_platform
        and product.get("nav")
        and product.get("master_unit_price") is not None
    ]
    ready = bool(accepted_orders) and all(
        product.get("nav") and product.get("master_unit_price") is not None
        for product in st.session_state.get("products", [])
        if str(product.get("platform") or "").strip() == SHOPEE_SG.persisted_platform
    )
    render_authoritative_status(
        title="Test candidates ready" if ready else "Needs Attention",
        message=(
            "SG source validation and deterministic Product Listing resolution passed. "
            "These are test candidates only; no historical database check or write was performed."
            if ready
            else "Resolve every SG product identity before continuing."
        ),
        state="ready" if ready else "blocked",
    )
    with st.container(horizontal=True):
        st.metric("Accepted candidates", len(accepted_orders), border=True)
        st.metric("Resolved product rows", len(resolved_products), border=True)
    st.caption(f"Product Listing source: {source_label} · worksheet: shopee sg")
    _render_next_step(
        "Continue to review",
        5,
        back_step=3,
        allowed=ready,
        include_invoice_exit=True,
        disabled_reason=None if ready else "Resolve every SG product identity first.",
    )


@st.dialog("Continue Remaining Invoices?", icon=":material/check_circle:")
def _render_already_imported_removal_confirmation() -> None:
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    sources = tuple(bulk.get("sources", ())) if isinstance(bulk, dict) else ()
    if not sources:
        _clear_pending_recovery()
        st.rerun()
    count = len(sources)
    _render_pending_recovery_dialog(
        body=(
            f"{count} already-imported source file{'s' if count != 1 else ''} "
            "will be removed only from this current staging batch. Existing "
            "Invoice_Orders and Invoice_Items in UAT2 will not be deleted or changed."
        ),
        confirm_label="Continue with remaining invoices",
        confirm_key="confirm_remove_already_imported_sources",
        cancel_label="Cancel",
        cancel_key="cancel_remove_already_imported_sources",
        failure_message=(
            "Unable to update current staging. Existing UAT2 data was not changed."
        ),
    )


@st.dialog("Remove All Non-NEW Sources?", icon=":material/warning:")
def _render_non_new_removal_confirmation() -> None:
    bulk = st.session_state.get("pending_validation_bulk_recovery")
    sources = tuple(bulk.get("sources", ())) if isinstance(bulk, dict) else ()
    if not sources:
        _clear_pending_recovery()
        st.rerun()
    count = len(sources)
    _render_pending_recovery_dialog(
        body=(
            f"{count} source PDF{'s' if count != 1 else ''} containing non-NEW "
            "Invoice results will be removed only from this current staging batch. "
            "If one of these PDFs also contains a NEW order, that whole PDF and all "
            "of its staged records will be removed. Existing UAT2 data and archived "
            "source files will not be changed."
        ),
        confirm_label=f"Remove {count} PDF{'s' if count != 1 else ''}",
        confirm_key="confirm_remove_all_non_new_sources",
        cancel_label="Cancel",
        cancel_key="cancel_remove_all_non_new_sources",
        failure_message=(
            "Unable to remove the non-NEW sources. Your current work has been kept."
        ),
    )


def _render_product_master_revalidation(entries: tuple[Any, ...]) -> None:
    notice = st.session_state.pop("product_master_revalidation_notice", None)
    if notice:
        st.success(notice, icon=":material/check_circle:")
    error_message = st.session_state.get("product_master_revalidation_error")
    if error_message:
        st.error(error_message, icon=":material/error:")
    if not has_product_master_dependent_blocker(entries):
        return
    st.caption(
        "Product Master recovery uses the latest configured source and keeps "
        "the current uploaded Invoice staging."
    )
    if not st.button(
        "Revalidate with latest Product Master",
        icon=":material/refresh:",
        key="revalidate_invoice_latest_product_master",
    ):
        return

    begin_workflow_activity(st.session_state, "Revalidating")
    try:
        clear_product_master_source_cache()
        context = _required_persistence_market_context()
        master, source_label = load_configured_product_price_master(context)
        result = revalidate_current_invoice_batch(
            st.session_state,
            price_master=master,
            repository=configured_uat2_data_settings(
                context, require_explicit_context=True
            ).create_repository(),
            staging_signature=_historical_commit_signature(),
        )
    except Exception as error:
        message = (
            f"Product Master revalidation failed: {error} "
            "Current Invoice staging was kept; retry when the latest Product "
            "Master is available."
        )
        st.session_state.product_master_revalidation_error = message
        st.error(message, icon=":material/error:")
    else:
        st.session_state.pop("product_master_revalidation_error", None)
        if result.remaining_pm_blockers:
            message = (
                f"Revalidated the current batch from {source_label}. "
                f"{result.remaining_pm_blockers} Product Master-dependent "
                "source(s) still need attention."
            )
        else:
            message = (
                f"Revalidated the current batch from {source_label}. Product "
                "Master-dependent blockers were resolved; normal readiness "
                "checks now apply."
            )
        st.session_state.product_master_revalidation_notice = message
        st.rerun()
    finally:
        end_workflow_activity(st.session_state)


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

def _render_monthly_statement_review_and_commit() -> None:
    review = _monthly_review()
    if review is None or review.stage.statement is None:
        st.warning("Upload and validate a Shopee Monthly Statement first.")
        _render_back_button(3, key="monthly_review_missing_back")
        return
    statement = review.stage.statement
    result = st.session_state.get("monthly_statement_commit_result")
    if result is not None:
        st.success("Monthly Statement imported", icon=":material/check_circle:")
        _render_monthly_metrics(statement)
        st.write(f"**Adjustment rows:** {len(statement.adjustments)}")
        st.caption(
            "Stored for historical reporting only. Weekly reconciliation, weekly records, "
            "Invoices, and Order Adjustments were not changed."
        )
        _render_monthly_upload_another_action("monthly_success_upload_another")
        return
    if not review.commit_ready:
        st.warning("Monthly Statement is not ready to commit.", icon=":material/warning:")
        _render_back_button(3, key="monthly_review_blocked_back")
        return
    st.write("**Shopee Monthly Statement**")
    _render_monthly_metrics(statement)
    render_authoritative_status(
        title="Ready to Commit",
        message="All Monthly source and internal validation controls passed.",
        state="ready",
    )
    st.caption(
        "This Monthly Statement is stored for historical reporting only. It does not "
        "change Weekly reconciliation or weekly records."
    )
    with st.container(horizontal=True):
        commit_clicked = st.button(
            "Commit Monthly Statement",
            type="primary",
            icon=":material/upload:",
            key="monthly_statement_commit",
        )
        back_clicked = st.button(
            "Back", icon=":material/arrow_back:", key="monthly_statement_commit_back"
        )
    if back_clicked:
        _set_step(3)
        st.rerun()
    if not commit_clicked:
        return
    try:
        writer = configured_uat2_data_settings().create_monthly_statement_writer()
        refreshed = refresh_monthly_statement_review(review, writer=writer)
        st.session_state.monthly_statement_review = refreshed
        if not refreshed.commit_ready:
            st.warning(
                "Monthly Statement state changed before commit. Refresh validation before trying again."
            )
            _set_step(3)
            st.rerun()
        commit_result = commit_monthly_statement_review(refreshed, writer=writer)
    except ApplicationCommitInProgress as error:
        st.warning(str(error))
        return
    except StatementWriteIntegrityError as error:
        st.error(f"Monthly Statement write requires manual integrity recovery: {error}")
        return
    except (HistoricalInvoiceStorageError, StatementCommitBlocked) as error:
        st.error(f"Monthly Statement commit failed before a safe write was confirmed: {error}")
        return
    st.session_state.monthly_statement_commit_result = commit_result
    st.rerun()


def _render_review_and_commit_step() -> None:
    st.subheader("Review & Commit")
    if st.session_state.get("import_source_type") == SHOPEE_MONTHLY_STATEMENT:
        _render_monthly_statement_review_and_commit()
        return
    if st.session_state.get("import_source_type") == SHOPEE_FULL_ORDER_LIST:
        _render_full_order_review_and_sync()
        return
    if _has_pending_recovery():
        _render_recovery_confirmation()
    result = _current_import_result()
    readiness = result.commit_readiness
    if st.session_state.get("import_source_type") == PLATFORM_ORDERS:
        if _active_invoice_market_context().key is MarketKey.SHOPEE_SG:
            _render_shopee_sg_test_review(result)
            return
        eligibility = invoice_upload_downstream_eligibility(st.session_state)
        if not eligibility.eligible:
            _render_blocked_invoice_destination(4)
            return
        if st.session_state.get("invoice_commit_completed"):
            imported_count = st.session_state.get("invoice_commit_completed_count")
            st.success(
                (
                    f"Historical Invoice Commit Complete — Imported: {imported_count}."
                    if imported_count is not None
                    else "Historical Invoice Commit Complete."
                ),
                icon=":material/check_circle:",
            )
            _render_back_button(4, key="completed_invoice_back")
            return
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
        _render_needs_attention_queue(
            build_historical_exception_work_queue(entries),
            key_prefix="historical_commit_exception_queue",
            allow_recovery=False,
        )
        _render_historical_invoice_commit()
        _render_back_button(4, key="invoice_commit_back")
        _render_invoice_exit_button(key="exit_invoice_commit")
        return
    if _render_statement_already_imported():
        return
    if _render_statement_source_error(result):
        return
    if st.session_state.get("weekly_statement_commit_completed"):
        st.success("Statement Commit Complete.", icon=":material/check_circle:")
        _render_back_button(4, key="completed_statement_back")
        _render_source_summary(result)
        return
    review = _weekly_review()
    if _statement_invoice_coverage(result) is not None:
        _render_statement_invoice_coverage_status(result)
        _render_statement_refresh_action(
            review,
            key="statement_commit_coverage_refresh",
        )
        _render_statement_invoice_coverage_details(result)
        _render_back_button(4, key="coverage_statement_commit_back")
        _render_source_summary(result)
        return
    if review is not None and review.active_gate == "FULL_ORDER_CURRENT_ORDER_EXISTENCE":
        if readiness.ready and review.commit_ready and not _statement_review_stale():
            st.success(
                "Statement review complete — Full Order coverage and source financial controls passed.",
                icon=":material/check_circle:",
            )
        else:
            st.error(
                "Statement cannot commit until the current source and Full Order coverage blockers are resolved. "
                + (readiness.reasons[0] if readiness.reasons else ""),
                icon=":material/error:",
            )
        _render_statement_commit(readiness.ready)
        _render_back_button(4, key="order_existence_statement_commit_back")
        _render_statement_exit_button(key="order_existence_statement_commit_exit")
        _render_source_summary(result)
        return
    if (
        review is not None
        and readiness.ready
        and review.commit_ready
        and not _statement_review_stale()
    ):
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
        _render_statement_commit(readiness.ready)
        _render_back_button(4, key="ready_statement_commit_back")
        _render_statement_exit_button(key="exit_ready_statement_commit")
        _render_source_summary(result)
    else:
        affected_orders, unresolved_issues = _statement_blocker_counts(result)
        if not _render_missing_invoice_status(result):
            st.error(
                "**Cannot commit Statement**\n\n"
                f"{affected_orders} order{'s' if affected_orders != 1 else ''} "
                f"{'still needs' if affected_orders == 1 else 'still need'} attention. "
                f"{unresolved_issues} unresolved "
                f"issue{'s' if unresolved_issues != 1 else ''} remain.\n\n"
                "Resolve all blocking issues in Statement Review before committing.",
                icon=":material/error:",
            )
        if _statement_review_stale():
            st.warning(
                str(st.session_state.get("weekly_statement_review_stale_reason")),
                icon=":material/refresh:",
            )
        elif not unresolved_issues and readiness.reasons:
            st.caption(readiness.reasons[0])
        blockers, notes = _partition_exception_work_queue(
            build_exception_work_queue(result)
        )
        _render_actionable_blockers_and_notes(
            blockers,
            notes,
            key_prefix="statement_commit_exception_queue",
            allow_recovery=False,
        )
        _render_statement_commit(readiness.ready)
        if st.button(
            "Back to Statement Review",
            icon=":material/arrow_back:",
            key="back_to_statement_review",
        ):
            _set_step(3)
            st.rerun()
        _render_statement_exit_button(key="exit_blocked_statement_commit")
        _render_source_summary(result)


def _render_full_order_review_and_sync() -> None:
    completed = st.session_state.get("full_order_sync_result")
    if completed is not None:
        st.success("Full Order current sync complete.", icon=":material/check_circle:")
        with st.container(horizontal=True):
            st.metric("New", completed.new_count, border=True)
            st.metric("Updated", completed.updated_count, border=True)
            st.metric("Unchanged", completed.unchanged_count, border=True)
        st.caption(
            "Canonical Invoice history was not overwritten. Re-uploading the same source creates no duplicates."
        )
        if st.button(
            "Upload another Full Order",
            type="primary",
            icon=":material/upload_file:",
            key="full_order_upload_another",
        ):
            begin_next_full_order_upload(st.session_state)
            st.rerun()
        return
    review = _full_order_review()
    if review is None or not review.ready:
        st.warning("Full Order validation is not ready to sync.")
        _render_back_button(3, key="full_order_commit_missing_back")
        return
    preview = review.sync_preview
    assert preview is not None
    render_authoritative_status(
        title="Ready to Sync",
        message="Fresh source and Product Master validation will run again before write.",
        state="ready",
    )
    with st.container(horizontal=True):
        st.metric("New", preview.new_count, border=True)
        st.metric("Updated", preview.updated_count, border=True)
        st.metric("Unchanged", preview.unchanged_count, border=True)
    st.caption(
        "Updated current Order IDs are replaced with their complete item snapshot. "
        "Orders absent from this workbook are not deleted."
    )
    with st.container(horizontal=True):
        sync_clicked = st.button(
            "Sync Full Order current data",
            type="primary",
            icon=":material/sync:",
            key="full_order_sync_current",
        )
        back_clicked = st.button(
            "Back", icon=":material/arrow_back:", key="full_order_sync_back"
        )
    if back_clicked:
        _set_step(4)
        st.rerun()
    if not sync_clicked:
        return
    context = _required_persistence_market_context()
    begin_workflow_activity(st.session_state, "Processing")
    try:
        settings = configured_uat2_data_settings(context, require_explicit_context=True)
        result = commit_shopee_full_order_review(
            review,
            load_product_master=lambda: load_configured_product_price_master(context)[0],
            repository=settings.create_full_order_current_repository(),
        )
        clear_cross_platform_reporting_snapshot_cache()
        st.session_state.full_order_sync_result = result
    except (
        ValueError,
        HistoricalInvoiceStorageError,
        MarketConfigurationUnavailable,
        ProductMasterSourceError,
        ApplicationCommitInProgress,
    ) as error:
        st.error(f"Full Order sync failed before safe completion: {error}")
    finally:
        end_workflow_activity(st.session_state)
    if st.session_state.get("full_order_sync_result") is not None:
        st.rerun()


def _render_shopee_sg_test_review(result: ImportResult) -> None:
    accepted = len(result.source_specific_details.get("orders", ()))
    products = len(result.source_specific_details.get("products", ()))
    manual_review = len(result.source_specific_details.get("manual_review", ()))
    if result.commit_readiness.ready:
        render_authoritative_status(
            title="Accepted test candidates",
            message=(
                "Shopee SG PDF validation and Product Listing resolution are complete. "
                "Permanent SG persistence is not approved for this test entry."
            ),
            state="ready",
        )
    else:
        render_authoritative_status(
            title="Needs Attention",
            message="Resolve the remaining SG Manual Review records before acceptance.",
            state="blocked",
        )
    with st.container(horizontal=True):
        st.metric("Accepted candidates", accepted, border=True)
        st.metric("Product rows", products, border=True)
        st.metric("Manual Review", manual_review, border=True)
    st.info(
        "Test-mode boundary: no SG Invoice_Orders or Invoice_Items write is available, "
        "and Shopee MY storage is never used as a fallback.",
        icon=":material/lock:",
    )
    _render_back_button(4, key="shopee_sg_test_review_back")
    _render_invoice_exit_button(key="exit_shopee_sg_test_review")


def _render_source_summary(result: ImportResult) -> None:
    st.caption(f"Current batch: {result.session_state.label}")
    _render_summary_items(result.source_summary.items)


def _render_summary_items(items: tuple[Any, ...]) -> None:
    render_summary_items(items)


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
        "Historical status is calculated during Reconcile and refreshed immediately before Commit."
    )
    _render_historical_partial_import_failure()
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
    commit_allowed = clean_batch and not refresh_required
    if st.button(
        "Commit Accepted Shopee Invoices",
        icon=":material/upload:",
        type="primary" if commit_allowed else "secondary",
        disabled=not commit_allowed,
        key="uat2_historical_commit",
    ):
        try:
            context = _required_persistence_market_context()
            repository = configured_uat2_data_settings(
                context, require_explicit_context=True
            ).create_repository()
            outcome = import_new_staging(entries, repository)
            st.session_state.uat2_historical_commit_entries = outcome.entries
            actual = outcome.bulk_result.results
            if any(item.status.value != "NEW" for item in actual):
                st.session_state.uat2_historical_commit_refresh_required = True
                st.warning("Historical state changed immediately before commit. No mixed-batch write was started; validate again.")
            else:
                st.session_state.invoice_commit_completed = True
                st.session_state.invoice_commit_completed_count = len(actual)
                mark_statement_review_stale_after_invoice_commit(st.session_state)
                st.rerun()
        except HistoricalInvoiceBulkImportError as error:
            st.session_state.uat2_historical_commit_refresh_required = True
            st.session_state.historical_partial_import_failure = {
                "confirmed_count": error.confirmed_count,
                "pending_count": error.pending_count,
                "chunk_size": error.chunk_size,
                "completed_chunks": error.completed_chunk_count,
                "failed_chunk_index": error.failed_chunk_index,
                "failed_chunk_size": error.failed_chunk_size,
                "total_chunks": error.total_chunks,
                "underlying_error_type": error.underlying_error_type,
                "underlying_error_message": error.underlying_error_message,
            }
            st.rerun()
        except ApplicationCommitInProgress as error:
            st.warning(str(error))
        except (HistoricalInvoiceStorageError, MarketConfigurationUnavailable) as error:
            st.error(f"Historical Invoice storage write failed: {error}")


def _render_historical_partial_import_failure() -> None:
    details = st.session_state.get("historical_partial_import_failure")
    if not isinstance(details, dict):
        return
    confirmed = int(details.get("confirmed_count") or 0)
    pending = int(details.get("pending_count") or 0)
    st.warning(
        "**Partial import completed**\n\n"
        f"{confirmed} invoice{'s were' if confirmed != 1 else ' was'} successfully "
        f"imported. {pending} invoice{'s were' if pending != 1 else ' was'} not "
        "written. The already imported invoices are safe in UAT2. Revalidate "
        "the batch before continuing.",
        icon=":material/warning:",
    )
    if st.button(
        "Revalidate remaining invoices",
        icon=":material/refresh:",
        type="primary",
        key="revalidate_partial_historical_import",
    ):
        _set_step(4)
        st.rerun()
    with st.expander("Technical details", expanded=False):
        rows = (
            ("Chunk size", details.get("chunk_size")),
            ("Completed chunks", details.get("completed_chunks")),
            ("Failed chunk", details.get("failed_chunk_index")),
            ("Failed chunk invoices", details.get("failed_chunk_size")),
            ("Total chunks", details.get("total_chunks")),
            ("Confirmed invoices", confirmed),
            ("Pending invoices", pending),
            ("Underlying error type", details.get("underlying_error_type")),
            ("Underlying error", details.get("underlying_error_message")),
        )
        st.dataframe(
            [
                {
                    "Diagnostic": label,
                    "Value": str(value) if value is not None else "Unavailable",
                }
                for label, value in rows
            ],
            hide_index=True,
        )


def _reconcile_historical_invoice_staging() -> tuple[Any, ...]:
    signature = _historical_commit_signature()
    refresh_required = st.session_state.get(
        "uat2_historical_commit_refresh_required", False
    )
    if (
        st.session_state.get("uat2_historical_commit_signature") != signature
        or refresh_required
    ):
        try:
            context = _required_persistence_market_context()
            master, _label = load_configured_product_price_master(context)
            candidates = build_current_batch_staging(
                batch_id=st.session_state.get("batch_id"),
                orders=st.session_state.get("orders", []),
                products=st.session_state.get("products", []),
                reviews=st.session_state.get("reviews", []),
                price_master=master,
            )
            entries = classify_staging(
                candidates,
                configured_uat2_data_settings(
                    context, require_explicit_context=True
                ).create_repository(),
            )
            st.session_state.uat2_historical_commit_entries = entries
            st.session_state.uat2_historical_commit_refresh_required = False
            st.session_state.uat2_historical_commit_signature = signature
            st.session_state.pop("historical_validation_blocker", None)
        except (
            HistoricalInvoiceStorageError,
            MarketConfigurationUnavailable,
            ProductMasterSourceError,
        ) as error:
            st.session_state.historical_validation_blocker = {
                "order_id": getattr(error, "affected_order_id", None),
                "technical_message": str(error),
            }
            return ()
    entries = tuple(st.session_state.get("uat2_historical_commit_entries", ()))
    return entries


def _render_historical_validation_blocker(blocker: Any) -> None:
    order_id = (
        str(blocker.get("order_id") or "").strip()
        if isinstance(blocker, dict)
        else ""
    )
    message = "The historical Invoice records must be corrected before continuing."
    if order_id:
        message = f"**Affected Order**\n\n`{order_id}`\n\n{message}"
    render_authoritative_status(
        title="Invoice data incomplete",
        message=message,
        state="blocked",
    )
    technical_message = (
        str(blocker.get("technical_message") or "").strip()
        if isinstance(blocker, dict)
        else ""
    )
    if technical_message:
        with st.expander("View technical details", expanded=False):
            st.code(technical_message, language=None)


def _render_historical_status_details(
    entries: tuple[Any, ...],
    *,
    allow_removal: bool,
    key_prefix: str,
    suppress_manual_review_duplicates: bool = False,
) -> None:
    if not entries:
        st.caption("No target Shopee Invoice source is ready for historical classification.")
        return
    manual_identities = {
        _platform_invoice_blocker_identity(
            platform=review.get("platform"),
            order_id=review.get("order_id"),
            source=review.get("source_pdf"),
        )
        for review in st.session_state.get("reviews", [])
        if isinstance(review, Mapping)
    }
    actionable_entries = tuple(
        entry
        for entry in entries
        if not (
            suppress_manual_review_duplicates
            and entry.status is IntakeStatus.NEEDS_REVIEW
            and _platform_invoice_blocker_identity(
                platform="Shopee",
                order_id=entry.order_id,
                source=entry.source_filename,
            )
            in manual_identities
        )
    )
    if allow_removal:
        non_new_sources = tuple(
            dict.fromkeys(
                entry.source_filename
                for entry in actionable_entries
                if entry.status is not IntakeStatus.NEW and entry.source_filename
            )
        )
        already_count = sum(
            entry.status is IntakeStatus.ALREADY_IMPORTED for entry in actionable_entries
        )
        new_count = sum(entry.status is IntakeStatus.NEW for entry in actionable_entries)
        if already_count:
            st.success(
                f"Already imported: {already_count} · Remaining new: {new_count}",
                icon=":material/check_circle:",
            )
            plan = plan_already_imported_source_removal(st.session_state, entries)
            if plan.safe_sources:
                st.caption(
                    f"{already_count} invoice{'s are' if already_count != 1 else ' is'} "
                    "already stored in UAT2. This action removes only eligible "
                    "already-imported sources from the current staging batch."
                )
                if st.button(
                    f"Continue with {new_count} remaining invoices",
                    icon=":material/forward:",
                    type="primary",
                    key=f"{key_prefix}_continue_remaining_invoices",
                ):
                    _queue_bulk_recovery(
                        list(plan.safe_sources),
                        label="Continue with remaining invoices",
                        confirmation_kind="already_imported_sources",
                    )
            if plan.retained_sources:
                retained_count = len(plan.retained_sources)
                st.warning(
                    "Mixed source — manual review / safe recovery required for "
                    f"{retained_count} source{'s' if retained_count != 1 else ''}."
                )
        if non_new_sources and st.button(
            "Remove all non-NEW source PDFs from current batch",
            icon=":material/delete_sweep:",
            key=f"{key_prefix}_remove_all_non_new_sources",
        ):
            _queue_bulk_recovery(
                list(non_new_sources),
                label="Remove all non-NEW source PDFs",
                confirmation_kind="non_new_sources",
            )
    with st.expander("Historical classification details", expanded=False):
        st.dataframe(
            [
                {
                    "Source PDF": entry.source_filename,
                    "Order ID": entry.order_id or "N/A",
                    "Historical Status": entry.status.value,
                    "Reason / Message": entry.message or "Ready for Review & Commit.",
                }
                for entry in actionable_entries
            ],
            hide_index=True,
        )
        if not allow_removal:
            return
        needs_review_sources = [
            entry.source_filename
            for entry in actionable_entries
            if entry.status is IntakeStatus.NEEDS_REVIEW and entry.source_filename
        ]
        if needs_review_sources and st.button(
            "Remove all NEEDS_REVIEW sources from current batch",
            icon=":material/delete_sweep:",
            key=f"{key_prefix}_remove_all_needs_review",
        ):
            _queue_bulk_recovery(
                needs_review_sources,
                label="Remove all NEEDS_REVIEW sources",
            )
        for entry in entries:
            if entry not in actionable_entries:
                continue
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


def _full_order_review() -> ShopeeFullOrderImportReview | None:
    review = st.session_state.get("full_order_review")
    return review if isinstance(review, ShopeeFullOrderImportReview) else None


def _weekly_review() -> StatementImportReview | None:
    review = st.session_state.get("weekly_statement_review")
    return review if isinstance(review, StatementImportReview) else None


def _monthly_review() -> MonthlyStatementReview | None:
    review = st.session_state.get("monthly_statement_review")
    return review if isinstance(review, MonthlyStatementReview) else None


def _reset_monthly_statement_workflow() -> None:
    version = int(st.session_state.get("monthly_statement_uploader_version", 0))
    st.session_state.pop("monthly_statement_review", None)
    st.session_state.pop("monthly_statement_commit_result", None)
    st.session_state.pop("batch_id", None)
    st.session_state.monthly_statement_uploader_version = version + 1


def _render_statement_already_imported() -> bool:
    """Present an exact committed Statement re-upload as terminal success."""

    if st.session_state.get("import_source_type") != SHOPEE_WEEKLY_STATEMENT:
        return False
    review = _weekly_review()
    statement = review.stage.statement if review is not None else None
    if review is None or not review.stage.already_imported or statement is None:
        return False

    st.success(
        "Statement already imported\n\n"
        "This Statement has already been successfully processed. No additional data was written.",
        icon=":material/check_circle:",
    )
    st.caption(
        f"Statement period: {statement.statement_period_from:%d/%m/%Y} – "
        f"{statement.statement_period_to:%d/%m/%Y} · "
        f"Order rows: {len(statement.order_rows)} · SKU rows: {len(statement.sku_rows)}"
    )
    with st.expander("Import details"):
        st.write(f"Source filename: {statement.source_filename}")
        st.write(f"Source hash: {statement.file_hash}")
    with st.container(horizontal=True):
        if st.button(
            "View Weekly Billing",
            icon=":material/calendar_month:",
            key="already_imported_statement_view_billing",
        ):
            clear_statement_upload_attempt(st.session_state)
            request_navigation(st.session_state, "Weekly Billing")
            st.rerun()
        if st.button(
            "Upload another Statement",
            icon=":material/upload_file:",
            key="already_imported_statement_upload_another",
        ):
            clear_statement_upload_attempt(st.session_state)
            _set_step(2)
            st.rerun()
    return True


def _render_statement_review_tables(*, collapsed: bool = False) -> None:
    review = _weekly_review()
    if (
        review is None
        or review.stage.statement is None
        or review.reconciliation_v2 is None
    ):
        return
    if collapsed:
        with st.expander("All reconciliation evidence", expanded=False):
            _render_statement_review_content(review, nested_disclosure=False)
        return
    _render_statement_review_content(review, nested_disclosure=True)


def _render_statement_review_content(
    review: StatementImportReview,
    *,
    nested_disclosure: bool,
) -> None:
    """Render the existing reconciliation evidence without changing its values."""

    batch = review.reconciliation_v2
    if batch is None:
        return
    st.subheader("Reconciliation V2 review")
    stale_reason = st.session_state.get("weekly_statement_review_stale_reason")
    if stale_reason:
        st.warning(str(stale_reason), icon=":material/refresh:")
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
    if nested_disclosure:
        with st.expander("Reconciliation notes", expanded=False):
            _render_reconciliation_notes(review)
    else:
        st.write("**Reconciliation notes**")
        _render_reconciliation_notes(review)
    with st.container(border=True):
        st.write("**Statement Adjustment evidence (separate)**")
        st.write(format_currency(batch.statement_adjustment_total))
        st.caption(
            "This is not included in original merchandise or seller-settlement reconciliation."
        )
    if nested_disclosure:
        with st.expander("Technical reconciliation evidence"):
            _render_statement_technical_evidence(batch)
    else:
        st.subheader("Technical reconciliation evidence")
        _render_statement_technical_evidence(batch)


def _render_reconciliation_notes(review: StatementImportReview) -> None:
    st.caption(
        "Identity, merchandise, and seller settlement are evaluated separately. "
        "GROUP is valid source-level reconciliation, not a failed item match."
    )
    for limitation in review.limitations:
        st.info(limitation, icon=":material/info:")
    st.caption(
        "Quantity evidence — Invoice quantity: available where captured. "
        "Statement quantity: not provided by source. No quantity-match claim is made."
    )


def _render_statement_technical_evidence(batch: Any) -> None:
    """Keep raw identity and settlement audit evidence reachable."""

    st.caption(
        f"Rule {batch.rule_version} · Product Master snapshot "
        f"{batch.product_family_snapshot.sha256}"
    )
    st.dataframe(_identity_evidence_rows(batch), hide_index=True)
    settlement_rows = _settlement_evidence_rows(batch)
    if settlement_rows:
        st.dataframe(settlement_rows, hide_index=True)


def _render_statement_commit(ready: bool) -> None:
    consistency = statement_stage_review_consistency(st.session_state)
    if not consistency.coherent or consistency.state != "coherent":
        st.button("Commit Statement", disabled=True, key="statement_commit")
        return
    review = _weekly_review()
    if review is None:
        st.button("Commit Statement", disabled=True, key="statement_commit")
        return
    commit_allowed = (
        ready
        and review.commit_ready
        and not _statement_review_stale()
    )
    with st.container(horizontal=True):
        commit_clicked = st.button(
            "Commit Statement",
            type="primary" if commit_allowed else "secondary",
            icon=":material/upload:",
            disabled=not commit_allowed,
            key="statement_commit",
        )
        refresh_clicked = st.button(
            "Refresh validation",
            icon=":material/refresh:",
            key="statement_refresh_validation",
        )
    if refresh_clicked:
        if _refresh_statement_review(review):
            st.rerun()
    if not commit_clicked:
        return
    try:
        context = _required_persistence_market_context()
        settings = configured_uat2_data_settings(
            context, require_explicit_context=True
        )
        master = (
            None
            if context.statement_order_existence_gate
            else load_configured_product_price_master(context)[0]
        )
        currency = check_statement_review_currency(
            review,
            writer=settings.create_statement_writer(),
            product_master=master,
            full_order_repository=settings.create_full_order_current_repository(),
            market_context=context,
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
            writer=settings.create_statement_writer(),
            load_product_master=lambda: load_configured_product_price_master(context)[0],
            full_order_repository=settings.create_full_order_current_repository(),
            market_context=context,
        )
    except ApplicationCommitInProgress as error:
        st.warning(str(error))
        return
    except StatementWriteIntegrityError as error:
        st.error(f"Statement write requires manual integrity recovery: {error}")
        return
    except (
        HistoricalInvoiceStorageError,
        MarketConfigurationUnavailable,
        ProductMasterSourceError,
        StatementCommitBlocked,
    ) as error:
        st.error(f"Statement commit failed before a safe write could be confirmed: {error}")
        return
    if attempt.committed:
        st.session_state.weekly_statement_commit_completed = True
        st.rerun()
    st.warning(
        "Statement state changed or the write was not applied. No new commit was confirmed; validation has been refreshed."
    )
    if not _refresh_statement_review(review):
        return
    _set_step(3)
    st.rerun()


def _refresh_statement_review(review: StatementImportReview) -> bool:
    try:
        context = _required_persistence_market_context()
        settings = configured_uat2_data_settings(
            context, require_explicit_context=True
        )
        if context.statement_order_existence_gate:
            master = None
        else:
            clear_product_master_source_cache()
            master, _label = load_configured_product_price_master(context)
        refreshed = refresh_statement_review(
            review,
            writer=settings.create_statement_writer(),
            product_master=master,
            full_order_repository=settings.create_full_order_current_repository(),
            market_context=context,
        )
    except (
        HistoricalInvoiceStorageError,
        MarketConfigurationUnavailable,
        ProductMasterSourceError,
        StatementCommitBlocked,
    ) as error:
        st.error(f"Statement validation refresh failed: {error}")
        return False
    replace_statement_review_after_refresh(st.session_state, refreshed)
    return True


def _render_statement_refresh_action(
    review: StatementImportReview | None,
    *,
    key: str,
) -> None:
    """Expose the authoritative refresh even while blockers prevent navigation."""

    if review is None:
        return
    if st.button(
        "Refresh validation",
        icon=":material/refresh:",
        key=key,
    ) and _refresh_statement_review(review):
        st.rerun()


def _render_stale_statement_refresh(key: str) -> bool:
    """Hide stale blocker facts until the user rebuilds the live review."""

    if not _statement_review_stale():
        return False
    reason = str(st.session_state.get("weekly_statement_review_stale_reason"))
    render_authoritative_status(
        title="Refresh required",
        message=reason,
        state="blocked",
    )
    st.caption(
        "The previous blocker list is hidden because it no longer represents "
        "the current Full Order, Statement, and Product Master evidence."
    )
    _render_statement_refresh_action(_weekly_review(), key=key)
    _render_statement_exit_button(key=f"{key}_exit")
    return True


def _render_statement_next_step(
    label: str,
    step: int,
    *,
    back_step: int,
) -> None:
    """Gate forward navigation with the authoritative Statement review."""

    allowed, disabled_reason = _statement_forward_gate()
    with st.container(horizontal=True):
        next_clicked = st.button(
            label,
            type="primary" if allowed else "secondary",
            icon=":material/arrow_forward:",
            key=f"statement_next_{step}",
            disabled=not allowed,
        )
        back_clicked = st.button(
            "Back",
            icon=":material/arrow_back:",
            key=f"statement_back_{step}",
        )
    _render_statement_exit_button(key=f"statement_exit_before_{step}")
    if not allowed and disabled_reason:
        st.caption(disabled_reason)
    if back_clicked:
        _set_step(back_step)
        st.rerun()
    if not next_clicked:
        return
    review = _weekly_review()
    if review is None:
        return
    if not _refresh_statement_review(review):
        return
    refreshed = _weekly_review()
    if refreshed is None or not refreshed.commit_ready or _statement_review_stale():
        st.warning(_statement_forward_gate()[1])
        return
    _set_step(step)
    st.rerun()


def _render_statement_resume_actions(*, back_step: int) -> None:
    with st.container(horizontal=True):
        continue_clicked = st.button(
            "Continue to validate",
            type="primary",
            icon=":material/arrow_forward:",
            key="statement_resume_validation",
        )
        back_clicked = st.button(
            "Back",
            icon=":material/arrow_back:",
            key="statement_resume_back",
        )
    _render_statement_exit_button(key="statement_resume_exit")
    if back_clicked:
        _set_step(back_step)
        st.rerun()
    if continue_clicked:
        review = _weekly_review()
        if review is None or not _refresh_statement_review(review):
            return
        _set_step(3)
        st.rerun()


def _statement_forward_gate() -> tuple[bool, str | None]:
    consistency = statement_stage_review_consistency(st.session_state)
    if not consistency.coherent or consistency.state != "coherent":
        return False, consistency.reason or "Check the Weekly Statement before continuing."
    review = _weekly_review()
    if review is None:
        return False, "Check the Weekly Statement before continuing."
    if _statement_review_stale():
        return False, "Refresh the Statement review before continuing."
    result = _current_import_result()
    coverage = _statement_invoice_coverage(result)
    if coverage is not None:
        count = len(coverage.additional_evidence)
        return (
            False,
            f"{count} order{'s' if count != 1 else ''} "
            f"{'require' if count != 1 else 'requires'} additional evidence before reconciliation can continue.",
        )
    if result.commit_readiness.ready and review.commit_ready:
        return True, None
    affected_orders, issue_count = _statement_blocker_counts(result)
    if issue_count:
        return (
            False,
            f"{affected_orders} affected order{'s' if affected_orders != 1 else ''}; "
            f"{issue_count} unresolved issue{'s' if issue_count != 1 else ''} "
            "still need attention before continuing.",
        )
    return False, _commit_readiness_reason(result)


def _statement_blocker_counts(result: ImportResult) -> tuple[int, int]:
    issues = result.validation.blocking_issues
    queue = build_exception_work_queue(result)
    affected_orders = {
        item.order_id
        for item in queue.items
        if item.order_id and any(issue.blocking for issue in item.issues)
    }
    return len(affected_orders), len(issues)


def _statement_removal_action() -> RecoveryAction | None:
    result = _current_import_result()
    queue = build_exception_work_queue(result)
    action = next(
        (
            action
            for item in queue.items
            for action in item.recovery_actions
            if action.action_type == REMOVE_STAGED_SOURCE
        ),
        None,
    )
    if action is not None:
        return action
    stage = _weekly_stage()
    if stage is None:
        return None
    actions = recovery_actions_for_source(
        source=stage.source_filename,
        action_type=REMOVE_STAGED_SOURCE,
        remove_label="Remove staged source",
        include_details=False,
    )
    return actions[0] if actions else None


def _render_statement_exit_button(*, key: str) -> None:
    if st.session_state.get("weekly_statement_commit_completed"):
        return
    stage = _weekly_stage()
    if stage is None or stage.duplicate_status:
        return
    action = _statement_removal_action()
    if action is None:
        return
    st.caption("Source recovery")
    if st.button(
        "Exit Statement Review",
        icon=":material/logout:",
        key=key,
    ):
        st.session_state.pending_validation_recovery_action = action
        st.session_state.pending_validation_recovery_context = "exit_statement"
        st.rerun()


def _render_invoice_exit_button(*, key: str) -> None:
    if st.session_state.get("import_source_type") != PLATFORM_ORDERS:
        return
    action = plan_current_invoice_staging_exit(st.session_state)
    if action is None:
        return
    st.caption("Source recovery")
    if st.button(
        "Exit Invoice Import",
        icon=":material/logout:",
        key=key,
    ):
        st.session_state.pending_validation_recovery_action = action
        st.session_state.pending_validation_recovery_context = "exit_invoice"
        st.rerun()


def _statement_review_stale() -> bool:
    if st.session_state.get("weekly_statement_review_stale_reason"):
        return True
    review = _weekly_review()
    evidence_version = getattr(review, "evidence_version", None)
    if evidence_version is None:
        return False
    if not statement_review_requires_proof_rule_refresh(
        review, _active_invoice_market_context()
    ):
        return False
    st.session_state["weekly_statement_review_stale_reason"] = (
        "Full Order Statement proof rules changed after this review. "
        "Refresh validation before continuing."
    )
    return True


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


def _render_back_button(step: int, *, key: str) -> None:
    if st.button("Back", icon=":material/arrow_back:", key=key):
        _set_step(step)
        st.rerun()


def _render_blocked_invoice_destination(step: int) -> None:
    """Fail closed when direct navigation bypasses an unresolved upload."""

    st.subheader(WIZARD_STEPS[step - 1])
    eligibility = invoice_upload_downstream_eligibility(st.session_state)
    render_authoritative_status(
        title="Upload not completed",
        message=eligibility.reason or "Return to Upload before continuing.",
        state="empty" if eligibility.state == "selected" else "blocked",
    )
    with st.container(horizontal=True):
        if st.button(
            "Back",
            icon=":material/arrow_back:",
            key=f"blocked_invoice_destination_{step}",
        ):
            _set_step(2)
            st.rerun()
    _render_invoice_exit_button(key=f"blocked_invoice_exit_{step}")


def _render_inconsistent_statement_destination(step: int) -> None:
    """Expose no operational controls for a partial Statement stage/review pair."""

    if step != 2:
        st.subheader(WIZARD_STEPS[step - 1])
    consistency = statement_stage_review_consistency(st.session_state)
    render_authoritative_status(
        title="Statement check required",
        message=consistency.reason or "Check the Weekly Statement again before continuing.",
        state="blocked",
    )
    with st.container(horizontal=True):
        if st.button(
            "Clear incomplete Statement attempt",
            type="primary",
            icon=":material/refresh:",
            key=f"clear_inconsistent_statement_{step}",
        ):
            clear_statement_upload_attempt(st.session_state)
            _set_step(2)
            st.rerun()
        if step != 2 and st.button(
            "Back to upload",
            icon=":material/arrow_back:",
            key=f"back_from_inconsistent_statement_{step}",
        ):
            _set_step(2)
            st.rerun()


def _render_unresolved_invoice_upload_actions(*, back_step: int) -> None:
    """Keep an unfinished Invoice upload in Upload until its attempt is resolved."""

    render_authoritative_status(
        title="Upload not completed",
        message="Re-upload the files or clear the incomplete upload attempt before continuing.",
        state="blocked",
    )
    with st.container(horizontal=True):
        if st.button(
            "Clear incomplete upload attempt",
            type="primary",
            icon=":material/refresh:",
            key=f"clear_invoice_upload_attempt_{back_step}",
        ):
            reset_invoice_upload_attempt(st.session_state)
            _set_step(2)
            st.rerun()
        if st.button(
            "Back",
            icon=":material/arrow_back:",
            key=f"data_import_upload_back_{back_step}",
        ):
            reset_invoice_upload_attempt(st.session_state)
            _set_step(back_step)
            st.rerun()
    _render_invoice_exit_button(key=f"invoice_exit_upload_{back_step}")


def _render_invoice_upload_back_actions(*, back_step: int) -> None:
    """Render neutral navigation while Upload is not yet eligible to advance."""

    with st.container(horizontal=True):
        if st.button(
            "Back",
            icon=":material/arrow_back:",
            key=f"data_import_upload_back_{back_step}",
        ):
            if st.session_state.get(_INVOICE_UPLOAD_ATTEMPT_KEY) in _UNRESOLVED_INVOICE_UPLOAD_ATTEMPTS:
                reset_invoice_upload_attempt(st.session_state)
            _set_step(back_step)
            st.rerun()
    _render_invoice_exit_button(key=f"invoice_exit_upload_{back_step}")


def _render_next_step(
    label: str,
    step: int,
    *,
    back_step: int | None = None,
    allowed: bool = True,
    include_invoice_exit: bool = False,
    disabled_reason: str | None = None,
) -> None:
    with st.container(horizontal=True):
        next_clicked = st.button(
            label,
            type="primary" if allowed else "secondary",
            icon=":material/arrow_forward:",
            key=f"data_import_next_{step}",
            disabled=not allowed,
        )
        back_clicked = (
            st.button(
                "Back",
                icon=":material/arrow_back:",
                key=f"data_import_back_{step}",
            )
            if back_step is not None
            else False
        )
    if include_invoice_exit:
        _render_invoice_exit_button(key=f"invoice_exit_before_{step}")
    if not allowed and disabled_reason:
        st.caption(disabled_reason)
    if back_clicked:
        _set_step(back_step)
        st.rerun()
    if next_clicked and allowed:
        _set_step(step)
        st.rerun()


def _commit_readiness_reason(result: ImportResult) -> str:
    if result.commit_readiness.reasons:
        return result.commit_readiness.reasons[0]
    return "Resolve the current step before continuing."


def _historical_forward_reason(entries: tuple[Any, ...]) -> str:
    if not entries:
        return "Complete historical Invoice reconciliation before continuing."
    unresolved = sum(entry.status is not IntakeStatus.NEW for entry in entries)
    return (
        f"Resolve or remove {unresolved} non-NEW Invoice source"
        f"{'s' if unresolved != 1 else ''} before continuing."
    )
