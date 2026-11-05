"""Targeted projection primitive for experimental incremental fact sync.

Publication uses an explicitly authorized target and caller transaction.
"""
from dataclasses import asdict
import os
from sqlalchemy import select, text

from src.invoice_app.pg_orm.pg_models import (
    FullOrderCurrentOrder, FullOrderCurrentItem, StatementData,
    StatementFinancialComponent,
)
from src.invoice_app.repositories.postgres_full_order_current_repository import (
    _order_from_row, _item_from_row,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import FullOrderCurrentSnapshot
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection
from src.invoice_app.services.google_sheets_statement_writer import CommittedStatementFinancialFacts
from src.invoice_app.services.market_context import resolve_market_context
from src.invoice_app.services.postgres_statement_repository import _order_fact, _component_fact
from src.invoice_app.services.shopee_order_financial_projection import read_order_financial_projections
from src.invoice_app.pg_orm.runtime import is_authorized_fact_server, assert_fact_server_target

_VERIFIED_SYNC_PUBLICATION = object()


def sync_test_facts(connection, market_context):
    """Consume a complete revision range and publish within the caller transaction.

    Callers must commit only on success and roll back on any exception. Source
    writers use the existing revision trigger, which updates the locked market
    revision row in their source transaction. Server use is explicitly opted in.
    """
    from src.invoice_app.services.dashboard_financial_summary import validate_revision_infrastructure
    context = resolve_market_context(market_context)
    market = context.key.value
    if market not in ('shopee_my', 'shopee_sg'):
        raise ValueError('Explicit Shopee MY or SG market required')
    if not connection.in_transaction():
        raise RuntimeError('An existing transaction is required')
    url = connection.engine.url
    server = is_authorized_fact_server(url)
    if not server and os.environ.get('INVOICEGATHER_FACT_SERVER_ENABLED') == '1' and os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') != '1':
        raise RuntimeError('Fact server target does not match explicit authorization')
    if server:
        assert_fact_server_target(connection)
    elif (url.host, url.port, url.database) != ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test'):
        raise RuntimeError('Refusing non-disposable fact sync target')
    if not server and connection.execute(text('SELECT current_database()')).scalar_one() != 'invoicegather_fact_atomicity_test':
        raise RuntimeError('Refusing unexpected database')
    if connection.execute(text('SHOW transaction_isolation')).scalar_one() != 'read committed':
        raise RuntimeError('Fact sync requires READ COMMITTED with the source revision lock')
    validate_revision_infrastructure(connection)
    source_revision = connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:market FOR UPDATE'),
        {'market': market}).scalar_one()
    fact_revision = connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market FOR UPDATE'),
        {'market': market}).scalar_one_or_none()
    if fact_revision is None:
        raise RuntimeError('A verified complete fact baseline is required')
    connection.execute(text('SELECT ig_fact_assert_capture_coverage(:market,:baseline,:current)'),
        {'market': market, 'baseline': fact_revision, 'current': source_revision})
    if connection.execute(text('''SELECT EXISTS (SELECT 1 FROM analysis_financial_fact_pending
        WHERE market=:market AND source_revision IS NOT NULL AND source_revision<=:baseline)'''),
        {'market': market, 'baseline': fact_revision}).scalar_one():
        raise RuntimeError('Unexpected unacknowledged changes at or before fact revision')
    if source_revision == fact_revision:
        return {'market': market, 'revision': source_revision, 'affected_orders': 0}
    pending = connection.execute(text('''SELECT DISTINCT order_id FROM analysis_financial_fact_pending
        WHERE market=:market AND source_revision>:baseline AND source_revision<=:current AND change_kind='order'
        ORDER BY order_id'''), {'market': market, 'baseline': fact_revision, 'current': source_revision}).scalars().all()
    facts = calculate_targeted_facts(connection, context, pending)
    calculated_ids = {fact.order_id for fact in facts}
    missing_ids = set(pending) - calculated_ids
    publish_test_facts(connection, context, facts, sorted(missing_ids), revision=source_revision,
                       _authorization=_VERIFIED_SYNC_PUBLICATION)
    connection.execute(text('''DELETE FROM analysis_financial_fact_pending WHERE market=:market
        AND source_revision>:baseline AND source_revision<=:current'''),
        {'market': market, 'baseline': fact_revision, 'current': source_revision})
    return {'market': market, 'revision': source_revision, 'affected_orders': len(pending)}


def publish_test_facts(connection, market_context, facts, delete_order_ids, *, revision, _authorization=None):
    """Publish precomputed facts inside the caller's transaction.

    Server publication requires the internal verified sync path and rechecks
    the locked source revision and coverage; local test revisions remain synthetic.
    This function never commits or rolls back the caller's transaction.
    """
    context = resolve_market_context(market_context)
    market = context.key.value
    if market not in ('shopee_my', 'shopee_sg'):
        raise ValueError('Explicit Shopee MY or SG market required')
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError('A nonnegative integer test revision is required')
    facts = tuple(facts)
    deleted = tuple(delete_order_ids)
    if any(not isinstance(f, OrderFact) or f.market != market or not f.order_id or f.order_id != f.order_id.strip() for f in facts):
        raise ValueError('Invalid or foreign-market fact identity')
    if any(not isinstance(i, str) or not i or i != i.strip() for i in deleted):
        raise ValueError('Invalid delete identity')
    ids = tuple(f.order_id for f in facts)
    if len(set(ids)) != len(ids) or len(set(deleted)) != len(deleted) or set(ids) & set(deleted):
        raise ValueError('Duplicate or conflicting publication identities')
    if not connection.in_transaction():
        raise RuntimeError('An existing transaction is required')
    url = connection.engine.url
    server = is_authorized_fact_server(url)
    if not server and os.environ.get('INVOICEGATHER_FACT_SERVER_ENABLED') == '1' and os.environ.get('INVOICEGATHER_ATOMICITY_TEST_MODE') != '1':
        raise RuntimeError('Fact server target does not match explicit authorization')
    if server:
        if _authorization is not _VERIFIED_SYNC_PUBLICATION:
            raise RuntimeError('Server publication requires verified sync coverage')
        assert_fact_server_target(connection)
        locked_source = connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:market FOR UPDATE'),
                                           {'market': market}).scalar_one()
        baseline = connection.execute(text('SELECT source_revision FROM analysis_financial_fact_revision WHERE market=:market FOR UPDATE'),
                                      {'market': market}).scalar_one()
        if revision != locked_source:
            raise RuntimeError('Server publication revision does not match locked source')
        connection.execute(text('SELECT ig_fact_assert_capture_coverage(:market,:baseline,:current)'),
                           {'market': market, 'baseline': baseline, 'current': locked_source})
    elif (url.host, url.port, url.database) != ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test'):
        raise RuntimeError('Refusing non-disposable fact publication target')
    if not server and connection.execute(text('SELECT current_database()')).scalar_one() != 'invoicegather_fact_atomicity_test':
        raise RuntimeError('Refusing unexpected database')
    columns = tuple(OrderFact.__dataclass_fields__)
    if facts:
        updates = ', '.join(f'{column}=EXCLUDED.{column}' for column in columns if column not in ('market', 'order_id'))
        statement = text('INSERT INTO analysis_order_financial_fact (' + ','.join(columns) + ') VALUES ('
                         + ','.join(':'+column for column in columns) + ') ON CONFLICT (market,order_id) DO UPDATE SET ' + updates)
        connection.execute(statement, [asdict(fact) for fact in facts])
    if deleted:
        connection.execute(text('DELETE FROM analysis_order_financial_fact WHERE market=:market AND order_id=ANY(:ids)'),
                           {'market': market, 'ids': list(deleted)})
    connection.execute(text('''INSERT INTO analysis_financial_fact_revision (market,source_revision)
        VALUES (:market,:revision) ON CONFLICT (market) DO UPDATE SET source_revision=EXCLUDED.source_revision'''),
        {'market': market, 'revision': revision})


def calculate_targeted_facts(connection, market_context, order_ids):
    """Calculate facts from one caller-owned snapshot; empty scope does no SQL."""
    context = resolve_market_context(market_context)
    ids = tuple(sorted({str(value).strip() for value in order_ids if value is not None and str(value).strip()}))
    if not ids:
        return ()
    if context.key.value not in ('shopee_my', 'shopee_sg'):
        raise ValueError('Explicit Shopee MY or SG market required')
    orders = tuple(_order_from_row(row) for row in connection.execute(
        select(FullOrderCurrentOrder.__table__).where(FullOrderCurrentOrder.market == context.key.value,
            FullOrderCurrentOrder.order_id.in_(ids))).mappings())
    items = tuple(_item_from_row(row) for row in connection.execute(
        select(FullOrderCurrentItem.__table__).where(FullOrderCurrentItem.market == context.key.value,
            FullOrderCurrentItem.order_id.in_(ids))).mappings())
    statement_orders = tuple(_order_fact(row) for row in connection.execute(
        select(StatementData.__table__).where(StatementData.platform == context.persisted_platform,
            StatementData.order_id.in_(ids), StatementData.record_type == 'ORDER',
            StatementData.validation_status == 'PASSED', StatementData.commit_status == 'COMMITTED')).mappings())
    components = tuple(_component_fact(row) for row in connection.execute(
        select(StatementFinancialComponent.__table__).where(
            StatementFinancialComponent.platform == context.persisted_platform,
            StatementFinancialComponent.order_id.in_(ids),
            StatementFinancialComponent.commit_status == 'COMMITTED',
            StatementFinancialComponent.component_amount.is_not(None))).mappings())
    snapshot = FullOrderCurrentSnapshot(orders=orders, items=items)

    class ScopedStatement:
        persisted_platform = context.persisted_platform

        def read_committed_financial_facts(self):
            return CommittedStatementFinancialFacts(context.persisted_platform, statement_orders, components)

        def read_order_adjustments(self, order_ids=None):
            # Adjustments are independent of Order Fact calculations.
            return ()

    class ScopedOrders:
        def read_snapshot(self):
            return snapshot

    projections = read_order_financial_projections(market_context=context,
        full_order_repository=ScopedOrders(), statement_reader=ScopedStatement(),
        order_ids=ids, current_snapshot=snapshot)
    return tuple(fact_from_projection(projection) for projection in projections)
