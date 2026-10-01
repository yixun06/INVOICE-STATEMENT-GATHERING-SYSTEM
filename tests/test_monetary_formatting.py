from decimal import Decimal

import pytest

from src.invoice_app.utils.monetary import (
    excel_currency_format,
    format_currency,
    streamlit_currency_format,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (0, "RM 0.00"),
        (1, "RM 1.00"),
        (1000, "RM 1,000.00"),
        (Decimal("30169.02"), "RM 30,169.02"),
        (1234567.8, "RM 1,234,567.80"),
        (Decimal("-30169.02"), "RM -30,169.02"),
    ),
)
def test_format_currency_uses_grouping_and_two_decimals(value, expected):
    assert format_currency(value) == expected


def test_format_currency_supports_sgd_without_enabling_sg_data_flow():
    assert format_currency(Decimal("30169.02"), "SGD") == "S$ 30,169.02"


def test_format_currency_preserves_missing_evidence():
    assert format_currency(None) is None
    assert format_currency(None, missing_value="N/A") == "N/A"
    assert format_currency(float("nan"), missing_value="—") == "—"


def test_presentation_formats_keep_numeric_values_at_ui_and_excel_boundaries():
    assert streamlit_currency_format("MYR") == "RM %,.2f"
    assert streamlit_currency_format("SGD") == "S$ %,.2f"
    assert excel_currency_format("MYR") == '"RM" #,##0.00;[Red]"RM" -#,##0.00'
