from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Event, Thread

import pytest

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceOrder
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    SettlementIncomeRow,
)
from src.invoice_app.services.shopee_statement_item_matching import (
    StatementItemMatch,
    StatementItemMatchBatch,
    StatementItemMatchStatus,
)
from src.invoice_app.services.shopee_statement_persistence import (
    STATEMENT_DATA_HEADERS,
    CommittedStatementReference,
    StatementBatchAudit,
    StatementCommitBlocked,
    StatementCommitState,
    prepare_statement_commit_plan,
    validate_current_statement_state,
    write_statement_plan_if_current,
)
from src.invoice_app.services.application_commit_lock import (
    ApplicationCommitInProgress,
    ApplicationCommitLock,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    stage_parsed_shopee_weekly_statement,
)


def _income(*, view_by, sequence, row_number, order_id="ORDER-1", released="10.00", product_price="10.00", refund="0.00"):
    return SettlementIncomeRow(
        sequence_no=sequence, view_by=view_by, order_id=order_id,
        product_id="PRODUCT-1", product_name="Green Tea",
        order_creation_date=date(2026, 8, 1), payout_completed_date=date(2026, 8, 8),
        release_channel="Seller Wallet", order_type="Normal",
        total_released_amount=Decimal(released),
        financial_components={"Product Price": Decimal(product_price), "Refund Amount": Decimal(refund)},
        source_values={}, source_row_number=row_number,
    )


def _statement(*, sku_rows=1, adjustments=True):
    order = _income(view_by="Order", sequence="1", row_number=2)
    skus = tuple(
        _income(view_by="Sku", sequence=str(index + 1), row_number=3 + index)
        for index in range(sku_rows)
    )
    adjustment_rows = (
        SettlementAdjustment("1", date(2026, 8, 7), "Adjustment", "Reason", Decimal("-1.00"), "ORDER-1", date(2026, 8, 8), 20),
    ) if adjustments else ()
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx", file_hash="hash-1",
        statement_period_from=date(2026, 8, 1), statement_period_to=date(2026, 8, 8),
        summary_total_released=Decimal("10.00"), adjustment_control_total=Decimal("-1.00") if adjustments else Decimal("0.00"),
        adjustment_footer_total=None, income_rows=(order, *skus), service_fee_details=(),
        shipping_fee_discrepancies=(), adjustments=adjustment_rows,
        source_value_issues=(), dimension_fallback_sheets=(),
    )


def _order(*, final_amount="10.00", order_income="9.00", income_type="Estimated"):
    return CanonicalInvoiceOrder(
        platform="Shopee", order_id="ORDER-1", final_amount=Decimal(final_amount) if final_amount is not None else None,
        order_income=Decimal(order_income) if order_income is not None else None, income_type=income_type,
    )


def _matches(statement):
    return StatementItemMatchBatch(
        matches=tuple(
            StatementItemMatch(
                statement_source_row=row.source_row_number, order_id=row.order_id,
                product_id=row.product_id, product_name=row.product_name,
                status=StatementItemMatchStatus.MATCHED, invoice_item_index=1,
                match_method="EXACT_PRODUCT_NAME", reason="Deterministic match.",
            ) for row in statement.sku_rows
        ),
        eligible_for_commit=True,
    )


def _audit():
    now = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    return StatementBatchAudit("batch-1", now, "admin@example.test", now)


def _plan(*, statement=None, order=None):
    statement = statement or _statement()
    return prepare_statement_commit_plan(
        statement, audit=_audit(), invoice_orders=[order or _order()],
        sku_matches=_matches(statement), validation_passed=True,
    )


def test_statement_data_headers_are_exact_approved_40_column_order():
    assert len(STATEMENT_DATA_HEADERS) == 40
    assert STATEMENT_DATA_HEADERS[:4] == (
        "statement_batch_id", "record_type", "sequence_no", "platform"
    )
    assert STATEMENT_DATA_HEADERS[-4:] == (
        "difference", "reconciliation_status", "matched_item_index", "match_method"
    )


def test_order_sku_adjustment_rows_serialize_in_schema_order():
    plan = _plan()
    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}

    assert len(plan.rows) == 3
    assert all(len(row) == 40 for row in plan.rows)
    assert [row[positions["record_type"]] for row in plan.rows] == ["ORDER", "SKU", "ADJUSTMENT"]
    assert plan.rows[0][positions["validation_status"]] == "PASSED"
    assert plan.rows[0][positions["commit_status"]] == "COMMITTED"
    assert plan.rows[1][positions["match_method"]] == "Product Name"
    assert plan.rows[2][positions["adjustment_amount"]] == "-1.00"


def test_adjustment_evidence_never_rewrites_original_invoice_business_facts():
    original = _order(final_amount="10.00", order_income="9.00")
    plan = _plan(statement=_statement(adjustments=True), order=original)

    assert original.final_amount == Decimal("10.00")
    assert original.order_income == Decimal("9.00")
    assert not hasattr(plan.invoice_order_updates[0], "final_amount")
    assert not hasattr(plan.invoice_order_updates[0], "refund_amount")
    assert not hasattr(plan.invoice_order_updates[0], "order_income")
    assert set(plan.invoice_order_updates[0].__dataclass_fields__) == {
        "order_id",
        "payout_completed_date",
        "payment_status",
        "difference",
    }


def test_final_amount_has_priority_and_difference_is_signed():
    plan = _plan(order=_order(final_amount="12.00", order_income="10.00"))
    comparison = plan.order_comparisons[0]

    assert comparison.comparison_source == "Final Amount"
    assert comparison.comparison_amount == Decimal("12.00")
    assert comparison.difference == Decimal("-2.00")
    assert comparison.reconciliation_status == "DIFFERENT"


def test_estimated_order_income_remains_estimated_only_and_is_not_relabelled():
    plan = _plan(order=_order(final_amount=None, order_income="10.00", income_type="Estimated"))

    assert plan.order_comparisons[0].comparison_source == "Order Income"
    assert plan.order_comparisons[0].reconciliation_status == "ESTIMATED_ONLY"
    status_index = STATEMENT_DATA_HEADERS.index("reconciliation_status")
    assert plan.rows[0][status_index] == "ESTIMATED_ONLY"
    assert not hasattr(plan.invoice_order_updates[0], "income_type")
    assert plan.invoice_order_updates[0].payment_status == "RELEASED"


def test_staging_and_persistence_share_the_same_estimated_decision():
    statement = _statement()
    order = _order(final_amount=None, order_income="12.00", income_type="Estimated")
    staged = stage_parsed_shopee_weekly_statement(
        statement,
        existing_orders=[
            {
                "platform": order.platform,
                "order_id": order.order_id,
                "final_amount": order.final_amount,
                "order_income": order.order_income,
                "income_type": order.income_type,
            }
        ],
    )
    plan = _plan(statement=statement, order=order)

    staged_comparison = staged.order_reconciliations[0]
    persisted_comparison = plan.order_comparisons[0]
    assert staged_comparison.status == "Estimated Only"
    assert persisted_comparison.reconciliation_status == "ESTIMATED_ONLY"
    assert staged_comparison.comparison_source == persisted_comparison.comparison_source
    assert staged_comparison.comparison_amount == persisted_comparison.comparison_amount
    assert staged_comparison.difference == persisted_comparison.difference


def test_unmatched_order_blocks_commit_plan():
    with pytest.raises(StatementCommitBlocked, match="coverage"):
        prepare_statement_commit_plan(
            _statement(), audit=_audit(), invoice_orders=(),
            sku_matches=_matches(_statement()), validation_passed=True,
        )


def test_existing_order_without_comparison_evidence_blocks_separately():
    with pytest.raises(StatementCommitBlocked, match="comparison evidence"):
        _plan(order=_order(final_amount=None, order_income=None))


def test_single_sku_match_creates_one_invoice_item_enrichment():
    plan = _plan()

    assert plan.invoice_item_updates == (
        plan.invoice_item_updates[0],
    )
    assert plan.invoice_item_updates[0].statement_net_selling_amount == Decimal("10.00")


def test_repeated_sku_rows_are_preserved_without_invoice_item_aggregation():
    statement = _statement(sku_rows=2)
    plan = _plan(statement=statement)

    assert [row[1] for row in plan.rows].count("SKU") == 2
    assert plan.invoice_item_updates == ()


def test_only_committed_duplicate_or_revision_blocks_fresh_preflight():
    plan = _plan()
    state = StatementCommitState(
        orders={"ORDER-1": _order()},
        committed_statements=(
            CommittedStatementReference("hash-1", date(2026, 8, 1), date(2026, 8, 8)),
            CommittedStatementReference("ignored", date(2026, 8, 1), date(2026, 8, 8), "FAILED"),
        ),
    )
    assert validate_current_statement_state(plan, state, sku_matching_is_current=True) == ("ALREADY_IMPORTED",)

    revision = StatementCommitState(
        orders={"ORDER-1": _order()},
        committed_statements=(CommittedStatementReference("other-hash", date(2026, 8, 1), date(2026, 8, 8)),),
    )
    assert validate_current_statement_state(plan, revision, sku_matching_is_current=True) == ("POSSIBLE_REVISION",)


def test_failed_staging_reference_is_not_a_committed_duplicate():
    plan = _plan()
    state = StatementCommitState(
        orders={"ORDER-1": _order()},
        committed_statements=(
            CommittedStatementReference("hash-1", date(2026, 8, 1), date(2026, 8, 8), "FAILED"),
        ),
    )

    assert validate_current_statement_state(plan, state, sku_matching_is_current=True) == ()


def test_precommit_detects_income_type_change_that_changes_estimated_semantics():
    plan = _plan(order=_order(final_amount=None, order_income="10.00", income_type="Estimated"))
    state = StatementCommitState(
        orders={"ORDER-1": _order(final_amount=None, order_income="10.00", income_type="Final")},
        committed_statements=(),
    )

    assert validate_current_statement_state(
        plan, state, sku_matching_is_current=True
    ) == ("COMPARISON_STATE_CHANGED:ORDER-1",)


def test_fresh_invoice_change_causes_zero_write():
    class Writer:
        called = False

        def write_statement_batch(self, _plan):
            self.called = True

    writer = Writer()
    result = write_statement_plan_if_current(
        _plan(),
        reload_state=lambda: StatementCommitState(orders={"ORDER-1": _order(final_amount="11.00")}, committed_statements=()),
        sku_matching_is_current=lambda: True,
        writer=writer,
    )

    assert result.committed is False
    assert result.reasons == ("COMPARISON_STATE_CHANGED:ORDER-1",)
    assert writer.called is False


def test_preflight_failure_never_calls_atomic_writer():
    class Writer:
        called = False

        def write_statement_batch(self, _plan):
            self.called = True

    writer = Writer()
    result = write_statement_plan_if_current(
        _plan(),
        reload_state=lambda: StatementCommitState(orders={}, committed_statements=()),
        sku_matching_is_current=lambda: True,
        writer=writer,
    )

    assert result.committed is False
    assert result.reasons == ("UNMATCHED_ORDER:ORDER-1",)
    assert writer.called is False


def test_commit_lock_is_held_before_preflight_and_until_writer_completes():
    lock = ApplicationCommitLock()
    events = []

    def assert_lock_held(label):
        with pytest.raises(ApplicationCommitInProgress):
            with lock.acquire():
                pass
        events.append(label)

    class Writer:
        def write_statement_batch(self, _plan):
            assert_lock_held("writer")

    result = write_statement_plan_if_current(
        _plan(),
        reload_state=lambda: (
            assert_lock_held("preflight")
            or StatementCommitState(orders={"ORDER-1": _order()}, committed_statements=())
        ),
        sku_matching_is_current=lambda: True,
        writer=Writer(),
        commit_lock=lock,
    )

    assert result.committed is True
    assert events == ["preflight", "writer"]


def test_concurrent_second_statement_commit_is_rejected_without_preflight_or_write():
    lock = ApplicationCommitLock()
    writer_started = Event()
    allow_first_writer_to_finish = Event()
    first_result = {}

    class BlockingWriter:
        def write_statement_batch(self, _plan):
            writer_started.set()
            assert allow_first_writer_to_finish.wait(timeout=2)

    def first_commit():
        first_result["result"] = write_statement_plan_if_current(
            _plan(),
            reload_state=lambda: StatementCommitState(orders={"ORDER-1": _order()}, committed_statements=()),
            sku_matching_is_current=lambda: True,
            writer=BlockingWriter(),
            commit_lock=lock,
        )

    thread = Thread(target=first_commit)
    thread.start()
    assert writer_started.wait(timeout=2)
    second_callbacks = []
    try:
        with pytest.raises(ApplicationCommitInProgress):
            write_statement_plan_if_current(
                _plan(),
                reload_state=lambda: second_callbacks.append("preflight"),
                sku_matching_is_current=lambda: second_callbacks.append("matching"),
                writer=BlockingWriter(),
                commit_lock=lock,
            )
    finally:
        allow_first_writer_to_finish.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert first_result["result"].committed is True
    assert second_callbacks == []


def test_failed_preflight_releases_lock_for_the_next_commit_without_writing():
    lock = ApplicationCommitLock()
    writes = []

    class Writer:
        def write_statement_batch(self, _plan):
            writes.append("write")

    failed = write_statement_plan_if_current(
        _plan(),
        reload_state=lambda: StatementCommitState(orders={}, committed_statements=()),
        sku_matching_is_current=lambda: True,
        writer=Writer(),
        commit_lock=lock,
    )
    succeeded = write_statement_plan_if_current(
        _plan(),
        reload_state=lambda: StatementCommitState(orders={"ORDER-1": _order()}, committed_statements=()),
        sku_matching_is_current=lambda: True,
        writer=Writer(),
        commit_lock=lock,
    )

    assert failed.committed is False
    assert succeeded.committed is True
    assert writes == ["write"]


def test_writer_exception_releases_lock_for_the_next_commit():
    lock = ApplicationCommitLock()

    class FailingWriter:
        def write_statement_batch(self, _plan):
            raise RuntimeError("synthetic write failure")

    with pytest.raises(RuntimeError, match="synthetic write failure"):
        write_statement_plan_if_current(
            _plan(),
            reload_state=lambda: StatementCommitState(orders={"ORDER-1": _order()}, committed_statements=()),
            sku_matching_is_current=lambda: True,
            writer=FailingWriter(),
            commit_lock=lock,
        )

    class SucceedingWriter:
        called = False

        def write_statement_batch(self, _plan):
            self.called = True

    writer = SucceedingWriter()
    result = write_statement_plan_if_current(
        _plan(),
        reload_state=lambda: StatementCommitState(orders={"ORDER-1": _order()}, committed_statements=()),
        sku_matching_is_current=lambda: True,
        writer=writer,
        commit_lock=lock,
    )

    assert result.committed is True
    assert writer.called is True
