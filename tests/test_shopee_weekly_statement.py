from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    parse_shopee_weekly_statement,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    NEEDS_REVIEW,
    READY_TO_COMMIT,
    REJECTED,
    StatementReference,
    stage_parsed_shopee_weekly_statement,
    stage_shopee_weekly_statement,
    validate_shopee_weekly_statement,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = (
    ROOT / "tests" / "fixtures" / "shopee_weekly_statement"
    / "Income.released.my.20260427_20260503.xlsx"
)


class FakeUploadedFile:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture(scope="module")
def parsed_sample():
    before = SAMPLE.read_bytes()
    statement = parse_shopee_weekly_statement(
        FakeUploadedFile(SAMPLE.name, before)
    )
    after = SAMPLE.read_bytes()
    assert before == after
    return statement


def test_native_shopee_export_dimension_fallback_and_contract(parsed_sample):
    with ZipFile(SAMPLE) as archive:
        with archive.open("xl/worksheets/sheet5.xml") as income_xml:
            xml_prefix = income_xml.read(1024).decode("utf-8")

    assert '<dimension ref="A1"' in xml_prefix
    assert parsed_sample.dimension_fallback_sheets == ("Income",)
    assert parsed_sample.statement_period_from == date(2026, 4, 27)
    assert parsed_sample.statement_period_to == date(2026, 5, 3)
    assert len(parsed_sample.order_rows) == 505
    assert len(parsed_sample.sku_rows) == 820
    assert len(parsed_sample.service_fee_details) == 501
    assert len(parsed_sample.shipping_fee_discrepancies) == 14
    assert len(parsed_sample.adjustments) == 3
    assert parsed_sample.summary_total_released == Decimal("20599.26")
    assert parsed_sample.adjustment_control_total == Decimal("-126.63")
    assert parsed_sample.source_value_issues == ()
    assert parsed_sample.file_hash == sha256(SAMPLE.read_bytes()).hexdigest()


def test_native_sample_is_ready_and_unmatched_reconciliation_is_non_blocking(
    parsed_sample,
):
    staged = stage_parsed_shopee_weekly_statement(parsed_sample)

    assert staged.result == READY_TO_COMMIT
    assert staged.validation_issues == ()
    assert staged.eligible_for_future_atomic_commit is True
    assert staged.whole_statement_atomic is True
    assert staged.reconciliation_counts == {
        "Matched": 0,
        "Different": 0,
        "Estimated Only": 0,
        "Unmatched Order": 505,
    }
    assert Counter(item.status for item in staged.adjustment_reconciliations) == {
        "Unmatched Adjustment": 3
    }

def _replace_income_row(statement, original, replacement):
    rows = list(statement.income_rows)
    rows[rows.index(original)] = replacement
    return replace(statement, income_rows=tuple(rows))


def _validation_codes(statement):
    return {issue.code for issue in validate_shopee_weekly_statement(statement)}


def test_each_confirmed_blocking_financial_validation_detects_mismatch(parsed_sample):
    assert "order_total_vs_summary_mismatch" in _validation_codes(
        replace(
            parsed_sample,
            summary_total_released=parsed_sample.summary_total_released
            + Decimal("0.03"),
        )
    )

    order_row = parsed_sample.order_rows[0]
    components = dict(order_row.financial_components)
    components[INCOME_COMPONENT_COLUMNS[0]] += Decimal("0.03")
    assert "order_component_mismatch" in _validation_codes(
        _replace_income_row(
            parsed_sample,
            order_row,
            replace(order_row, financial_components=components),
        )
    )

    sku_row = parsed_sample.sku_rows[0]
    assert "sku_total_vs_order_mismatch" in _validation_codes(
        _replace_income_row(
            parsed_sample,
            sku_row,
            replace(
                sku_row,
                total_released_amount=sku_row.total_released_amount
                + Decimal("0.03"),
            ),
        )
    )

    detail = parsed_sample.service_fee_details[0]
    fee_components = dict(detail.components)
    first_fee = next(iter(fee_components))
    fee_components[first_fee] += Decimal("0.03")
    service_details = list(parsed_sample.service_fee_details)
    service_details[0] = replace(detail, components=fee_components)
    assert "service_fee_detail_mismatch" in _validation_codes(
        replace(parsed_sample, service_fee_details=tuple(service_details))
    )

    assert "adjustment_detail_vs_control_mismatch" in _validation_codes(
        replace(
            parsed_sample,
            adjustment_control_total=parsed_sample.adjustment_control_total
            + Decimal("0.03"),
        )
    )


def test_invalid_statement_period_is_blocking(parsed_sample):
    invalid = replace(
        parsed_sample,
        statement_period_from=parsed_sample.statement_period_to,
        statement_period_to=parsed_sample.statement_period_from,
    )
    staged = stage_parsed_shopee_weekly_statement(invalid)

    assert staged.result == NEEDS_REVIEW
    assert "invalid_statement_period" in {
        issue.code for issue in staged.validation_issues
    }
    assert staged.order_reconciliations == ()


def test_reconciliation_uses_final_estimated_and_unmatched_rules(parsed_sample):
    first, second, third, fourth = parsed_sample.order_rows[:4]
    existing_orders = [
        {
            "platform": "Shopee",
            "order_id": first.order_id,
            "income_type": "Final",
            "order_income": first.total_released_amount,
        },
        {
            "platform": "Shopee",
            "order_id": second.order_id,
            "income_type": "Final",
            "order_income": second.total_released_amount + Decimal("1.00"),
        },
        {
            "platform": "Shopee",
            "order_id": third.order_id,
            "income_type": "Estimated",
            "order_income": third.total_released_amount + Decimal("2.00"),
        },
    ]
    staged = stage_parsed_shopee_weekly_statement(
        parsed_sample, existing_orders=existing_orders
    )
    by_order = {item.order_id: item for item in staged.order_reconciliations}

    assert by_order[first.order_id].status == "Matched"
    assert by_order[second.order_id].status == "Different"
    assert by_order[third.order_id].status == "Estimated Only"
    assert by_order[fourth.order_id].status == "Unmatched Order"
    assert staged.result == READY_TO_COMMIT


def test_reconciliation_uses_rm002_tolerance_without_blocking_ready_state(
    parsed_sample,
):
    within_tolerance, beyond_tolerance = parsed_sample.order_rows[:2]
    staged = stage_parsed_shopee_weekly_statement(
        parsed_sample,
        existing_orders=[
            {
                "platform": "Shopee",
                "order_id": within_tolerance.order_id,
                "income_type": "Final",
                "order_income": within_tolerance.total_released_amount
                - Decimal("0.02"),
            },
            {
                "platform": "Shopee",
                "order_id": beyond_tolerance.order_id,
                "income_type": "Final",
                "order_income": beyond_tolerance.total_released_amount
                - Decimal("0.03"),
            },
        ],
    )
    by_order = {item.order_id: item for item in staged.order_reconciliations}

    assert by_order[within_tolerance.order_id].difference == Decimal("0.02")
    assert by_order[within_tolerance.order_id].status == "Matched"
    assert by_order[beyond_tolerance.order_id].difference == Decimal("0.03")
    assert by_order[beyond_tolerance.order_id].status == "Different"
    assert staged.result == READY_TO_COMMIT
    assert staged.validation_issues == ()
    assert "Underpayment" not in {
        item.status for item in staged.order_reconciliations
    }


def test_reconciliation_uses_shopee_platform_and_order_id_identity(parsed_sample):
    order_row = parsed_sample.order_rows[0]
    staged = stage_parsed_shopee_weekly_statement(
        parsed_sample,
        existing_orders=[
            {
                "platform": "Lazada",
                "order_id": order_row.order_id,
                "income_type": "Final",
                "order_income": order_row.total_released_amount,
            }
        ],
    )
    by_order = {item.order_id: item for item in staged.order_reconciliations}

    assert by_order[order_row.order_id].status == "Unmatched Order"
    assert staged.result == READY_TO_COMMIT


def _workbook_with_empty_income(source_bytes: bytes) -> bytes:
    output = BytesIO()
    empty_income = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/'
        b'spreadsheetml/2006/main"><dimension ref="A1"/><sheetData/></worksheet>'
    )
    with ZipFile(BytesIO(source_bytes)) as source, ZipFile(
        output, "w", ZIP_DEFLATED
    ) as destination:
        for item in source.infolist():
            if item.filename == "xl/worksheets/sheet5.xml":
                destination.writestr(item, empty_income)
            else:
                destination.writestr(item, source.read(item.filename))
    return output.getvalue()


def test_fallback_without_required_populated_income_is_rejected():
    empty_income = _workbook_with_empty_income(SAMPLE.read_bytes())
    staged = stage_shopee_weekly_statement(
        empty_income, source_filename="native-empty-income.xlsx"
    )

    assert staged.result == REJECTED
    assert staged.statement is None
    assert "Income is missing required column" in staged.rejection_reasons[0]


def test_corrupt_workbook_is_rejected():
    staged = stage_shopee_weekly_statement(
        b"not an xlsx workbook", source_filename="corrupt.xlsx"
    )

    assert staged.result == REJECTED
    assert staged.statement is None
    assert staged.rejection_reasons


def test_duplicate_gates_do_not_create_a_second_import(parsed_sample):
    exact = StatementReference(
        file_hash=parsed_sample.file_hash,
        statement_period_from=parsed_sample.statement_period_from,
        statement_period_to=parsed_sample.statement_period_to,
    )
    exact_result = stage_parsed_shopee_weekly_statement(
        parsed_sample, existing_statements=[exact]
    )

    assert exact_result.result == READY_TO_COMMIT
    assert exact_result.duplicate_status == "Exact Duplicate"
    assert exact_result.eligible_for_future_atomic_commit is False

    revised = replace(exact, file_hash="different-file-hash")
    revised_result = stage_parsed_shopee_weekly_statement(
        parsed_sample, existing_statements=[revised]
    )

    assert revised_result.result == NEEDS_REVIEW
    assert revised_result.duplicate_status == "Same Period Different File"
    assert revised_result.eligible_for_future_atomic_commit is False


def test_adjustment_linking_is_non_blocking(parsed_sample):
    linked = parsed_sample.adjustments[0].linked_order_id
    staged = stage_parsed_shopee_weekly_statement(
        parsed_sample,
        existing_orders=[
            {
                "platform": "Shopee",
                "order_id": linked,
                "income_type": "Final",
                "order_income": "0.00",
            }
        ],
    )
    counts = Counter(item.status for item in staged.adjustment_reconciliations)

    assert counts == {"Matched": 1, "Unmatched Adjustment": 2}
    assert staged.result == READY_TO_COMMIT