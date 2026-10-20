from types import SimpleNamespace

import pytest

from src.invoice_app.services.workflow_navigation import (
    ALLOWED_ACTIVITY_NAMES,
    begin_workflow_activity,
    end_workflow_activity,
)
from src.invoice_app.ui import data_import


class _Container:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _SessionState(dict[str, object]):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state = _SessionState()
        self.errors: list[str] = []

    def container(self, **_kwargs):
        return _Container()

    def metric(self, *_args, **_kwargs):
        pass

    def caption(self, *_args, **_kwargs):
        pass

    def button(self, _label, *, key, **_kwargs):
        return key == "full_order_sync_current"

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def rerun(self):
        pass


def _ready_review():
    return SimpleNamespace(
        ready=True,
        sync_preview=SimpleNamespace(new_count=1, updated_count=0, unchanged_count=0),
    )


def _configure_full_order_sync(monkeypatch, *, commit):
    fake = _FakeStreamlit()
    monkeypatch.setattr(data_import, "st", fake)
    monkeypatch.setattr(data_import, "_full_order_review", _ready_review)
    monkeypatch.setattr(data_import, "render_authoritative_status", lambda **_kwargs: None)
    monkeypatch.setattr(data_import, "_active_invoice_market_context", lambda: object())
    monkeypatch.setattr(
        data_import,
        "configured_uat2_data_settings",
        lambda _context: SimpleNamespace(create_full_order_current_repository=lambda: object()),
    )
    monkeypatch.setattr(data_import, "load_configured_product_price_master", lambda _context: (object(), "test"))
    monkeypatch.setattr(data_import, "commit_shopee_full_order_review", commit)
    return fake


def test_full_order_sync_reuses_existing_processing_activity_and_clears_on_success(monkeypatch):
    result = SimpleNamespace(new_count=1, updated_count=0, unchanged_count=0)
    fake = _configure_full_order_sync(monkeypatch, commit=lambda *_args, **_kwargs: result)

    data_import._render_full_order_review_and_sync()

    assert fake.session_state["full_order_sync_result"] is result
    assert "workflow_activity" not in fake.session_state
    assert fake.errors == []


def test_full_order_sync_clears_activity_and_surfaces_the_real_sync_error(monkeypatch):
    def fail_sync(*_args, **_kwargs):
        raise ValueError("underlying sync failure")

    fake = _configure_full_order_sync(monkeypatch, commit=fail_sync)

    data_import._render_full_order_review_and_sync()

    assert "full_order_sync_result" not in fake.session_state
    assert "workflow_activity" not in fake.session_state
    assert fake.errors == [
        "Full Order sync failed before safe completion: underlying sync failure"
    ]


def test_existing_activity_allowlist_is_preserved_and_rejects_unknown_names():
    assert ALLOWED_ACTIVITY_NAMES == frozenset(
        {"Processing", "Validating", "Revalidating"}
    )
    for name in ALLOWED_ACTIVITY_NAMES:
        state: dict[str, object] = {}
        begin_workflow_activity(state, name)
        assert state["workflow_activity"] == name
        end_workflow_activity(state)
        assert "workflow_activity" not in state

    with pytest.raises(ValueError, match="Unsupported workflow activity: Syncing"):
        begin_workflow_activity({}, "Syncing")
