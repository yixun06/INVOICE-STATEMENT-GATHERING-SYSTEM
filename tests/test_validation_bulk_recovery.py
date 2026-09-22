from hashlib import sha256
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.services.historical_invoice_intake import IntakeStatus, InvoiceIntakeEntry
from src.invoice_app.services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
)
from src.invoice_app.services.validation_recovery import (
    REMOVE_STAGED_SOURCE,
    execute_current_batch_bulk_recovery,
    execute_current_batch_recovery,
    plan_already_imported_source_removal,
    plan_duplicate_source_removal,
    recovery_actions_for_source,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _state():
    return {
        "orders": [
            {"platform": "Shopee", "order_id": "GOOD", "source_pdf": "good.pdf", "status": "Accepted"},
            {"platform": "Shopee", "order_id": "BAD-1", "source_pdf": "bad-1.pdf", "status": "Accepted"},
            {"platform": "Shopee", "order_id": "BAD-2", "source_pdf": "bad-2.pdf", "status": "Accepted"},
        ],
        "products": [
            {"platform": "Shopee", "order_id": "GOOD", "source_pdf": "good.pdf", "status": "Accepted"},
            {"platform": "Shopee", "order_id": "BAD-1", "source_pdf": "bad-1.pdf", "status": "Accepted"},
            {"platform": "Shopee", "order_id": "BAD-2", "source_pdf": "bad-2.pdf", "status": "Accepted"},
        ],
        "reviews": [
            {"platform": "Shopee", "order_id": "BAD-1", "source_pdf": "bad-1.pdf", "status": "Manual Review"},
            {"platform": "Shopee", "order_id": "BAD-2", "source_pdf": "bad-2.pdf", "status": "Manual Review"},
        ],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
        "upload_result_summary": {"pdfs_processed": 3},
        "uat2_historical_commit_entries": ("stale",),
        "uat2_historical_commit_refresh_required": True,
        "uat2_historical_commit_signature": "stale",
    }


def test_bulk_recovery_removes_only_selected_current_batch_sources_and_revalidates_once():
    state = _state()

    execution = execute_current_batch_bulk_recovery(
        state,
        ["bad-1.pdf", "bad-2.pdf", "bad-1.pdf"],
    )

    assert execution.changed is True
    assert execution.revalidated is True
    assert execution.removed_counts == {
        "orders": 2,
        "products": 2,
        "reviews": 2,
        "duplicate_skipped": 0,
        "unsupported_files": 0,
        "processing_errors": 0,
    }
    assert [record["source_pdf"] for record in state["orders"]] == ["good.pdf"]
    # Revalidation applies the existing product eligibility rules after the
    # selected sources are removed; these minimal fixture products are not
    # eligible to remain as accepted product rows.
    assert state["products"] == []
    assert all(
        record.get("source_pdf") not in {"bad-1.pdf", "bad-2.pdf"}
        for bucket in ("orders", "products", "reviews", "duplicate_skipped", "unsupported_files", "processing_errors")
        for record in state[bucket]
    )
    assert "uat2_historical_commit_entries" not in state
    assert "uat2_historical_commit_refresh_required" not in state
    assert "uat2_historical_commit_signature" not in state


def _historical_entry(source, order_id, status):
    return InvoiceIntakeEntry(
        staging_id=f"{source}:{order_id}",
        source_filename=source,
        source_hash="a" * 64,
        order_id=order_id,
        status=status,
        message=None,
        bundle=None,
    )


def test_already_imported_bulk_recovery_removes_only_safe_sources_from_session():
    state = {
        "orders": [
            {"platform": "Shopee", "status": "Accepted", "source_pdf": "stored-1.pdf", "order_id": "STORED-1"},
            {"platform": "Shopee", "status": "Accepted", "source_pdf": "stored-2.pdf", "order_id": "STORED-2"},
            {"platform": "Shopee", "status": "Accepted", "source_pdf": "new-1.pdf", "order_id": "NEW-1"},
        ],
        "products": [
            {"source_pdf": "stored-1.pdf", "order_id": "STORED-1"},
            {"source_pdf": "stored-2.pdf", "order_id": "STORED-2"},
            {"source_pdf": "new-1.pdf", "order_id": "NEW-1"},
        ],
        "reviews": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
        "uat2_historical_commit_entries": ("stale",),
        "uat2_historical_commit_refresh_required": False,
        "uat2_historical_commit_signature": "stale",
        "historical_partial_import_failure": {"confirmed_count": 2},
    }
    entries = (
        _historical_entry("stored-1.pdf", "STORED-1", IntakeStatus.ALREADY_IMPORTED),
        _historical_entry("stored-2.pdf", "STORED-2", IntakeStatus.ALREADY_IMPORTED),
        _historical_entry("new-1.pdf", "NEW-1", IntakeStatus.NEW),
    )

    plan = plan_already_imported_source_removal(state, entries)
    execution = execute_current_batch_bulk_recovery(state, plan.safe_sources)

    assert plan.safe_sources == ("stored-1.pdf", "stored-2.pdf")
    assert plan.retained_sources == ()
    assert execution.changed is True
    assert [row["order_id"] for row in state["orders"]] == ["NEW-1"]
    assert "historical_partial_import_failure" not in state
    assert "uat2_historical_commit_entries" not in state

    repeated = execute_current_batch_bulk_recovery(state, plan.safe_sources)
    assert repeated.changed is False
    assert [row["order_id"] for row in state["orders"]] == ["NEW-1"]


@pytest.mark.parametrize(
    "unsafe_status",
    (IntakeStatus.NEW, IntakeStatus.NEEDS_REVIEW, IntakeStatus.SOURCE_CONFLICT),
)
def test_already_imported_planner_retains_mixed_status_source(unsafe_status):
    state = {
        "orders": [
            {"source_pdf": "mixed.pdf", "order_id": "STORED"},
            {"source_pdf": "mixed.pdf", "order_id": "UNSAFE"},
        ],
        "products": [],
        "reviews": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
    }
    entries = (
        _historical_entry("mixed.pdf", "STORED", IntakeStatus.ALREADY_IMPORTED),
        _historical_entry("mixed.pdf", "UNSAFE", unsafe_status),
    )

    plan = plan_already_imported_source_removal(state, entries)

    assert plan.safe_sources == ()
    assert plan.retained_sources == ("mixed.pdf",)
    assert len(state["orders"]) == 2


def test_already_imported_planner_retains_source_with_review_sibling():
    state = {
        "orders": [{"source_pdf": "mixed.pdf", "order_id": "STORED"}],
        "products": [],
        "reviews": [{"source_pdf": "mixed.pdf", "order_id": "REVIEW"}],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
    }
    entries = (
        _historical_entry("mixed.pdf", "STORED", IntakeStatus.ALREADY_IMPORTED),
    )

    plan = plan_already_imported_source_removal(state, entries)

    assert plan.safe_sources == ()
    assert plan.retained_sources == ("mixed.pdf",)


def test_duplicate_only_source_is_safe_for_whole_source_removal():
    state = {
        "orders": [],
        "products": [],
        "reviews": [],
        "duplicate_skipped": [
            {"source_pdf": "duplicate.pdf", "order_id": "DUP-1"},
        ],
        "unsupported_files": [],
        "processing_errors": [],
    }

    plan = plan_duplicate_source_removal(state, ["duplicate.pdf"])

    assert plan.safe_sources == ("duplicate.pdf",)
    assert plan.retained_sources == ()
    execute_current_batch_bulk_recovery(state, plan.safe_sources)
    assert state["duplicate_skipped"] == []


def test_mixed_duplicate_source_is_retained_with_its_new_siblings():
    state = {
        "orders": [
            {"source_pdf": "multi.pdf", "order_id": "ORDER-A", "status": "Accepted"},
            {"source_pdf": "multi.pdf", "order_id": "ORDER-C", "status": "Accepted"},
        ],
        "products": [
            {"source_pdf": "multi.pdf", "order_id": "ORDER-A", "status": "Accepted"},
            {"source_pdf": "multi.pdf", "order_id": "ORDER-C", "status": "Accepted"},
        ],
        "reviews": [],
        "duplicate_skipped": [
            {"source_pdf": "multi.pdf", "order_id": "ORDER-B", "status": "Duplicate Skipped"},
        ],
        "unsupported_files": [],
        "processing_errors": [],
    }

    plan = plan_duplicate_source_removal(state, ["multi.pdf"])

    assert plan.safe_sources == ()
    assert plan.retained_sources == ("multi.pdf",)
    assert [item["order_id"] for item in state["orders"]] == ["ORDER-A", "ORDER-C"]
    assert state["duplicate_skipped"][0]["order_id"] == "ORDER-B"


def test_source_conflict_is_never_a_safe_duplicate_removal_target():
    state = {
        "orders": [],
        "products": [],
        "reviews": [
            {"source_pdf": "conflict.pdf", "status": "SOURCE_CONFLICT"},
        ],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
    }

    plan = plan_duplicate_source_removal(state, ["conflict.pdf"])

    assert plan.safe_sources == ()
    assert plan.retained_sources == ("conflict.pdf",)


def test_weekly_statement_duplicate_removal_uses_staged_source_action():
    state = {
        "weekly_statement_stage": object(),
        "weekly_statement_review": object(),
        "weekly_statement_review_stale_reason": "Refresh required",
        "weekly_statement_issue_order_id": "ORDER-1",
        "weekly_statement_uploader_version": 3,
        "orders": [{"order_id": "PERSISTED-INVOICE"}],
        "products": [{"order_id": "PERSISTED-INVOICE", "item_index": 0}],
        "reviews": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "processing_errors": [],
    }
    action = recovery_actions_for_source(
        source="statement.xlsx",
        action_type=REMOVE_STAGED_SOURCE,
        remove_label="Remove staged duplicate statement from current batch",
        include_details=False,
    )[0]

    execution = execute_current_batch_recovery(state, action)

    assert execution.changed is True
    for key in (
        "weekly_statement_stage",
        "weekly_statement_review",
        "weekly_statement_review_stale_reason",
        "weekly_statement_issue_order_id",
    ):
        assert key not in state
    assert state["weekly_statement_uploader_version"] == 4
    assert state["orders"] == [{"order_id": "PERSISTED-INVOICE"}]
    assert state["products"] == [
        {"order_id": "PERSISTED-INVOICE", "item_index": 0}
    ]


def test_validate_weekly_duplicate_removal_clears_the_staged_statement(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    stage = StagedShopeeWeeklyStatement(
        result="NEEDS_REVIEW",
        source_filename="duplicate-statement.xlsx",
        file_hash="hash",
        statement=None,
        validation_issues=(),
        review_reasons=(),
        rejection_reasons=(),
        duplicate_status="ALREADY_STAGED",
        order_reconciliations=(),
        adjustment_reconciliations=(),
    )
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "duplicate-statement-batch",
        "import_source_type": "Shopee Weekly Statement",
        "data_import_step": 3,
        "weekly_statement_stage": stage,
    }.items():
        app.session_state[key] = value
    app.run(timeout=20)

    next(
        button
        for button in app.button
        if button.label == "Remove staged duplicate statement from current batch"
    ).click().run(timeout=20)
    next(
        button
        for button in app.button
        if button.label == "Remove Statement"
    ).click().run(timeout=20)

    assert app.exception == []
    assert "weekly_statement_stage" not in app.session_state.filtered_state


def test_validate_duplicate_only_source_offers_safe_removal_and_keeps_audit_expander(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "duplicate-only-batch",
        "import_source_type": "Platform Orders",
        "data_import_step": 3,
        "upload_result_summary": {"pdfs_processed": 1},
        "orders": [{"source_pdf": "good.pdf", "order_id": "GOOD", "status": "Accepted"}],
        "products": [],
        "reviews": [],
        "processing_errors": [],
        "unsupported_files": [],
        "duplicate_skipped": [
            {"source_pdf": "duplicate.pdf", "order_id": "DUP-1", "status": "Duplicate Skipped"},
        ],
    }.items():
        app.session_state[key] = value
    app.run(timeout=20)

    assert "Original issue evidence" in {item.label for item in app.expander}
    next(
        button
        for button in app.button
        if button.label == "Remove 1 safe duplicate source(s)"
    ).click().run(timeout=20)
    next(
        button
        for button in app.button
        if button.label == "Remove 1 Source"
    ).click().run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["duplicate_skipped"] == []


def test_validate_mixed_duplicate_source_has_no_whole_source_removal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "mixed-duplicate-batch",
        "import_source_type": "Platform Orders",
        "data_import_step": 3,
        "upload_result_summary": {"pdfs_processed": 1},
        "orders": [
            {"source_pdf": "multi.pdf", "order_id": "ORDER-A", "status": "Accepted"},
            {"source_pdf": "multi.pdf", "order_id": "ORDER-C", "status": "Accepted"},
        ],
        "products": [],
        "reviews": [],
        "processing_errors": [],
        "unsupported_files": [],
        "duplicate_skipped": [
            {"source_pdf": "multi.pdf", "order_id": "ORDER-B", "status": "Duplicate Skipped"},
        ],
    }.items():
        app.session_state[key] = value
    app.run(timeout=20)

    assert app.exception == []
    assert not any("safe duplicate source" in button.label for button in app.button)
    assert any("also contain valid or reviewable orders" in item.value for item in app.caption)
    assert [item["order_id"] for item in app.session_state.filtered_state["orders"]] == ["ORDER-A", "ORDER-C"]


def test_manual_review_csv_downloads_remain_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    for key, value in {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "manual-review-csv-batch",
        "import_source_type": "Platform Orders",
        "data_import_step": 3,
        "upload_result_summary": {"pdfs_processed": 1},
        "orders": [],
        "products": [],
        "processing_errors": [],
        "unsupported_files": [],
        "duplicate_skipped": [],
        "reviews": [
            {"source_pdf": "reupload.pdf", "order_id": "REUPLOAD", "status": "Manual Review", "reason": "Source evidence required"},
            {"source_pdf": "online.pdf", "order_id": "ONLINE", "status": "Manual Review", "reason_code": "PRODUCT_COUNT_MISMATCH", "reason": "Product Count Mismatch: source declares 1 product anchors."},
        ],
    }.items():
        app.session_state[key] = value
    app.run(timeout=20)

    assert app.exception == []
    labels = {item.label for item in app.get("download_button")}
    assert "📥 Download All Manual Reviews (CSV)" in labels
    assert "📥 Download Re-upload Listing (CSV)" in labels


def _historical_signature(state):
    rows = []
    for bucket in ("orders", "products", "reviews", "processing_errors", "duplicate_skipped", "unsupported_files"):
        for record in state.get(bucket, []):
            if isinstance(record, dict):
                rows.append((bucket, tuple(sorted((str(key), repr(value)) for key, value in record.items()))))
    return sha256(repr((state.get("batch_id"), tuple(rows))).encode("utf-8")).hexdigest()


def test_validate_bulk_manual_review_removal_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "bulk-review-batch",
        "import_source_type": "Platform Orders",
        "data_import_step": 3,
        "upload_result_summary": {"pdfs_processed": 1},
        "orders": [{"platform": "Shopee", "order_id": "GOOD", "source_pdf": "good.pdf", "status": "Accepted"}],
        "products": [],
        "reviews": [
            {"platform": "Shopee", "order_id": "BAD-1", "source_pdf": "bad-1.pdf", "status": "Manual Review", "reason": "Missing data"},
            {"platform": "Shopee", "order_id": "BAD-2", "source_pdf": "bad-2.pdf", "status": "Manual Review", "reason": "Missing data"},
        ],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
    }
    for key, value in state.items():
        app.session_state[key] = value
    app.run(timeout=20)

    next(button for button in app.button if button.label == "Remove all Manual Review sources from current batch").click().run(timeout=20)
    assert len(app.session_state.filtered_state["reviews"]) == 2
    next(button for button in app.button if button.label == "Remove 2 Sources").click().run(timeout=20)
    assert app.exception == []
    assert app.session_state.filtered_state["reviews"] == []
    assert [row["source_pdf"] for row in app.session_state.filtered_state["orders"]] == ["good.pdf"]


def test_reconcile_bulk_needs_review_removal_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    state = {
        "authenticated": True,
        "navigation": "Data Import",
        "batch_id": "bulk-needs-review-batch",
        "import_source_type": "Platform Orders",
        "data_import_step": 4,
        "orders": [
            {"platform": "Shopee", "order_id": "NEEDS-1", "source_pdf": "needs-1.pdf", "status": "Accepted"},
            {"platform": "Shopee", "order_id": "NEW-1", "source_pdf": "new.pdf", "status": "Accepted"},
        ],
        "products": [],
        "reviews": [],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
    }
    state["uat2_historical_commit_entries"] = (
        InvoiceIntakeEntry("needs-1", "needs-1.pdf", "hash-1", "NEEDS-1", IntakeStatus.NEEDS_REVIEW, "NAV unresolved", None),
        InvoiceIntakeEntry("new-1", "new.pdf", "hash-2", "NEW-1", IntakeStatus.NEW, None, None),
    )
    state["uat2_historical_commit_signature"] = _historical_signature(state)
    for key, value in state.items():
        app.session_state[key] = value
    app.run(timeout=20)

    next(button for button in app.button if button.label == "Remove all NEEDS_REVIEW sources from current batch").click().run(timeout=20)
    assert len(app.session_state.filtered_state["orders"]) == 2
    next(button for button in app.button if button.label == "Remove 1 Source").click().run(timeout=20)
    assert app.exception == []
    assert [row["source_pdf"] for row in app.session_state.filtered_state["orders"]] == ["new.pdf"]


def _partial_import_failure_app():
    import streamlit as st
    from src.invoice_app.ui import data_import

    st.session_state.setdefault("data_import_step", 5)
    st.session_state.setdefault(
        "historical_partial_import_failure",
        {
            "confirmed_count": 250,
            "pending_count": 282,
            "chunk_size": 50,
            "completed_chunks": 5,
            "failed_chunk_index": 6,
            "failed_chunk_size": 50,
            "total_chunks": 11,
            "underlying_error_type": "HttpError",
            "underlying_error_message": "Service unavailable; token=[REDACTED]",
        },
    )
    data_import._render_historical_partial_import_failure()


def test_partial_import_ui_shows_counts_revalidate_action_and_sanitized_details():
    app = AppTest.from_function(_partial_import_failure_app)

    app.run()

    assert app.exception == []
    warning = next(item.value for item in app.warning if "Partial import" in item.value)
    assert "250 invoices were successfully imported" in warning
    assert "282 invoices were not written" in warning
    assert "Technical details" in {item.label for item in app.expander}
    diagnostics = next(
        frame.value for frame in app.dataframe if "Diagnostic" in frame.value.columns
    )
    values = dict(zip(diagnostics["Diagnostic"], diagnostics["Value"]))
    assert values["Failed chunk"] == "6"
    assert values["Underlying error type"] == "HttpError"
    assert "[REDACTED]" in values["Underlying error"]
    next(
        button for button in app.button
        if button.label == "Revalidate remaining invoices"
    ).click().run()
    assert app.session_state.filtered_state["data_import_step"] == 4


def _already_imported_bulk_recovery_app():
    import streamlit as st
    from src.invoice_app.services.historical_invoice_intake import (
        IntakeStatus,
        InvoiceIntakeEntry,
    )
    from src.invoice_app.ui import data_import

    st.session_state.setdefault(
        "orders",
        [
            {"platform": "Shopee", "status": "Accepted", "source_pdf": "stored.pdf", "order_id": "STORED"},
            {"platform": "Shopee", "status": "Accepted", "source_pdf": "new.pdf", "order_id": "NEW"},
        ],
    )
    st.session_state.setdefault("products", [])
    st.session_state.setdefault("reviews", [])
    st.session_state.setdefault("duplicate_skipped", [])
    st.session_state.setdefault("unsupported_files", [])
    st.session_state.setdefault("processing_errors", [])
    entries = (
        InvoiceIntakeEntry(
            "stored", "stored.pdf", "a" * 64, "STORED",
            IntakeStatus.ALREADY_IMPORTED, None, None,
        ),
        InvoiceIntakeEntry(
            "new", "new.pdf", "b" * 64, "NEW", IntakeStatus.NEW, None, None,
        ),
    )
    if data_import._has_pending_recovery():
        data_import._render_recovery_confirmation()
    else:
        data_import._render_historical_status_details(
            entries,
            allow_removal=True,
            key_prefix="test_historical",
        )


def test_already_imported_bulk_recovery_ui_requires_confirmation_and_is_staging_only():
    app = AppTest.from_function(_already_imported_bulk_recovery_app)

    app.run()
    assert any("Already imported: 1" in item.value for item in app.success)
    next(
        button for button in app.button
        if button.label == "Continue with 1 remaining invoices"
    ).click().run()
    assert [row["order_id"] for row in app.session_state.filtered_state["orders"]] == [
        "STORED", "NEW",
    ]
    next(
        button for button in app.button
        if button.label == "Continue with remaining invoices"
    ).click().run()

    assert app.exception == []
    assert [row["order_id"] for row in app.session_state.filtered_state["orders"]] == [
        "NEW",
    ]


def _all_non_new_bulk_recovery_app():
    import streamlit as st
    from src.invoice_app.services.historical_invoice_intake import (
        IntakeStatus,
        InvoiceIntakeEntry,
    )
    from src.invoice_app.ui import data_import

    st.session_state.setdefault(
        "orders",
        [
            {
                "platform": "Shopee", "source_pdf": "stored.pdf",
                "order_id": "STORED", "status": "Accepted",
            },
            {
                "platform": "Shopee", "source_pdf": "conflict.pdf",
                "order_id": "CONFLICT", "status": "Accepted",
            },
            {
                "platform": "Shopee", "source_pdf": "conflict.pdf",
                "order_id": "NEW-IN-MIXED-PDF", "status": "Accepted",
            },
            {
                "platform": "Shopee", "source_pdf": "review.pdf",
                "order_id": "REVIEW", "status": "Accepted",
            },
            {
                "platform": "Shopee", "source_pdf": "new.pdf",
                "order_id": "NEW", "status": "Accepted",
            },
        ],
    )
    for bucket in (
        "products",
        "reviews",
        "duplicate_skipped",
        "unsupported_files",
        "processing_errors",
    ):
        st.session_state.setdefault(bucket, [])
    entries = (
        InvoiceIntakeEntry(
            "stored", "stored.pdf", "a" * 64, "STORED",
            IntakeStatus.ALREADY_IMPORTED, None, None,
        ),
        InvoiceIntakeEntry(
            "conflict", "conflict.pdf", "b" * 64, "CONFLICT",
            IntakeStatus.SOURCE_CONFLICT, "Stored source differs.", None,
        ),
        InvoiceIntakeEntry(
            "new-mixed", "conflict.pdf", "b" * 64, "NEW-IN-MIXED-PDF",
            IntakeStatus.NEW, None, None,
        ),
        InvoiceIntakeEntry(
            "review", "review.pdf", "c" * 64, "REVIEW",
            IntakeStatus.NEEDS_REVIEW, "Review required.", None,
        ),
        InvoiceIntakeEntry(
            "new", "new.pdf", "d" * 64, "NEW",
            IntakeStatus.NEW, None, None,
        ),
    )
    if data_import._has_pending_recovery():
        data_import._render_recovery_confirmation()
    else:
        data_import._render_historical_status_details(
            entries,
            allow_removal=True,
            key_prefix="test_all_non_new",
        )


def test_all_non_new_bulk_recovery_removes_every_non_new_source_in_one_action():
    app = AppTest.from_function(_all_non_new_bulk_recovery_app)

    app.run()
    next(
        button for button in app.button
        if button.label == "Remove all non-NEW source PDFs from current batch"
    ).click().run()
    assert [row["order_id"] for row in app.session_state.filtered_state["orders"]] == [
        "STORED", "CONFLICT", "NEW-IN-MIXED-PDF", "REVIEW", "NEW",
    ]
    next(button for button in app.button if button.label == "Remove 3 PDFs").click().run()

    assert app.exception == []
    assert [row["order_id"] for row in app.session_state.filtered_state["orders"]] == [
        "NEW",
    ]
