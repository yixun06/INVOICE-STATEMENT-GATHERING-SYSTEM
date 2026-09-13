"""Local-only, read-only acceptance harness for Shopee Reconciliation V2."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from src.invoice_app.domain.historical_invoice import InvoiceBundle
from src.invoice_app.domain.statement_reconciliation_v2 import IdentityScope
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    _deserialize_snapshot,
)
from src.invoice_app.services.import_result_adapters import (
    adapt_shopee_weekly_statement_import_result,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.product_master_source import (
    load_configured_product_price_master,
)
from src.invoice_app.services.shopee_statement_import import (
    review_statement_upload,
)
from src.invoice_app.services.shopee_statement_persistence import (
    StatementCommitState,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read local workbook snapshots and print aggregate Reconciliation V2 "
            "acceptance evidence. Inputs are never modified."
        )
    )
    parser.add_argument("--uat2-xlsx", type=Path, required=True)
    parser.add_argument("--statement-xlsx", type=Path, required=True)
    product_master_group = parser.add_mutually_exclusive_group(required=True)
    product_master_group.add_argument("--product-master-xlsx", type=Path)
    product_master_group.add_argument(
        "--configured-product-master",
        action="store_true",
        help="Use the same configured Product Master source as the application.",
    )
    parser.add_argument(
        "--ui-smoke",
        action="store_true",
        help="Render the real review through the Streamlit Review page without clicking commit.",
    )
    args = parser.parse_args()

    bundles = _read_invoice_bundles(args.uat2_xlsx)
    product_master, product_master_source = (
        load_configured_product_price_master()
        if args.configured_product_master
        else (
            ProductPriceMaster.from_xlsx(args.product_master_xlsx),
            str(args.product_master_xlsx),
        )
    )
    repository = _SnapshotRepository(bundles)
    writer = _ReadOnlySnapshotWriter(bundles)
    review = review_statement_upload(
        args.statement_xlsx,
        source_filename=args.statement_xlsx.name,
        batch_id="GOLDEN_RECONCILIATION_BENCHMARK_V1",
        uploaded_by="local-read-only-acceptance",
        repository=repository,
        writer=writer,
        product_master=product_master,
        now=lambda: datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    result = review.reconciliation_v2
    if result is None or review.stage.statement is None:
        raise RuntimeError("Application-level Statement review did not produce V2 evidence.")
    statement = review.stage.statement
    presentation = adapt_shopee_weekly_statement_import_result(
        review.stage,
        batch_id=review.batch_id,
        review=review,
    )

    invoice_order_ids = {bundle.order.order_id for bundle in bundles}
    statement_order_ids = {row.order_id for row in statement.order_rows}
    invoice_items = tuple(item for bundle in bundles for item in bundle.items)
    member_scopes = Counter(
        identity.identity_scope.value
        for order_result in result.orders
        for identity in order_result.evidence.identities
        for _ in identity.statement_members
    )
    settlement = Counter(
        order_result.summary.settlement_basis.value for order_result in result.orders
    )
    invoice_merchandise = sum(
        (bundle.order.product_price or Decimal("0") for bundle in bundles),
        Decimal("0"),
    )
    statement_merchandise = sum(
        (
            row.financial_components.get("Product Price") or Decimal("0")
            for row in statement.order_rows
        ),
        Decimal("0"),
    )
    unexplained_residual = sum(
        (
            order_result.evidence.settlement.unexplained_residual or Decimal("0")
            for order_result in result.orders
            if order_result.summary.settlement_basis.value == "NONE"
        ),
        Decimal("0"),
    )
    report = {
        "order_population": {
            "invoice": len(invoice_order_ids),
            "statement": len(statement_order_ids),
            "bidirectional_overlap": len(invoice_order_ids & statement_order_ids),
            "invoice_only": len(invoice_order_ids - statement_order_ids),
            "statement_only": len(statement_order_ids - invoice_order_ids),
        },
        "product_population": {
            "invoice_items": len(invoice_items),
            "statement_sku_rows": len(statement.sku_rows),
            "statement_members_classified": sum(member_scopes.values()),
        },
        "identity_member_counts": {
            scope.value: member_scopes[scope.value] for scope in IdentityScope
        },
        "coverage": {
            "duplicate_item_consumption": len(
                result.coverage.duplicate_invoice_consumption
            ),
            "uncovered_statement_members": len(
                result.coverage.uncovered_statement_members
            ),
            "uncovered_invoice_members": len(result.coverage.uncovered_invoice_members),
        },
        "merchandise": {
            "invoice_product_price": _money(invoice_merchandise),
            "statement_product_price": _money(statement_merchandise),
            "residual": _money(statement_merchandise - invoice_merchandise),
            "reconciled_orders": sum(
                order_result.summary.merchandise_reconciled
                for order_result in result.orders
            ),
            "total_orders": len(result.orders),
        },
        "settlement": {
            "EXACT": settlement["EXACT"],
            "EXPLAINED": settlement["EXPLAINED"],
            "NONE": settlement["NONE"],
            "sum_unexplained_residual": _money(unexplained_residual),
        },
        "adjustment_total_separate": _money(result.statement_adjustment_total),
        "quantity": {
            "invoice_items_available": sum(
                item.quantity is not None for item in invoice_items
            ),
            "invoice_items_total": len(invoice_items),
            "statement_quantity_available": False,
        },
        "statement_validation_issue_count": len(result.statement_validation_issues),
        "product_family_snapshot_sha256": result.product_family_snapshot.sha256,
        "product_master_source": product_master_source,
        "rule_version": result.rule_version,
        "application_review": {
            "review_ready": review.ready,
            "business_blockers": list(review.blockers),
            "limitations": list(review.limitations),
            "legacy_commit_available": review.commit_ready,
            "presentation_batch_status": presentation.batch_status,
            "presentation_blocking_issue_count": len(
                presentation.validation.blocking_issues
            ),
            "live_writes": writer.writes,
        },
    }
    if args.ui_smoke:
        report["streamlit_ui_smoke"] = _run_ui_smoke(review)
    print(json.dumps(report, indent=2, sort_keys=True))


def _read_invoice_bundles(source: Path) -> tuple[InvoiceBundle, ...]:
    workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
    try:
        tabs = {
            name: tuple(
                tuple(cell.value for cell in row)
                for row in workbook[name].iter_rows()
            )
            for name in ("Invoice_Orders", "Invoice_Items")
        }
    finally:
        workbook.close()
    return tuple(_deserialize_snapshot(tabs, 0).bundles.values())


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


class _SnapshotRepository:
    def __init__(self, bundles: tuple[InvoiceBundle, ...]) -> None:
        self._items = {bundle.order.order_id: bundle.items for bundle in bundles}

    def refresh(self) -> None:
        return None

    def get_items_by_order_ids(self, platform, order_ids):
        if platform != "Shopee":
            return {}
        return {
            order_id: self._items[order_id]
            for order_id in dict.fromkeys(order_ids)
            if order_id in self._items
        }


class _ReadOnlySnapshotWriter:
    def __init__(self, bundles: tuple[InvoiceBundle, ...]) -> None:
        self._orders = {bundle.order.order_id: bundle.order for bundle in bundles}
        self.writes = 0

    def reload_commit_state(self) -> StatementCommitState:
        return StatementCommitState(orders=self._orders, committed_statements=())

    def write_statement_batch(self, plan) -> None:
        self.writes += 1
        raise AssertionError("Acceptance harness must never perform a live write.")


def _run_ui_smoke(review) -> dict[str, object]:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state["authenticated"] = True
    app.session_state["navigation"] = "Data Import"
    app.session_state["batch_id"] = review.batch_id
    app.session_state["import_source_type"] = "Shopee Weekly Statement"
    app.session_state["data_import_step"] = 5
    app.session_state["weekly_statement_stage"] = review.stage
    app.session_state["weekly_statement_review"] = review
    app.run(timeout=60)
    public_messages = [
        str(element.value)
        for collection in (app.error, app.warning, app.info, app.success)
        for element in collection
    ]
    legacy_fragments = (
        "Normalized exact Product Name did not leave exactly one family candidate.",
        "Line subtotal within RM0.02 did not leave exactly one non-promotion candidate.",
    )
    return {
        "exceptions": [str(item.value) for item in app.exception],
        "subheaders": [str(item.value) for item in app.subheader],
        "business_error_count": len(app.error),
        "legacy_matcher_error_visible": any(
            fragment in message
            for fragment in legacy_fragments
            for message in public_messages
        ),
        "commit_button_disabled": any(
            button.label == "Commit Statement" and button.disabled
            for button in app.button
        ),
    }


if __name__ == "__main__":
    main()
