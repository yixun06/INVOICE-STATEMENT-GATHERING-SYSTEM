from pathlib import Path

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
REPORT_PAGES = (
    "Dashboard",
    "Cross Platform Summary",
    "Shopee",
    "Lazada",
    "ZENXIN",
)


def _navigate(app: AppTest, page: str) -> AppTest:
    next(button for button in app.button if button.label == page).click().run(
        timeout=20
    )
    return app


def _active_platform_batch() -> list[dict[str, object]]:
    return [
        {
            "platform": "Shopee",
            "order_id": "SHP-ACCEPTED",
            "status": "Accepted",
            "payment_status": "Pending",
            "income_type": "Final",
            "order_income": "12.34",
        }
    ]


def test_self_test_navigation_preserves_current_batch_and_syncs_accepted_orders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Dashboard"
    app.session_state["batch_id"] = "active-platform-batch"
    app.session_state["import_source_type"] = "Platform Orders"
    app.session_state["data_import_step"] = 3
    app.session_state["orders"] = _active_platform_batch()
    app.session_state["products"] = []
    app.session_state["reviews"] = [
        {
            "platform": "Shopee",
            "order_id": "SHP-REVIEW",
            "status": "Manual Review",
            "reason": "Income Completion Anchor Missing",
        }
    ]
    app.session_state["pdf_count"] = 2
    app.run(timeout=20)

    expected_navigation = {
        "Data Import",
        *REPORT_PAGES,
        "Settlement Test Lab",
    }
    labels = {button.label for button in app.button}
    assert expected_navigation <= labels
    assert {"Daily Task", "Payment Check", "How to Use"}.isdisjoint(labels)

    for report_page in REPORT_PAGES:
        if report_page != "Dashboard":
            _navigate(app, report_page)
        _navigate(app, "Data Import")
        assert "Data Import" in {title.value for title in app.title}
        restored = app.session_state.filtered_state
        assert restored["batch_id"] == "active-platform-batch"
        assert restored["data_import_step"] == 3
        assert restored["orders"] == _active_platform_batch()
        assert restored["reviews"][0]["status"] == "Manual Review"

    next(button for button in app.button if button.label == "Sync Accepted Orders to Test Session").click().run(timeout=20)
    synced = app.session_state.filtered_state["settlement_test_lab_accepted_orders"]
    assert synced == _active_platform_batch()
    assert all(order["status"] == "Accepted" for order in synced)

    _navigate(app, "Settlement Test Lab")
    assert "Settlement Test Lab" in {title.value for title in app.title}
    assert "← Back to Data Import" in {button.label for button in app.button}
    next(button for button in app.button if button.label == "← Back to Data Import").click().run(timeout=20)
    restored = app.session_state.filtered_state
    assert restored["navigation"] == "Data Import"
    assert restored["batch_id"] == "active-platform-batch"

