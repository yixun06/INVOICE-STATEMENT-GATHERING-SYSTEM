from datetime import date, datetime
from pathlib import Path


def test_calendar_component_decodes_only_iso_date_endpoints():
    from src.invoice_app.ui.available_payout_date_calendar import (
        component_date_value,
    )

    assert component_date_value({"value": "2026-08-10"}) == date(2026, 8, 10)
    assert component_date_value({"value": datetime(2026, 8, 10)}) is None
    assert component_date_value({"value": "not-a-date"}) is None


def test_calendar_uses_native_disabled_buttons_in_a_popup_date_input():
    from src.invoice_app.ui import available_payout_date_calendar as calendar

    source = Path(calendar.__file__).read_text(encoding="utf-8")

    assert "button.disabled = disabledValue;" in source
    assert "!allowed.has(day)" in source
    assert "disabled(dayIso)" in source
    assert "picker__input" in source
    assert "picker__panel" in source
    assert 'data.label || "Payout date"' in source
    assert "position: absolute" in source
