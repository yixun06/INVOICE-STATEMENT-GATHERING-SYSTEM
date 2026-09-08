"""UAT2 Weekly Billing workspace with Phase 3 historical Invoice intake."""

from __future__ import annotations

import streamlit as st

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import HistoricalInvoiceStorageError
from src.invoice_app.repositories.historical_invoice_repository import HistoricalInvoiceBulkImportError
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeUpload,
    classify_staging,
    import_new_staging,
    process_and_classify_uploads,
    remove_staging_entry,
)
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings


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
_INTAKE_ENTRIES_KEY = "uat2_invoice_intake_entries"
_INTAKE_REFRESH_REQUIRED_KEY = "uat2_invoice_intake_refresh_required"


def render_weekly_billing() -> None:
    """Render UAT2; storage is accessed only by explicit Invoice Intake actions."""
    st.title(WEEKLY_BILLING_PAGE)
    st.caption("Statement-driven weekly billing workspace for Shopee UAT2.")
    st.info(
        "Invoice Intake supports preview-before-write historical Shopee invoices. "
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
        st.subheader("Historical Invoice intake")
        st.caption(
            "Upload Shopee Invoice PDFs, process a no-write preview, then explicitly import only New invoices."
        )
        uploaded_files = st.file_uploader(
            "Upload Shopee Invoice PDFs",
            type="pdf",
            accept_multiple_files=True,
            key="uat2_invoice_intake_uploads",
        )
        if st.button("Process for preview", icon=":material/preview:", key="uat2_invoice_intake_preview"):
            if not uploaded_files:
                st.warning("Select one or more Shopee Invoice PDFs before processing.")
            else:
                try:
                    repository = configured_uat2_data_settings().create_repository()
                    st.session_state[_INTAKE_ENTRIES_KEY] = process_and_classify_uploads(
                        (InvoiceIntakeUpload(source_filename=file.name, content=file.getvalue()) for file in uploaded_files),
                        repository,
                    )
                    st.session_state[_INTAKE_REFRESH_REQUIRED_KEY] = False
                except HistoricalInvoiceStorageError as error:
                    st.error(f"Historical Invoice storage is unavailable: {error}")

    entries = tuple(st.session_state.get(_INTAKE_ENTRIES_KEY, ()))
    if not entries:
        st.button(
            "Import Accepted Invoices",
            icon=":material/upload:",
            type="primary",
            disabled=True,
            key="uat2_invoice_intake_import",
        )
        return
    _render_intake_preview(entries)


def _render_intake_preview(entries) -> None:
    counts = {status: sum(entry.status is status for entry in entries) for status in IntakeStatus}
    with st.container(horizontal=True, gap="small"):
        st.metric("Selected PDFs", len({entry.source_hash for entry in entries}), border=True)
        st.metric("New", counts[IntakeStatus.NEW], border=True)
        st.metric("Already Imported", counts[IntakeStatus.ALREADY_IMPORTED], border=True)
        st.metric("Needs Review", counts[IntakeStatus.NEEDS_REVIEW], border=True)
        st.metric("Source Conflict", counts[IntakeStatus.SOURCE_CONFLICT], border=True)
        if counts[IntakeStatus.IMPORTED]:
            st.metric("Imported", counts[IntakeStatus.IMPORTED], border=True)

    with st.container(border=True):
        st.subheader("Preview results")
        st.dataframe(
            [
                {
                    "Source PDF": entry.source_filename,
                    "Order ID": entry.order_id or "N/A",
                    "Status": entry.status.value.replace("_", " ").title(),
                    "Reason / message": entry.message or "Ready for explicit import.",
                }
                for entry in entries
            ],
            hide_index=True,
        )
        for entry in entries:
            if entry.status in {IntakeStatus.NEEDS_REVIEW, IntakeStatus.SOURCE_CONFLICT}:
                if st.button(
                    f"Remove {entry.source_filename} ({entry.order_id or 'no Order ID'})",
                    icon=":material/remove_circle:",
                    key=f"uat2_invoice_intake_remove_{entry.staging_id}",
                ):
                    st.session_state[_INTAKE_ENTRIES_KEY] = remove_staging_entry(entries, entry.staging_id)
                    st.rerun()

    refresh_required = bool(st.session_state.get(_INTAKE_REFRESH_REQUIRED_KEY, False))
    if refresh_required:
        st.warning("A storage write was interrupted. Refresh status before attempting another import.")
        if st.button("Refresh Status", icon=":material/refresh:", key="uat2_invoice_intake_refresh"):
            try:
                repository = configured_uat2_data_settings().create_repository()
                repository.refresh()
                st.session_state[_INTAKE_ENTRIES_KEY] = classify_staging(entries, repository)
                st.session_state[_INTAKE_REFRESH_REQUIRED_KEY] = False
                st.rerun()
            except HistoricalInvoiceStorageError as error:
                st.error(f"Historical Invoice storage refresh failed: {error}")

    new_entries = [entry for entry in entries if entry.status is IntakeStatus.NEW and entry.bundle is not None]
    if st.button(
        "Import Accepted Invoices",
        icon=":material/upload:",
        type="primary",
        disabled=not new_entries or refresh_required,
        key="uat2_invoice_intake_import",
    ):
        try:
            repository = configured_uat2_data_settings().create_repository()
            outcome = import_new_staging(entries, repository)
            st.session_state[_INTAKE_ENTRIES_KEY] = outcome.entries
            st.success(f"Imported {len(new_entries)} historical Invoice bundle(s) in chunks: {outcome.bulk_result.chunk_sizes}.")
        except HistoricalInvoiceBulkImportError as error:
            st.session_state[_INTAKE_REFRESH_REQUIRED_KEY] = True
            confirmed_order_ids = ", ".join(result.order_id for result in error.confirmed_results) or "none"
            pending_order_ids = ", ".join(order_id for _platform, order_id in error.pending_identities) or "none"
            st.error(
                "Historical Invoice write was interrupted after "
                f"{len(error.confirmed_results)} confirmed bundle(s). Confirmed Order IDs: {confirmed_order_ids}. "
                f"Pending Order IDs: {pending_order_ids}. Refresh Status before retrying."
            )
        except HistoricalInvoiceStorageError as error:
            st.error(f"Historical Invoice storage write failed: {error}")


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
