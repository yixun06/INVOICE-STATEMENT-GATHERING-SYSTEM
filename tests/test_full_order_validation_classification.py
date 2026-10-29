from dataclasses import replace

import pytest

from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot, _classify,
)
from src.invoice_app.services.market_context import SHOPEE_MY
from src.invoice_app.services.shopee_full_order_adapter import adapt_shopee_full_order
from src.invoice_app.services.shopee_full_order_import import review_shopee_full_order_upload
from src.invoice_app.parsers.shopee_full_order_parser import parse_shopee_full_order
from tests.test_shopee_full_order_sync import _master, _rows, _workbook_bytes


class Repository:
    def __init__(self, snapshot, fail=False):
        self.snapshot = snapshot
        self.fail = fail
        self.calls = 0

    def classify(self, batch):
        self.calls += 1
        if self.fail:
            raise RuntimeError("database unavailable")
        return _classify(batch, self.snapshot)


def _source():
    base = list(_rows()[0])
    rows = []
    for order_id in ("NEW", "UPDATED", "UNCHANGED", "REVIEW"):
        row = base.copy()
        row[0] = order_id
        if order_id == "REVIEW":
            row[4] = row[6] = "MISSING"
            row[5] = "Unknown item"
        rows.append(row)
    rows.append(base.copy()[:0] + list(_rows()[1]))
    rows[-1][0] = "REVIEW"
    rows.append(list(rows[3]))
    return _workbook_bytes(rows)


@pytest.mark.parametrize("mode, expected", [("new", (3, 0, 0)), ("unchanged", (0, 0, 3)), ("mixed", (1, 1, 1))])
def test_complete_orders_classified_once_with_blocked_multi_item_order(mode, expected):
    source = _source()
    parsed = parse_shopee_full_order(source, source_filename="test.xlsx", market=SHOPEE_MY)
    batch = adapt_shopee_full_order(parsed, product_master=_master())
    stored = []
    for order in batch.orders:
        if mode == "unchanged" or (mode == "mixed" and order.order_id != "NEW"):
            stored.append(replace(order, snapshot_hash="changed" if mode == "mixed" and order.order_id == "UPDATED" else order.snapshot_hash))
    repo = Repository(FullOrderCurrentSnapshot(tuple(stored), ()))
    review = review_shopee_full_order_upload(source, source_filename="test.xlsx", market=SHOPEE_MY, product_master=_master(), repository=repo)
    counts = (review.sync_preview.new_count, review.sync_preview.updated_count, review.sync_preview.unchanged_count)
    assert counts == expected
    assert len({issue.order_id for issue in review.current_batch.issues}) == 1
    assert len(review.current_batch.issues) == 2
    assert sum(counts) + 1 == len(review.parsed.order_ids) == 4
    assert not review.ready and repo.calls == 1


def test_comparison_failure_is_unavailable_and_blocks_commit():
    review = review_shopee_full_order_upload(_source(), source_filename="test.xlsx", market=SHOPEE_MY, product_master=_master(), repository=Repository(None, fail=True))
    assert review.sync_preview is None
    assert review.comparison_error and not review.ready


def test_multiple_valid_items_remain_one_order():
    source = _workbook_bytes(_rows())
    repo = Repository(FullOrderCurrentSnapshot((), ()))
    review = review_shopee_full_order_upload(source, source_filename="test.xlsx", market=SHOPEE_MY, product_master=_master(), repository=repo)
    assert len(review.current_batch.items) == 2
    assert len(review.parsed.order_ids) == review.sync_preview.new_count == 1
    assert review.ready
