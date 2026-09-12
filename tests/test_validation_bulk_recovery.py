from hashlib import sha256
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.historical_invoice_intake import IntakeStatus, InvoiceIntakeEntry
from src.invoice_app.services.validation_recovery import execute_current_batch_bulk_recovery


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
    next(button for button in app.button if button.label == "Confirm removal and revalidate").click().run(timeout=20)
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
    next(button for button in app.button if button.label == "Confirm removal and revalidate").click().run(timeout=20)
    assert app.exception == []
    assert [row["source_pdf"] for row in app.session_state.filtered_state["orders"]] == ["new.pdf"]
