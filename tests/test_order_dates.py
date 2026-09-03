from datetime import date

from src.invoice_app.utils.order_dates import shopee_order_date_from_id


def test_shopee_order_id_date_prefix_requires_a_real_calendar_date():
    assert shopee_order_date_from_id("260828J247W9SW") == date(2026, 8, 28)
    assert shopee_order_date_from_id("260231INVALID") is None
    assert shopee_order_date_from_id("SHP-260828") is None
