from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _seed_invoice_validate(app: AppTest, *, blocked: bool) -> None:
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = "wave1-batch"
    app.session_state["pdf_count"] = 4
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = [
        {
            "platform": "Shopee",
            "order_id": "READY-1",
            "order_income": "39.90",
            "final_amount": "35.00",
            "source_pdf": "ready.pdf",
        }
    ]
    app.session_state["products"] = [
        {
            "platform": "Shopee",
            "order_id": "READY-1",
            "product_name": "Bundle set",
            "seller_sku": "BUNDLE-1",
            "quantity": 2,
        }
    ]
    app.session_state["reviews"] = []
    app.session_state["processing_errors"] = (
        [
            {
                "filename": "broken.pdf",
                "source_pdf": "broken.pdf",
                "status": "Processing Error",
                "message": "PDF processing failed.",
            }
        ]
        if blocked
        else []
    )
    app.session_state["duplicate_skipped"] = []
    app.session_state["unsupported_files"] = []
    app.session_state["upload_result_summary"] = {
        "pdfs_processed": 3,
        "orders_imported": 7,
        "manual_reviews": 0,
        "duplicate_orders": 0,
        "unsupported_files": 0,
        "processing_errors": int(blocked),
    }


def _walk(node):
    for child in node.children.values():
        yield child
        if hasattr(child, "children"):
            yield from _walk(child)


def _position(app: AppTest, *, element_type: str, text: str) -> int:
    for index, element in enumerate(_walk(app.main)):
        if element.type != element_type:
            continue
        value = str(getattr(element, "value", "") or getattr(element, "label", ""))
        if text in value:
            return index
    raise AssertionError(f"Could not find {element_type!r} containing {text!r}")


def test_blocked_validate_is_blocker_first_and_keeps_forward_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_validate(app, blocked=True)

    app.run(timeout=20)

    assert app.exception == []
    status = next(error for error in app.error if "Needs Attention" in error.value)
    assert "1 current-batch item" in status.value
    continue_button = next(
        button for button in app.button if button.label == "Continue to reconcile"
    )
    assert continue_button.disabled is True
    assert _position(app, element_type="error", text="Needs Attention") < _position(
        app, element_type="button", text="Continue to reconcile"
    ) < _position(app, element_type="subheader", text="Current batch summary")
    assert _position(app, element_type="error", text="PDF processing failed") < _position(
        app, element_type="subheader", text="Current Batch — Order Level Data"
    )


def test_ready_validate_puts_usable_continue_next_to_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_validate(app, blocked=False)

    app.run(timeout=20)

    assert app.exception == []
    assert any(
        "Ready" in success.value and "1 accepted order validated successfully" in success.value
        for success in app.success
    )
    continue_button = next(
        button for button in app.button if button.label == "Continue to reconcile"
    )
    assert continue_button.disabled is False
    assert _position(app, element_type="success", text="Ready") < _position(
        app, element_type="button", text="Continue to reconcile"
    ) < _position(app, element_type="subheader", text="Current batch summary")


def test_validate_has_one_batch_metric_group_and_keeps_latest_action_separate(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_validate(app, blocked=False)

    app.run(timeout=20)

    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Orders", "1"),
        ("Products", "1"),
        ("Quantity", "2"),
        ("Order Income", "RM 39.90"),
        ("Final Amount", "RM 35.00"),
        ("Manual Review", "0"),
    ]
    assert any(
        "3 PDF(s) in the latest action" in caption.value
        and "7 order(s) imported" in caption.value
        for caption in app.caption
    )
    assert not any("Processing complete" in item.value for item in app.success)


def test_compact_stepper_is_the_only_progress_layer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_invoice_validate(app, blocked=False)

    app.run(timeout=20)

    stepper = [item.value for item in app.markdown if "badge[" in item.value]
    assert len(stepper) == 5
    assert any("1. Select Source" in item and "Completed" in item for item in stepper)
    assert any("3. Validate" in item and "Current" in item for item in stepper)
    assert any("5. Review & Commit" in item and "Pending" in item for item in stepper)
    assert app.get("progress") == []
