"""Isolated-test-only financial fact backfill; not an application reader."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import os
from time import perf_counter

from sqlalchemy import text

from src.invoice_app.services.dashboard_financial_summary import load_optimized_snapshot, validate_revision_infrastructure
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_analysis import (
    _is_charge, _is_credit, _order_level_components, _refund_total, _text,
    ShopeeAnalysisReport,
)
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services.postgres_statement_repository import PostgresStatementRepository


@dataclass(frozen=True)
class OrderFact:
    market: str
    order_id: str
    order_created_date: date
    settlement_state: str
    payout_completed_date: date | None
    released_amount: Decimal | None
    order_component_count: int
    charges_amount: Decimal
    credits_amount: Decimal
    refund_amount: Decimal
    non_product_amount: Decimal


@dataclass(frozen=True)
class FactSnapshot:
    """Shared source snapshot for an experimental adapter, not yet a dashboard report."""
    market: str
    current: object
    facts: tuple[OrderFact, ...]
    adjustments: tuple[object, ...]
    fee_names: tuple[str, ...]
    source_revision: int
    cache_token: str | None = None
    cache_created_monotonic: float | None = None
    cache_target_id: str | None = None

    @property
    def presentation(self):
        from src.invoice_app.services.market_context import resolve_market_context
        from src.invoice_app.services.shopee_analysis import ShopeeAnalysisPresentation
        from src.invoice_app.utils.monetary import currency_symbol
        context = resolve_market_context(SHOPEE_MY if self.market == SHOPEE_MY.key.value else SHOPEE_SG)
        if context.key.value != self.market:
            raise RuntimeError('Fact market mismatch')
        return ShopeeAnalysisPresentation(context.display_name, context.currency_code, currency_symbol(context.currency_code))

    def available_dates(self, basis):
        from src.invoice_app.services.shopee_analysis import AnalysisDateBasis
        if basis is AnalysisDateBasis.ORDER_CREATED:
            return tuple(sorted({f.order_created_date for f in self.facts}))
        return tuple(sorted({f.payout_completed_date for f in self.facts
                             if f.settlement_state == 'AVAILABLE' and f.payout_completed_date is not None}))


@dataclass(frozen=True)
class FactReport(ShopeeAnalysisReport):
    named_totals: tuple

    @property
    def component_totals(self):
        return self.named_totals


def fact_from_projection(projection) -> OrderFact:
    components = _order_level_components(projection.financial_components)
    zero = Decimal('0.00')
    return OrderFact(
        projection.market, projection.order_id, projection.order_created_date,
        projection.settlement_state.value, projection.payout_completed_date,
        projection.released_amount, len(components),
        sum((c.component_amount for c in components if _is_charge(c)), zero),
        sum((c.component_amount for c in components if _is_credit(c)), zero),
        _refund_total(components),
        sum((c.component_amount for c in components if _text(c.component_name).casefold() != 'product price'), zero),
    )


def assert_test_target(engine, connection):
    url = engine.url
    from src.invoice_app.pg_orm.runtime import is_authorized_fact_server, assert_fact_server_target
    if is_authorized_fact_server(url):
        assert_fact_server_target(connection)
        return
    if os.environ.get('INVOICEGATHER_FACT_SERVER_ENABLED') == '1' and os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') != '1':
        raise RuntimeError('Fact server target does not match explicit authorization')
    if os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') == '1':
        if (url.host, url.port, url.database) != ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test'):
            raise RuntimeError('Refusing non-isolated upload test target')
        if connection.execute(text('SELECT current_database(), current_schema()')).one() != ('invoicegather_fact_atomicity_test', 'ig_real_upload_uat'):
            raise RuntimeError('Refusing unexpected isolated upload test schema')
        return
    if (url.host, url.port, url.database) != ('127.0.0.1', 5433, 'invoicegather_perf_scale'):
        raise RuntimeError('Refusing non-POC PostgreSQL target')
    if connection.execute(text('SELECT current_database()')).scalar_one() != 'invoicegather_perf_scale':
        raise RuntimeError('Refusing unexpected database')


def _check_fact_snapshot(connection, market_context):
    from src.invoice_app.services.market_context import resolve_market_context
    context = resolve_market_context(market_context)
    assert_test_target(connection.engine, connection)
    if connection.execute(text('SHOW transaction_isolation')).scalar_one() != 'repeatable read':
        raise RuntimeError('Fact reads require REPEATABLE READ')
    if connection.execute(text('SHOW transaction_read_only')).scalar_one() != 'on':
        raise RuntimeError('Fact reads require a read-only transaction')
    validate_revision_infrastructure(connection)
    state = connection.execute(text('''SELECT s.revision, f.source_revision
        FROM analysis_source_revision s LEFT JOIN analysis_financial_fact_revision f USING (market)
        WHERE s.market=:market'''), {'market': context.key.value}).one_or_none()
    if state is None or state[1] is None or state[0] != state[1]:
        raise RuntimeError('Financial facts are stale or unavailable; refresh required')
    incomplete = connection.execute(text('''SELECT EXISTS (
        SELECT 1 FROM full_order_current_orders o FULL OUTER JOIN analysis_order_financial_fact f
          ON o.market=f.market AND o.order_id=f.order_id
        WHERE (o.market=:market OR f.market=:market)
          AND (o.order_id IS NULL OR f.order_id IS NULL))'''), {'market': context.key.value}).scalar_one()
    if incomplete:
        raise RuntimeError('Financial facts are incomplete; refresh required')
    return context


def read_checked_facts(connection, market_context):
    """Read a complete fact set only in the caller's consistent read-only snapshot."""
    context = _check_fact_snapshot(connection, market_context)
    return tuple(OrderFact(**row) for row in connection.execute(text('''SELECT market, order_id,
        order_created_date, settlement_state, payout_completed_date, released_amount,
        order_component_count, charges_amount, credits_amount, refund_amount, non_product_amount
        FROM analysis_order_financial_fact WHERE market=:market ORDER BY order_id'''),
        {'market': context.key.value}).mappings())


def fee_options(connection, market_context):
    """Return native names from orders with Statement evidence, including conflicts."""
    context = _check_fact_snapshot(connection, market_context)
    rows = connection.execute(text('''SELECT DISTINCT c.component_name
        FROM statement_financial_components c
        JOIN analysis_order_financial_fact f ON f.market=:market AND f.order_id=c.order_id
        WHERE c.platform=:platform AND c.commit_status='COMMITTED'
          AND btrim(c.record_type)='ORDER' AND c.component_amount IS NOT NULL
          AND f.settlement_state <> 'MISSING_STATEMENT' '''),
        {'market': context.key.value, 'platform': context.persisted_platform})
    return tuple(sorted((row[0] for row in rows), key=str.casefold))


def fee_matching_orders(connection, market_context, order_ids, fee_names):
    """Return matching order identities; never materialize component rows."""
    context = _check_fact_snapshot(connection, market_context)
    if not order_ids or not fee_names:
        return frozenset()
    return frozenset(connection.execute(text('''SELECT DISTINCT f.order_id
        FROM analysis_order_financial_fact f
        WHERE f.market=:market AND f.order_id = ANY(:orders)
          AND f.settlement_state <> 'MISSING_STATEMENT' AND EXISTS (
            SELECT 1 FROM statement_financial_components c
            WHERE c.platform=:platform AND c.order_id=f.order_id
              AND c.commit_status='COMMITTED' AND btrim(c.record_type)='ORDER'
              AND c.component_amount IS NOT NULL AND c.component_name = ANY(:names))'''),
        {'market': context.key.value, 'platform': context.persisted_platform,
         'orders': list(order_ids), 'names': list(fee_names)}).scalars())


def fee_breakdown(connection, market_context, order_ids):
    """Aggregate all ORDER components of the settled selected cohort in PostgreSQL."""
    context = _check_fact_snapshot(connection, market_context)
    if not order_ids:
        return ()
    rows = connection.execute(text('''SELECT c.component_name, sum(c.component_amount) AS amount
        FROM statement_financial_components c
        JOIN analysis_order_financial_fact f ON f.market=:market AND f.order_id=c.order_id
        WHERE c.platform=:platform AND c.commit_status='COMMITTED'
          AND btrim(c.record_type)='ORDER' AND c.component_amount IS NOT NULL
          AND f.settlement_state='AVAILABLE' AND f.released_amount IS NOT NULL
          AND f.order_id = ANY(:orders)
        GROUP BY c.component_name'''),
        {'market': context.key.value, 'platform': context.persisted_platform, 'orders': list(order_ids)})
    return tuple(sorted(((row[0], row[1]) for row in rows), key=lambda row: row[0].casefold()))


def load_fact_snapshot(*, engine, market_context):
    """Load facts, order data, and small auxiliary results in one read-only snapshot."""
    from sqlalchemy import select
    from src.invoice_app.pg_orm.pg_models import OrderAdjustment
    from src.invoice_app.services import analysis_perf as perf
    from src.invoice_app.services.market_context import resolve_market_context
    from src.invoice_app.services.postgres_statement_repository import _adjustment_from_model
    context = resolve_market_context(market_context)
    repo = PostgresFullOrderCurrentRepository(engine=engine, market_context=context)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            facts = read_checked_facts(connection, context)
            current = repo._read_snapshot(connection)
            adjustments = perf.read_rows(connection, select(OrderAdjustment.__table__).where(
                OrderAdjustment.platform == context.persisted_platform), _adjustment_from_model, 'fact_adjustments')
            names = fee_options(connection, context)
            revision = connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market'),
                                          {'market': context.key.value}).scalar_one()
            return FactSnapshot(context.key.value, current, facts, adjustments, names, revision)


def build_fact_report(snapshot, filters, *, matching_fee_orders=None, breakdown=()):
    """Experimental report adapter; caller supplies revision-checked fee results."""
    from collections import defaultdict
    from types import SimpleNamespace
    from src.invoice_app.services.market_context import resolve_market_context
    from src.invoice_app.services.shopee_order_financial_projection import OrderFinancialProjectionState
    from src.invoice_app.services.shopee_analysis import (
        AnalysisDateBasis, ShopeeAnalysisComponentTotal, ShopeeAnalysisOrderRow,
        ShopeeAnalysisPresentation, _aggregate_products,
        _is_eligible_status, _item_identity, _reconciliation,
    )
    from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
    from src.invoice_app.utils.monetary import currency_symbol

    if filters.fee_types and matching_fee_orders is None:
        raise RuntimeError('Fee membership must be checked in the same source revision')
    context = resolve_market_context(SHOPEE_MY if snapshot.market == SHOPEE_MY.key.value else SHOPEE_SG)
    if context.key.value != snapshot.market:
        raise RuntimeError('Fact market mismatch')
    orders = {o.order_id: o for o in snapshot.current.orders}
    items = defaultdict(list)
    for item in snapshot.current.items:
        if item.market != snapshot.market:
            raise RuntimeError('Fact item market mismatch')
        items[_text(item.order_id)].append(item)
    events = defaultdict(list)
    for event in sorted(snapshot.adjustments, key=lambda e: (e.adjustment_complete_date, e.linked_order_id, e.adjustment_event_fingerprint)):
        if event.platform != context.persisted_platform:
            raise RuntimeError('Fact adjustment market mismatch')
        events[_text(event.linked_order_id)].append(event)
    selected, selected_items = [], []
    for fact in sorted(snapshot.facts, key=lambda f: f.order_id):
        if fact.market != snapshot.market or fact.order_id not in orders:
            raise RuntimeError('Fact order market or identity mismatch')
        order = orders[fact.order_id]
        state = OrderFinancialProjectionState(fact.settlement_state)
        if not _is_eligible_status(order.order_status): continue
        if filters.date_basis is AnalysisDateBasis.ORDER_CREATED:
            if not filters.from_date <= fact.order_created_date <= filters.to_date: continue
        elif state is not OrderFinancialProjectionState.AVAILABLE or fact.payout_completed_date is None or not filters.from_date <= fact.payout_completed_date <= filters.to_date:
            continue
        if filters.order_statuses and order.order_status not in filters.order_statuses: continue
        if filters.order_id_query and filters.order_id_query.casefold().strip() not in fact.order_id.casefold(): continue
        if filters.settlement_states and state not in filters.settlement_states: continue
        matching = tuple(i for i in items[_text(fact.order_id)] if not filters.product_keys or _item_identity(i)[0] in filters.product_keys)
        if filters.product_keys and not matching: continue
        if filters.fee_types and fact.order_id not in matching_fee_orders: continue
        if filters.has_refund is True and fact.refund_amount == 0: continue
        if filters.has_refund is False and fact.refund_amount != 0: continue
        order_events = tuple(events[_text(fact.order_id)])
        if filters.has_adjustment is True and not order_events: continue
        if filters.has_adjustment is False and order_events: continue
        sales = sum((i.product_subtotal for i in matching), Decimal('0.00'))
        # Pass only an internal aggregate to the existing reconciliation function;
        # it is never exposed as a native component or used for fee breakdown.
        components = ((SimpleNamespace(component_name='fact aggregate', component_amount=fact.non_product_amount),)
                      if fact.order_component_count else ())
        expected, difference, status, reason = _reconciliation(product_sales=sales,
            released_amount=fact.released_amount, settlement_state=state, components=components,
            product_scope_is_partial=bool(filters.product_keys))
        selected.append(ShopeeAnalysisOrderRow(order_id=fact.order_id, order_status=order.order_status,
            order_created_date=fact.order_created_date, payout_completed_date=fact.payout_completed_date,
            settlement_state=state, product_sales=sales, quantity=sum(i.quantity for i in matching),
            released_amount=fact.released_amount, charges=fact.charges_amount, credits=fact.credits_amount,
            refund_amount=fact.refund_amount, adjustment_total=sum((e.adjustment_amount for e in order_events), Decimal('0.00')),
            expected_released_amount=expected, reconciliation_difference=difference, reconciliation_status=status,
            reconciliation_reason=reason, component_totals=(), items=matching, financial_components=(), adjustment_events=order_events))
        selected_items.extend(matching)
    return FactReport(presentation=ShopeeAnalysisPresentation(context.display_name, context.currency_code, currency_symbol(context.currency_code)),
        filters=filters, rows=tuple(selected), products=_aggregate_products(selected_items),
        named_totals=tuple(ShopeeAnalysisComponentTotal(name, amount) for name, amount in breakdown))


def build_checked_fact_report(engine, context, snapshot, filters):
    """Build a report and its fee evidence against the snapshot's exact revision."""
    from dataclasses import replace
    from src.invoice_app.services.dashboard_financial_summary import DashboardRefreshRequired
    from src.invoice_app.services.shopee_analysis import ShopeeAnalysisComponentTotal
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            checked = _check_fact_snapshot(connection, context)
            if checked.key.value != snapshot.market:
                raise RuntimeError('Fact market mismatch')
            revision = connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:market'),
                                          {'market': snapshot.market}).scalar_one()
            if revision != snapshot.source_revision:
                raise DashboardRefreshRequired('Financial data changed; refresh required')
            matches = (fee_matching_orders(connection, context, [f.order_id for f in snapshot.facts], filters.fee_types)
                       if filters.fee_types else None)
            preliminary = build_fact_report(snapshot, filters, matching_fee_orders=matches)
            totals = fee_breakdown(connection, context, [row.order_id for row in preliminary.settled_rows])
            return replace(preliminary, named_totals=tuple(ShopeeAnalysisComponentTotal(name, amount)
                                                           for name, amount in totals))


def prepare_isolated_facts(engine):
    """Create fresh POC tables once; atomically publish only unchanged source revisions."""
    from dataclasses import asdict
    from sqlalchemy import inspect
    # The POC compares its own projection/readback, but does not certify source
    # identity or pending history. Never use it as a production baseline.
    from src.invoice_app.pg_orm.runtime import is_authorized_fact_server
    if is_authorized_fact_server(engine.url):
        raise RuntimeError('Server baseline is not certified; isolated POC backfill is forbidden')
    started = perf_counter()
    with engine.connect() as connection:
        assert_test_target(engine, connection)
        existing = set(inspect(connection).get_table_names())
        if existing & {'analysis_order_financial_fact', 'analysis_financial_fact_revision'}:
            raise RuntimeError('Fact table already exists; refusing to overwrite it')
    records, revisions = [], {}
    for context in (SHOPEE_MY, SHOPEE_SG):
        snapshot = load_optimized_snapshot(
            market_context=context,
            full_order_repository=PostgresFullOrderCurrentRepository(engine=engine, market_context=context),
            statement_reader=PostgresStatementRepository(engine=engine, persisted_platform=context.persisted_platform),
            compact_summary=True,
        )
        revisions[context.key.value] = snapshot.source_revision
        records.extend(asdict(fact_from_projection(p)) for p in snapshot.projections)
        del snapshot
    columns = tuple(OrderFact.__dataclass_fields__)
    with engine.begin() as connection:
        assert_test_target(engine, connection)
        for market, expected in revisions.items():
            actual = connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:market FOR UPDATE'), {'market': market}).scalar_one()
            if actual != expected:
                raise RuntimeError('Source changed during fact generation; no publication')
        connection.execute(text('''CREATE TABLE analysis_order_financial_fact (
            market text NOT NULL, order_id text NOT NULL, order_created_date date NOT NULL,
            settlement_state text NOT NULL, payout_completed_date date, released_amount numeric,
            order_component_count bigint NOT NULL, charges_amount numeric NOT NULL,
            credits_amount numeric NOT NULL, refund_amount numeric NOT NULL,
            non_product_amount numeric NOT NULL, PRIMARY KEY (market, order_id))'''))
        connection.execute(text('''CREATE TABLE analysis_financial_fact_revision (
            market text PRIMARY KEY, source_revision bigint NOT NULL)'''))
        connection.execute(text('INSERT INTO analysis_order_financial_fact (' + ','.join(columns) + ') VALUES (' + ','.join(':'+c for c in columns) + ')'), records)
        connection.execute(text('INSERT INTO analysis_financial_fact_revision (market,source_revision) VALUES (:market,:revision)'),
            [{'market': market, 'revision': revision} for market, revision in revisions.items()])
        rows = connection.execute(text('SELECT ' + ','.join(columns) + ' FROM analysis_order_financial_fact')).mappings()
        expected = {(r['market'], r['order_id']): tuple(r[c] for c in columns) for r in records}
        count = 0
        for row in rows:
            if expected.pop((row['market'], row['order_id']), None) != tuple(row[c] for c in columns):
                raise RuntimeError('Fact readback mismatch; transaction rolled back')
            count += 1
        if expected:
            raise RuntimeError('Fact readback incomplete; transaction rolled back')
        size = connection.execute(text("SELECT pg_total_relation_size('analysis_order_financial_fact') + pg_total_relation_size('analysis_financial_fact_revision')")).scalar_one()
    return {'facts_verified': count, 'by_market': {m: sum(r['market']==m for r in records) for m in revisions},
            'total_bytes': size, 'elapsed_seconds': round(perf_counter()-started, 2)}
