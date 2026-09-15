from hashlib import sha256
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.historical_invoice_intake import IntakeStatus, InvoiceIntakeEntry
from src.invoice_app.services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
)
from src.invoice_app.services.validation_recovery import (
    REMOVE_STAGED_SOURCE,
    execute_current_batch_bulk_recovery,
    execute_current_batch_recovery,
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

    assert "View skipped duplicate files (1)" in {item.label for item in app.expander}
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
