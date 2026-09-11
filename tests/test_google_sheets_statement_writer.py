from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Event, Thread

import pytest

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem, CanonicalInvoiceOrder
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (
    GoogleApiHistoricalInvoiceGateway,
    _serialize_item,
    _serialize_order,
)
from src.invoice_app.services.application_commit_lock import (
    ApplicationCommitInProgress,
    ApplicationCommitLock,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
    StatementWriteIntegrityError,
    write_google_statement_plan_if_current,
)
from src.invoice_app.services.shopee_statement_item_matching import (
    StatementItemMatch,
    StatementItemMatchBatch,
    StatementItemMatchStatus,
)
from src.invoice_app.services.shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitBlocked,
    prepare_statement_commit_plan,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
)


SHEET_IDS = {INVOICE_ORDERS_TAB: 11, INVOICE_ITEMS_TAB: 12, STATEMENT_DATA_TAB: 13}


class InMemoryStatementGateway:
    def __init__(self, *, order: CanonicalInvoiceOrder | None = None) -> None:
        order = order or _invoice_order()
        self.tabs = {
            INVOICE_ORDERS_TAB: [list(INVOICE_ORDERS_HEADERS), list(_serialize_order(order))],
            INVOICE_ITEMS_TAB: [list(INVOICE_ITEMS_HEADERS), list(_serialize_item(_invoice_item()))],
            STATEMENT_DATA_TAB: [list(STATEMENT_DATA_HEADERS)],
        }
        self.read_calls = 0
        self.sheet_id_calls = 0
        self.batch_calls: list[tuple[dict, ...]] = []
        self.fail_mode: str | None = None
        self.before_read_tabs = None

    def read_sheet_ids(self, _spreadsheet_id, tabs):
        self.sheet_id_calls += 1
        return {tab: SHEET_IDS[tab] for tab in tabs}

    def read_tabs(self, _spreadsheet_id, tabs):
        self.read_calls += 1
        if self.before_read_tabs is not None:
            self.before_read_tabs(self.read_calls, self)
        return {tab: [list(row) for row in self.tabs[tab]] for tab in tabs}

    def batch_update(self, _spreadsheet_id, requests):
        self.batch_calls.append(tuple(requests))
        if self.fail_mode == "before":
            raise OSError("synthetic failure before apply")
        selected = requests[:1] if self.fail_mode == "mixed" else requests
        for request in selected:
            self._apply_update(request["updateCells"])
        if self.fail_mode in {"after", "mixed"}:
            raise OSError("synthetic uncertain response")

    def _apply_update(self, update):
        grid_range = update["range"]
        tab = next(name for name, sheet_id in SHEET_IDS.items() if sheet_id == grid_range["sheetId"])
        start_row = grid_range["startRowIndex"]
        start_column = grid_range["startColumnIndex"]
        for offset, source_row in enumerate(update["rows"]):
            row_index = start_row + offset
            while len(self.tabs[tab]) <= row_index:
                self.tabs[tab].append([])
            target = self.tabs[tab][row_index]
            values = [cell["userEnteredValue"]["stringValue"] for cell in source_row["values"]]
            if len(target) < start_column + len(values):
                target.extend([""] * (start_column + len(values) - len(target)))
            target[start_column : start_column + len(values)] = values


def _invoice_order(*, income_type="Estimated", final_amount="10.00"):
    return CanonicalInvoiceOrder(
        platform="Shopee",
        order_id="ORDER-1",
        final_amount=Decimal(final_amount) if final_amount is not None else None,
        order_income=Decimal("9.00"),
        income_type=income_type,
        payment_status="Pending",
        first_imported_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _invoice_item():
    return CanonicalInvoiceItem(
        platform="Shopee",
        order_id="ORDER-1",
        item_index=1,
        product_name="Green Tea",
        quantity=1,
        actual_selling_unit_price=Decimal("10.00"),
        line_subtotal=Decimal("10.00"),
    )


def _income(*, view_by, sequence, row_number, released="10.00"):
    return SettlementIncomeRow(
        sequence_no=sequence,
        view_by=view_by,
        order_id="ORDER-1",
        product_id="PRODUCT-1",
        product_name="Green Tea",
        order_creation_date=date(2026, 8, 1),
        payout_completed_date=date(2026, 8, 8),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal(released),
        financial_components={
            "Product Price": Decimal("10.00"),
            "Refund Amount": Decimal("0.00"),
        },
        source_values={},
        source_row_number=row_number,
    )


def _statement(*, sku_rows=1, file_hash="hash-1", released="10.00"):
    order = _income(view_by="Order", sequence="1", row_number=2, released=released)
    skus = tuple(
        _income(view_by="Sku", sequence=str(index + 1), row_number=3 + index, released=released)
        for index in range(sku_rows)
    )
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash=file_hash,
        statement_period_from=date(2026, 8, 1),
        statement_period_to=date(2026, 8, 8),
        summary_total_released=Decimal(released),
        adjustment_control_total=Decimal("0.00"),
        adjustment_footer_total=None,
        income_rows=(order, *skus),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )


def _matches(statement):
    return StatementItemMatchBatch(
        matches=tuple(
            StatementItemMatch(
                statement_source_row=row.source_row_number,
                order_id=row.order_id,
                product_id=row.product_id,
                product_name=row.product_name,
                status=StatementItemMatchStatus.MATCHED,
                invoice_item_index=1,
                match_method="EXACT_PRODUCT_NAME",
                reason="Deterministic match.",
            )
            for row in statement.sku_rows
        ),
        eligible_for_commit=True,
    )


def _plan(*, statement=None, order=None):
    statement = statement or _statement()
    order = order or _invoice_order()
    now = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    return prepare_statement_commit_plan(
        statement,
        audit=StatementBatchAudit("batch-1", now, "admin@example.test", now),
        invoice_orders=(order,),
        sku_matches=_matches(statement),
        validation_passed=True,
    )


def _commit(gateway, plan, *, lock=None):
    writer = GoogleSheetsStatementWriter(spreadsheet_id="synthetic-sheet", gateway=gateway)
    return write_google_statement_plan_if_current(
        plan,
        writer=writer,
        sku_matching_is_current=lambda: True,
        **({"commit_lock": lock} if lock is not None else {}),
    )


def _row_values(gateway, tab, row=1):
    headers = INVOICE_ORDERS_HEADERS if tab == INVOICE_ORDERS_TAB else INVOICE_ITEMS_HEADERS
    values = gateway.tabs[tab][row]
    return {header: values[index] if index < len(values) else "" for index, header in enumerate(headers)}


def test_one_google_batch_contains_statement_order_and_eligible_item_updates():
    gateway = InMemoryStatementGateway()

    result = _commit(gateway, _plan())

    assert result.committed is True
    assert len(gateway.batch_calls) == 1
    requests = gateway.batch_calls[0]
    assert {request["updateCells"]["range"]["sheetId"] for request in requests} == {11, 12, 13}
    assert _row_values(gateway, INVOICE_ORDERS_TAB)["payment_status"] == "RELEASED"
    assert _row_values(gateway, INVOICE_ORDERS_TAB)["income_type"] == "Estimated"
    assert _row_values(gateway, INVOICE_ORDERS_TAB)["order_income"] == "9.00"
    assert _row_values(gateway, INVOICE_ORDERS_TAB)["final_amount"] == "10.00"
    assert _row_values(gateway, INVOICE_ITEMS_TAB)["statement_net_selling_amount"] == "10.00"


def test_missing_or_duplicate_order_target_produces_zero_write():
    missing = InMemoryStatementGateway()
    missing.tabs[INVOICE_ORDERS_TAB] = [list(INVOICE_ORDERS_HEADERS)]
    result = _commit(missing, _plan())
    assert result.committed is False
    assert result.reasons == ("UNMATCHED_ORDER:ORDER-1",)
    assert missing.batch_calls == []

    duplicate = InMemoryStatementGateway()
    duplicate.tabs[INVOICE_ORDERS_TAB].append(list(duplicate.tabs[INVOICE_ORDERS_TAB][1]))
    with pytest.raises(StatementCommitBlocked, match="duplicate authoritative key"):
        _commit(duplicate, _plan())
    assert duplicate.batch_calls == []


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_duplicate_item_target_produces_zero_write(duplicate):
    gateway = InMemoryStatementGateway()
    if duplicate:
        gateway.tabs[INVOICE_ITEMS_TAB].append(list(gateway.tabs[INVOICE_ITEMS_TAB][1]))
    else:
        gateway.tabs[INVOICE_ITEMS_TAB] = [list(INVOICE_ITEMS_HEADERS)]

    with pytest.raises(StatementCommitBlocked, match="Invoice_Items"):
        _commit(gateway, _plan())

    assert gateway.batch_calls == []


def test_statement_append_position_comes_from_writer_fresh_snapshot():
    gateway = InMemoryStatementGateway()
    prior_row = list(_plan(statement=_statement(file_hash="prior-hash")).rows[0])
    prior_row[STATEMENT_DATA_HEADERS.index("statement_batch_id")] = "prior-batch"
    prior_row[STATEMENT_DATA_HEADERS.index("statement_period_from")] = "2026-07-01"
    prior_row[STATEMENT_DATA_HEADERS.index("statement_period_to")] = "2026-07-08"

    def inject_after_outer_preflight(read_number, current_gateway):
        if read_number == 2:
            current_gateway.tabs[STATEMENT_DATA_TAB].append(prior_row)

    gateway.before_read_tabs = inject_after_outer_preflight
    _commit(gateway, _plan())

    statement_request = next(
        request for request in gateway.batch_calls[0]
        if request["updateCells"]["range"]["sheetId"] == SHEET_IDS[STATEMENT_DATA_TAB]
    )
    assert statement_request["updateCells"]["range"]["startRowIndex"] == 2


@pytest.mark.parametrize(
    ("final_amount", "expected"),
    [("9.00", "1.00"), ("11.00", "-1.00"), ("10.00", "0.00")],
)
def test_signed_difference_is_written_exactly(final_amount, expected):
    order = _invoice_order(final_amount=final_amount)
    gateway = InMemoryStatementGateway(order=order)

    _commit(gateway, _plan(order=order))

    assert _row_values(gateway, INVOICE_ORDERS_TAB)["difference"] == expected


def test_final_income_type_remains_final_and_single_match_enriches_item():
    order = _invoice_order(income_type="Final")
    gateway = InMemoryStatementGateway(order=order)

    _commit(gateway, _plan(order=order))

    assert _row_values(gateway, INVOICE_ORDERS_TAB)["income_type"] == "Final"
    assert _row_values(gateway, INVOICE_ORDERS_TAB)["payment_status"] == "RELEASED"
    item = _row_values(gateway, INVOICE_ITEMS_TAB)
    assert item["statement_product_price"] == "10.00"
    assert item["statement_refund_amount"] == "0.00"
    assert item["statement_net_selling_amount"] == "10.00"


def test_repeated_statement_rows_are_preserved_and_item_enrichment_is_omitted():
    statement = _statement(sku_rows=2)
    plan = _plan(statement=statement)
    gateway = InMemoryStatementGateway()

    _commit(gateway, plan)

    record_type_index = STATEMENT_DATA_HEADERS.index("record_type")
    persisted_types = [row[record_type_index] for row in gateway.tabs[STATEMENT_DATA_TAB][1:]]
    assert persisted_types == ["ORDER", "SKU", "SKU"]
    assert plan.invoice_item_updates == ()
    assert not any(
        request["updateCells"]["range"]["sheetId"] == SHEET_IDS[INVOICE_ITEMS_TAB]
        for request in gateway.batch_calls[0]
    )


def test_committed_file_hash_and_same_period_revision_both_block_with_zero_write():
    duplicate = InMemoryStatementGateway()
    duplicate.tabs[STATEMENT_DATA_TAB].append(list(_plan().rows[0]))
    result = _commit(duplicate, _plan())
    assert result.reasons == ("ALREADY_IMPORTED",)
    assert duplicate.batch_calls == []

    revision = InMemoryStatementGateway()
    prior = list(_plan(statement=_statement(file_hash="prior-hash")).rows[0])
    revision.tabs[STATEMENT_DATA_TAB].append(prior)
    result = _commit(revision, _plan())
    assert result.reasons == ("POSSIBLE_REVISION",)
    assert revision.batch_calls == []


def test_second_concurrent_commit_cannot_enter_writer():
    lock = ApplicationCommitLock()
    started = Event()
    finish = Event()

    class BlockingGateway(InMemoryStatementGateway):
        def batch_update(self, spreadsheet_id, requests):
            started.set()
            assert finish.wait(timeout=2)
            return super().batch_update(spreadsheet_id, requests)

    gateway = BlockingGateway()
    first = {}
    thread = Thread(target=lambda: first.setdefault("result", _commit(gateway, _plan(), lock=lock)))
    thread.start()
    assert started.wait(timeout=2)
    try:
        with pytest.raises(ApplicationCommitInProgress):
            _commit(InMemoryStatementGateway(), _plan(), lock=lock)
    finally:
        finish.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert first["result"].committed is True


def test_preflight_and_all_writer_reads_are_after_lock_acquisition():
    lock = ApplicationCommitLock()

    class LockCheckingGateway(InMemoryStatementGateway):
        def _assert_locked(self):
            with pytest.raises(ApplicationCommitInProgress):
                with lock.acquire():
                    pass

        def read_sheet_ids(self, *args):
            self._assert_locked()
            return super().read_sheet_ids(*args)

        def read_tabs(self, *args):
            self._assert_locked()
            return super().read_tabs(*args)

        def batch_update(self, *args):
            self._assert_locked()
            return super().batch_update(*args)

    result = _commit(LockCheckingGateway(), _plan(), lock=lock)

    assert result.committed is True


def test_uncertain_all_applied_is_success_and_not_retried():
    gateway = InMemoryStatementGateway()
    gateway.fail_mode = "after"

    result = _commit(gateway, _plan())

    assert result.committed is True
    assert len(gateway.batch_calls) == 1


def test_uncertain_none_applied_is_safe_not_applied_and_not_retried():
    gateway = InMemoryStatementGateway()
    gateway.fail_mode = "before"

    result = _commit(gateway, _plan())

    assert result.committed is False
    assert result.reasons == ("WRITE_NOT_APPLIED",)
    assert len(gateway.batch_calls) == 1


def test_uncertain_mixed_result_requires_manual_integrity_recovery_without_retry():
    gateway = InMemoryStatementGateway()
    gateway.fail_mode = "mixed"

    with pytest.raises(StatementWriteIntegrityError, match="manual recovery"):
        _commit(gateway, _plan())

    assert len(gateway.batch_calls) == 1


def test_google_api_gateway_submits_business_requests_in_one_batch_update():
    class Request:
        def execute(self):
            return {}

    class SheetsApi:
        def __init__(self):
            self.batch_bodies = []

        def batchUpdate(self, **kwargs):
            self.batch_bodies.append(kwargs["body"])
            return Request()

    class Service:
        def __init__(self, sheets):
            self.sheets = sheets

        def spreadsheets(self):
            return self.sheets

    sheets = SheetsApi()
    gateway = GoogleApiHistoricalInvoiceGateway({"private_key": "placeholder"})
    gateway._service = Service(sheets)
    requests = ({"updateCells": {"range": {"sheetId": 13}}},)

    gateway.batch_update("synthetic-sheet", requests)

    assert sheets.batch_bodies == [{"requests": list(requests)}]
