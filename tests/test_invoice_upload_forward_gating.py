from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.ui.data_import import (
    clear_invoice_upload_attempt,
    invoice_upload_is_resolved,
    invoice_upload_presentation_state,
    mark_invoice_upload_attempt,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _seed_invoice_upload(
    app: AppTest,
    *,
    attempt: str | None,
    step: int = 2,
    include_completed_upload: bool = True,
) -> None:
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "existing-invoice-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = step
    app.session_state["orders"] = [
        {"platform": "Shopee", "order_id": "EARLIER-ORDER", "source_pdf": "earlier.pdf"}
    ]
    app.session_state["products"] = [
        {
            "platform": "Shopee",
            "order_id": "EARLIER-ORDER",
            "product_name": "Earlier product",
            "source_pdf": "earlier.pdf",
        }
    ]
    app.session_state["reviews"] = []
    app.session_state["processing_errors"] = []
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    if include_completed_upload:
        app.session_state["upload_result_summary"] = {
            "pdfs_processed": 1,
            "orders_imported": 1,
            "manual_reviews": 0,
            "duplicate_orders": 0,
            "unsupported_files": 0,
            "processing_errors": 0,
        }
    if attempt is not None:
        app.session_state["invoice_upload_attempt"] = attempt


def _button_labels(app: AppTest) -> set[str]:
    return {button.label for button in app.button}


@pytest.mark.parametrize("attempt", ("processing", "failed"))
def test_interrupted_invoice_upload_hides_continue_and_shows_warning(
    tmp_path, monkeypatch, attempt: str
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt=attempt)

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" not in _button_labels(app)
    assert {"Back", "Exit Invoice Import"} <= _button_labels(app)
    assert any("Upload not completed" in error.value for error in app.error)


def test_pristine_upload_is_neutral_and_cannot_continue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt=None, include_completed_upload=False)

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" not in _button_labels(app)
    assert not any("Upload not completed" in error.value for error in app.error)
    assert "Back" in _button_labels(app)
    assert next(button for button in app.button if button.label == "Process files").disabled
    assert next(button for button in app.button if button.label == "Clear uploaded files").disabled
    assert invoice_upload_presentation_state(app.session_state.filtered_state) == "pristine"


def test_selected_upload_is_neutral_and_remains_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt=None, include_completed_upload=False)

    app.run(timeout=20)
    app.file_uploader[0].upload(
        "selected.pdf", b"test upload", "application/pdf"
    ).run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" not in _button_labels(app)
    assert not any("Upload not completed" in error.value for error in app.error)
    assert not next(button for button in app.button if button.label == "Process files").disabled
    assert not next(button for button in app.button if button.label == "Clear uploaded files").disabled
    assert invoice_upload_presentation_state(app.session_state.filtered_state) == "selected"


def test_clearing_selected_upload_returns_to_pristine_presentation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt=None, include_completed_upload=False)

    app.run(timeout=20)
    app.file_uploader[0].upload(
        "selected.pdf", b"test upload", "application/pdf"
    ).run(timeout=20)
    next(button for button in app.button if button.label == "Clear uploaded files").click().run(
        timeout=20
    )

    assert app.exception == []
    assert "Continue to validate" not in _button_labels(app)
    assert not any("Upload not completed" in error.value for error in app.error)
    assert next(button for button in app.button if button.label == "Process files").disabled
    assert next(button for button in app.button if button.label == "Clear uploaded files").disabled
    assert invoice_upload_presentation_state(app.session_state.filtered_state) == "pristine"


def test_completed_invoice_upload_keeps_continue_to_validate_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt="resolved")

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" in _button_labels(app)
    next(button for button in app.button if button.label == "Continue to validate").click().run(
        timeout=20
    )
    assert app.session_state.filtered_state["data_import_step"] == 3


def test_existing_completed_upload_stays_eligible_after_interrupted_selection_is_cleared(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt="selected")

    app.run(timeout=20)
    assert "Continue to validate" not in _button_labels(app)
    assert not any("Upload not completed" in error.value for error in app.error)

    clear_invoice_upload_attempt(app.session_state)
    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" in _button_labels(app)


def test_stale_processing_activity_blocks_a_previously_completed_upload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt=None)
    app.session_state["workflow_activity"] = "Processing"

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" not in _button_labels(app)
    assert any("Upload not completed" in error.value for error in app.error)


def test_successful_retry_marks_the_upload_attempt_resolved():
    state: dict[str, object] = {"upload_result_summary": {"pdfs_processed": 1}}

    mark_invoice_upload_attempt(state, "processing")
    assert invoice_upload_is_resolved(state) is False

    mark_invoice_upload_attempt(state, "resolved")
    assert invoice_upload_is_resolved(state) is True
    assert invoice_upload_presentation_state(state) == "resolved"


def test_cleared_upload_without_a_completed_result_returns_to_pristine():
    state: dict[str, object] = {}

    mark_invoice_upload_attempt(state, "selected")
    clear_invoice_upload_attempt(state)

    assert invoice_upload_presentation_state(state) == "pristine"
    assert invoice_upload_is_resolved(state) is False


def test_invoice_exit_clears_an_unresolved_upload_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt="selected")

    app.run(timeout=20)
    next(button for button in app.button if button.label == "Exit Invoice Import").click().run(
        timeout=20
    )
    next(button for button in app.button if button.label == "Leave Invoice Import").click().run(
        timeout=20
    )

    assert app.exception == []
    assert "invoice_upload_attempt" not in app.session_state.filtered_state


def test_direct_validate_access_returns_to_upload_when_attempt_is_unresolved(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt="selected", step=3)

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to reconcile" not in _button_labels(app)
    assert "Current batch summary" not in {element.value for element in app.subheader}
    assert not any("Upload not completed" in error.value for error in app.error)

    next(button for button in app.button if button.label == "Back").click().run(timeout=20)
    assert app.session_state.filtered_state["data_import_step"] == 2


def test_completed_upload_without_new_attempt_preserves_existing_happy_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_upload(app, attempt=None)

    app.run(timeout=20)

    assert app.exception == []
    assert "Continue to validate" in _button_labels(app)
