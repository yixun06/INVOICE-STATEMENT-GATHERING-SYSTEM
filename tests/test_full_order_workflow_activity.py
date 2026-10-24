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
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.dataframes: list[object] = []

    def container(self, **_kwargs):
        return _Container()

    def metric(self, *_args, **_kwargs):
        pass

    def caption(self, *_args, **_kwargs):
        pass

    def dataframe(self, value, **_kwargs):
        self.dataframes.append(value)

    def button(self, _label, *, key, **_kwargs):
        return key == "full_order_sync_current"

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def success(self, message, **_kwargs):
        self.successes.append(message)

    def warning(self, message, **_kwargs):
        self.warnings.append(message)

    def rerun(self):
        pass


def _ready_review():
    return SimpleNamespace(
        ready=True,
        sync_preview=SimpleNamespace(new_count=1, updated_count=0, unchanged_count=0),
    )


def _configure_full_order_sync(monkeypatch, *, commit):
    fake = _FakeStreamlit()
    invalidations: list[None] = []
    monkeypatch.setattr(data_import, "st", fake)
    monkeypatch.setattr(data_import, "_full_order_review", _ready_review)
    monkeypatch.setattr(data_import, "render_authoritative_status", lambda **_kwargs: None)
    monkeypatch.setattr(data_import, "_required_persistence_market_context", lambda: object())
    monkeypatch.setattr(
        data_import,
        "configured_uat2_data_settings",
        lambda _context, **_kwargs: SimpleNamespace(create_full_order_current_repository=lambda: object()),
    )
    monkeypatch.setattr(data_import, "load_configured_product_price_master", lambda _context: (object(), "test"))
    monkeypatch.setattr(data_import, "commit_shopee_full_order_review", commit)
    monkeypatch.setattr(
        data_import,
        "clear_cross_platform_reporting_snapshot_cache",
        lambda: invalidations.append(None),
    )
    return fake, invalidations


def test_full_order_sync_reuses_existing_processing_activity_and_clears_on_success(monkeypatch):
    result = SimpleNamespace(new_count=1, updated_count=0, unchanged_count=0)
    fake, invalidations = _configure_full_order_sync(
        monkeypatch, commit=lambda *_args, **_kwargs: result
    )

    data_import._render_full_order_review_and_sync()

    assert fake.session_state["full_order_sync_result"] is result
    assert invalidations == [None]
    assert "workflow_activity" not in fake.session_state
    assert fake.errors == []


def test_full_order_sync_clears_activity_and_surfaces_the_real_sync_error(monkeypatch):
    def fail_sync(*_args, **_kwargs):
        raise ValueError("underlying sync failure")

    fake, invalidations = _configure_full_order_sync(monkeypatch, commit=fail_sync)

    data_import._render_full_order_review_and_sync()

    assert "full_order_sync_result" not in fake.session_state
    assert invalidations == []
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


def test_full_order_validation_displays_listing_resolve_sku_separately(monkeypatch):
    fake = _FakeStreamlit()
    item = SimpleNamespace(
        order_id="ORDER-1",
        item_index=0,
        source_seller_sku="SOURCE-SKU",
        resolved_seller_sku="FINANCIAL-SKU",
        resolve_sku="9555208010555-2;\nNAV:5001438-3",
        resolve_sku_warning=None,
    )
    review = SimpleNamespace(
        ready=True,
        sync_preview=SimpleNamespace(new_count=1, updated_count=0, unchanged_count=0),
        current_batch=SimpleNamespace(items=(item,), issues=()),
        parsed=SimpleNamespace(order_ids=("ORDER-1",), dimension_fallback_used=False),
    )
    monkeypatch.setattr(data_import, "st", fake)
    monkeypatch.setattr(data_import, "_full_order_review", lambda: review)
    monkeypatch.setattr(data_import, "render_authoritative_status", lambda **_kwargs: None)
    monkeypatch.setattr(data_import, "_render_next_step", lambda *_args, **_kwargs: None)

    data_import._render_full_order_validation()

    assert fake.dataframes == [
        [
            {
                "Order ID": "ORDER-1",
                "Item": 1,
                "Source Seller SKU": "SOURCE-SKU",
                "Resolved Seller SKU": "FINANCIAL-SKU",
                "Resolve SKU": "9555208010555-2;\nNAV:5001438-3",
                "POS mapping": "Ready",
            }
        ]
    ]


def test_completed_full_order_sync_starts_a_clean_next_upload_without_reusing_review(monkeypatch):
    fake = _FakeStreamlit()
    fake.session_state.update(
        {
            "import_source_type": data_import.SHOPEE_FULL_ORDER_LIST,
            "data_import_step": 5,
            "batch_id": "july",
            "full_order_review": object(),
            "full_order_sync_result": SimpleNamespace(
                new_count=1, updated_count=0, unchanged_count=0
            ),
            "full_order_uploader_version": 2,
            "full_order_uploader_2": object(),
        }
    )
    monkeypatch.setattr(data_import, "st", fake)
    monkeypatch.setattr(data_import, "_full_order_review", _ready_review)
    monkeypatch.setattr(data_import, "render_authoritative_status", lambda **_kwargs: None)
    monkeypatch.setattr(
        fake,
        "button",
        lambda _label, *, key, **_kwargs: key == "full_order_upload_another",
    )

    data_import._render_full_order_review_and_sync()

    assert "full_order_review" not in fake.session_state
    assert "full_order_sync_result" not in fake.session_state
    assert "batch_id" not in fake.session_state
    assert "full_order_uploader_2" not in fake.session_state
    assert fake.session_state["full_order_uploader_version"] == 3
    assert fake.session_state["data_import_step"] == 2


def test_completed_full_order_sync_keeps_success_state_when_review_was_cleared(monkeypatch):
    fake = _FakeStreamlit()
    fake.session_state["full_order_sync_result"] = SimpleNamespace(
        new_count=1, updated_count=0, unchanged_count=0
    )
    monkeypatch.setattr(data_import, "st", fake)
    monkeypatch.setattr(data_import, "_full_order_review", lambda: None)

    data_import._render_full_order_review_and_sync()

    assert fake.successes == ["Full Order current sync complete."]
    assert fake.warnings == []
    assert fake.errors == []


def test_stale_full_order_completed_step_returns_to_upload_without_a_review():
    state = {
        "import_source_type": data_import.SHOPEE_FULL_ORDER_LIST,
        "data_import_step": 3,
        "full_order_uploader_version": 0,
    }

    assert data_import._normalize_step_for_source(state) == 2
    assert state["data_import_step"] == 2
