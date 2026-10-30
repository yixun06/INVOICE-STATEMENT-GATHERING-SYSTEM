from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _app(tmp_path, monkeypatch, **state):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    for key, value in state.items():
        app.session_state[key] = value
    return app.run(timeout=30)


def test_sidebar_and_platforms_show_the_phase_one_surface(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    assert not app.exception
    labels = [button.label for button in app.button]
    expected = ["Data Import", "Consolidated Product Summary", "Shopee MY", "Shopee SG", "Lazada", "TikTok"]
    assert [label for label in labels if label in expected] == expected
    assert {"Weekly Billing", "Monthly Billing", "Enter Lazada", "Enter TikTok"}.isdisjoint(labels)
    assert {"Enter Shopee MY", "Enter Shopee SG", "Refresh Product Master", "Discard current batch", "Logout"} <= set(labels)
    html = " ".join(str(element.proto) for element in app.get("html"))
    assert "ZEONIQ SYNC SALES" in html and "LIVE ANALYSIS DASHBOARD" in html
    assert "BILLING &amp; ACCOUNTING" not in html and "BILLING & ACCOUNTING" not in html


@pytest.mark.parametrize("market", ["Shopee MY", "Shopee SG"])
@pytest.mark.parametrize("source", ["Full Order Import", "Statement"])
def test_retained_workflows_reach_upload(tmp_path, monkeypatch, market, source):
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if button.label == f"Enter {market}").click().run(timeout=30)
    selector = next(item for item in app.get("button_group") if item.label == "Import workflow")
    assert selector.options == ["Full Order Import", "Statement"]
    selector.set_value(source).run(timeout=30)
    next(button for button in app.button if button.label == "Continue to upload").click().run(timeout=30)
    assert not app.exception
    assert app.file_uploader


@pytest.mark.parametrize("page", ["Lazada", "TikTok"])
def test_other_analysis_routes_are_safe(tmp_path, monkeypatch, page):
    app = _app(tmp_path, monkeypatch, navigation=page)
    assert not app.exception
    assert page in {title.value for title in app.title}
    assert not app.file_uploader


def test_stale_legacy_source_does_not_expose_uploader_or_discard_state(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, batch_id="legacy-batch", import_source_type="Platform Orders", data_import_step=3, orders=[{"platform": "Shopee", "order_id": "existing"}])
    assert not app.exception
    assert app.session_state["batch_id"] == "legacy-batch"
    assert app.session_state["orders"][0]["order_id"] == "existing"
    assert not app.file_uploader
    assert any("no longer available" in warning.value for warning in app.warning)


@pytest.mark.parametrize("market", ["Shopee MY", "Shopee SG"])
def test_partial_statement_state_still_blocks_review(tmp_path, monkeypatch, market):
    app = _app(tmp_path, monkeypatch)
    next(button for button in app.button if button.label == f"Enter {market}").click().run(timeout=30)
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 3
    app.session_state["weekly_statement_stage"] = object()
    app.run(timeout=30)
    assert not app.exception
    assert any("Statement check required" in item.value for item in app.error)
    assert not any(button.label == "Commit Statement" and not button.disabled for button in app.button)
