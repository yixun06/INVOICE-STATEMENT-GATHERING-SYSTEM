"""Focused candidate-scope authority checks; no live PostgreSQL or UI."""

from dataclasses import replace
from sqlalchemy import event

from src.invoice_app.services import postgres_statement_repository as pg
from src.invoice_app.services.shopee_statement_persistence import (
    prepare_full_order_existence_statement_commit_plan,
    preview_active_statement_state,
)
from test_postgres_my_statement_cutover import _readback_test_engine, _rollback_control_plan


def _next_plan(plan, *, batch_id="next-batch", statement=None):
    statement = statement or plan.statement
    return prepare_full_order_existence_statement_commit_plan(
        statement,
        audit=replace(plan.audit, statement_batch_id=batch_id),
        full_order_ids=tuple(row.order_id for row in statement.order_rows),
        validation_passed=True,
        persisted_platform="Shopee",
        semantic_overlap_dedupe=True,
    )


def _load(engine, plan):
    repository = pg.PostgresStatementRepository(engine=engine)
    with engine.connect() as connection:
        return repository._read_candidate_state(connection, plan)


def test_all_new_ignores_unrelated_history_and_has_bounded_reads():
    engine = _readback_test_engine()
    history = _rollback_control_plan()
    candidate = _rollback_control_plan()
    with engine.begin() as connection:
        pg._insert_plan(connection, history)
    repository = pg.PostgresStatementRepository(engine=engine)
    with engine.connect() as connection:
        whole_market = repository._read_state(connection)
    assert (len(whole_market.committed_statement_rows),
            len(whole_market.committed_financial_component_rows),
            len(whole_market.order_adjustments)) == (
                len(history.rows), len(history.financial_component_rows), 1,
            )
    queries = []
    event.listen(engine, "before_cursor_execute", lambda *args: queries.append(args[2]))
    state = _load(engine, candidate)
    assert not state.committed_statement_rows
    assert not state.committed_financial_component_rows
    assert not state.order_adjustments
    assert preview_active_statement_state(candidate, state).new_order_ids == (
        candidate.statement.order_rows[0].order_id,
    )
    assert len(queries) == 4  # rows, components, fingerprint, adjustment identity


def test_all_historical_batches_for_exact_order_and_cross_period_conflict():
    engine = _readback_test_engine()
    history = _rollback_control_plan()
    candidate = _next_plan(history)
    with engine.begin() as connection:
        pg._insert_plan(connection, history)
    state = _load(engine, candidate)
    assert preview_active_statement_state(candidate, state).exact_duplicate_order_ids

    # A second committed batch in another period remains visible. Its altered
    # settlement fact conflicts even though the first batch is an exact match.
    later = _next_plan(history, batch_id="other-period")
    amount = pg.STATEMENT_DATA_HEADERS.index("total_released_amount")
    changed = list(later.rows[0]); changed[amount] = "11.00"
    later = replace(later, rows=(tuple(changed), *later.rows[1:]))
    period_start = pg.STATEMENT_DATA_HEADERS.index("statement_period_from")
    period_end = pg.STATEMENT_DATA_HEADERS.index("statement_period_to")
    later_rows = []
    for row in later.rows:
        cells = list(row); cells[period_start] = "2025-01-01"; cells[period_end] = "2025-01-07"
        later_rows.append(tuple(cells))
    later = replace(later, rows=tuple(later_rows))
    with engine.begin() as connection:
        pg._insert_values(connection, pg.StatementData, pg.STATEMENT_DATA_HEADERS, later.rows)
        pg._insert_values(connection, pg.StatementFinancialComponent,
                          pg.STATEMENT_FINANCIAL_COMPONENT_HEADERS, later.financial_component_rows)
    state = _load(engine, candidate)
    assert len(state.committed_statement_rows) == 2 * len(history.rows) - 2  # adjustment rows have no order_id
    assert len(state.committed_financial_component_rows) == 2 * len(history.financial_component_rows)
    assert preview_active_statement_state(candidate, state).incomplete_unsafe_order_ids

    # Without the exact historical batch, the changed evidence is a conflict.
    engine2 = _readback_test_engine()
    with engine2.begin() as connection:
        pg._insert_values(connection, pg.StatementData, pg.STATEMENT_DATA_HEADERS, later.rows)
        pg._insert_values(connection, pg.StatementFinancialComponent,
                          pg.STATEMENT_FINANCIAL_COMPONENT_HEADERS, later.financial_component_rows)
    assert preview_active_statement_state(candidate, _load(engine2, candidate)).conflict_order_ids


def test_adjustment_duplicate_and_conflict_use_independent_identity():
    engine = _readback_test_engine()
    history = _rollback_control_plan()
    with engine.begin() as connection:
        pg._insert_plan(connection, history)
    candidate = _next_plan(history)
    state = _load(engine, candidate)
    assert len(state.order_adjustments) == 1
    assert preview_active_statement_state(candidate, state).skipped_adjustment_rows == 1

    # Unlinked events have no candidate order ID. Their stable fingerprint and
    # (platform, blank link, type, date) conflict identity still load.
    unlinked = replace(history.statement.adjustments[0], linked_order_id="")
    source = replace(history.statement, adjustments=(unlinked,))
    unlinked_history = _next_plan(history, batch_id="unlinked-old", statement=source)
    engine2 = _readback_test_engine()
    with engine2.begin() as connection:
        pg._insert_plan(connection, unlinked_history)
    duplicate = _next_plan(unlinked_history, batch_id="unlinked-new")
    assert preview_active_statement_state(duplicate, _load(engine2, duplicate)).skipped_adjustment_rows == 1
    altered = replace(unlinked, adjustment_amount=unlinked.adjustment_amount - 1)
    changed = _next_plan(unlinked_history, batch_id="unlinked-changed",
                         statement=replace(source, adjustments=(altered,)))
    assert preview_active_statement_state(changed, _load(engine2, changed)).conflicting_adjustment_rows == 1


def test_many_candidate_orders_remain_set_based():
    engine = _readback_test_engine()
    base = _rollback_control_plan()
    order = base.statement.order_rows[0]
    source = replace(base.statement, income_rows=tuple(
        replace(order, order_id=f"CANDIDATE-{number}", sequence_no=str(number))
        for number in range(2400)
    ))
    candidate = replace(base, statement=source, order_adjustments=())
    queries = []
    event.listen(engine, "before_cursor_execute", lambda *args: queries.append(args[2]))
    state = _load(engine, candidate)
    assert not state.committed_statement_rows
    assert len(queries) == 2  # one Statement and one component SELECT, never per order
