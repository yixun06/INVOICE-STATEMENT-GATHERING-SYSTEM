"""Market-scoped summary and revision-checked native evidence reads."""
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple
from sqlalchemy import case, func, select, text
from src.invoice_app.pg_orm.pg_models import StatementFinancialComponent as Component
from src.invoice_app.services import analysis_perf as perf
from src.invoice_app.services.market_context import resolve_market_context


@dataclass(frozen=True)
class DashboardComponentSummary:
    order_id: str | None
    record_type: str | None
    component_name: str | None
    amount_sign: int | None
    amount: Decimal | None
    source_rows: int

    @property
    def component_amount(self):
        return self.amount


class CompactComponentSummary(NamedTuple):
    """The six SQL summary fields, with the existing component interface."""
    order_id: str | None
    record_type: str | None
    component_name: str | None
    amount_sign: int | None
    amount: Decimal | None
    source_rows: int

    @property
    def component_amount(self):
        return self.amount


def summary_query(market_context):
    platform = resolve_market_context(market_context).persisted_platform
    sign = case((Component.component_amount.is_(None), None),
                (Component.component_amount < 0, -1),
                (Component.component_amount > 0, 1), else_=0)
    return select(Component.order_id, Component.record_type, Component.component_name,
                  sign.label("amount_sign"), func.sum(Component.component_amount).label("amount"),
                  func.count().label("source_rows")).where(
        Component.platform == platform, Component.commit_status == "COMMITTED"
    ).group_by(Component.order_id, Component.record_type, Component.component_name, sign)


def read_component_summaries(connection, market_context):
    """Preserve record types, exact labels, signs, NULL groups and unlinked rows.

    Aggregation is SQL-side. Statement ORDER facts must independently remain
    complete for duplicate/conflict classification. No batch provenance is
    fabricated here: it belongs to the unchanged native detail reader.
    """
    return perf.read_rows(connection, summary_query(market_context),
        lambda row: DashboardComponentSummary(**{key:row[key] for key in
            ("order_id", "record_type", "component_name", "amount_sign", "amount", "source_rows")}),
        "financial_component_summaries")


def read_compact_component_summaries(connection, market_context):
    """Read the identical grouped SQL rows without mapping/dict conversion."""
    with perf.stage("financial_component_summaries_execute", query_count=1):
        rows = connection.execute(summary_query(market_context))
    with perf.stage("financial_component_summaries_fetch_convert") as counts:
        result = tuple(CompactComponentSummary(*row) for row in rows)
        counts["row_count"] = len(result)
    return result


def detail_query(market_context, order_id):
    platform = resolve_market_context(market_context).persisted_platform
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("An exact nonblank order identity is required.")
    return select(Component.__table__).where(Component.platform == platform,
        Component.commit_status == "COMMITTED", Component.order_id == order_id).order_by(Component.id)


def read_native_order_details(connection, market_context, order_id):
    """Return all columns and rows, including NULL amounts; no LIMIT/deduplication.

    Internal primitive only: caller must validate source version in the SAME
    repeatable read-only transaction before presenting these native details.
    """
    return perf.read_rows(connection, detail_query(market_context, order_id), dict,
                          "financial_component_details")


class DashboardRefreshRequired(RuntimeError):
    """The dashboard snapshot no longer matches committed source evidence."""


_REVISION_TABLES = ("full_order_current_orders", "full_order_current_items", "statement_data",
                    "statement_financial_components", "order_adjustments")


def validate_revision_infrastructure(connection):
    """Require enabled revision triggers before relying on revision-checked details."""
    rows = connection.execute(text("""
        SELECT c.relname, t.tgname, t.tgenabled
        FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND NOT t.tgisinternal
    """)).all()
    enabled = {(row[0], row[1]) for row in rows if row[2] in ("O", "A")}
    required = {(table, f"ig_revision_{table}_{operation}")
                for table in _REVISION_TABLES for operation in ("insert", "update", "delete")}
    required.update((table, "ig_revision_truncate") for table in _REVISION_TABLES)
    if not required <= enabled:
        raise RuntimeError("Analysis source revision triggers are missing or disabled.")


def read_revision(connection, market_context):
    market = resolve_market_context(market_context).key.value
    value = connection.execute(text("SELECT revision FROM analysis_source_revision WHERE market = :market"),
                               {"market": market}).scalar_one_or_none()
    if value is None:
        raise RuntimeError("Analysis source revision is unavailable; refresh is required.")
    return value


def read_checked_order_details(engine, market_context, order_id, expected_revision):
    """Read revision and detail in the same PostgreSQL read-only MVCC snapshot.

    Callers must obtain expected_revision and summary in their own single
    repeatable-read transaction. This primitive does not create a summary.
    """
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Consistent detail reads require PostgreSQL.")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
        raise ValueError("A valid expected revision is required.")
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            validate_revision_infrastructure(connection)
            with perf.stage("detail_version_check"):
                current = read_revision(connection, market_context)
            if current != expected_revision:
                raise DashboardRefreshRequired("Financial data changed. Refresh the dashboard before viewing details.")
            with perf.stage("detail_lookup"):
                return read_native_order_details(connection, market_context, order_id)


def load_optimized_snapshot(*, market_context, full_order_repository, statement_reader, compact_summary=False):
    """Load all authorities through a single, short read-only PostgreSQL snapshot."""
    from dataclasses import replace
    from src.invoice_app.pg_orm.pg_models import StatementData, OrderAdjustment
    from src.invoice_app.services.google_sheets_statement_writer import CommittedStatementFinancialFacts
    from src.invoice_app.services.postgres_statement_repository import _order_fact, _adjustment_from_model
    from src.invoice_app.services.shopee_analysis import load_shopee_analysis_snapshot
    context = resolve_market_context(market_context)
    if getattr(full_order_repository, "_market", None) != context.key.value:
        raise RuntimeError("Full Order market mismatch.")
    if statement_reader.persisted_platform != context.persisted_platform:
        raise RuntimeError("Statement market mismatch.")
    engine = full_order_repository._engine
    if engine.dialect.name != "postgresql" or engine.url != statement_reader._engine.url:
        raise RuntimeError("Financial summary requires one shared PostgreSQL target.")
    class SharedFullOrder:
        def read_snapshot(self): return full_order_repository._read_snapshot(connection)
    class SharedStatement:
        persisted_platform = context.persisted_platform
        def read_committed_financial_facts(self):
            orders = perf.read_rows(connection, select(StatementData.__table__).where(
                StatementData.platform == context.persisted_platform,
                StatementData.validation_status == "PASSED", StatementData.commit_status == "COMMITTED",
                StatementData.record_type == "ORDER"), _order_fact, "summary_statement_orders")
            summaries = (read_compact_component_summaries if compact_summary else read_component_summaries)(connection, context)
            if any(not summary.component_name for summary in summaries if summary.amount is not None):
                raise RuntimeError("PostgreSQL committed Statement component is incomplete.")
            return CommittedStatementFinancialFacts(platform=context.persisted_platform, order_facts=orders,
                financial_components=tuple(s for s in summaries if s.amount is not None))
        def read_order_adjustments(self, order_ids=None):
            requested = None if order_ids is None else {str(value).strip() for value in order_ids if str(value).strip()}
            if requested is not None and not requested:
                return ()
            events = perf.read_rows(connection, select(OrderAdjustment.__table__).where(
                OrderAdjustment.platform == context.persisted_platform), _adjustment_from_model, "summary_adjustments")
            return tuple(event for event in events if requested is None or str(event.linked_order_id or "").strip() in requested)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            validate_revision_infrastructure(connection)
            revision = read_revision(connection, context)
            snapshot = load_shopee_analysis_snapshot(market_context=context,
                full_order_repository=SharedFullOrder(), statement_reader=SharedStatement())
            return replace(snapshot, source_revision=revision)
