from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.parsers.shopee_weekly_statement_parser import ADJUSTMENT_REQUIRED_COLUMNS, _parse_adjustment_rows
from src.invoice_app.domain.order_adjustment import make_statement_adjustment
from src.invoice_app.services.shopee_statement_persistence import _classify_adjustment_events, serialize_order_adjustment
from src.invoice_app.services.uat2_persistence_schema import ORDER_ADJUSTMENTS_HEADERS
from src.invoice_app.services.postgres_statement_repository import _insert_values
from src.invoice_app.pg_orm.pg_models import OrderAdjustment
from sqlalchemy import create_engine, select


@pytest.mark.parametrize("payout", [None, "", "2026-04-12"])
def test_adjustment_payout_is_optional_or_valid(payout):
    issues = []
    rows = [ADJUSTMENT_REQUIRED_COLUMNS, ("1", "2026-04-10", "Compensation as Parcel was Lost", "", "87.90", "260321NP2UWT7W", payout)]
    parsed = _parse_adjustment_rows(rows, issues)
    assert not issues
    assert parsed[0].payout_completed_date == (date(2026, 4, 12) if payout else None)
    assert parsed[0].adjustment_amount == Decimal("87.90")


@pytest.mark.parametrize(("complete", "payout", "code"), [(None, None, "invalid_adjustment_complete_date"), ("2026-04-10", "bad", "invalid_adjustment_payout_date")])
def test_adjustment_invalid_dates_remain_blocking(complete, payout, code):
    issues = []
    _parse_adjustment_rows([ADJUSTMENT_REQUIRED_COLUMNS, ("1", complete, "Compensation", "", "87.90", "ORDER", payout)], issues)
    assert code in {issue.code for issue in issues}


def test_null_payout_event_identity_and_conflict():
    args = dict(linked_order_id="ORDER", adjustment_description="Compensation", adjustment_reason=None, adjustment_complete_date=date(2026, 4, 10), adjustment_amount=Decimal("87.90"), payout_completed_date=None, statement_batch_id="batch", statement_sequence_no="1", statement_source_filename="source.xlsx", statement_file_hash="hash", first_observed_at=datetime(2026, 4, 10, tzinfo=timezone.utc))
    event = make_statement_adjustment(**args)
    assert event.payout_completed_date is None
    assert serialize_order_adjustment(event)[ORDER_ADJUSTMENTS_HEADERS.index("payout_completed_date")] == ""
    engine = create_engine("sqlite:///:memory:")
    OrderAdjustment.__table__.create(engine)
    with engine.begin() as connection:
        _insert_values(connection, OrderAdjustment, ORDER_ADJUSTMENTS_HEADERS, [serialize_order_adjustment(event)])
        assert connection.execute(select(OrderAdjustment.__table__.c.payout_completed_date)).scalar_one() is None
    assert _classify_adjustment_events([event], [event])[1:] == (1, 0)
    changed = make_statement_adjustment(**(args | {"adjustment_amount": Decimal("88.00")}))
    assert _classify_adjustment_events([changed], [event])[1:] == (0, 1)
