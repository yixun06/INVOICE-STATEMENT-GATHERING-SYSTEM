from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.ui.weekly_billing import WEEKLY_BILLING_PAGE, WEEKLY_BILLING_TABS


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_weekly_billing_page_contract_locks_the_exact_workspace_tabs():
    assert WEEKLY_BILLING_PAGE == "Weekly Billing"
    assert WEEKLY_BILLING_TABS == (
        "Overview",
        "Invoice Intake",
        "Weekly Statement",
        "Missing Sources",
        "Verification",
        "Billing Preview",
        "Export",
    )


def test_weekly_billing_is_the_single_uat2_sidebar_page_and_existing_pages_remain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = WEEKLY_BILLING_PAGE
    app.session_state["batch_id"] = "active-batch"
    app.session_state["orders"] = [{"platform": "Shopee", "order_id": "SHP-1", "status": "Accepted"}]
    app.session_state["products"] = []
    app.session_state["reviews"] = []
    app.run(timeout=20)

    assert app.exception == []
    assert WEEKLY_BILLING_PAGE in {title.value for title in app.title}
    assert tuple(tab.label for tab in app.tabs) == WEEKLY_BILLING_TABS
    labels = {button.label for button in app.button}
    assert {"Data Import", "Dashboard", "Settlement Test Lab", WEEKLY_BILLING_PAGE} <= labels
    assert app.session_state.filtered_state["batch_id"] == "active-batch"
    assert app.session_state.filtered_state["orders"] == [
        {"platform": "Shopee", "order_id": "SHP-1", "status": "Accepted"}
    ]


def test_weekly_billing_shell_has_no_processing_persistence_or_export_dependency():
    source = (Path(__file__).parents[1] / "src" / "invoice_app" / "ui" / "weekly_billing.py").read_text(encoding="utf-8").casefold()
    assert "from src.invoice_app" not in source
    assert "import src.invoice_app" not in source
    assert "import_invoice(" not in source
    assert "process_uploads(" not in source
    assert "disabled=true" in source
