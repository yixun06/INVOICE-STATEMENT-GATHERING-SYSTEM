from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest
from sqlalchemy import event

from src.invoice_app.services import shopee_statement_import as service
from src.invoice_app.services import shopee_statement_persistence as persistence
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository
from src.invoice_app.parsers.shopee_weekly_statement_parser import ServiceFeeDetail, ShippingFeeDiscrepancy
from test_shopee_statement_import import _review, _statement, NOW
from test_postgres_my_statement_cutover import _rollback_control_plan, _readback_test_engine
from test_postgres_full_order_runtime import _repository, _batch


def _prepare(plan, context=None, **changes):
    values = dict(statement=plan.statement, audit=plan.audit,
                  full_order_ids=tuple(row.order_id for row in plan.statement.order_rows),
                  validation_passed=True, persisted_platform="Shopee",
                  semantic_overlap_dedupe=True, source_context=context)
    values.update(changes)
    return persistence.prepare_full_order_existence_statement_commit_plan(**values)


def test_identity_linear_and_exact_diagnostics():
    statement = _statement()
    persistence._validate_statement_row_identity(statement)
    # Mixed record types may share a sequence; duplicate ORDER identities may not.
    duplicate = replace(statement, income_rows=(*statement.income_rows, statement.order_rows[0]))
    with pytest.raises(persistence.StatementCommitBlocked, match="stable row identity is duplicated") as first:
        persistence._validate_statement_row_identity(duplicate)
    with pytest.raises(persistence.StatementCommitBlocked) as second:
        persistence._validate_statement_row_identity(replace(duplicate, income_rows=tuple(reversed(duplicate.income_rows))))
    assert str(first.value) == str(second.value)
    with pytest.raises(persistence.StatementCommitBlocked, match="sequence is missing"):
        persistence._validate_statement_row_identity(replace(statement, income_rows=(replace(statement.order_rows[0], sequence_no=""),)))
    assert ".count(" not in inspect.getsource(persistence._validate_statement_row_identity)


@pytest.mark.parametrize("field,value", [
    ("statement_source_sheet", "Wrong"), ("statement_source_row_number", "999"),
    ("sequence_no", "999"), ("order_id", ""), ("component_name", ""),
    ("record_type", "INVALID"), ("record_type", "SKU"),
])
def test_income_provenance_rejects_invalid_keys(field, value):
    plan = _rollback_control_plan()
    row = list(plan.financial_component_rows[0])
    row[persistence.STATEMENT_FINANCIAL_COMPONENT_HEADERS.index(field)] = value
    with pytest.raises(persistence.StatementCommitBlocked):
        persistence._validate_financial_component_rows((tuple(row),), plan.statement)


def test_provenance_mixed_sources_exact_existing_predicates():
    plan = _rollback_control_plan()
    statement = replace(plan.statement,
        service_fee_details=(ServiceFeeDetail("2", "ORDER", {"Fee": Decimal("-1")}, 20),),
        shipping_fee_discrepancies=(ShippingFeeDiscrepancy("ORDER", Decimal("1"), Decimal("2"), "reason", {}, 30),))
    plan = _prepare(plan, statement=statement)
    persistence._validate_financial_component_rows(plan.financial_component_rows, statement)
    with pytest.raises(persistence.StatementCommitBlocked, match="duplicate stable identity"):
        persistence._validate_financial_component_rows((plan.financial_component_rows[0],) * 2, statement)
    with pytest.raises(persistence.StatementCommitBlocked, match="cannot be traced"):
        persistence._validate_financial_component_rows(plan.financial_component_rows, replace(statement, income_rows=()))
    headers = persistence.STATEMENT_FINANCIAL_COMPONENT_HEADERS
    for record_type in ("SERVICE_FEE_DETAIL", "SHIPPING_FEE_DISCREPANCY"):
        row = list(next(row for row in plan.financial_component_rows if row[headers.index("record_type")] == record_type))
        row[headers.index("order_id")] = "OTHER"
        with pytest.raises(persistence.StatementCommitBlocked, match="cannot be traced"):
            persistence._validate_financial_component_rows((tuple(row),), statement)
    # Income order ID historically only requires nonblank, not an equality join.
    row = list(plan.financial_component_rows[0]); row[headers.index("order_id")] = "OTHER"
    persistence._validate_financial_component_rows((tuple(row),), statement)


def test_source_plan_reuse_rebinds_all_audit_times_and_detects_mutation():
    original = _rollback_control_plan()
    context = persistence.StatementSourcePlanContext()
    _prepare(original, context)
    audit = replace(original.audit, committed_at=original.audit.committed_at + timedelta(days=1))
    assert _prepare(original, context, audit=audit) == _prepare(original, audit=audit)
    original.statement.income_rows[0].financial_components["Product Price"] = Decimal("11")
    assert _prepare(original, context, audit=audit) == _prepare(original, audit=audit)
    with pytest.raises(persistence.StatementCommitBlocked):
        _prepare(original, context, full_order_ids=())
    with pytest.raises(persistence.StatementCommitBlocked):
        _prepare(original, context, validation_passed=False)


def test_repository_proof_does_not_trust_supplied_rows_or_reuse_classification():
    plan = _rollback_control_plan()
    repository = PostgresStatementRepository(engine=_readback_test_engine())
    empty = persistence.StatementCommitState(orders={}, committed_statements=())
    first, prepared = repository._preflight(plan, empty)
    assert first.eligible
    # Supplied serialized rows are never the repository's authority.
    assert repository._preflight(replace(plan, rows=()), empty)[1] == prepared
    drift = replace(empty, committed_statement_rows=prepared.rows,
                    committed_financial_component_rows=prepared.financial_component_rows,
                    order_adjustments=prepared.order_adjustments)
    later, _ = repository._preflight(plan, drift)
    assert not later.eligible


@pytest.mark.parametrize("change", ["bytes", "market", "rules", "filename"])
def test_source_context_invalidation(monkeypatch, change):
    review, _, writer = _review(monkeypatch)
    original = service.stage_shopee_weekly_statement
    calls = []
    def stage(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, "stage_shopee_weekly_statement", stage)
    # First refresh records this implementation identity.
    review = service.refresh_statement_review(review, writer=writer, product_master=None)
    calls.clear()
    same = service.refresh_statement_review(review, writer=writer, product_master=None)
    assert not calls
    assert same.source_artifact is review.source_artifact
    context = SHOPEE_MY
    if change == "bytes": review = replace(review, source_bytes=b"changed")
    elif change == "market": context = SHOPEE_SG
    elif change == "rules": monkeypatch.setattr(service, "_SOURCE_RULE_VERSION", "NEXT")
    else: review = replace(review, stage=replace(review.stage, source_filename="new.xlsx"))
    service.refresh_statement_review(review, writer=writer, product_master=None, market_context=context)
    assert len(calls) == 1


def test_source_copy_isolation_and_fresh_review_reads(monkeypatch):
    review, _, writer = _review(monkeypatch)
    calls = []
    original = writer.reload_active_commit_state
    def reload():
        calls.append(1)
        return original()
    monkeypatch.setattr(writer, "reload_active_commit_state", reload)
    for _ in range(4):
        review = service.refresh_statement_review(review, writer=writer, product_master=None)
    assert len(calls) == 4  # exactly one preview authority read per refresh
    review.stage.statement.income_rows[0].financial_components["Product Price"] = Decimal("999")
    assert review.source_artifact.stage.statement.income_rows[0].financial_components["Product Price"] != Decimal("999")
    other, _, _ = _review(monkeypatch)
    assert other.source_artifact is not review.source_artifact


def test_scoped_full_order_no_items_market_and_empty_request():
    repository = _repository()
    repository.sync(_batch())
    sql = []
    event.listen(repository._engine, "before_cursor_execute", lambda conn, cursor, statement, parameters, context, many: sql.append(statement))
    assert repository.read_order_ids(("MY-PG-1", "MY-PG-1", "missing"), market=SHOPEE_MY.key.value) == frozenset({"MY-PG-1"})
    assert len(sql) == 1 and "full_order_current_items" not in sql[0].lower()
    sql.clear()
    assert not repository.read_order_ids((), market=SHOPEE_MY.key.value)
    assert not sql
    with pytest.raises(Exception, match="market mismatch"):
        repository.read_order_ids(("MY-PG-1",), market=SHOPEE_SG.key.value)


def test_scoped_reader_failure_still_blocks_new_orders(monkeypatch):
    review, _, writer = _review(monkeypatch)
    class Broken:
        def read_order_ids(self, *args, **kwargs): raise RuntimeError("unavailable")
        def read_snapshot(self): pytest.fail("must not fall back")
    refreshed = service.refresh_statement_review(review, writer=writer, product_master=None, full_order_repository=Broken())
    assert not refreshed.commit_ready


@pytest.mark.parametrize("context", [SHOPEE_MY, SHOPEE_SG])
@pytest.mark.parametrize("drift", [False, True])
@pytest.mark.parametrize("fresh_instances", [False, True])
def test_ordinary_route_one_source_proof_six_fresh_states(monkeypatch, context, drift, fresh_instances):
    from src.invoice_app.services.shopee_weekly_statement_service import stage_parsed_shopee_weekly_statement
    # SQLite only. The adapter removes the PostgreSQL-only lock statement;
    # production transaction flow, fresh reads, insert, readback all execute.
    engine = _readback_test_engine()
    events = []
    counts = {"parse": 0, "state": 0, "coverage": 0, "build": 0}
    class Connection:
        def __init__(self, real): self.real = real
        def __enter__(self): self.real.__enter__(); return self
        def __exit__(self, *args): return self.real.__exit__(*args)
        def execution_options(self, **kwargs):
            assert kwargs == {"isolation_level": "SERIALIZABLE"}
            events.append("serializable")
            self.real.execution_options(**kwargs); return self
        def begin(self): return self.real.begin()
        def execute(self, query, *args, **kwargs):
            if "pg_advisory_xact_lock" in str(query):
                events.append("lock"); return None
            return self.real.execute(query, *args, **kwargs)
    class Engine:
        def connect(self): return Connection(engine.connect())
    def make_writer():
        writer = PostgresStatementRepository(engine=Engine(), persisted_platform=context.persisted_platform)
        read_state = writer._read_candidate_state
        def load(connection, plan):
            counts["state"] += 1
            events.append("state")
            state = read_state(connection, plan)
            if drift and counts["state"] == 6:
                state = replace(state, committed_statement_rows=review.plan.rows,
                                committed_financial_component_rows=review.plan.financial_component_rows)
            return state
        monkeypatch.setattr(writer, "_read_candidate_state", load)
        return writer
    writer = make_writer()
    statement = _statement()
    def stage(*args, **kwargs):
        counts["parse"] += 1
        return stage_parsed_shopee_weekly_statement(statement, evaluate_legacy_order_reconciliation=False, semantic_overlap_dedupe=True)
    monkeypatch.setattr(service, "stage_shopee_weekly_statement", stage)
    original_identity = persistence._validate_statement_row_identity
    def identity(source):
        counts["build"] += 1
        return original_identity(source)
    monkeypatch.setattr(persistence, "_validate_statement_row_identity", identity)
    class Coverage:
        def read_order_ids(self, ids, *, market):
            counts["coverage"] += 1
            assert market == context.key.value
            return frozenset(ids)
        def read_snapshot(self): pytest.fail("item snapshot forbidden")
    coverage = Coverage()
    review = service.review_statement_upload(b"synthetic", source_filename="statement.xlsx", batch_id="count-test",
        uploaded_by="test", writer=writer, product_master=None, full_order_repository=coverage, market_context=context, now=lambda: NOW)
    for _ in range(2):
        if fresh_instances: writer = make_writer()
        review = service.refresh_statement_review(review, writer=writer, product_master=None, market_context=context, now=lambda: NOW)
    assert counts["state"] == 3
    if fresh_instances: writer = make_writer()
    assert service.check_statement_review_currency(review, writer=writer, product_master=None,
        market_context=context).is_current
    if fresh_instances: writer = make_writer()
    attempt = service.commit_statement_review(review, writer=writer, load_product_master=lambda: None, market_context=context)
    if drift:
        assert not attempt.committed
        from sqlalchemy import select, func
        from src.invoice_app.pg_orm.pg_models import StatementData
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(StatementData)) == 0
    else:
        assert attempt.committed, attempt.reasons
    assert counts == {"parse": 1, "state": 6, "coverage": 5, "build": 6 if fresh_instances else 2}
    assert events[-3:] == ["serializable", "lock", "state"]
    if drift:
        return
    # An unchanged source proof cannot prevent fresh state from classifying a replay.
    replay = service.refresh_statement_review(review, writer=writer, product_master=None, market_context=context)
    assert replay.commit_preflight.no_new_facts
    assert counts["coverage"] == 5  # NEW-only: no Full Order query for replay


def test_invalid_source_reused_without_becoming_ready():
    writer = PostgresStatementRepository(engine=_readback_test_engine())
    review = service.review_statement_upload(b"not an xlsx", source_filename="broken.xlsx", batch_id="bad",
        uploaded_by="test", writer=writer, product_master=None)
    refreshed = service.refresh_statement_review(review, writer=writer, product_master=None)
    assert refreshed.source_artifact is review.source_artifact
    assert not refreshed.commit_ready
    assert refreshed.stage == review.stage


def test_provenance_indexes_visit_each_source_once():
    from types import SimpleNamespace
    plan = _rollback_control_plan()
    class Visits(list):
        iterations = 0
        def __iter__(self):
            self.iterations += 1
            return super().__iter__()
    source = SimpleNamespace(income_rows=Visits(plan.statement.income_rows),
                             service_fee_details=Visits(), shipping_fee_discrepancies=Visits())
    persistence._validate_financial_component_rows(plan.financial_component_rows, source)
    assert [source.income_rows.iterations, source.service_fee_details.iterations,
            source.shipping_fee_discrepancies.iterations] == [1, 1, 1]


def test_scoped_coverage_chunk_growth_and_cross_market():
    from sqlalchemy import select, insert
    from src.invoice_app.pg_orm.pg_models import FullOrderCurrentOrder
    repository = _repository()
    repository.sync(_batch())
    table = FullOrderCurrentOrder.__table__
    with repository._engine.begin() as connection:
        values = dict(connection.execute(select(table)).mappings().one())
        values.update(market=SHOPEE_SG.key.value, order_id="SG-ONLY")
        connection.execute(insert(table), values)
    queries = []
    event.listen(repository._engine, "before_cursor_execute", lambda *args: queries.append(args[2]))
    requested = ("MY-PG-1", "SG-ONLY", *(f"missing-{index}" for index in range(499)))
    assert repository.read_order_ids(requested, market=SHOPEE_MY.key.value) == frozenset({"MY-PG-1"})
    assert len(queries) == 2


def test_performance_logs_are_payload_free():
    import logging
    from src.invoice_app.services import statement_performance as performance
    messages = []
    class Sink(logging.Handler):
        def emit(self, record): messages.append(record.getMessage())
    sink = Sink()
    performance._LOG.addHandler(sink)
    try:
        @performance.measured("test_boundary")
        def function(secret):
            return secret
        assert function("secret-must-not-appear") == "secret-must-not-appear"
        with pytest.raises(ValueError):
            with performance.measure("test_failure"):
                raise ValueError("private-payload")
    finally:
        performance._LOG.removeHandler(sink)
    assert "stage=test_boundary" in messages[0] and "count=1" in messages[0]
    assert "success=False" in messages[1]
    assert all("secret-must-not-appear" not in text and "private-payload" not in text for text in messages)
