"""UAT2 Weekly Billing workspace with Phase 3 historical Invoice intake."""

from __future__ import annotations

import streamlit as st

from src.invoice_app.services.workflow_navigation import request_navigation
from src.invoice_app.ui.data_import import DATA_IMPORT_PAGE


WEEKLY_BILLING_PAGE = "Weekly Billing"
WEEKLY_BILLING_TABS = (
    "Overview",
    "Invoice Intake",
    "Weekly Statement",
    "Missing Sources",
    "Verification",
    "Billing Preview",
    "Export",
)


def render_weekly_billing() -> None:
    """Render UAT2; storage is accessed only by explicit Invoice Intake actions."""
    st.title(WEEKLY_BILLING_PAGE)
    st.caption("Statement-driven weekly billing workspace for Shopee UAT2.")
    st.info(
        "Historical Invoice documents are processed through Data Import. "
        "The remaining billing workflow is delivered in later UAT2 phases.",
        icon=":material/info:",
    )

    (
        overview_tab,
        invoice_intake_tab,
        weekly_statement_tab,
        missing_sources_tab,
        verification_tab,
        billing_preview_tab,
        export_tab,
    ) = st.tabs(WEEKLY_BILLING_TABS)

    with overview_tab:
        _render_overview()
    with invoice_intake_tab:
        _render_invoice_intake()
    with weekly_statement_tab:
        _render_weekly_statement()
    with missing_sources_tab:
        _render_missing_sources()
    with verification_tab:
        _render_verification()
    with billing_preview_tab:
        _render_billing_preview()
    with export_tab:
        _render_export()


def _render_overview() -> None:
    st.subheader("Billing readiness")
    st.caption("Readiness will be evaluated after later UAT2 intake and verification phases.")
    first_row = st.container(horizontal=True, gap="small")
    with first_row:
        st.metric("Statement Orders", "—", border=True)
        st.metric("Invoice Source Found", "—", border=True)
        st.metric("Missing Source", "—", border=True)
        st.metric("Order Verification", "Not available yet", border=True)
    second_row = st.container(horizontal=True, gap="small")
    with second_row:
        st.metric("Refund Verification", "Not available yet", border=True)
        st.metric("Product Reconciliation", "Not available yet", border=True)
        st.metric("Merchandise Difference", "—", border=True)
        st.metric("Billing Status", "Not evaluated", border=True)

    with st.container(border=True):
        st.subheader("Planned workflow")
        st.markdown(
            "1. Invoice source intake\n"
            "2. Weekly Statement\n"
            "3. Source coverage\n"
            "4. Invoice ↔ Statement verification\n"
            "5. Billing preview\n"
            "6. Readiness gate\n"
            "7. Export"
        )


def _render_invoice_intake() -> None:
    with st.container(border=True):
        st.subheader("Historical Invoice Sources")
        st.write(
            "Historical Invoice PDFs are processed and committed through Data Import. "
            "Weekly Billing uses persisted Shopee historical sources."
        )
        if st.button("Go to Data Import", icon=":material/arrow_forward:", key="uat2_go_to_data_import"):
            if request_navigation(st.session_state, DATA_IMPORT_PAGE):
                st.rerun()


def _render_weekly_statement() -> None:
    with st.container(border=True):
        st.subheader("Weekly Statement")
        st.write(
            "Weekly Statement upload and persistence will be connected in Phase 4. "
            "The statement will define target Order IDs for one payout or settlement period."
        )
        st.caption("No Statement file is read, parsed, or persisted here.")


def _render_missing_sources() -> None:
    with st.container(border=True):
        st.subheader("Source coverage")
        st.write(
            "Statement target Order IDs → historical Invoice lookup → Found / Missing → "
            "request missing Invoice PDFs → reload/recheck."
        )
        st.caption("Coverage has not been calculated yet.")


def _render_verification() -> None:
    st.subheader("Future verification")
    for label in (
        "Source Coverage",
        "Order Verification",
        "Refund Verification",
        "Product Reconciliation",
        "Financial Reconciliation",
    ):
        with st.container(border=True):
            st.write(label)
            st.caption("Not available yet")


def _render_billing_preview() -> None:
    with st.container(border=True):
        st.subheader("Consolidated product output")
        st.caption("Seller SKU · Product Name · Variation · Total Quantity · Unit Price · Total Sold Amount")
        st.write("No billing rows are available until later UAT2 verification phases.")
    with st.container(border=True):
        st.subheader("Future field authority")
        st.markdown(
            "- **Total Quantity** = reliable Invoice quantity\n"
            "- **Unit Price** = Product Master normal/POS Unit Price\n"
            "- **Total Sold Amount** = Weekly Statement SKU net sold amount"
        )
    with st.container(border=True):
        st.subheader("Weekly Statement financial summary")
        st.caption("Not available yet; no financial values are calculated in this shell.")


def _render_export() -> None:
    with st.container(border=True):
        st.subheader("Consolidated billing export")
        st.write("Export becomes available only after all required billing readiness gates pass.")
        st.button(
            "Export Billing Dataset",
            icon=":material/download:",
            disabled=True,
            key="weekly_billing_export_disabled",
        )
