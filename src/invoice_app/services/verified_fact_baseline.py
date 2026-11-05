"""Transaction-scoped, fail-closed initial financial fact baseline."""
from dataclasses import asdict
from sqlalchemy import select, text

from src.invoice_app.pg_orm.pg_models import StatementData, StatementFinancialComponent
from src.invoice_app.pg_orm.runtime import is_authorized_fact_server, assert_fact_server_target
from src.invoice_app.repositories.postgres_full_order_current_repository import PostgresFullOrderCurrentRepository
from src.invoice_app.services.dashboard_financial_summary import validate_revision_infrastructure
from src.invoice_app.services.financial_fact_poc import OrderFact, fact_from_projection
from src.invoice_app.services.google_sheets_statement_writer import CommittedStatementFinancialFacts
from src.invoice_app.services.incremental_financial_fact import calculate_targeted_facts
from src.invoice_app.services.market_context import resolve_market_context
from src.invoice_app.services.postgres_statement_repository import _order_fact, _component_fact
from src.invoice_app.services.shopee_order_financial_projection import read_order_financial_projections


def create_verified_baseline(connection, market_context):
    """Publish one market's initial baseline; caller must commit the transaction.

    Source tables are locked before the source snapshot is read. Only complete
    revision-0-forward capture is accepted; historical unverifiable work is not
    removed. A savepoint ensures exceptions cannot leave partial publication.
    """
    if market_context is None:
        raise RuntimeError('Explicit market is required')
    context = resolve_market_context(market_context)
    market = context.key.value
    if market not in ('shopee_my', 'shopee_sg') or not connection.in_transaction():
        raise RuntimeError('Explicit market and an existing transaction are required')
    url = connection.engine.url
    if is_authorized_fact_server(url):
        assert_fact_server_target(connection)
    elif (url.host, url.port, url.database) != ('127.0.0.1', 5433, 'invoicegather_fact_atomicity_test'):
        raise RuntimeError('Unapproved baseline target')
    elif connection.execute(text('SELECT current_database(),current_schema()')).one() != ('invoicegather_fact_atomicity_test', 'ig_fact_baseline_task2'):
        raise RuntimeError('Unapproved baseline database or schema')
    if connection.execute(text('SHOW transaction_isolation')).scalar_one() != 'read committed':
        raise RuntimeError('Baseline requires READ COMMITTED and source table locks')
    with connection.begin_nested():
        connection.execute(text('LOCK TABLE full_order_current_orders, full_order_current_items, statement_data, statement_financial_components, order_adjustments IN SHARE MODE'))
        connection.execute(text('LOCK TABLE analysis_order_financial_fact, analysis_financial_fact_revision, analysis_financial_fact_pending IN SHARE ROW EXCLUSIVE MODE'))
        validate_revision_infrastructure(connection)
        capture_count = connection.execute(text('''SELECT count(*) FROM pg_trigger t
          JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname=current_schema() AND NOT t.tgisinternal
            AND t.tgname LIKE 'ig_revision_%' AND t.tgenabled IN ('O','A')
            AND r.relname IN ('full_order_current_orders','full_order_current_items',
              'statement_data','statement_financial_components','order_adjustments')
            AND position('analysis_financial_fact_pending' IN pg_get_functiondef(t.tgfoid))>0''')).scalar_one()
        if capture_count != 20:
            raise RuntimeError('Revision-aware source capture is incomplete')
        revision = connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:m FOR UPDATE'), {'m': market}).scalar_one()
        if connection.execute(text('SELECT EXISTS (SELECT FROM analysis_financial_fact_revision WHERE market=:m) OR EXISTS (SELECT FROM analysis_order_financial_fact WHERE market=:m)'), {'m': market}).scalar_one():
            raise RuntimeError('Baseline target must have no existing facts or revision for this market')
        connection.execute(text('SELECT ig_fact_assert_capture_coverage(:m,0,:r)'), {'m': market, 'r': revision})
        if connection.execute(text('SELECT EXISTS (SELECT FROM analysis_financial_fact_pending WHERE market=:m AND (source_revision IS NULL OR source_revision<=0 OR source_revision>:r))'), {'m': market, 'r': revision}).scalar_one():
            raise RuntimeError('Pending history is outside verified source coverage')
        orphan = connection.execute(text('''SELECT EXISTS (SELECT FROM full_order_current_items i
          WHERE i.market=:m AND NOT EXISTS (SELECT FROM full_order_current_orders o WHERE o.market=i.market AND o.order_id=i.order_id))
          OR EXISTS (SELECT FROM statement_data s WHERE s.platform=:p AND s.record_type='ORDER'
            AND s.validation_status='PASSED' AND s.commit_status='COMMITTED'
            AND NOT EXISTS (SELECT FROM full_order_current_orders o WHERE o.market=:m AND o.order_id=s.order_id))
          OR EXISTS (SELECT FROM statement_financial_components s WHERE s.platform=:p
            AND s.record_type='ORDER' AND s.commit_status='COMMITTED' AND btrim(coalesce(s.order_id,''))<>''
            AND NOT EXISTS (SELECT FROM full_order_current_orders o WHERE o.market=:m AND o.order_id=s.order_id))'''), {'m': market, 'p': context.persisted_platform}).scalar_one()
        if orphan:
            raise RuntimeError('Orphaned source order or item evidence')
        ids = tuple(connection.execute(text('SELECT order_id FROM full_order_current_orders WHERE market=:m ORDER BY order_id'), {'m': market}).scalars())
        if any(not value or value != value.strip() for value in ids) or len(ids) != len(set(ids)):
            raise RuntimeError('Invalid or duplicate source identities')
        snapshot = PostgresFullOrderCurrentRepository(engine=connection.engine, market_context=context)._read_snapshot(connection)
        orders = tuple(_order_fact(row) for row in connection.execute(select(StatementData.__table__).where(
            StatementData.platform == context.persisted_platform, StatementData.record_type == 'ORDER',
            StatementData.validation_status == 'PASSED', StatementData.commit_status == 'COMMITTED')).mappings())
        components = tuple(_component_fact(row) for row in connection.execute(select(StatementFinancialComponent.__table__).where(
            StatementFinancialComponent.platform == context.persisted_platform,
            StatementFinancialComponent.commit_status == 'COMMITTED', StatementFinancialComponent.component_amount.is_not(None))).mappings())

        class LegacyStatement:
            persisted_platform = context.persisted_platform
            def read_committed_financial_facts(self):
                return CommittedStatementFinancialFacts(context.persisted_platform, orders, components)
            def read_order_adjustments(self, order_ids=None):
                return ()  # Independent adjustments do not contribute to Order Facts.

        legacy = read_order_financial_projections(market_context=context, full_order_repository=None,
            statement_reader=LegacyStatement(), current_snapshot=snapshot)
        expected = {p.order_id: fact_from_projection(p) for p in legacy}
        calculated = calculate_targeted_facts(connection, context, ids)
        actual = {f.order_id: f for f in calculated}
        if len(expected) != len(legacy) or len(actual) != len(calculated) or set(ids) != set(expected) or actual != expected:
            raise RuntimeError('Baseline identity or legacy financial parity mismatch')
        columns = tuple(OrderFact.__dataclass_fields__)
        if calculated:
            connection.execute(text('INSERT INTO analysis_order_financial_fact (' + ','.join(columns) + ') VALUES (' + ','.join(':'+col for col in columns) + ')'), [asdict(f) for f in calculated])
        persisted = tuple(OrderFact(**row) for row in connection.execute(text('SELECT '+','.join(columns)+' FROM analysis_order_financial_fact WHERE market=:m'), {'m': market}).mappings())
        if len(persisted) != len(expected) or {f.order_id: f for f in persisted} != expected:
            raise RuntimeError('Persisted baseline identity or financial mismatch')
        if connection.execute(text('SELECT revision FROM analysis_source_revision WHERE market=:m'), {'m': market}).scalar_one() != revision:
            raise RuntimeError('Source revision changed during baseline')
        connection.execute(text('DELETE FROM analysis_financial_fact_pending WHERE market=:m AND source_revision>0 AND source_revision<=:r'), {'m': market, 'r': revision})
        connection.execute(text('INSERT INTO analysis_financial_fact_revision(market,source_revision) VALUES (:m,:r)'), {'m': market, 'r': revision})
        return {'market': market, 'source_count': len(ids), 'fact_count': len(persisted), 'mismatches': 0, 'revision': revision}
