from __future__ import annotations

from src.invoice_app.services.exception_presentation import (
    build_exception_work_queue,
    build_historical_exception_work_queue,
)
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    InvoiceIntakeEntry,
)
from src.invoice_app.services.import_result_adapters import (
    adapt_platform_orders_import_result,
)
from src.invoice_app.services.import_result_contract import (
    CommitReadiness,
    ImportResult,
    ReconciliationResult,
    SessionState,
    SourceSummary,
    ValidationIssue,
    ValidationResult,
)
from src.invoice_app.services.validation_recovery import (
    REMOVE_DUPLICATE,
    REMOVE_SOURCE,
)


def _platform_result(**overrides) -> ImportResult:
    values = {
        "batch_id": "batch-1",
        "orders": [{"platform": "Shopee", "order_id": "READY-1"}],
        "products": [],
        "reviews": [],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
    }
    values.update(overrides)
    return adapt_platform_orders_import_result(**values)


def test_no_issues_produces_no_work_queue_items():
    queue = build_exception_work_queue(_platform_result())

    assert queue.items == ()
    assert queue.source_issue_count == queue.represented_issue_count == 0


def test_one_manual_review_appears_once_with_existing_form_direction():
    queue = build_exception_work_queue(
        _platform_result(
            reviews=[
                {
                    "platform": "Shopee",
                    "order_id": "REVIEW-1",
                    "source_pdf": "review.pdf",
                    "status": "Manual Review",
                    "reason": "Source-visible quantity needs review.",
                }
            ]
        )
    )

    assert len(queue.items) == 1
    item = queue.items[0]
    assert item.order_id == "REVIEW-1"
    assert item.source == "review.pdf"
    assert item.platform == "Shopee"
    assert item.action_hint == "Review form below"
    assert item.issue_count == 1


def test_processing_error_keeps_file_context_and_existing_source_recovery():
    queue = build_exception_work_queue(
        _platform_result(
            processing_errors=[
                {
                    "filename": "broken.pdf",
                    "platform": "Shopee",
                    "error": "Unreadable PDF",
                }
            ]
        )
    )

    item = queue.items[0]
    assert (item.scope, item.source, item.platform) == (
        "Source",
        "broken.pdf",
        "Shopee",
    )
    assert item.summary == "Unreadable PDF"
    assert item.action_hint == "Remove source from current batch"
    assert REMOVE_SOURCE in {action.action_type for action in item.recovery_actions}


def test_duplicate_and_unsupported_keep_their_distinct_existing_semantics():
    queue = build_exception_work_queue(
        _platform_result(
            duplicate_skipped=[
                {
                    "source_pdf": "duplicate.pdf",
                    "order_id": "DUP-1",
                    "status": "Duplicate Skipped",
                }
            ],
            unsupported_files=[
                {
                    "filename": "notes.txt",
                    "status": "Unsupported",
                    "message": "Unsupported source type.",
                }
            ],
        )
    )

    assert len(queue.items) == 2
    duplicate = next(item for item in queue.items if item.key == "category:duplicate")
    unsupported = next(item for item in queue.items if item.source == "notes.txt")
    assert REMOVE_DUPLICATE in {
        action.action_type for action in duplicate.recovery_actions
    }
    assert duplicate.action_hint == "Remove Duplicate"
    assert REMOVE_SOURCE in {
        action.action_type for action in unsupported.recovery_actions
    }
    assert unsupported.summary == "Unsupported source type."


def test_mixed_manual_review_duplicate_and_unsupported_is_complete_and_truthful():
    queue = build_exception_work_queue(
        _platform_result(
            reviews=[
                {
                    "source_pdf": "review.pdf",
                    "order_id": "REVIEW-1",
                    "status": "Manual Review",
                    "reason": "Manual Review is required.",
                }
            ],
            duplicate_skipped=[
                {"source_pdf": "duplicate.pdf", "order_id": "DUP-1"}
            ],
            unsupported_files=[{"filename": "notes.txt"}],
        )
    )

    assert len(queue.items) == 3
    assert queue.source_issue_count == queue.represented_issue_count == 3
    assert {issue.category for item in queue.items for issue in item.issues} == {
        "manual_review",
        "duplicate",
        "ingestion",
    }
    assert sum(item.blocking for item in queue.items) == 0


def test_non_actionable_issue_offers_details_without_inventing_recovery():
    issue = ValidationIssue(
        layer="existing_check",
        severity="warning",
        blocking=False,
        reason="Existing evidence needs inspection.",
        evidence={"code": "EXISTING-1"},
    )
    result = ImportResult(
        source_type="Platform Orders",
        batch_status="Ready to Commit",
        source_summary=SourceSummary(title="Platform Orders"),
        validation=ValidationResult(warnings=(issue,)),
        reconciliation=ReconciliationResult(available=False, status="Not Applicable"),
        commit_readiness=CommitReadiness(ready=True, status="Ready to Commit"),
        session_state=SessionState(
            applied_to_current_session=True,
            label="Applied to Current Session",
        ),
    )

    item = build_exception_work_queue(result).items[0]

    assert item.action_hint == "View details"
    assert item.recovery_actions == ()
    assert item.issues[0].evidence == {"code": "EXISTING-1"}


def test_historical_queue_excludes_new_and_preserves_conflict_source_action():
    entries = (
        InvoiceIntakeEntry(
            staging_id="new-1",
            source_filename="new.pdf",
            source_hash="hash-new",
            order_id="NEW-1",
            status=IntakeStatus.NEW,
            message=None,
        ),
        InvoiceIntakeEntry(
            staging_id="conflict-1",
            source_filename="conflict.pdf",
            source_hash="hash-conflict",
            order_id="CONFLICT-1",
            status=IntakeStatus.SOURCE_CONFLICT,
            message="The stored source differs.",
        ),
    )

    queue = build_historical_exception_work_queue(entries)

    assert len(queue.items) == 1
    assert queue.source_issue_count == queue.represented_issue_count == 1
    assert queue.items[0].order_id == "CONFLICT-1"
    assert queue.items[0].source == "conflict.pdf"
    assert REMOVE_SOURCE in {
        action.action_type for action in queue.items[0].recovery_actions
    }
