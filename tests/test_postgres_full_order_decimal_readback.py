from dataclasses import replace
from decimal import Decimal

import pytest

from src.invoice_app.repositories.google_sheets_historical_invoice_repository import HistoricalInvoiceStorageError
from src.invoice_app.repositories.postgres_full_order_current_repository import (
    _item_from_row, _item_values, _same_postgres_snapshot,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import FullOrderCurrentSnapshot
from test_postgres_full_order_runtime import _batch, _repository


@pytest.mark.parametrize("left,right,expected", [
    (Decimal("0.00"), Decimal("0"), True),
    (Decimal("1.2"), Decimal("1.20"), True),
    (Decimal("1.20"), Decimal("1.21"), False),
    (None, Decimal("0"), False),
])
def test_decimal_comparison_preserves_numeric_identity(left, right, expected):
    item = _batch(items=1).items[0]
    a = FullOrderCurrentSnapshot((), (replace(item, unit_price=left),))
    b = FullOrderCurrentSnapshot((), (replace(item, unit_price=right),))
    assert _same_postgres_snapshot(a, b) is expected


def test_multi_item_zero_gift_and_zero_subtotal_roundtrip():
    batch = _batch(items=2)
    gift = replace(batch.items[1], product_name="[Not for sale] Mini Trial Pack",
                   resolved_seller_sku="5000000", nav="5000000",
                   resolve_sku="5000000-free gift", unit_price=Decimal("0.00"),
                   product_subtotal=Decimal("0.00"))
    batch = replace(batch, items=(batch.items[0], gift))
    repository = _repository()
    assert repository.sync(batch).new_count == 1
    actual = repository.read_snapshot().items
    assert actual[0].unit_price == Decimal("6.00")
    assert actual[1].unit_price == Decimal("0.00")
    assert actual[1].product_subtotal == Decimal("0.00")
    assert actual[1].resolve_sku == "5000000-free gift"


@pytest.mark.parametrize("field", ["unit_price", "product_subtotal", "quantity"])
@pytest.mark.parametrize("missing", [None, ""])
def test_missing_numeric_readback_fails_closed(field, missing):
    values = _item_values(_batch(items=1).items[0])
    values[field] = missing
    with pytest.raises((HistoricalInvoiceStorageError, ValueError)):
        _item_from_row(values)


def test_genuine_mismatch_rolls_back_entire_transaction(monkeypatch):
    repository = _repository()
    original = repository._read_snapshot
    calls = 0

    def drift(connection):
        nonlocal calls
        calls += 1
        snapshot = original(connection)
        if calls == 2:
            changed = replace(snapshot.items[0], unit_price=Decimal("6.01"))
            return replace(snapshot, items=(changed, *snapshot.items[1:]))
        return snapshot

    monkeypatch.setattr(repository, "_read_snapshot", drift)
    with pytest.raises(HistoricalInvoiceStorageError, match="readback did not match"):
        repository.sync(_batch())
    state = repository.read_snapshot()
    assert not state.orders and not state.items
