from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    GoogleApiHistoricalInvoiceGateway,
    _deserialize_order,
    _serialize_order,
)
from src.invoice_app.repositories.historical_invoice_repository import source_fact_fingerprint
from src.invoice_app.services.application_commit_lock import (
    ApplicationCommitInProgress,
    ApplicationCommitLock,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_DIFFERENCE_INDEX,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    LEGACY_INVOICE_ORDERS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB,
)
from src.invoice_app.services.uat2_statement_schema_migration import (
    SheetSchema,
    SpreadsheetSchemaSnapshot,
    UAT2StatementSchemaMigrationError,
    UAT2StatementSchemaMigrationStatus,
    UAT2StatementSchemaMigrator,
    UAT2StatementSchemaState,
    build_uat2_statement_schema_migration_requests,
    classify_uat2_statement_schema,
)


class InMemorySchemaGateway:
    def __init__(
        self,
        *,
        include_statement_data: bool = False,
        include_components: bool = False,
    ) -> None:
        self.schema = {
            INVOICE_ORDERS_TAB: SheetSchema(11, LEGACY_INVOICE_ORDERS_HEADERS),
            INVOICE_ITEMS_TAB: SheetSchema(12, INVOICE_ITEMS_HEADERS),
        }
        if include_statement_data:
            self.schema[STATEMENT_DATA_TAB] = SheetSchema(13, STATEMENT_DATA_HEADERS)
        if include_components:
            self.schema[STATEMENT_FINANCIAL_COMPONENTS_TAB] = SheetSchema(
                14, STATEMENT_FINANCIAL_COMPONENT_HEADERS
            )
        self.rows = {
            INVOICE_ORDERS_TAB: [
                list(LEGACY_INVOICE_ORDERS_HEADERS),
                [f"legacy-{index}" for index in range(len(LEGACY_INVOICE_ORDERS_HEADERS))],
            ],
            INVOICE_ITEMS_TAB: [list(INVOICE_ITEMS_HEADERS), ["item"] * len(INVOICE_ITEMS_HEADERS)],
        }
        if include_statement_data:
            self.rows[STATEMENT_DATA_TAB] = [list(STATEMENT_DATA_HEADERS)]
        if include_components:
            self.rows[STATEMENT_FINANCIAL_COMPONENTS_TAB] = [
                list(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
            ]
        self.read_calls = 0
        self.batch_bodies: list[tuple[dict, ...]] = []
        self.fail_before_apply = False
        self.fail_after_apply = False

    def read_schema(self, _spreadsheet_id):
        self.read_calls += 1
        return SpreadsheetSchemaSnapshot(tabs=dict(self.schema))

    def apply_schema_migration(self, _spreadsheet_id, requests):
        self.batch_bodies.append(tuple(requests))
        if self.fail_before_apply:
            raise OSError("synthetic pre-apply network failure")
        for request in requests:
            if "addSheet" in request:
                properties = request["addSheet"]["properties"]
                title = properties["title"]
                self.schema[title] = SheetSchema(properties["sheetId"], ())
                self.rows[title] = [[]]
            elif "insertDimension" in request:
                dimension = request["insertDimension"]["range"]
                title = self._title_for_id(dimension["sheetId"])
                for row in self.rows[title]:
                    row.insert(dimension["startIndex"], "")
                self.schema[title] = SheetSchema(
                    dimension["sheetId"], tuple(self.rows[title][0])
                )
            elif "updateCells" in request:
                update = request["updateCells"]
                title = self._title_for_id(update["range"]["sheetId"])
                start = update["range"]["startColumnIndex"]
                headers = [
                    value["userEnteredValue"]["stringValue"]
                    for value in update["rows"][0]["values"]
                ]
                row = self.rows[title][0]
                if len(row) < start + len(headers):
                    row.extend([""] * (start + len(headers) - len(row)))
                row[start : start + len(headers)] = headers
                self.schema[title] = SheetSchema(
                    update["range"]["sheetId"], tuple(row)
                )
        if self.fail_after_apply:
            raise OSError("synthetic uncertain network result")

    def _title_for_id(self, sheet_id):
        return next(title for title, schema in self.schema.items() if schema.sheet_id == sheet_id)


def _old_snapshot() -> SpreadsheetSchemaSnapshot:
    return InMemorySchemaGateway().read_schema("synthetic-sheet")


def _order(*, difference: Decimal | None) -> CanonicalInvoiceOrder:
    return CanonicalInvoiceOrder(
        platform="Shopee",
        order_id="ORDER-1",
        order_created_date=date(2026, 9, 1),
        order_income=Decimal("10.00"),
        income_type="Final",
        difference=difference,
        first_imported_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def _bundle(*, difference: Decimal | None) -> InvoiceBundle:
    order = _order(difference=difference)
    return InvoiceBundle(
        order=order,
        items=(
            CanonicalInvoiceItem(
                platform="Shopee",
                order_id=order.order_id,
                item_index=0,
                quantity=1,
                actual_selling_unit_price=Decimal("10.00"),
                line_subtotal=Decimal("10.00"),
            ),
        ),
    )


def test_exact_legacy_schema_is_the_only_eligible_preflight_state():
    assert classify_uat2_statement_schema(_old_snapshot()) is UAT2StatementSchemaState.ELIGIBLE_FOR_MIGRATION


def test_exact_target_schema_is_recognized_as_already_migrated():
    gateway = InMemorySchemaGateway()
    gateway.apply_schema_migration(
        "synthetic-sheet", build_uat2_statement_schema_migration_requests(gateway.read_schema("synthetic-sheet"))
    )

    assert classify_uat2_statement_schema(gateway.read_schema("synthetic-sheet")) is UAT2StatementSchemaState.ALREADY_MIGRATED


def test_exact_existing_statement_schema_is_eligible_only_for_component_ledger_tab():
    gateway = InMemorySchemaGateway(include_statement_data=True)
    gateway.schema[INVOICE_ORDERS_TAB] = SheetSchema(11, INVOICE_ORDERS_HEADERS)
    gateway.rows[INVOICE_ORDERS_TAB][0] = list(INVOICE_ORDERS_HEADERS)

    assert classify_uat2_statement_schema(
        gateway.read_schema("synthetic-sheet")
    ) is UAT2StatementSchemaState.ELIGIBLE_FOR_COMPONENT_LEDGER_MIGRATION
    requests = build_uat2_statement_schema_migration_requests(
        gateway.read_schema("synthetic-sheet")
    )
    assert [next(iter(request)) for request in requests] == ["addSheet", "updateCells"]


def test_unexpected_or_mixed_headers_fail_closed():
    gateway = InMemorySchemaGateway()
    gateway.schema[INVOICE_ORDERS_TAB] = SheetSchema(11, ("wrong", *LEGACY_INVOICE_ORDERS_HEADERS[1:]))

    with pytest.raises(UAT2StatementSchemaMigrationError, match="unexpected or mixed"):
        classify_uat2_statement_schema(gateway.read_schema("synthetic-sheet"))


def test_statement_data_existing_with_legacy_orders_is_rejected_without_a_write():
    gateway = InMemorySchemaGateway(include_statement_data=True)

    with pytest.raises(UAT2StatementSchemaMigrationError, match="unexpected or mixed"):
        UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway).migrate()

    assert gateway.batch_bodies == []


def test_difference_is_exactly_aj_and_legacy_source_audit_values_only_shift_right():
    gateway = InMemorySchemaGateway()
    legacy_row = tuple(gateway.rows[INVOICE_ORDERS_TAB][1])

    result = UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway).migrate()

    assert result.status is UAT2StatementSchemaMigrationStatus.MIGRATED
    assert INVOICE_ORDERS_DIFFERENCE_INDEX == 35
    assert INVOICE_ORDERS_HEADERS[INVOICE_ORDERS_DIFFERENCE_INDEX] == "difference"
    assert tuple(gateway.rows[INVOICE_ORDERS_TAB][1]) == (
        *legacy_row[:INVOICE_ORDERS_DIFFERENCE_INDEX],
        "",
        *legacy_row[INVOICE_ORDERS_DIFFERENCE_INDEX:],
    )


def test_blank_and_signed_difference_round_trip_as_nullable_decimal():
    blank = _deserialize_order(_serialize_order(_order(difference=None)), 2)
    signed = _deserialize_order(_serialize_order(_order(difference=Decimal("-1.23"))), 2)

    assert blank.difference is None
    assert signed.difference == Decimal("-1.23")


def test_difference_is_excluded_from_source_fingerprint():
    original = _bundle(difference=None)
    enriched = replace(original, order=replace(original.order, difference=Decimal("-2.50")))

    assert source_fact_fingerprint(original) == source_fact_fingerprint(enriched)


def test_migration_uses_one_batch_with_only_schema_requests():
    gateway = InMemorySchemaGateway()

    UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway).migrate()

    assert len(gateway.batch_bodies) == 1
    requests = gateway.batch_bodies[0]
    assert [next(iter(request)) for request in requests] == [
        "addSheet", "updateCells", "addSheet", "updateCells",
        "insertDimension", "updateCells",
    ]
    assert requests[4]["insertDimension"]["range"] == {
        "sheetId": 11,
        "dimension": "COLUMNS",
        "startIndex": 35,
        "endIndex": 36,
    }
    assert requests[5]["updateCells"]["range"] == {
        "sheetId": 11,
        "startRowIndex": 0,
        "endRowIndex": 1,
        "startColumnIndex": 35,
        "endColumnIndex": 36,
    }


def test_preflight_failure_produces_zero_write():
    gateway = InMemorySchemaGateway()
    gateway.schema[INVOICE_ITEMS_TAB] = SheetSchema(12, ("wrong", *INVOICE_ITEMS_HEADERS[1:]))

    with pytest.raises(UAT2StatementSchemaMigrationError, match="Invoice_Items"):
        UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway).migrate()

    assert gateway.batch_bodies == []


def test_retry_after_completed_schema_does_not_reapply_the_batch():
    gateway = InMemorySchemaGateway()
    migrator = UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway)

    assert migrator.migrate().status is UAT2StatementSchemaMigrationStatus.MIGRATED
    assert migrator.migrate().status is UAT2StatementSchemaMigrationStatus.ALREADY_MIGRATED
    assert len(gateway.batch_bodies) == 1


def test_uncertain_write_result_is_resolved_by_fresh_target_schema_read_without_retrying():
    gateway = InMemorySchemaGateway()
    gateway.fail_after_apply = True

    result = UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway).migrate()

    assert result.status is UAT2StatementSchemaMigrationStatus.MIGRATED_AFTER_UNCERTAIN_RESULT
    assert len(gateway.batch_bodies) == 1


def test_failed_write_rechecks_but_does_not_blindly_reapply_the_batch():
    gateway = InMemorySchemaGateway()
    gateway.fail_before_apply = True

    with pytest.raises(UAT2StatementSchemaMigrationError, match="do not blindly retry"):
        UAT2StatementSchemaMigrator(spreadsheet_id="synthetic-sheet", gateway=gateway).migrate()

    assert len(gateway.batch_bodies) == 1


def test_migration_preflight_and_write_are_inside_the_shared_commit_lock():
    lock = ApplicationCommitLock()

    class LockCheckingGateway(InMemorySchemaGateway):
        def _assert_lock_held(self):
            with pytest.raises(ApplicationCommitInProgress):
                with lock.acquire():
                    pass

        def read_schema(self, *args):
            self._assert_lock_held()
            return super().read_schema(*args)

        def apply_schema_migration(self, *args):
            self._assert_lock_held()
            return super().apply_schema_migration(*args)

    gateway = LockCheckingGateway()
    result = UAT2StatementSchemaMigrator(
        spreadsheet_id="synthetic-sheet", gateway=gateway, commit_lock=lock
    ).migrate()

    assert result.status is UAT2StatementSchemaMigrationStatus.MIGRATED


def test_invoice_items_schema_is_unchanged():
    assert INVOICE_ITEMS_HEADERS == (
        "platform", "order_id", "item_index", "seller_sku", "nav", "product_name", "variation", "quantity", "unit_price", "actual_selling_unit_price", "line_subtotal", "promotion_group_id", "promotion_label", "source_group_total", "statement_product_price", "statement_refund_amount", "statement_net_selling_amount", "source_pdf", "source_hash",
    )
    assert len(STATEMENT_DATA_HEADERS) == 40


def test_google_gateway_submits_the_prebuilt_migration_as_one_batch_update():
    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class ValuesApi:
        def batchGet(self, **kwargs):
            assert kwargs["ranges"] == ["Invoice_Orders!1:1", "Invoice_Items!1:1"]
            return Request({
                "valueRanges": [
                    {"values": [list(LEGACY_INVOICE_ORDERS_HEADERS)]},
                    {"values": [list(INVOICE_ITEMS_HEADERS)]},
                ]
            })

    class SheetsApi:
        def __init__(self):
            self.batch_bodies = []

        def get(self, **_kwargs):
            return Request({"sheets": [
                {"properties": {"sheetId": 11, "title": INVOICE_ORDERS_TAB}},
                {"properties": {"sheetId": 12, "title": INVOICE_ITEMS_TAB}},
            ]})

        def values(self):
            return ValuesApi()

        def batchUpdate(self, **kwargs):
            self.batch_bodies.append(kwargs["body"])
            return Request({})

    class Service:
        def __init__(self, sheets):
            self.sheets_api = sheets

        def spreadsheets(self):
            return self.sheets_api

    sheets = SheetsApi()
    gateway = GoogleApiHistoricalInvoiceGateway({"private_key": "placeholder"})
    gateway._service = Service(sheets)
    requests = build_uat2_statement_schema_migration_requests(
        gateway.read_schema("synthetic-sheet")
    )

    gateway.apply_schema_migration("synthetic-sheet", requests)

    assert len(sheets.batch_bodies) == 1
    assert sheets.batch_bodies[0]["requests"] == list(requests)
