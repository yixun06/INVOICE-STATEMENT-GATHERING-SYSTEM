from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from src.invoice_app.repositories.historical_invoice_repository import (
    InMemoryHistoricalInvoiceRepository,
)
from src.invoice_app.services.historical_invoice_intake import (
    PRODUCT_MASTER_ENRICHMENT_REQUIRED,
    IntakeStatus,
    InvoiceIntakeEntry,
)
from src.invoice_app.services.invoice_product_master_revalidation import (
    has_product_master_dependent_blocker,
    revalidate_current_invoice_batch,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SOURCE_HASH = "a" * 64


def _order(order_id: str, source_pdf: str) -> dict:
    return {
        "platform": "Shopee",
        "order_id": order_id,
        "source_pdf": source_pdf,
        "status": "Accepted",
        "order_income": Decimal("10.00"),
        "income_type": "Final",
        "payment_status": "Released",
    }


def _product(order_id: str, source_pdf: str, sku: str) -> dict:
    return {
        "platform": "Shopee",
        "order_id": order_id,
        "source_pdf": source_pdf,
        "status": "Accepted",
        "seller_sku": sku,
        "product_name": f"Product {sku}",
        "variation": "Standard",
        "quantity": 1,
        "source_line_subtotal": Decimal("10.00"),
    }


def _master(*skus: str) -> ProductPriceMaster:
    return ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": sku,
                "parent_sku": "",
                "product_name": f"Product {sku}",
                "variation_name": "Standard",
                "unit_selling_price": "10.00",
                "nav_code": f"NAV-{sku}",
            }
            for sku in skus
        ]
    )


def _pm_blocker(order_id: str, source_pdf: str) -> InvoiceIntakeEntry:
    return InvoiceIntakeEntry(
        staging_id=f"{source_pdf}:{SOURCE_HASH}:{order_id}",
        source_filename=source_pdf,
        source_hash=SOURCE_HASH,
        order_id=order_id,
        status=IntakeStatus.NEEDS_REVIEW,
        message="Product Name / Variation did not identify one candidate master identity.",
        bundle=None,
        reason_code=PRODUCT_MASTER_ENRICHMENT_REQUIRED,
    )


def _non_pm_blocker() -> InvoiceIntakeEntry:
    return InvoiceIntakeEntry(
        staging_id="review:source.pdf:SHP-1",
        source_filename="source.pdf",
        source_hash=SOURCE_HASH,
        order_id="SHP-1",
        status=IntakeStatus.NEEDS_REVIEW,
        message="Manual Review source remains in the current batch.",
        bundle=None,
    )


class ClassificationOnlyRepository(InMemoryHistoricalInvoiceRepository):
    def __init__(self) -> None:
        super().__init__()
        self.classification_calls = 0

    def classify_invoices(self, bundles):
        self.classification_calls += 1
        return super().classify_invoices(bundles)

    def import_invoice(self, bundle):  # pragma: no cover - must never be called
        raise AssertionError("Product Master revalidation must not persist invoices.")

    def import_invoices(self, bundles, *, chunk_size=50):  # pragma: no cover
        raise AssertionError("Product Master revalidation must not persist invoices.")


def _state(*pairs: tuple[str, str]) -> dict:
    orders = [_order(order_id, source_pdf) for order_id, source_pdf in pairs]
    products = [
        _product(order_id, source_pdf, f"SKU-{index}")
        for index, (order_id, source_pdf) in enumerate(pairs, start=1)
    ]
    return {
        "batch_id": "batch-pm-revalidate",
        "import_source_type": "Platform Orders",
        "invoice_upload_attempt": "resolved",
        "orders": orders,
        "products": products,
        "reviews": [],
        "processing_errors": [],
        "duplicate_skipped": [],
        "unsupported_files": [],
        "uat2_historical_commit_entries": tuple(
            _pm_blocker(order_id, source_pdf) for order_id, source_pdf in pairs
        ),
        "uat2_historical_commit_signature": "old-signature",
        "uat2_historical_commit_refresh_required": False,
    }


def test_pm_blocker_controls_batch_action_visibility() -> None:
    assert has_product_master_dependent_blocker((_pm_blocker("SHP-1", "one.pdf"),))
    assert not has_product_master_dependent_blocker((_non_pm_blocker(),))


def test_latest_master_revalidates_whole_batch_without_pdf_read_or_write(
    monkeypatch,
) -> None:
    state = _state(("SHP-1", "one.pdf"), ("SHP-2", "two.pdf"))
    original_orders = deepcopy(state["orders"])
    original_products = deepcopy(state["products"])
    repository = ClassificationOnlyRepository()
    monkeypatch.setattr(
        "src.invoice_app.services.historical_invoice_intake.resolve_archived_pdf_path",
        lambda *_args: (_ for _ in ()).throw(AssertionError("PDF was read again")),
    )

    result = revalidate_current_invoice_batch(
        state,
        price_master=_master("SKU-1", "SKU-2"),
        repository=repository,
        staging_signature="new-signature",
    )

    assert result.previous_pm_blockers == 2
    assert result.remaining_pm_blockers == 0
    assert repository.classification_calls == 1
    assert state["orders"] == original_orders
    assert state["products"] == original_products
    assert all(
        entry.status is IntakeStatus.NEW and entry.bundle is not None
        for entry in state["uat2_historical_commit_entries"]
    )
    assert state["uat2_historical_commit_signature"] == "new-signature"
    assert state["uat2_historical_commit_refresh_required"] is False


@pytest.mark.parametrize(
    "master",
    (
        _master(),
        ProductPriceMaster.from_rows(
            [
                {
                    "seller_sku": "SKU-1",
                    "parent_sku": "",
                    "product_name": "Wrong A",
                    "variation_name": "",
                    "unit_selling_price": "10.00",
                    "nav_code": "NAV-A",
                },
                {
                    "seller_sku": "SKU-1",
                    "parent_sku": "",
                    "product_name": "Wrong B",
                    "variation_name": "",
                    "unit_selling_price": "12.00",
                    "nav_code": "NAV-B",
                },
            ]
        ),
    ),
)
def test_unchanged_or_still_ambiguous_master_keeps_blocker(master) -> None:
    state = _state(("SHP-1", "one.pdf"))

    result = revalidate_current_invoice_batch(
        state,
        price_master=master,
        repository=ClassificationOnlyRepository(),
        staging_signature="same-batch-new-master",
    )

    assert result.remaining_pm_blockers == 1
    entry = state["uat2_historical_commit_entries"][0]
    assert entry.status is IntakeStatus.NEEDS_REVIEW
    assert entry.reason_code == PRODUCT_MASTER_ENRICHMENT_REQUIRED


def test_revalidation_failure_preserves_all_staging_and_old_classification() -> None:
    class FailingMaster:
        def lookup(self, **_kwargs):
            raise RuntimeError("latest Product Master is temporarily unavailable")

    state = _state(("SHP-1", "one.pdf"))
    before = deepcopy(state)

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        revalidate_current_invoice_batch(
            state,
            price_master=FailingMaster(),
            repository=ClassificationOnlyRepository(),
            staging_signature="must-not-be-applied",
        )

    assert state == before


def _historical_signature(state: dict) -> str:
    rows = []
    for bucket in (
        "orders",
        "products",
        "reviews",
        "processing_errors",
        "duplicate_skipped",
        "unsupported_files",
    ):
        for record in state.get(bucket, []):
            if isinstance(record, dict):
                rows.append(
                    (
                        bucket,
                        tuple(
                            sorted(
                                (str(key), repr(value))
                                for key, value in record.items()
                            )
                        ),
                    )
                )
    return sha256(
        repr((state.get("batch_id"), tuple(rows))).encode("utf-8")
    ).hexdigest()


def _seed_reconcile_app(app: AppTest) -> None:
    state = _state(("SHP-1", "one.pdf"))
    state.update(
        authenticated=True,
        navigation="Data Import",
        data_import_step=4,
        upload_result_summary={"pdfs_processed": 1, "orders_imported": 1},
    )
    state["uat2_historical_commit_signature"] = _historical_signature(state)
    for key, value in state.items():
        app.session_state[key] = value


def test_apptest_revalidate_refreshes_master_and_unlocks_only_normal_ready_batch(
    tmp_path, monkeypatch
) -> None:
    from src.invoice_app.ui import data_import

    monkeypatch.chdir(tmp_path)
    refresh_calls = []
    repository = ClassificationOnlyRepository()
    monkeypatch.setattr(
        data_import,
        "clear_product_master_source_cache",
        lambda: refresh_calls.append("cleared"),
    )
    monkeypatch.setattr(
        data_import,
        "load_configured_product_price_master",
        lambda: (_master("SKU-1"), "Latest synthetic master"),
    )
    monkeypatch.setattr(
        data_import,
        "configured_uat2_data_settings",
        lambda: SimpleNamespace(create_repository=lambda: repository),
    )

    app = AppTest.from_file(str(APP_PATH))
    _seed_reconcile_app(app)
    app.run(timeout=20)

    button = next(
        item
        for item in app.button
        if item.label == "Revalidate with latest Product Master"
    )
    assert next(
        item for item in app.button if item.label == "Continue to review & commit"
    ).disabled

    button.click().run(timeout=20)

    assert app.exception == []
    assert refresh_calls == ["cleared"]
    assert "workflow_activity" not in app.session_state.filtered_state
    assert not next(
        item for item in app.button if item.label == "Continue to review & commit"
    ).disabled
    assert "Revalidate with latest Product Master" not in {
        item.label for item in app.button
    }


def test_apptest_refresh_failure_keeps_retry_and_clears_activity(
    tmp_path, monkeypatch
) -> None:
    from src.invoice_app.services.product_master_source import ProductMasterSourceError
    from src.invoice_app.ui import data_import

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(data_import, "clear_product_master_source_cache", lambda: None)
    monkeypatch.setattr(
        data_import,
        "load_configured_product_price_master",
        lambda: (_ for _ in ()).throw(ProductMasterSourceError("network unavailable")),
    )

    app = AppTest.from_file(str(APP_PATH))
    _seed_reconcile_app(app)
    before_orders = deepcopy(app.session_state.filtered_state["orders"])
    before_products = deepcopy(app.session_state.filtered_state["products"])
    before_entries = tuple(
        app.session_state.filtered_state["uat2_historical_commit_entries"]
    )
    app.run(timeout=20)
    next(
        item
        for item in app.button
        if item.label == "Revalidate with latest Product Master"
    ).click().run(timeout=20)

    assert app.exception == []
    assert "workflow_activity" not in app.session_state.filtered_state
    assert app.session_state.filtered_state["orders"] == before_orders
    assert app.session_state.filtered_state["products"] == before_products
    assert tuple(
        app.session_state.filtered_state["uat2_historical_commit_entries"]
    ) == before_entries
    assert any("network unavailable" in item.value for item in app.error)
    assert "Revalidate with latest Product Master" in {
        item.label for item in app.button
    }


def test_apptest_unchanged_master_keeps_attention_and_retry(tmp_path, monkeypatch) -> None:
    from src.invoice_app.ui import data_import

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(data_import, "clear_product_master_source_cache", lambda: None)
    monkeypatch.setattr(
        data_import,
        "load_configured_product_price_master",
        lambda: (_master(), "Latest synthetic master"),
    )
    monkeypatch.setattr(
        data_import,
        "configured_uat2_data_settings",
        lambda: SimpleNamespace(
            create_repository=lambda: ClassificationOnlyRepository()
        ),
    )

    app = AppTest.from_file(str(APP_PATH))
    _seed_reconcile_app(app)
    app.run(timeout=20)
    next(
        item
        for item in app.button
        if item.label == "Revalidate with latest Product Master"
    ).click().run(timeout=20)

    assert app.exception == []
    assert next(
        item for item in app.button if item.label == "Continue to review & commit"
    ).disabled
    assert "Revalidate with latest Product Master" in {
        item.label for item in app.button
    }


def test_apptest_conflicting_master_returns_source_correction_to_validate(
    tmp_path, monkeypatch
) -> None:
    from src.invoice_app.ui import data_import

    conflicting_master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "",
                "product_name": "Wrong A",
                "variation_name": "",
                "unit_selling_price": "10.00",
                "nav_code": "NAV-A",
            },
            {
                "seller_sku": "SKU-1",
                "parent_sku": "",
                "product_name": "Wrong B",
                "variation_name": "",
                "unit_selling_price": "12.00",
                "nav_code": "NAV-B",
            },
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(data_import, "clear_product_master_source_cache", lambda: None)
    monkeypatch.setattr(
        data_import,
        "load_configured_product_price_master",
        lambda: (conflicting_master, "Latest synthetic master"),
    )

    app = AppTest.from_file(str(APP_PATH))
    _seed_reconcile_app(app)
    app.run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["reviews"][0]["order_id"] == "SHP-1"
    assert next(
        item for item in app.button if item.label == "Continue to review & commit"
    ).disabled
    assert "Revalidate with latest Product Master" not in {
        item.label for item in app.button
    }

    next(item for item in app.button if item.label == "Back").click().run(timeout=20)

    assert app.exception == []
    assert app.session_state.filtered_state["data_import_step"] == 3
    assert "Resolve Manual Review" in {item.value for item in app.subheader}
    assert "Seller SKU" in {item.label for item in app.text_input}


def test_unresolved_upload_cannot_expose_or_use_product_master_revalidation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(APP_PATH))
    _seed_reconcile_app(app)
    app.session_state["invoice_upload_attempt"] = "failed"

    app.run(timeout=20)

    assert app.exception == []
    assert any("Upload not completed" in item.value for item in app.error)
    assert "Revalidate with latest Product Master" not in {
        item.label for item in app.button
    }
