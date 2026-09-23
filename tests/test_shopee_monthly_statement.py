from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from openpyxl.utils.cell import range_boundaries

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeStatement,
    ServiceFeeDetail,
    SettlementAdjustment,
    SettlementIncomeRow,
    StatementSummaryLine,
)
from src.invoice_app.services.google_sheets_monthly_statement_writer import (
    GoogleSheetsMonthlyStatementWriter,
    MonthlySchemaStatus,
)
from src.invoice_app.services.shopee_monthly_statement_persistence import (
    prepare_monthly_statement_commit_plan,
)
from src.invoice_app.services.shopee_monthly_statement_service import (
    MonthlyStatementReference,
    is_full_calendar_month,
    stage_parsed_shopee_monthly_statement,
)
from src.invoice_app.services.shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitBlocked,
    StatementWriteIntegrityError,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    ALREADY_IMPORTED,
    NEEDS_REVIEW,
    READY_TO_COMMIT,
    REJECTED,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_TAB,
    MONTHLY_STATEMENT_DATA_HEADERS,
    MONTHLY_STATEMENT_DATA_TAB,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
    MONTHLY_STATEMENT_SUMMARY_HEADERS,
    MONTHLY_STATEMENT_SUMMARY_TAB,
    ORDER_ADJUSTMENTS_TAB,
    STATEMENT_DATA_HEADERS,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_SUMMARY_HEADERS,
)
from src.invoice_app.services.uat2_statement_schema_migration import (
    SheetSchema,
    SpreadsheetSchemaSnapshot,
)


def _components(product_price: str) -> dict[str, Decimal]:
    values = {name: Decimal("0.00") for name in INCOME_COMPONENT_COLUMNS}
    values["Product Price"] = Decimal(product_price)
    return values


def _statement() -> ParsedShopeeStatement:
    period_from = date(2026, 8, 1)
    period_to = date(2026, 8, 31)
    order = SettlementIncomeRow(
        sequence_no="1",
        view_by="Order",
        order_id="ORDER-1",
        product_id="",
        product_name="",
        order_creation_date=period_from,
        payout_completed_date=period_to,
        release_channel="Seller Wallet",
        order_type="Normal Order",
        total_released_amount=Decimal("10.00"),
        financial_components=_components("10.00"),
        source_values={},
        source_row_number=2,
    )
    sku = replace(
        order,
        sequence_no="2",
        view_by="Sku",
        product_id="PRODUCT-1",
        product_name="Product",
        source_row_number=3,
    )
    adjustments = (
        SettlementAdjustment(
            sequence_no="1",
            adjustment_complete_date=period_to,
            adjustment_type="New platform adjustment type",
            adjustment_reason="Adjustment/Compensation",
            adjustment_amount=Decimal("137.24"),
            linked_order_id="ORDER-X",
            payout_completed_date=period_to,
            source_row_number=14,
        ),
        SettlementAdjustment(
            sequence_no="2",
            adjustment_complete_date=period_to,
            adjustment_type="Return Refund Adjustment After Order Completed",
            adjustment_reason="Return Refund Adjustment After Order Completed",
            adjustment_amount=Decimal("-15.04"),
            linked_order_id="ORDER-Y",
            payout_completed_date=period_to,
            source_row_number=15,
        ),
    )
    return ParsedShopeeStatement(
        source_filename="Aug 2026.xlsx",
        file_hash="monthly-hash",
        statement_period_from=period_from,
        statement_period_to=period_to,
        summary_total_released=Decimal("10.00"),
        adjustment_control_total=Decimal("122.20"),
        adjustment_footer_total=Decimal("122.20"),
        income_rows=(order, sku),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=adjustments,
        source_value_issues=(),
        dimension_fallback_sheets=(),
        summary_lines=(
            StatementSummaryLine(
                statement_source_sheet="Summary",
                statement_source_row_number=10,
                native_label="3. Total Released Amount",
                line_type="TOTAL",
                parent_source_row_number=None,
                component_amount=Decimal("10.00"),
                currency="RM",
            ),
        ),
    )


def _audit() -> StatementBatchAudit:
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    return StatementBatchAudit("monthly-batch", now, "Admin", now)


class MonthlyGateway:
    def __init__(
        self, *, with_monthly_schema: bool = True, trim_trailing_blanks: bool = False
    ) -> None:
        self.schema = {
            "Existing": SheetSchema(1, ("existing",)),
        }
        self.tabs = {"Existing": [["existing"], ["unchanged"]]}
        if with_monthly_schema:
            self._install_monthly_schema()
        self.value_calls: list[tuple[dict, ...]] = []
        self.schema_calls: list[tuple[dict, ...]] = []
        self.fail_mode: str | None = None
        self.trim_trailing_blanks = trim_trailing_blanks

    def _install_monthly_schema(self) -> None:
        for offset, (tab, headers) in enumerate(
            (
                (MONTHLY_STATEMENT_DATA_TAB, MONTHLY_STATEMENT_DATA_HEADERS),
                (
                    MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
                    MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
                ),
                (MONTHLY_STATEMENT_SUMMARY_TAB, MONTHLY_STATEMENT_SUMMARY_HEADERS),
            ),
            start=2,
        ):
            self.schema[tab] = SheetSchema(offset, headers)
            self.tabs[tab] = [list(headers)]

    def read_schema(self, _spreadsheet_id):
        return SpreadsheetSchemaSnapshot(dict(self.schema))

    def apply_schema_migration(self, _spreadsheet_id, requests):
        self.schema_calls.append(tuple(requests))
        additions = [request for request in requests if "addSheet" in request]
        for request in additions:
            props = request["addSheet"]["properties"]
            tab = props["title"]
            headers = {
                MONTHLY_STATEMENT_DATA_TAB: MONTHLY_STATEMENT_DATA_HEADERS,
                MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB: MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS,
                MONTHLY_STATEMENT_SUMMARY_TAB: MONTHLY_STATEMENT_SUMMARY_HEADERS,
            }[tab]
            self.schema[tab] = SheetSchema(props["sheetId"], headers)
            self.tabs[tab] = [list(headers)]

    def read_tabs(self, _spreadsheet_id, tabs):
        values = {tab: [list(row) for row in self.tabs[tab]] for tab in tabs}
        if self.trim_trailing_blanks:
            for rows in values.values():
                for row in rows:
                    while row and row[-1] in (None, ""):
                        row.pop()
        return values

    def batch_update_values(self, _spreadsheet_id, data):
        self.value_calls.append(tuple(data))
        selected = data[:1] if self.fail_mode == "mixed" else data
        for value_range in selected:
            tab = value_range["range"].split("!", 1)[0][1:-1]
            _, min_row, _, _ = range_boundaries(value_range["range"].split("!", 1)[1])
            start = min_row - 1
            for offset, values in enumerate(value_range["values"]):
                while len(self.tabs[tab]) <= start + offset:
                    self.tabs[tab].append([])
                self.tabs[tab][start + offset] = list(values)
        if self.fail_mode in {"mixed", "after"}:
            raise OSError("uncertain write")


@pytest.mark.parametrize(
    "period_from,period_to,expected",
    [
        (date(2026, 8, 1), date(2026, 8, 31), True),
        (date(2026, 2, 1), date(2026, 2, 28), True),
        (date(2024, 2, 1), date(2024, 2, 29), True),
        (date(2026, 8, 17), date(2026, 8, 23), False),
        (date(2026, 8, 1), date(2026, 8, 30), False),
        (date(2026, 8, 31), date(2026, 9, 6), False),
    ],
)
def test_full_calendar_month_rule(period_from, period_to, expected):
    assert is_full_calendar_month(period_from, period_to) is expected


def test_wrong_period_is_source_level_rejection_with_weekly_guidance():
    statement = replace(
        _statement(),
        statement_period_from=date(2026, 8, 17),
        statement_period_to=date(2026, 8, 23),
    )
    stage = stage_parsed_shopee_monthly_statement(statement)

    assert stage.result == REJECTED
    assert stage.source_error is not None
    assert stage.source_error.code == "WRONG_STATEMENT_GRANULARITY"
    assert stage.source_error.technical_message == (
        "This Statement covers 17/08/2026–23/08/2026 and is not a full monthly "
        "Statement. Upload it using Shopee Weekly Statement."
    )


def test_monthly_internal_validation_has_no_invoice_coverage_dependency():
    stage = stage_parsed_shopee_monthly_statement(_statement())

    assert stage.result == READY_TO_COMMIT
    assert stage.commit_ready is True
    assert not hasattr(stage, "order_reconciliations")


def test_monthly_duplicate_and_revision_use_only_monthly_references():
    statement = _statement()
    exact = MonthlyStatementReference(
        statement.file_hash, statement.statement_period_from, statement.statement_period_to
    )
    other_hash = replace(exact, file_hash="different-hash")

    assert stage_parsed_shopee_monthly_statement(
        statement, existing_monthly_statements=(exact,)
    ).result == ALREADY_IMPORTED
    revision = stage_parsed_shopee_monthly_statement(
        statement, existing_monthly_statements=(other_hash,)
    )
    assert revision.result == NEEDS_REVIEW
    assert revision.duplicate_status == "POSSIBLE_REVISION"


def test_monthly_plan_preserves_open_ended_adjustments_and_common_schema():
    statement = _statement()
    plan = prepare_monthly_statement_commit_plan(
        stage_parsed_shopee_monthly_statement(statement), audit=_audit()
    )
    adjustment_rows = [row for row in plan.rows if row[1] == "ADJUSTMENT"]
    positions = {
        header: index for index, header in enumerate(MONTHLY_STATEMENT_DATA_HEADERS)
    }

    assert MONTHLY_STATEMENT_DATA_HEADERS == STATEMENT_DATA_HEADERS[:34]
    assert MONTHLY_STATEMENT_FINANCIAL_COMPONENT_HEADERS == STATEMENT_FINANCIAL_COMPONENT_HEADERS
    assert MONTHLY_STATEMENT_SUMMARY_HEADERS == STATEMENT_SUMMARY_HEADERS
    assert len(adjustment_rows) == 2
    assert sum(
        Decimal(row[positions["adjustment_amount"]]) for row in adjustment_rows
    ) == Decimal("122.20")
    assert adjustment_rows[0][positions["adjustment_reason"]] == "Adjustment/Compensation"
    assert adjustment_rows[0][positions["adjustment_type"]] == "New platform adjustment type"


def test_monthly_schema_creation_adds_only_three_monthly_tabs():
    gateway = MonthlyGateway(with_monthly_schema=False)
    writer = GoogleSheetsMonthlyStatementWriter(
        spreadsheet_id="sheet", gateway=gateway
    )

    assert writer.ensure_schema() is MonthlySchemaStatus.CREATED
    assert set(gateway.schema) == {
        "Existing",
        MONTHLY_STATEMENT_DATA_TAB,
        MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
        MONTHLY_STATEMENT_SUMMARY_TAB,
    }
    assert gateway.tabs["Existing"] == [["existing"], ["unchanged"]]
    add_requests = [
        request["addSheet"]["properties"]
        for request in gateway.schema_calls[0]
        if "addSheet" in request
    ]
    assert next(
        item for item in add_requests if item["title"] == MONTHLY_STATEMENT_DATA_TAB
    )["gridProperties"]["columnCount"] == 34


def test_monthly_atomic_write_touches_only_monthly_tabs():
    gateway = MonthlyGateway(trim_trailing_blanks=True)
    writer = GoogleSheetsMonthlyStatementWriter(
        spreadsheet_id="sheet", gateway=gateway
    )
    plan = prepare_monthly_statement_commit_plan(
        stage_parsed_shopee_monthly_statement(_statement()), audit=_audit()
    )

    result = writer.write_monthly_batch(plan)

    assert result.data_rows == 4
    assert len(gateway.value_calls) == 1
    ranges = {item["range"].split("!", 1)[0][1:-1] for item in gateway.value_calls[0]}
    assert ranges == {
        MONTHLY_STATEMENT_DATA_TAB,
        MONTHLY_STATEMENT_FINANCIAL_COMPONENTS_TAB,
        MONTHLY_STATEMENT_SUMMARY_TAB,
    }
    assert not ranges & {
        INVOICE_ORDERS_TAB,
        INVOICE_ITEMS_TAB,
        ORDER_ADJUSTMENTS_TAB,
        "Statement_Data",
        "Statement_Financial_Components",
        "Statement_Summary",
    }


def test_monthly_mixed_write_never_reports_success():
    gateway = MonthlyGateway()
    gateway.fail_mode = "mixed"
    writer = GoogleSheetsMonthlyStatementWriter(
        spreadsheet_id="sheet", gateway=gateway
    )
    plan = prepare_monthly_statement_commit_plan(
        stage_parsed_shopee_monthly_statement(_statement()), audit=_audit()
    )

    with pytest.raises(StatementWriteIntegrityError):
        writer.write_monthly_batch(plan)


def test_monthly_header_mismatch_fails_before_write():
    gateway = MonthlyGateway()
    gateway.schema[MONTHLY_STATEMENT_DATA_TAB] = SheetSchema(2, ("wrong",))
    writer = GoogleSheetsMonthlyStatementWriter(
        spreadsheet_id="sheet", gateway=gateway
    )

    with pytest.raises(StatementCommitBlocked, match="exact approved schema"):
        writer.committed_references()
    assert gateway.value_calls == []
