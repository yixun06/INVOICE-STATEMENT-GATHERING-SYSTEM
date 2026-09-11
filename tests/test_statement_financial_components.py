from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceOrder
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    ServiceFeeDetail,
    SettlementIncomeRow,
    ShippingFeeDiscrepancy,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
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
    STATEMENT_DATA_HEADERS,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
)
from tests.test_google_sheets_statement_writer import InMemoryStatementGateway


def _components(*, sku: bool = False) -> dict[str, Decimal | None]:
    values = {name: Decimal("0.00") for name in INCOME_COMPONENT_COLUMNS}
    values["Product Price"] = Decimal("30.00")
    values["Refund Amount"] = Decimal("-2.00")
    values["Commission Fee (incl. SST)"] = Decimal("-1.20")
    values["Reverse Shipping Fee"] = Decimal("-3.00")
    values["AMS Commission Fee"] = Decimal("-5.95")
    values["Ads Escrow Top Up Fee"] = Decimal("-14.85")
    if sku:
        values["Saver Programme Fee (Incl. SST)"] = None
    return values


def _income(*, view_by: str, sequence: str, row_number: int) -> SettlementIncomeRow:
    return SettlementIncomeRow(
        sequence_no=sequence,
        view_by=view_by,
        order_id="ORDER-1",
        product_id="PRODUCT-1",
        product_name="Green Tea",
        order_creation_date=date(2026, 9, 1),
        payout_completed_date=date(2026, 9, 8),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal("10.00"),
        financial_components=_components(sku=view_by == "Sku"),
        source_values={},
        source_row_number=row_number,
    )


def _statement(*, sku_rows: int = 1) -> ParsedShopeeWeeklyStatement:
    order = _income(view_by="Order", sequence="1", row_number=2)
    skus = tuple(
        _income(view_by="Sku", sequence=str(index + 1), row_number=3 + index)
        for index in range(sku_rows)
    )
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash="component-hash",
        statement_period_from=date(2026, 9, 1),
        statement_period_to=date(2026, 9, 8),
        summary_total_released=Decimal("10.00"),
        adjustment_control_total=Decimal("0.00"),
        adjustment_footer_total=None,
        income_rows=(order, *skus),
        service_fee_details=(
            ServiceFeeDetail(
                sequence_no="1",
                order_id="ORDER-1",
                components={"Platform Support Fee": Decimal("0.00"), "Dynamic Fee": Decimal("-1.50")},
                source_row_number=30,
            ),
        ),
        shipping_fee_discrepancies=(
            ShippingFeeDiscrepancy(
                order_id="ORDER-1",
                expected_shipping_fee=Decimal("0.00"),
                actual_shipping_fee=Decimal("-3.00"),
                reason="Parcel size discrepancy",
                source_values={},
                source_row_number=40,
            ),
        ),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )


def _plan(*, sku_rows: int = 1):
    statement = _statement(sku_rows=sku_rows)
    audit_time = datetime(2026, 9, 9, tzinfo=timezone.utc)
    return prepare_statement_commit_plan(
        statement,
        audit=StatementBatchAudit("component-batch", audit_time, "admin@test", audit_time),
        invoice_orders=(
            CanonicalInvoiceOrder(
                platform="Shopee",
                order_id="ORDER-1",
                final_amount=Decimal("10.00"),
                order_income=Decimal("10.00"),
                income_type="Final",
            ),
        ),
        sku_matches=StatementItemMatchBatch(
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
        ),
        validation_passed=True,
    )


def _ledger_records(plan):
    return [
        dict(zip(STATEMENT_FINANCIAL_COMPONENT_HEADERS, row))
        for row in plan.financial_component_rows
    ]


def test_exact_component_schema_is_preserved_without_changing_statement_data_schema():
    assert len(STATEMENT_FINANCIAL_COMPONENT_HEADERS) == 17
    assert STATEMENT_FINANCIAL_COMPONENT_HEADERS[0] == "statement_batch_id"
    assert STATEMENT_FINANCIAL_COMPONENT_HEADERS[-1] == "commit_status"
    assert len(STATEMENT_DATA_HEADERS) == 40


def test_income_components_preserve_order_sku_signed_zero_and_none_contracts():
    records = _ledger_records(_plan())
    order = [row for row in records if row["record_type"] == "ORDER"]
    sku = [row for row in records if row["record_type"] == "SKU"]

    assert {row["component_name"] for row in order} == set(INCOME_COMPONENT_COLUMNS)
    assert {row["component_name"] for row in sku} == set(INCOME_COMPONENT_COLUMNS) - {"Saver Programme Fee (Incl. SST)"}
    assert next(row for row in order if row["component_name"] == "AMS Commission Fee")["component_amount"] == "-5.95"
    assert next(row for row in sku if row["component_name"] == "Ads Escrow Top Up Fee")["component_amount"] == "-14.85"
    assert next(row for row in order if row["component_name"] == "Transaction Fee (Incl. SST)")["component_amount"] == "0.00"
    assert all(row["component_amount"] or row["component_note"] for row in records)


def test_product_price_and_refund_ledger_values_agree_with_statement_projection():
    plan = _plan()
    ledger = _ledger_records(plan)
    order_projection = dict(zip(STATEMENT_DATA_HEADERS, plan.rows[0]))
    sku_projection = dict(zip(STATEMENT_DATA_HEADERS, plan.rows[1]))
    for record_type, projection in (("ORDER", order_projection), ("SKU", sku_projection)):
        rows = {row["component_name"]: row for row in ledger if row["record_type"] == record_type}
        assert rows["Product Price"]["component_amount"] == projection["statement_product_price"]
        assert rows["Refund Amount"]["component_amount"] == projection["statement_refund_amount"]


def test_service_fee_and_shipping_discrepancy_evidence_are_preserved():
    records = _ledger_records(_plan())
    by_name = {(row["record_type"], row["component_name"]): row for row in records}

    assert by_name[("SERVICE_FEE_DETAIL", "Dynamic Fee")]["component_amount"] == "-1.50"
    assert by_name[("SERVICE_FEE_DETAIL", "Platform Support Fee")]["component_amount"] == "0.00"
    assert by_name[("SHIPPING_FEE_DISCREPANCY", "Expected Shipping Fee:")]["component_amount"] == "0.00"
    assert by_name[("SHIPPING_FEE_DISCREPANCY", "Actual Shipping Fee Charged by Logistic Provider:")]["component_amount"] == "-3.00"
    assert by_name[("SHIPPING_FEE_DISCREPANCY", "Discrepancy reason")]["component_note"] == "Parcel size discrepancy"


def test_order_and_sku_components_have_distinct_stable_identities_and_multiple_sku_rows_survive():
    records = _ledger_records(_plan(sku_rows=2))
    identities = {
        (
            row["statement_batch_id"],
            row["statement_source_sheet"],
            row["statement_source_row_number"],
            row["component_name"],
        )
        for row in records
    }

    assert len(identities) == len(records)
    assert sum(row["record_type"] == "SKU" for row in records) == 40


def test_duplicate_component_identity_blocks_the_entire_google_write():
    plan = _plan()
    duplicate_plan = replace(
        plan,
        financial_component_rows=(
            plan.financial_component_rows[0],
            plan.financial_component_rows[0],
        ),
    )
    gateway = InMemoryStatementGateway()
    writer = GoogleSheetsStatementWriter(spreadsheet_id="synthetic-sheet", gateway=gateway)

    with pytest.raises(StatementCommitBlocked, match="duplicate stable identity"):
        write_google_statement_plan_if_current(
            duplicate_plan, writer=writer, sku_matching_is_current=lambda: True
        )

    assert gateway.batch_calls == []
