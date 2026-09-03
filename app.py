from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.invoice_app.config import APP_TITLE, SHOPEE_PRODUCT_MASTER_PATH
from src.invoice_app.services.auth_service import authenticate
from src.invoice_app.services.product_price_master import (
    ProductPriceMaster,
    load_shopee_product_price_master,
)
from src.invoice_app.services.analytics import (
    compute_overall_dashboard,
    compute_platform_dashboard,
    compute_platform_kpis,
)
from src.invoice_app.services.all_products import (
    ALL_PRODUCT_COLUMNS,
    ALL_PRODUCT_DISPLAY_COLUMNS,
    ALL_PRODUCT_DISPLAY_FIELD_LABELS,
    CROSS_PLATFORM_PRODUCT_DISPLAY_COLUMNS,
    CROSS_PLATFORM_PRODUCT_DISPLAY_FIELD_LABELS,
    ALL_PRODUCT_FIELD_LABELS,
    ALL_PRODUCT_REVIEW_COLUMNS,
    ALL_PRODUCT_REVIEW_FIELD_LABELS,
    CROSS_PLATFORM_SUMMARY_COLUMNS,
    CROSS_PLATFORM_SUMMARY_FIELD_LABELS,
    build_all_product_views,
    build_cross_platform_product_rows,
    build_shopee_product_level_rows,
    filter_cross_platform_product_rows,
    partition_cross_platform_product_summary_rows,
    summarize_cross_platform_products,
)
from src.invoice_app.services.batch_service import (
    FIELD_LABELS,
    MISSING_VALUE_PLACEHOLDER,
    PLATFORM_ORDER_FIELDS,
    PLATFORM_PRODUCT_FIELDS,
    PLATFORMS,
    append_batch_results_with_metadata,
    canonical_platform_label,
    create_batch_id,
    create_file_outcome_record,
    is_manual_review_record,
    prepare_uploaded_invoice_files,
    process_pdf_file_with_outcome,
    resolve_archived_pdf_path,
    split_by_platform,
)
from src.invoice_app.services.workflow_navigation import (
    begin_workflow_activity,
    end_workflow_activity,
    request_navigation,
)
from src.invoice_app.services.exporter import (
    export_all_products_report,
    export_platform_report,
    export_product_summary_report,
    export_review_report,
)
from src.invoice_app.ui.data_import import (
    DATA_IMPORT_PAGE,
    initialize_data_import_state,
    render_data_import,
    reset_data_import_state,
)
from src.invoice_app.ui.settlement_test_lab import (
    SETTLEMENT_TEST_LAB_PAGE,
    render_settlement_test_lab,
    reset_settlement_test_lab_state,
)

st.set_page_config(page_title=APP_TITLE, page_icon=":material/receipt_long:", layout="wide")


ORDER_REQUIRED_COLUMNS = ["platform", "order_id"]
PRODUCT_REQUIRED_COLUMNS = ["platform", "order_id", "product_name"]
INITIAL_PLATFORM_ORDER_COLUMNS = {
    "Shopee": [
        "platform",
        "order_id",
        "order_status",
        "payment_status",
        "order_created_date",
        "fund_transfer_date",
        "order_income",
        "merchandise_subtotal",
    ],
    "Lazada": [
        "platform",
        "order_id",
        "invoice_number",
        "order_date",
        "invoice_date",
        "payment_method",
        "subtotal",
        "net_paid",
        "source_pdf",
        "status",
    ],
}
MONEY_COLUMNS = {
    "gross_sales",
    "delivery_fee",
    "commission_fee",
    "service_fee",
    "transaction_fee",
    "voucher",
    "platform_fees",
    "ads_fee",
    "estimated_order_income",
    "net_income",
    "net_amount",
    "merchandise_subtotal",
    "product_price",
    "shipping_subtotal",
    "shipping_fee_paid_by_buyer",
    "shipping_fee_charged_by_logistic_provider",
    "shipping_fee_rebate_from_shopee",
    "seller_paid_shipping_fee_sst",
    "vouchers_rebates_total",
    "voucher_amount",
    "fees_charges_total",
    "ads_escrow_top_up_fee",
    "order_income",
    "final_amount",
    "buyer_merchandise_subtotal",
    "buyer_shipping_fee",
    "shopee_voucher",
    "seller_voucher",
    "total_buyer_payment",
    "subtotal",
    "voucher_applied",
    "total",
    "shipping_fee",
    "net_paid",
    "invoice_amount",
    "discount",
    "unit_price",
    "line_total",
    "line_subtotal",
    "price",
    "paid_price",
    "line_total_inc_tax",
}
NUMERIC_COLUMNS = MONEY_COLUMNS | {"quantity"}
DATE_FORMATS_BY_PLATFORM = {
    "Shopee": {
        "order_created_date": "%d/%m/%Y %H:%M",
        "fund_transfer_date": "%d/%m/%Y",
        "delivered_date": "%d/%m/%Y %H:%M",
        "completed_date": "%d/%m/%Y %H:%M",
    },
    "Lazada": {
        "order_date": "%d %m %Y",
        "invoice_date": "%d %m %Y",
    },
    "ZENXIN": {
        "invoice_date": "%d/%m/%Y",
    },
}
DATE_COLUMNS = {
    "order_created_date",
    "fund_transfer_date",
    "delivered_date",
    "completed_date",
    "order_date",
    "invoice_date",
    "reporting_order_created_date",
    "processing_timestamp",
    "payout_completed_date",
}
PINNED_COLUMNS = {"platform", "order_id", "product_name", "seller_sku"}
REPORT_NAVIGATION_PAGES = ["Dashboard", "Cross Platform Summary", *PLATFORMS]
DEVELOPMENT_NAVIGATION_PAGES = [SETTLEMENT_TEST_LAB_PAGE]
NAVIGATION_ICONS = {
    DATA_IMPORT_PAGE: "upload_file",
    "Dashboard": "dashboard",
    "Cross Platform Summary": "inventory_2",
    "Shopee": "storefront",
    "Lazada": "shopping_bag",
    "ZENXIN": "language",
    SETTLEMENT_TEST_LAB_PAGE: "science",
}
PAGE_CONTROL_WIDGET_SUFFIXES = (
    "_order_filter",
    "_product_filter",
    "_platform_filter",
    "_from_date",
    "_to_date",
    "_optional_order_columns",
    "_optional_product_columns",
)


def preserve_page_control_state() -> None:
    for key in list(st.session_state.keys()):
        if key.endswith(PAGE_CONTROL_WIDGET_SUFFIXES):
            st.session_state[key] = st.session_state[key]



def reset_batch() -> None:
    current_uploader_version = int(st.session_state.get("uploader_version", 0))
    batch_keys = {
        "orders",
        "products",
        "reviews",
        "batch_id",
        "pdf_count",
        "upload_notice",
        "upload_result_summary",
        "duplicate_skipped",
        "unsupported_files",
        "processing_errors",
        "view_customize_open",
    }
    batch_widget_suffixes = (
        "_order_filter",
        "_product_filter",
        "_platform_filter",
        "_from_date",
        "_to_date",
        "_optional_order_columns",
        "_optional_product_columns",
        "_export_success",
        "_export",
        "_table",
    )
    for key in list(st.session_state.keys()):
        if key in batch_keys or key.startswith("pdf_uploader_") or key.endswith(batch_widget_suffixes):
            st.session_state.pop(key, None)
    reset_data_import_state()
    reset_settlement_test_lab_state()
    st.session_state.uploader_version = current_uploader_version + 1


def frame_with_columns(
    rows: list[dict],
    columns: list[str],
    missing_value: str | None = None,
) -> pd.DataFrame:
    dataframe = pd.DataFrame(rows)
    for column in columns:
        if column not in dataframe.columns:
            dataframe[column] = ""
    if missing_value is not None:
        for column in columns:
            dataframe[column] = dataframe[column].map(
                lambda value: missing_value if pd.isna(value) or str(value).strip() == "" else value
            )
    return dataframe


def _parse_display_date(series: pd.Series, expected_format: str | None = None) -> pd.Series:
    if expected_format:
        parsed = pd.to_datetime(series, format=expected_format, errors="coerce")
        if not parsed.isna().all() or series.isna().all():
            return parsed
    return pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=True)


def display_frame(
    dataframe: pd.DataFrame,
    columns: list[str],
    column_labels: dict[str, str] | None = None,
    platform_name: str | None = None,
) -> pd.DataFrame:
    visible = dataframe[columns].copy() if columns else dataframe.copy()
    for column in columns:
        if column in NUMERIC_COLUMNS:
            visible[column] = visible[column].map(display_numeric_value)
    if "quantity" in visible.columns:
        visible["quantity"] = pd.to_numeric(visible["quantity"], errors="coerce").astype("Int64")
    for column in MONEY_COLUMNS.intersection(visible.columns):
        visible[column] = pd.to_numeric(visible[column], errors="coerce")
    platform_formats = DATE_FORMATS_BY_PLATFORM.get(platform_name, {})
    for column in visible.columns:
        col_lower = str(column).lower()
        if column in platform_formats:
            visible[column] = _parse_display_date(visible[column], platform_formats[column])
        elif column in DATE_COLUMNS or "date" in col_lower or "timestamp" in col_lower:
            visible[column] = _parse_display_date(visible[column])
    return visible.rename(columns=column_labels or FIELD_LABELS)


def display_numeric_value(value: Any) -> Decimal | None:
    if value is None or pd.isna(value):
        return None

    text = str(value).strip()
    if text == "" or text.upper() == MISSING_VALUE_PLACEHOLDER:
        return None

    negative = text.startswith("(") and text.endswith(")")
    normalized = text.strip("()").replace(",", "").replace("RM", "").replace("rm", "").strip()
    if normalized == "":
        return None

    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return None
    return -number if negative else number


def pascal_case_label(column_name: str) -> str:
    if column_name == "payment_status":
        return FIELD_LABELS[column_name]
    return "".join(part.capitalize() for part in column_name.split("_") if part)


def pascal_case_frame(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.rename(columns={column: pascal_case_label(column) for column in dataframe.columns})


def public_review_frame(
    reviews: list[dict],
    *,
    include_payment_status: bool = False,
) -> pd.DataFrame:
    actionable_reviews = [review for review in reviews if is_manual_review_record(review)]
    dataframe = pd.DataFrame(actionable_reviews)
    if include_payment_status:
        dataframe["payment_status"] = [
            _review_payment_status(review) for review in actionable_reviews
        ]
    return dataframe.drop(columns=["order_payload", "product_payloads"], errors="ignore")


def _review_payment_status(review: dict) -> str:
    order_payload = review.get("order_payload")
    if not isinstance(order_payload, dict):
        return MISSING_VALUE_PLACEHOLDER
    payment_status = str(order_payload.get("payment_status", "")).strip()
    return payment_status or MISSING_VALUE_PLACEHOLDER


def reviews_for_platform(reviews: list[dict], platform_name: str) -> list[dict]:
    return [
        review
        for review in reviews
        if is_manual_review_record(review)
        and canonical_platform_label(review.get("platform")) == platform_name
    ]


def optional_columns(available_columns: list[str], required_columns: list[str]) -> list[str]:
    return [column for column in available_columns if column not in required_columns]


def selected_view_columns(
    available_columns: list[str],
    required_columns: list[str],
    selected_optional_columns: list[str],
) -> list[str]:
    required = [column for column in required_columns if column in available_columns]
    optional = [
        column
        for column in selected_optional_columns
        if column in available_columns and column not in required
    ]
    return required + optional


def dataframe_column_config(
    columns: list[str],
    column_labels: dict[str, str] | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for column in columns:
        label = (column_labels or FIELD_LABELS).get(column, column)
        col_lower = str(column).lower()
        if column == "product_name":
            config[label] = st.column_config.TextColumn(label, pinned=True, width="large")
        elif column == "platform":
            config[label] = st.column_config.TextColumn(label, pinned=True, width="small")
        elif column in {"quantity", "qty"} or col_lower.endswith("quantity") or col_lower.endswith("qty"):
            config[label] = st.column_config.NumberColumn(label, format="%d", width="small")
        elif column in MONEY_COLUMNS or col_lower.endswith("price") or col_lower.endswith("fee") or col_lower.endswith("subtotal") or col_lower.endswith("amount") or col_lower.endswith("total") or col_lower.endswith("income") or col_lower.endswith("discount"):
            if column not in {"income_type", "payment_method", "voucher_code", "voucher_funded_by", "voucher_type"} and not col_lower.endswith("quantity") and not col_lower.endswith("count"):
                config[label] = st.column_config.NumberColumn(label, format="RM %.2f", width="small", alignment="right")
        elif column in DATE_COLUMNS or "date" in col_lower or "timestamp" in col_lower or column in DATE_FORMATS_BY_PLATFORM.get(platform_name, {}):
            if "timestamp" in col_lower or column in {"order_created_date", "delivered_date", "completed_date"}:
                config[label] = st.column_config.DatetimeColumn(label, format="DD/MM/YYYY HH:mm", width="small")
            else:
                config[label] = st.column_config.DateColumn(label, format="DD/MM/YYYY", width="small")
        elif column in PINNED_COLUMNS:
            config[label] = st.column_config.TextColumn(label, pinned=True, width="medium")
        elif column in {"reason", "source_pdf", "all_review_reason"}:
            config[label] = st.column_config.TextColumn(label, width="large")
    return config


def show_data_table(
    dataframe: pd.DataFrame,
    columns: list[str],
    key: str,
    column_labels: dict[str, str] | None = None,
    available_columns: list[str] | None = None,
    height: int = 360,
    platform_name: str | None = None,
) -> None:
    table_columns = available_columns or columns
    labels = column_labels or FIELD_LABELS
    st.dataframe(
        display_frame(dataframe, table_columns, column_labels, platform_name),
        hide_index=True,
        column_order=[labels.get(column, column) for column in columns],
        column_config=dataframe_column_config(table_columns, column_labels, platform_name),
        key=key,
        height=height,
    )


def show_locked_columns(required_columns: list[str]) -> None:
    labels = ", ".join(FIELD_LABELS.get(column, column) for column in required_columns)
    st.caption(f"Always included: {labels}")


def mark_export_success(platform_name: str, export_scope: str) -> None:
    st.session_state[f"{platform_name}_export_success"] = export_scope


def show_table_section_heading(title: str, description: str) -> None:
    with st.container(gap="xxsmall"):
        st.subheader(title)
        st.caption(description)


def show_manual_review_metric(review_count: int, scope: str) -> None:
    tone = "warning" if review_count else "neutral"
    with st.container(key=f"manual-review-{tone}-{scope}", gap=None):
        st.metric("Manual Review", review_count, border=True)


def show_login() -> None:
    st.title("InvoiceGather")
    st.caption("ZENXIN invoice operations")
    with st.container(border=True):
        st.subheader("Sign in")
        with st.form("login_form", border=False):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", type="primary", icon=":material/login:")
            if submitted:
                if authenticate(username, password):
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Invalid username or password.", icon=":material/error:")
       


def request_batch_discard_confirmation() -> None:
    st.session_state.pending_batch_discard_confirmation = True


def _render_workflow_safety_dialogs() -> None:
    blocked = st.session_state.get("workflow_navigation_blocked")
    if blocked:
        _render_navigation_blocked_dialog(str(blocked.get("activity", "workflow operation")))
    if st.session_state.get("pending_batch_discard_confirmation"):
        _render_batch_discard_dialog()


@st.dialog("Processing in progress", icon=":material/info:")
def _render_navigation_blocked_dialog(activity: str) -> None:
    st.info(
        f"{activity} is still running. Navigation is temporarily unavailable until it completes.",
        icon=":material/info:",
    )
    if st.button("Stay on current page", type="primary", key="stay_during_workflow_activity"):
        st.session_state.pop("workflow_navigation_blocked", None)
        st.rerun()


@st.dialog("Discard current batch?", icon=":material/warning:")
def _render_batch_discard_dialog() -> None:
    st.warning("This removes the current session batch and its staging state. Archived source files are not changed.")
    with st.container(horizontal=True):
        if st.button("Discard current batch", type="primary", icon=":material/delete:", key="confirm_discard_current_batch"):
            reset_batch()
            st.session_state.pop("pending_batch_discard_confirmation", None)
            st.rerun()
        if st.button("Cancel", key="cancel_discard_current_batch"):
            st.session_state.pop("pending_batch_discard_confirmation", None)
            st.rerun()

def show_sidebar(pdf_count: int) -> str:
    with st.sidebar:
        st.html(
            """
            <style>
            section[data-testid="stSidebar"],
            section[data-testid="stSidebar"] > div:first-child {
                min-width: 15.5rem !important;
                width: 15.5rem !important;
                max-width: 15.5rem !important;
            }

            [data-testid="stMain"] [data-testid="stMetric"] {
                min-height: 0;
                padding: 0.55rem 0.72rem 0.62rem;
                border-color: #dfe8da;
                border-radius: 9px;
            }

            [data-testid="stMain"] [data-testid="stMetricLabel"] {
                color: #68756b;
                font-size: 0.78rem;
            }

            [class*="st-key-manual-review-warning-"] [data-testid="stMetricLabel"],
            [class*="st-key-manual-review-warning-"] [data-testid="stMetricValue"] {
                color: #a15c07;
            }

            [data-testid="stMain"] [data-testid="stTextInput"] input,
            [data-testid="stMain"] [data-baseweb="select"] > div {
                min-height: 2.25rem;
                border-radius: 8px;
            }

            [data-testid="stMain"] [data-testid="stCaptionContainer"] {
                color: #879087;
                font-size: 0.78rem;
            }

            section[data-testid="stSidebar"] h1 {
                font-size: 1.25rem;
                letter-spacing: -0.02em;
            }

            section[data-testid="stSidebar"] .sidebar-section-label {
                margin: 0.8rem 0 0.32rem;
                color: #77837b;
                font-size: 0.72rem;
                font-weight: 600;
                letter-spacing: 0.04em;
                line-height: 1.2;
                text-transform: uppercase;
            }

            section[data-testid="stSidebar"] .st-key-navigation {
                margin-top: -0.15rem;
                width: 100%;
            }

            section[data-testid="stSidebar"] .st-key-navigation [role="radiogroup"] {
                gap: 0.18rem;
                width: 100%;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"] {
                position: relative;
                width: 100%;
                min-height: 2.2rem;
                margin: 0;
                padding: 0.42rem 0.58rem;
                border: 1px solid transparent;
                border-radius: 9px;
                color: #445148;
                cursor: pointer;
                transition:
                    background-color 120ms ease,
                    border-color 120ms ease,
                    color 120ms ease;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"] > div:first-child {
                position: absolute;
                width: 1px;
                height: 1px;
                overflow: hidden;
                opacity: 0;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"] > div:last-child {
                width: 100%;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"] p {
                display: flex;
                align-items: center;
                gap: 0.58rem;
                margin: 0;
                font-size: 0.88rem;
                line-height: 1.2;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"] .material-symbols-rounded {
                color: currentColor;
                font-size: 1.05rem;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"]:hover {
                background: #f0f6ed;
                color: #244b32;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"]:has(input:checked) {
                background: #e4f1df;
                color: #14532d;
                font-weight: 600;
            }

            section[data-testid="stSidebar"] .st-key-navigation label[data-baseweb="radio"]:has(input:focus-visible) {
                outline: 2px solid #9db798;
                outline-offset: 1px;
            }

            section[data-testid="stSidebar"] [data-testid="stButton"] {
                margin-top: 0.08rem;
            }

            section[data-testid="stSidebar"] [data-testid="stButton"] button {
                min-height: 1.95rem;
                height: 1.95rem;
                padding: 0 0.58rem;
                border-color: #d2ddd0;
                background: transparent;
                box-shadow: none;
                font-size: 0.78rem;
                font-weight: 500;
            }

            section[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
                border-color: #96ac8d;
                background: #f7faf5;
                color: #14532d;
            }

            section[data-testid="stSidebar"] .sidebar-footnote {
            section[data-testid="stSidebar"] .sidebar-batch-summary {
                display: flex;
                align-items: center;
                gap: 0.42rem;
                margin: 0.08rem 0 0.18rem;
                padding: 0.38rem 0.48rem;
                border: 1px solid #e0e8dc;
                border-radius: 8px;
                color: #69766d;
                font-size: 0.78rem;
                line-height: 1.2;
            }

            section[data-testid="stSidebar"] .sidebar-batch-summary .material-symbols-rounded {
                color: #4a7650;
                font-size: 0.95rem;
            }
                margin: 0.4rem 0 0;
                color: #929d95;
                font-size: 0.68rem;
                line-height: 1.4;
            }
            </style>
            """
        )

        st.title("ZENXIN")
        st.caption("Invoice operations")

        navigation = st.session_state.get("navigation", DATA_IMPORT_PAGE)
        if navigation == "All Products":
            navigation = "Cross Platform Summary"
        if navigation not in {
            DATA_IMPORT_PAGE,
            *REPORT_NAVIGATION_PAGES,
            *DEVELOPMENT_NAVIGATION_PAGES,
        }:
            navigation = DATA_IMPORT_PAGE
        st.session_state.navigation = navigation
        def select_page(page: str) -> None:
            request_navigation(st.session_state, page)

        def render_navigation_section(label: str, pages: list[str]) -> None:
            st.html(f'<p class="sidebar-section-label">{label}</p>')
            for page in pages:
                st.button(
                    page,
                    icon=f":material/{NAVIGATION_ICONS[page]}:",
                    key=f"sidebar_navigation_{page.casefold().replace(' ', '_')}",
                    type="primary" if navigation == page else "secondary",
                    on_click=select_page,
                    args=(page,),
                    width="stretch",
                )

        render_navigation_section("ADMIN", [DATA_IMPORT_PAGE])
        render_navigation_section("REPORTS", REPORT_NAVIGATION_PAGES)
        render_navigation_section("DEVELOPMENT / TESTING", DEVELOPMENT_NAVIGATION_PAGES)

        st.html('<p class="sidebar-section-label">Current batch</p>')
        batch_id = st.session_state.get("batch_id")
        if batch_id:
            st.html(f'<div class="sidebar-batch-summary"><span class="material-symbols-rounded">picture_as_pdf</span>{pdf_count} PDFs processed</div>')
        else:
            st.html('<div class="sidebar-batch-summary">No active batch</div>')

        if st.button("Discard current batch", icon=":material/delete_outline:", width="stretch"):
            st.session_state.pending_batch_discard_confirmation = True
            st.rerun()

        if st.button("Logout", icon=":material/logout:", width="stretch"):
            st.session_state.authenticated = False
            reset_batch()
            st.rerun()

        st.html(
            '<p class="sidebar-footnote">'
            "Refresh, logout, or session end can clear unexported batch data."
            "</p>"
        )
        _render_workflow_safety_dialogs()
        return st.session_state.navigation


def show_upload_result_summary(summary: dict[str, int]) -> None:
    orders = st.session_state.get("orders", [])
    products = st.session_state.get("products", [])
    reviews = st.session_state.get("reviews", [])
    manual_review_count = sum(is_manual_review_record(review) for review in reviews)
    duplicate_count = len(st.session_state.get("duplicate_skipped", []))
    unsupported_count = len(st.session_state.get("unsupported_files", []))
    processing_error_count = len(st.session_state.get("processing_errors", []))

    with st.container(border=True):
        st.subheader("Result Summary")
        st.success("Processing complete", icon=":material/check_circle:")
        st.caption(
            f"Current batch. {int(summary.get('pdfs_processed', 0))} PDF(s) processed in the latest action."
        )
        with st.container(horizontal=True, gap="xsmall"):
            st.metric("PDFs", int(st.session_state.get("pdf_count", 0)), border=True)
            st.metric("Orders", len(orders), border=True)
            st.metric("Products", len(products), border=True)
            show_manual_review_metric(manual_review_count, "upload")

        skipped_parts = []
        if duplicate_count:
            skipped_parts.append(f"Duplicate skipped: {duplicate_count}")
        if unsupported_count:
            skipped_parts.append(f"Unsupported: {unsupported_count}")
        if skipped_parts:
            st.caption(" · ".join(skipped_parts))
        if processing_error_count:
            st.caption(f"Processing errors: {processing_error_count}. View details below.")
        if manual_review_count:
            st.warning(
                f"{manual_review_count} item(s) need manual checking before final use.",
                icon=":material/warning:",
            )




def show_batch_outcome_details() -> None:
    duplicate_orders = st.session_state.get("duplicate_skipped", [])
    unsupported_files = st.session_state.get("unsupported_files", [])
    processing_errors = st.session_state.get("processing_errors", [])

    if duplicate_orders or unsupported_files:
        with st.expander("View skipped items"):
            if duplicate_orders:
                st.markdown("**Duplicate Orders**")
                for item in duplicate_orders:
                    st.write(f"Order {item.get('order_id', MISSING_VALUE_PLACEHOLDER)}")
                    st.caption(
                        f"{item.get('platform', 'Unknown')} | Source: {item.get('source_pdf', '')} | "
                        f"{item.get('message', 'Already exists in current batch.')}"
                    )
            if unsupported_files:
                st.markdown("**Unsupported Files**")
                for item in unsupported_files:
                    st.write(item.get("filename", item.get("source_pdf", "Unknown file")))
                    st.caption(item.get("message", "Not recognized as a supported invoice."))

    if processing_errors:
        with st.expander("View processing errors"):
            for item in processing_errors:
                st.write(item.get("filename", item.get("source_pdf", "Unknown file")))
                st.caption(item.get("message", "File processing failed."))


def show_current_batch_outcomes() -> None:
    summary = st.session_state.get("upload_result_summary")
    if summary:
        show_upload_result_summary(summary)
    show_batch_outcome_details()

def show_upload_panel() -> list[Any] | None:
    if "uploader_version" not in st.session_state:
        st.session_state.uploader_version = 0

    upload_notice = st.session_state.pop("upload_notice", None)
    if upload_notice:
        level, message = upload_notice
        getattr(st, level)(message, icon=":material/check_circle:" if level == "success" else ":material/warning:")

    with st.container(border=True):
        st.subheader("Upload PDF or ZIP")
        st.caption("Upload mixed invoices. InvoiceGather detects each supported platform automatically.")
        uploaded_files = st.file_uploader(
            "Upload PDF or ZIP files",
            type=["pdf", "zip"],
            accept_multiple_files=True,
            key=f"pdf_uploader_{st.session_state.uploader_version}",
        )

        if uploaded_files:
            st.caption(f"{len(uploaded_files)} file(s) selected.")

        with st.container(horizontal=True, vertical_alignment="center"):
            process_clicked = st.button(
                "Process files",
                disabled=not uploaded_files,
                type="primary",
                icon=":material/play_arrow:",
            )
            clear_clicked = st.button(
                "Clear uploaded files",
                disabled=not uploaded_files,
                icon=":material/close:",
            )

        if clear_clicked:
            st.session_state.uploader_version += 1
            st.rerun()

        if uploaded_files and process_clicked:
            begin_workflow_activity(st.session_state, "Processing")
            try:
                process_uploads(uploaded_files)
            finally:
                end_workflow_activity(st.session_state)
    upload_result_summary = st.session_state.get("upload_result_summary")
    if upload_result_summary:
        show_upload_result_summary(upload_result_summary)
    show_batch_outcome_details()

    return uploaded_files


def process_uploads(uploaded_files: list[Any]) -> None:
    batch_id = st.session_state.get("batch_id") or create_batch_id()
    st.session_state.batch_id = batch_id
    existing_orders = st.session_state.get("orders", [])
    existing_products = st.session_state.get("products", [])
    existing_reviews = st.session_state.get("reviews", [])
    existing_pdf_count = st.session_state.get("pdf_count", 0)
    orders = list(existing_orders)
    products = list(existing_products)
    reviews = list(existing_reviews)
    duplicate_skipped = list(st.session_state.get("duplicate_skipped", []))
    unsupported_files = list(st.session_state.get("unsupported_files", []))
    processing_errors = list(st.session_state.get("processing_errors", []))
    archived_pdfs, preparation_outcomes = prepare_uploaded_invoice_files(uploaded_files, batch_id)
    action_summary = {
        "pdfs_processed": len(archived_pdfs),
        "orders_imported": 0,
        "manual_reviews": 0,
        "duplicate_orders": 0,
        "unsupported_files": 0,
        "processing_errors": 0,
    }

    for outcome in preparation_outcomes:
        if outcome.get("status") == "Unsupported":
            unsupported_files.append(outcome)
            action_summary["unsupported_files"] += 1
        else:
            processing_errors.append(outcome)
            action_summary["processing_errors"] += 1

    with st.status("Processing invoices", expanded=True) as status:
        st.write(f"Batch ID: {batch_id}")
        if archived_pdfs:
            progress = st.progress(0)
            for index, archived_pdf in enumerate(archived_pdfs, start=1):
                st.write(f"{index}/{len(archived_pdfs)} - {archived_pdf.source_pdf}")
                try:
                    file_result = process_pdf_file_with_outcome(
                        archived_pdf.source_pdf,
                        archived_pdf.archive_path,
                        batch_id,
                    )
                except Exception as exc:
                    processing_errors.append(
                        create_file_outcome_record(
                            source_pdf=archived_pdf.source_pdf,
                            status="Processing Error",
                            message=f"PDF processing failed: {exc}",
                        )
                    )
                    action_summary["processing_errors"] += 1
                    progress.progress(index / len(archived_pdfs))
                    continue

                unsupported_files.extend(file_result.unsupported_files)
                processing_errors.extend(file_result.processing_errors)
                action_summary["unsupported_files"] += len(file_result.unsupported_files)
                action_summary["processing_errors"] += len(file_result.processing_errors)

                append_result = append_batch_results_with_metadata(
                    orders,
                    products,
                    reviews,
                    file_result.orders,
                    file_result.products,
                    file_result.reviews,
                )
                orders = append_result.orders
                products = append_result.products
                reviews = append_result.reviews
                duplicate_skipped.extend(append_result.duplicate_orders)
                action_summary["orders_imported"] += append_result.imported_order_count
                action_summary["manual_reviews"] += append_result.manual_review_count
                action_summary["duplicate_orders"] += len(append_result.duplicate_orders)
                progress.progress(index / len(archived_pdfs))
        else:
            st.write("No PDF invoices were found in the selected upload(s).")

        st.session_state.orders = orders
        st.session_state.products = products
        st.session_state.reviews = reviews
        st.session_state.pdf_count = existing_pdf_count + len(archived_pdfs)
        st.session_state.duplicate_skipped = duplicate_skipped
        st.session_state.unsupported_files = unsupported_files
        st.session_state.processing_errors = processing_errors
        st.session_state.upload_result_summary = action_summary
        st.session_state.data_import_step = 3
        status.update(label="Processing complete", state="complete", expanded=False)

    st.session_state.uploader_version += 1
    st.rerun()


def show_overall_dashboard(orders: list[dict], products: list[dict], reviews: list[dict], pdf_count: int) -> None:
    dashboard = compute_overall_dashboard(orders, products, pdf_count)
    st.subheader("Current batch")
    with st.container(horizontal=True, gap="xsmall"):
        st.metric("PDFs", int(dashboard["pdf_count"]), border=True, icon=":material/picture_as_pdf:")
        st.metric("Orders", int(dashboard["order_count"]), border=True, icon=":material/receipt_long:")
        st.metric("Products", int(dashboard["product_rows"]), border=True, icon=":material/category:")
        st.metric("Quantity", int(dashboard["total_quantity"]), border=True, icon=":material/inventory_2:")
        st.metric("Income", f"RM {dashboard['income']}", border=True, icon=":material/payments:")


def apply_platform_filters(
    platform_name: str,
    order_df: pd.DataFrame,
    product_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    st.subheader("Search and Filters")
    if platform_name == "All":
        filter_col1, filter_col2, platform_col = st.columns([2, 2, 1], gap="small")
    else:
        filter_col1, filter_col2 = st.columns(2, gap="small")
        platform_col = None

    order_filter = filter_col1.text_input(
        "Order ID",
        value="",
        placeholder="Search order",
        key=f"{platform_name}_order_filter",
    ).strip()
    product_filter = filter_col2.text_input(
        "Product or SKU",
        value="",
        placeholder="Search product or SKU",
        key=f"{platform_name}_product_filter",
    ).strip()
    platform_filter = "All"
    if platform_col is not None:
        platform_filter = platform_col.selectbox(
            "Platform",
            ["All", *PLATFORMS],
            key=f"{platform_name}_platform_filter",
        )

    if order_filter:
        order_df = order_df[order_df["order_id"].astype(str).str.contains(order_filter, case=False, na=False)]
        product_df = product_df[product_df["order_id"].astype(str).str.contains(order_filter, case=False, na=False)]
    if product_filter:
        product_mask = product_df["product_name"].astype(str).str.contains(
            product_filter,
            case=False,
            na=False,
        ) | product_df["seller_sku"].astype(str).str.contains(product_filter, case=False, na=False)
        product_df = product_df[product_mask]
        matching_order_ids = set(product_df["order_id"].astype(str))
        order_df = order_df[order_df["order_id"].astype(str).isin(matching_order_ids)]
    if platform_filter != "All":
        order_df = order_df[order_df["platform"].astype(str) == platform_filter]
        product_df = product_df[product_df["platform"].astype(str) == platform_filter]

    return order_df, product_df


def show_platform_tab(
    platform_name: str,
    platform_orders: list[dict],
    platform_products: list[dict],
    platform_reviews: list[dict],
) -> None:
    title_col, export_col = st.columns([5, 2], vertical_alignment="center")
    title_col.title(platform_name)

    export_actions = export_col.container(horizontal_alignment="right", gap=None)
    dashboard = compute_platform_dashboard(platform_orders, platform_products)
    with st.container(horizontal=True, gap="xsmall"):
        st.metric("Orders", int(dashboard["orders"]), border=True)
        st.metric("Products", int(dashboard["products"]), border=True)
        st.metric("Quantity", int(dashboard["quantity"]), border=True)
        st.metric("Income", f"RM {dashboard['income']}", border=True)
        show_manual_review_metric(len(platform_reviews), platform_name.lower())

    if not (platform_orders or platform_products):
        export_actions.button(
            f"Export {platform_name}",
            disabled=True,
            icon=":material/download:",
            key=f"{platform_name}_export_disabled",
        )
        if platform_reviews:
            show_manual_review(
                platform_reviews,
                title="Manual Review",
                table_key=f"{platform_name}_manual_review_table",
                include_download=False,
                include_payment_status=platform_name == "Shopee",
            )
        else:
            st.caption(f"No {platform_name} data in this batch.")
        return

    full_batch_kpi = compute_platform_kpis(platform_orders, platform_products)
    platform_order_columns = PLATFORM_ORDER_FIELDS[platform_name]
    platform_product_columns = PLATFORM_PRODUCT_FIELDS[platform_name]
    order_optional_columns = optional_columns(platform_order_columns, ORDER_REQUIRED_COLUMNS)
    initial_order_optional_columns = [
        column
        for column in INITIAL_PLATFORM_ORDER_COLUMNS.get(platform_name, platform_order_columns)
        if column in order_optional_columns
    ]
    product_optional_columns = optional_columns(platform_product_columns, PRODUCT_REQUIRED_COLUMNS)
    selected_order_columns = selected_view_columns(
        platform_order_columns,
        ORDER_REQUIRED_COLUMNS,
        initial_order_optional_columns,
    )
    selected_product_columns = selected_view_columns(
        platform_product_columns,
        PRODUCT_REQUIRED_COLUMNS,
        product_optional_columns,
    )
    missing_value = MISSING_VALUE_PLACEHOLDER if platform_name == "Shopee" else None
    order_df = frame_with_columns(platform_orders, platform_order_columns, missing_value)
    product_display_rows = platform_products
    if platform_name == "Shopee":
        price_master, _ = _load_cross_platform_price_master()
        product_display_rows = build_shopee_product_level_rows(
            platform_products,
            price_master=price_master,
        )
    product_df = frame_with_columns(product_display_rows, platform_product_columns, missing_value)
    filtered_order_df, filtered_product_df = apply_platform_filters(platform_name, order_df, product_df)

    if platform_orders:
        show_table_section_heading(
            "Order Level",
            "Choose default order/export columns. The table toolbar can reveal every available order field.",
        )
        show_locked_columns(ORDER_REQUIRED_COLUMNS)
        selected_optional_order_columns = st.multiselect(
            "Order columns",
            options=order_optional_columns,
            default=initial_order_optional_columns,
            format_func=lambda column: FIELD_LABELS.get(column, column),
            key=f"{platform_name}_optional_order_columns",
            help="Required columns are always included in preview and export.",
        )
        selected_order_columns = selected_view_columns(
            platform_order_columns,
            ORDER_REQUIRED_COLUMNS,
            selected_optional_order_columns,
        )
        if filtered_order_df.empty:
            st.caption("No orders match the current filters.")
        else:
            show_data_table(
                filtered_order_df,
                selected_order_columns,
                key=f"{platform_name}_orders_table",
                available_columns=platform_order_columns,
                height=320,
                platform_name=platform_name,
            )

    if platform_products:
        show_table_section_heading(
            "Product Level",
            "Choose default product/export columns. The table toolbar can reveal every available product field.",
        )
        show_locked_columns(PRODUCT_REQUIRED_COLUMNS)
        selected_optional_product_columns = st.multiselect(
            "Product columns",
            options=product_optional_columns,
            default=product_optional_columns,
            format_func=lambda column: FIELD_LABELS.get(column, column),
            key=f"{platform_name}_optional_product_columns",
            help="Required columns are always included in preview and export.",
        )
        selected_product_columns = selected_view_columns(
            platform_product_columns,
            PRODUCT_REQUIRED_COLUMNS,
            selected_optional_product_columns,
        )
        if filtered_product_df.empty:
            st.caption("No products match the current filters.")
        else:
            show_data_table(
                filtered_product_df,
                selected_product_columns,
                key=f"{platform_name}_products_table",
                available_columns=platform_product_columns,
                height=420,
                platform_name=platform_name,
            )

    show_manual_review(
        platform_reviews,
        title="Manual Review",
        table_key=f"{platform_name}_manual_review_table",
        include_download=False,
        include_payment_status=platform_name == "Shopee",
    )
    show_platform_export(
        platform_name=platform_name,
        full_batch_kpi=full_batch_kpi,
        full_order_df=order_df,
        full_product_df=product_df,
        filtered_order_df=filtered_order_df,
        filtered_product_df=filtered_product_df,
        selected_order_columns=selected_order_columns,
        selected_product_columns=selected_product_columns,
        action_container=export_actions,
    )


def show_platform_export(
    platform_name: str,
    full_batch_kpi: dict[str, str | int],
    full_order_df: pd.DataFrame,
    full_product_df: pd.DataFrame,
    filtered_order_df: pd.DataFrame,
    filtered_product_df: pd.DataFrame,
    selected_order_columns: list[str],
    selected_product_columns: list[str],
    action_container: Any,
) -> None:
    export_dir = Path("exports")
    export_dir.mkdir(exist_ok=True)
    batch_id = st.session_state.get("batch_id", "batch")

    full_batch_path = export_dir / f"{batch_id}-{platform_name.lower()}-full-batch-report.xlsx"
    export_platform_report(
        destination=full_batch_path,
        platform_name=platform_name,
        summary=full_batch_kpi,
        orders=full_order_df.to_dict("records"),
        products=full_product_df.to_dict("records"),
        order_columns=selected_order_columns,
        product_columns=selected_product_columns,
        column_labels=FIELD_LABELS,
    )
    with open(full_batch_path, "rb") as file_data:
        action_container.download_button(
            f"Export full {platform_name} batch",
            file_data.read(),
            file_name=full_batch_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{platform_name}_full_batch_export",
            on_click=mark_export_success,
            args=(platform_name, "Full batch"),
            icon=":material/download:",
            type="primary",
            help="Exports every accepted order and product in the current platform batch, ignoring search filters.",
        )

    if filtered_order_df.empty and filtered_product_df.empty:
        action_container.button(
            "Export current filtered view",
            disabled=True,
            icon=":material/filter_alt:",
            key=f"{platform_name}_filtered_export_disabled",
            help="No orders or products match the current search filters.",
        )
    else:
        filtered_path = export_dir / f"{batch_id}-{platform_name.lower()}-filtered-view-report.xlsx"
        filtered_kpi = compute_platform_kpis(
            filtered_order_df.to_dict("records"),
            filtered_product_df.to_dict("records"),
        )
        export_platform_report(
            destination=filtered_path,
            platform_name=platform_name,
            summary=filtered_kpi,
            orders=filtered_order_df.to_dict("records"),
            products=filtered_product_df.to_dict("records"),
            order_columns=selected_order_columns,
            product_columns=selected_product_columns,
            column_labels=FIELD_LABELS,
        )
        with open(filtered_path, "rb") as file_data:
            action_container.download_button(
                "Export current filtered view",
                file_data.read(),
                file_name=filtered_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"{platform_name}_filtered_view_export",
                on_click=mark_export_success,
                args=(platform_name, "Filtered view"),
                icon=":material/filter_alt:",
                type="secondary",
                help="Exports only the orders and products currently matching the search filters.",
            )

    export_scope = st.session_state.pop(f"{platform_name}_export_success", None)
    if export_scope:
        st.toast(f"{export_scope} export is ready.", icon=":material/check_circle:")

def show_cross_platform_filters() -> tuple[str, date | None, date | None]:
    show_table_section_heading(
        "Filters",
        "Apply the same Platform, From Date, and To Date filters to Product Summary and All Products.",
    )
    platform_col, from_date_col, to_date_col = st.columns(3, gap="small")
    platform_filter = platform_col.selectbox(
        "Platform",
        ["All", *PLATFORMS],
        key="Cross Platform Summary_platform_filter",
    )
    start_date = from_date_col.date_input(
        "From Date",
        value=None,
        format="DD/MM/YYYY",
        key="Cross Platform Summary_from_date",
    )
    end_date = to_date_col.date_input(
        "To Date",
        value=None,
        format="DD/MM/YYYY",
        key="Cross Platform Summary_to_date",
    )
    if start_date is not None and end_date is not None and start_date > end_date:
        st.error("From Date must be on or before To Date.")
        return platform_filter, None, None
    return platform_filter, start_date, end_date

def show_cross_platform_summary_table(summary_rows: list[dict]) -> None:
    if not summary_rows:
        st.caption("No Seller SKU summary rows match the current filters.")
        return

    summary_df = frame_with_columns(summary_rows, CROSS_PLATFORM_SUMMARY_COLUMNS)
    summary_df["total_quantity"] = pd.to_numeric(summary_df["total_quantity"], errors="coerce").astype("Int64")
    for field in ("unit_selling_price", "total_selling_price", "total_discount_given"):
        summary_df[field] = pd.to_numeric(
            summary_df[field].map(lambda value: float(value) if isinstance(value, Decimal) else value),
            errors="coerce",
        )
    summary_labels = CROSS_PLATFORM_SUMMARY_FIELD_LABELS
    st.dataframe(
        summary_df[CROSS_PLATFORM_SUMMARY_COLUMNS].rename(columns=summary_labels),
        hide_index=True,
        key="cross_platform_product_summary_table",
        column_config={
            summary_labels["seller_sku"]: st.column_config.TextColumn(
                summary_labels["seller_sku"], pinned=True, width="medium"
            ),
            summary_labels["product_name"]: st.column_config.TextColumn(
                summary_labels["product_name"], pinned=True, width="large"
            ),
            summary_labels["unit_selling_price"]: st.column_config.NumberColumn(
                summary_labels["unit_selling_price"], format="RM %.2f", width="small", alignment="right"
            ),
            summary_labels["total_quantity"]: st.column_config.NumberColumn(
                summary_labels["total_quantity"], format="%d", width="small"
            ),
            summary_labels["total_selling_price"]: st.column_config.NumberColumn(
                summary_labels["total_selling_price"], format="RM %.2f", width="small", alignment="right"
            ),
            summary_labels["total_discount_given"]: st.column_config.NumberColumn(
                summary_labels["total_discount_given"], format="RM %.2f", width="small", alignment="right"
            ),
        },
        height=320,
    )


def show_cross_platform_summary_export(summary_rows: list[dict]) -> None:
    if not summary_rows:
        st.button(
            "Export Product Summary",
            disabled=True,
            icon=":material/download:",
            key="cross_platform_product_summary_export_disabled",
        )
        return

    export_dir = Path("exports")
    export_dir.mkdir(exist_ok=True)
    export_path = export_dir / (
        f"{st.session_state.get('batch_id', 'batch')}-product-summary-report.xlsx"
    )
    export_product_summary_report(
        destination=export_path,
        summary_rows=summary_rows,
        summary_columns=CROSS_PLATFORM_SUMMARY_COLUMNS,
        column_labels=CROSS_PLATFORM_SUMMARY_FIELD_LABELS,
    )
    with open(export_path, "rb") as file_data:
        st.download_button(
            "Export Product Summary",
            file_data.read(),
            file_name=export_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="cross_platform_product_summary_export",
            on_click=mark_export_success,
            args=("Cross Platform Summary", "Product Summary"),
            icon=":material/download:",
            type="primary",
            help="Exports the Product Summary currently matching the active Platform and date filters.",
        )
    if st.session_state.pop("Cross Platform Summary_export_success", False):
        st.toast("Product Summary export is ready.", icon=":material/check_circle:")

def show_cross_platform_summary_exclusions(
    exclusion_rows: list[dict],
    *,
    batch_id: str | None,
) -> None:
    if not exclusion_rows:
        return

    show_table_section_heading(
        "Excluded from Product Summary",
        "Manual Review orders with product-summary exclusion reason codes remain available in All Products and Manual Review.",
    )
    for index, row in enumerate(exclusion_rows):
        with st.container(border=True):
            details, source = st.columns([3, 2], gap="medium")
            details.markdown(f"**Order ID:** {row['order_id']}")
            details.caption(f"{row['platform']} | {row['reason']}")
            source_pdf = str(row.get("source_pdf") or "").strip()
            source.caption(f"Source PDF: {source_pdf or MISSING_VALUE_PLACEHOLDER}")
            archive_path = resolve_archived_pdf_path(batch_id, source_pdf)
            if archive_path is None:
                source.caption("View PDF unavailable: archived source was not found.")
            else:
                source.download_button(
                    "View PDF",
                    data=archive_path.read_bytes(),
                    file_name=archive_path.name,
                    mime="application/pdf",
                    key=f"cross_platform_summary_excluded_pdf_{index}",
                    icon=":material/picture_as_pdf:",
                )
def _load_cross_platform_price_master() -> tuple[ProductPriceMaster | None, str | None]:
    if not SHOPEE_PRODUCT_MASTER_PATH.is_file():
        return None, f"Shopee Product Master is unavailable at {SHOPEE_PRODUCT_MASTER_PATH.name}."
    try:
        return load_shopee_product_price_master(SHOPEE_PRODUCT_MASTER_PATH), None
    except (OSError, ValueError) as error:
        return None, f"Shopee Product Master could not be loaded: {error}"
def show_all_tab(orders: list[dict], products: list[dict], reviews: list[dict]) -> None:
    all_product_rows, all_review_rows = build_all_product_views(orders, products, reviews)
    price_master, price_master_message = _load_cross_platform_price_master()
    reporting_product_rows = build_cross_platform_product_rows(
        orders,
        products,
        reviews,
        price_master=price_master,
    )
    all_review_df = frame_with_columns(all_review_rows, [*ALL_PRODUCT_REVIEW_COLUMNS, "order_id"])
    st.title("Cross Platform Summary")

    product_summary_slot = st.empty()
    platform_filter, start_date, end_date = show_cross_platform_filters()
    filtered_product_rows = filter_cross_platform_product_rows(
        reporting_product_rows,
        platform=platform_filter,
        start_date=start_date,
        end_date=end_date,
    )
    summary_product_rows, summary_exclusion_rows = partition_cross_platform_product_summary_rows(
        reporting_product_rows,
        reviews,
    )
    filtered_summary_product_rows = filter_cross_platform_product_rows(
        summary_product_rows,
        platform=platform_filter,
        start_date=start_date,
        end_date=end_date,
    )
    filtered_summary_exclusion_rows = filter_cross_platform_product_rows(
        summary_exclusion_rows,
        platform=platform_filter,
        start_date=start_date,
        end_date=end_date,
    )

    with product_summary_slot.container():
        show_table_section_heading(
            "Product Summary",
            "Grouped by Seller SKU, Product Name, and available Variation. Product pricing uses the Shopee Product Master.",
        )
        product_summary_rows = summarize_cross_platform_products(filtered_summary_product_rows)
        show_cross_platform_summary_table(product_summary_rows)
        show_cross_platform_summary_export(product_summary_rows)
        if price_master_message:
            st.caption(price_master_message)
        incomplete_promotion_count = sum(
            row.get("reporting_pricing_status") == "promotion_evidence_incomplete"
            for row in filtered_summary_product_rows
        )
        if incomplete_promotion_count:
            st.caption(
                f"{incomplete_promotion_count} product row(s) have incomplete promotion evidence; unavailable pricing values remain N/A."
            )
        missing_sku_count = sum(
            str(row.get("seller_sku", "")).strip().upper() in {"", MISSING_VALUE_PLACEHOLDER}
            for row in filtered_summary_product_rows
        )
        if missing_sku_count:
            st.caption(
                f"{missing_sku_count} Product Summary-eligible row(s) without a Seller SKU remain in All Products but cannot be aggregated."
            )
        show_cross_platform_summary_exclusions(
            filtered_summary_exclusion_rows,
            batch_id=st.session_state.get("batch_id"),
        )

    if start_date is not None or end_date is not None:
        platform_rows = filter_cross_platform_product_rows(reporting_product_rows, platform=platform_filter)
        missing_date_count = sum(
            not isinstance(row.get("reporting_order_created_date"), date) for row in platform_rows
        )
        if missing_date_count:
            st.caption(
                f"{missing_date_count} eligible All Products row(s) without a valid Order Created Date are excluded while the date range is active."
            )

    show_table_section_heading(
        "All Products",
        "Cross-platform product rows eligible for All.",
    )
    product_df = frame_with_columns(filtered_product_rows, [*ALL_PRODUCT_COLUMNS, "order_id", "reporting_order_created_date"])
    product_df["reporting_order_created_date"] = pd.to_datetime(
        product_df["reporting_order_created_date"], errors="coerce"
    )
    if product_df.empty:
        st.caption("No products match the current filters.")
        st.button(
            "Export All Products",
            disabled=True,
            icon=":material/download:",
            key="All_export_disabled",
        )
    else:
        product_column_config = dataframe_column_config(
            CROSS_PLATFORM_PRODUCT_DISPLAY_COLUMNS, CROSS_PLATFORM_PRODUCT_DISPLAY_FIELD_LABELS
        )
        product_column_config[CROSS_PLATFORM_PRODUCT_DISPLAY_FIELD_LABELS["reporting_order_created_date"]] = (
            st.column_config.DateColumn("Order Created Date", format="DD/MM/YYYY", width="small")
        )
        st.dataframe(
            product_df[CROSS_PLATFORM_PRODUCT_DISPLAY_COLUMNS].rename(
                columns=CROSS_PLATFORM_PRODUCT_DISPLAY_FIELD_LABELS
            ),
            hide_index=True,
            key="all_products_table",
            column_config=product_column_config,
            height=420,
        )
        export_dir = Path("exports")
        export_dir.mkdir(exist_ok=True)
        export_path = export_dir / f"{st.session_state.get('batch_id', 'batch')}-all-products-report.xlsx"
        export_all_products_report(
            destination=export_path,
            products=product_df.to_dict("records"),
            product_columns=ALL_PRODUCT_COLUMNS,
            column_labels=ALL_PRODUCT_FIELD_LABELS,
        )
        with open(export_path, "rb") as file_data:
            st.download_button(
                "Export All Products",
                file_data.read(),
                file_name=export_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="All_export",
                on_click=mark_export_success,
                args=("All", "All Products"),
                icon=":material/download:",
                type="primary",
            )
        if st.session_state.pop("All_export_success", False):
            st.toast("Current view export is ready.", icon=":material/check_circle:")

    if not all_review_df.empty:
        show_table_section_heading(
            "All Manual Review",
            "Product rows that cannot form the All products view. This does not change their original Manual Review record.",
        )
        st.dataframe(
            all_review_df[ALL_PRODUCT_REVIEW_COLUMNS].rename(
                columns=ALL_PRODUCT_REVIEW_FIELD_LABELS
            ),
            hide_index=True,
            key="all_manual_review_products_table",
            column_config=dataframe_column_config(ALL_PRODUCT_REVIEW_COLUMNS, ALL_PRODUCT_REVIEW_FIELD_LABELS),
            height=320,
        )

def show_manual_review(
    reviews: list[dict],
    *,
    title: str = "Manual Review",
    table_key: str = "manual_review_table",
    include_download: bool = True,
    include_payment_status: bool = False,
) -> None:
    review_frame = public_review_frame(
        reviews,
        include_payment_status=include_payment_status,
    )
    if review_frame.empty:
        return

    show_table_section_heading(
        title,
        f"{len(review_frame)} item(s) need manual checking before they can be used in the final dataset.",
    )
    if "processing_timestamp" in review_frame.columns:
        review_frame["processing_timestamp"] = pd.to_datetime(review_frame["processing_timestamp"], errors="coerce")
    review_display_frame = pascal_case_frame(review_frame)
    review_column_config = {}
    for col in review_display_frame.columns:
        col_lower = str(col).lower()
        if "timestamp" in col_lower or "date" in col_lower:
            review_column_config[col] = st.column_config.DatetimeColumn(col, format="DD/MM/YYYY HH:mm", width="medium")
        elif "price" in col_lower or "amount" in col_lower or "fee" in col_lower or "income" in col_lower:
            review_column_config[col] = st.column_config.NumberColumn(col, format="RM %.2f", width="small", alignment="right")
        elif "quantity" in col_lower or "qty" in col_lower:
            review_column_config[col] = st.column_config.NumberColumn(col, format="%d", width="small")

    st.dataframe(
        review_display_frame,
        hide_index=True,
        key=table_key,
        column_config=review_column_config,
        height=320,
    )
    if not include_download:
        return
    review_path = Path("exports") / f"{st.session_state.get('batch_id', 'batch')}-reviews.xlsx"
    export_review_report(review_display_frame.to_dict("records"), review_path)
    with open(review_path, "rb") as review_data:
        st.download_button(
            "Download review report",
            review_data.read(),
            file_name=review_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
        )


if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    show_login()
    st.stop()

orders = st.session_state.get("orders", [])
products = st.session_state.get("products", [])
reviews = st.session_state.get("reviews", [])
pdf_count = st.session_state.get("pdf_count", 0)

preserve_page_control_state()
initialize_data_import_state()
selected_page = show_sidebar(pdf_count)

if selected_page == DATA_IMPORT_PAGE:
    render_data_import(
        render_platform_orders_upload=show_upload_panel,
        render_platform_orders_outcomes=show_current_batch_outcomes,
        discard_current_batch=request_batch_discard_confirmation,
    )
elif selected_page == SETTLEMENT_TEST_LAB_PAGE:
    render_settlement_test_lab()
elif selected_page == "Dashboard":
    st.title("Dashboard")
    st.caption("Current active-batch reporting view. Import and validation remain in Data Import.")
    show_overall_dashboard(orders, products, reviews, pdf_count)
elif selected_page == "Cross Platform Summary":
    show_all_tab(orders, products, reviews)
else:
    orders_by_platform = split_by_platform(orders)
    products_by_platform = split_by_platform(products)
    show_platform_tab(
        selected_page,
        orders_by_platform.get(selected_page, []),
        products_by_platform.get(selected_page, []),
        reviews_for_platform(reviews, selected_page),
    )
