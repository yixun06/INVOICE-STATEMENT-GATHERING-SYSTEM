"""MY-only PostgreSQL persistence adapter for the active Statement contract.

The adapter deliberately delegates all business classification to the shared
Statement preflight.  It owns only typed PostgreSQL reconstruction, one locked
transaction, and deterministic readback.  It never calls a Google Sheets
Statement tab and never falls back when PostgreSQL is unavailable.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import Integer, Numeric, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.invoice_app.domain.order_adjustment import CanonicalOrderAdjustment, EvidenceStatus
from src.invoice_app.pg_orm.pg_models import (
    OrderAdjustment,
    StatementData,
    StatementFinancialComponent,
    StatementSummary,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    CommittedStatementFinancialComponentFact,
    CommittedStatementFinancialFacts,
    CommittedStatementOrderFact,
)
from src.invoice_app.services.shopee_statement_persistence import (
    CommittedStatementReference,
    StatementCommitBlocked,
    StatementCommitPlan,
    StatementCommitPreflight,
    StatementCommitState,
    _canonicalize_bundle_monetary_value,
    prepare_full_order_existence_statement_commit_plan,
    preview_active_statement_state,
    serialize_order_adjustment,
)
from src.invoice_app.services.uat2_persistence_schema import (
    ORDER_ADJUSTMENTS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_SUMMARY_HEADERS,
)


_MY_PLATFORM = "Shopee"
_MY_STATEMENT_ADVISORY_LOCK = "invoicegather:my-statement"


class PostgresStatementRepository:
    """One MY-locked committed Statement reader/writer backed only by PostgreSQL."""

    def __init__(self, *, engine: Engine, persisted_platform: str = _MY_PLATFORM) -> None:
        if persisted_platform != _MY_PLATFORM:
            raise ValueError("PostgreSQL Statement runtime is MY-only; SG remains unchanged.")
        self._engine = engine
        self._persisted_platform = persisted_platform

    @property
    def persisted_platform(self) -> str:
        return self._persisted_platform

    def reload_active_commit_state(self) -> StatementCommitState:
        with self._engine.connect() as connection:
            return self._read_state(connection)

    def reload_commit_state(self) -> StatementCommitState:
        """Compatibility entry point; active MY never reads Invoice/PDF state."""
        return self.reload_active_commit_state()

    def read_committed_financial_facts(self) -> CommittedStatementFinancialFacts:
        with self._engine.connect() as connection:
            order_rows = connection.execute(
                select(StatementData.__table__).where(
                    StatementData.platform == self._persisted_platform,
                    StatementData.validation_status == "PASSED",
                    StatementData.commit_status == "COMMITTED",
                    StatementData.record_type == "ORDER",
                )
            ).mappings()
            orders = tuple(_order_fact(row) for row in order_rows)
            component_rows = connection.execute(
                select(StatementFinancialComponent.__table__).where(
                    StatementFinancialComponent.platform == self._persisted_platform,
                    StatementFinancialComponent.commit_status == "COMMITTED",
                    # Text-only Shipping Fee Discrepancy reasons are retained in
                    # the immutable native ledger/readback state. They are not
                    # financial facts: the shared projection contract requires a
                    # Decimal component amount and never allocates text evidence.
                    StatementFinancialComponent.component_amount.is_not(None),
                )
            ).mappings()
            components = tuple(_component_fact(row) for row in component_rows)
        return CommittedStatementFinancialFacts(
            platform=self._persisted_platform,
            order_facts=orders,
            financial_components=components,
        )

    def preview_statement_batch(self, plan: StatementCommitPlan) -> StatementCommitPreflight:
        _validate_plan_market(plan, self._persisted_platform)
        with self._engine.connect() as connection:
            return self._preflight(plan, self._read_state(connection))[0]

    def write_statement_batch(self, plan: StatementCommitPlan) -> None:
        """Classify and commit an active MY plan in one serializable transaction.

        ``pg_advisory_xact_lock`` serializes MY Statement writers across app
        processes.  The existing unique constraints remain the final identity
        protection; serializable isolation rejects a stale concurrent snapshot.
        """

        _validate_plan_market(plan, self._persisted_platform)
        try:
            with self._engine.connect().execution_options(
                isolation_level="SERIALIZABLE"
            ) as connection:
                with connection.begin():
                    connection.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": _MY_STATEMENT_ADVISORY_LOCK},
                    )
                    preview, effective = self._preflight(plan, self._read_state(connection))
                    if not preview.eligible or effective is None:
                        raise StatementCommitBlocked(
                            "Fresh PostgreSQL Statement preflight failed: "
                            + ", ".join(
                                preview.blocker_codes
                                or preview.blocker_reasons
                                or ("ALREADY_IMPORTED / NO_NEW_FACTS",)
                            )
                        )
                    _insert_plan(connection, effective)
                    _verify_inserted(connection, effective)
        except StatementCommitBlocked:
            raise
        except IntegrityError as error:
            raise StatementCommitBlocked(
                "PostgreSQL Statement transaction rolled back because a committed "
                "identity conflicts with the fresh preflight."
            ) from error
        except SQLAlchemyError as error:
            raise StatementCommitBlocked(
                "PostgreSQL Statement transaction rolled back; no Google Sheets fallback is permitted."
            ) from error

    def weekly_statement_rows(self) -> Mapping[str, tuple[tuple[str, ...], ...]]:
        """Return only MY Statement-owned rows for the legacy Billing projector."""

        with self._engine.connect() as connection:
            return {
                "statement_data": tuple(
                    _model_cells(row, STATEMENT_DATA_HEADERS)
                    for row in connection.execute(
                        select(StatementData.__table__)
                        .where(StatementData.platform == self._persisted_platform)
                        .order_by(StatementData.id)
                    ).mappings()
                ),
                "statement_financial_components": tuple(
                    _model_cells(row, STATEMENT_FINANCIAL_COMPONENT_HEADERS)
                    for row in connection.execute(
                        select(StatementFinancialComponent.__table__)
                        .where(StatementFinancialComponent.platform == self._persisted_platform)
                        .order_by(StatementFinancialComponent.id)
                    ).mappings()
                ),
                "statement_summary": tuple(
                    _model_cells(row, STATEMENT_SUMMARY_HEADERS)
                    for row in connection.execute(
                        select(StatementSummary.__table__)
                        .where(StatementSummary.platform == self._persisted_platform)
                        .order_by(StatementSummary.id)
                    ).mappings()
                ),
            }

    def reporting_statement_data_rows(self) -> tuple[tuple[str, ...], ...]:
        """Return MY committed-order date authority for reporting without Sheets."""

        with self._engine.connect() as connection:
            return tuple(
                _model_cells(row, STATEMENT_DATA_HEADERS)
                for row in connection.execute(
                    select(StatementData.__table__)
                    .where(StatementData.platform == self._persisted_platform)
                    .order_by(StatementData.id)
                ).mappings()
            )

    def _preflight(
        self, plan: StatementCommitPlan, state: StatementCommitState
    ) -> tuple[StatementCommitPreflight, StatementCommitPlan | None]:
        if plan.authority != "FULL_ORDER_CURRENT":
            raise StatementCommitBlocked(
                "PostgreSQL Statement runtime accepts only the active Full Order Current contract."
            )
        prepared = prepare_full_order_existence_statement_commit_plan(
            plan.statement,
            audit=plan.audit,
            full_order_ids=tuple(
                row.order_id for row in plan.statement.order_rows if row.order_id
            ),
            validation_passed=True,
            persisted_platform=self._persisted_platform,
            semantic_overlap_dedupe=True,
        )
        preview = preview_active_statement_state(prepared, state)
        return preview, preview.proposed_plan

    def _read_state(self, connection: Connection) -> StatementCommitState:
        data_rows = tuple(
            _model_cells(row, STATEMENT_DATA_HEADERS)
            for row in connection.execute(
                select(StatementData.__table__)
                .where(StatementData.platform == self._persisted_platform)
                .order_by(StatementData.id)
            ).mappings()
        )
        component_rows = tuple(
            _model_cells(row, STATEMENT_FINANCIAL_COMPONENT_HEADERS)
            for row in connection.execute(
                select(StatementFinancialComponent.__table__)
                .where(StatementFinancialComponent.platform == self._persisted_platform)
                .order_by(StatementFinancialComponent.id)
            ).mappings()
        )
        adjustment_rows = tuple(
            _adjustment_from_model(row)
            for row in connection.execute(
                select(OrderAdjustment.__table__)
                .where(OrderAdjustment.platform == self._persisted_platform)
                .order_by(OrderAdjustment.adjustment_event_fingerprint)
            ).mappings()
        )
        positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
        references: list[CommittedStatementReference] = []
        seen: set[tuple[str, str, str]] = set()
        for values in data_rows:
            if values[positions["commit_status"]] != "COMMITTED":
                continue
            key = (
                values[positions["statement_file_hash"]],
                values[positions["statement_period_from"]],
                values[positions["statement_period_to"]],
            )
            if not all(key) or key in seen:
                continue
            seen.add(key)
            references.append(
                CommittedStatementReference(
                    file_hash=key[0],
                    statement_period_from=_date(key[1], "statement_period_from"),
                    statement_period_to=_date(key[2], "statement_period_to"),
                )
            )
        return StatementCommitState(
            orders={},
            items=(),
            committed_statements=tuple(references),
            order_adjustments=adjustment_rows,
            committed_statement_rows=data_rows,
            committed_financial_component_rows=component_rows,
        )


def _validate_plan_market(plan: StatementCommitPlan, platform: str) -> None:
    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    for values in plan.rows:
        if values[positions["platform"]] != platform:
            raise StatementCommitBlocked("Statement plan platform does not match the PostgreSQL MY writer.")


def _insert_plan(connection: Connection, plan: StatementCommitPlan) -> None:
    _insert_values(connection, StatementData, STATEMENT_DATA_HEADERS, plan.rows)
    _insert_values(
        connection,
        StatementFinancialComponent,
        STATEMENT_FINANCIAL_COMPONENT_HEADERS,
        plan.financial_component_rows,
    )
    _insert_values(connection, StatementSummary, STATEMENT_SUMMARY_HEADERS, plan.summary_rows)
    adjustment_values = tuple(serialize_order_adjustment(event) for event in plan.order_adjustments)
    _insert_values(connection, OrderAdjustment, ORDER_ADJUSTMENTS_HEADERS, adjustment_values)


def _insert_values(
    connection: Connection,
    model: type[Any],
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
) -> None:
    values = tuple(rows)
    if not values:
        return
    table = model.__table__
    records = [
        {
            name: _database_value(table.c[name].type, value)
            for name, value in zip(headers, row)
        }
        for row in values
    ]
    connection.execute(table.insert(), records)


def _verify_inserted(connection: Connection, plan: StatementCommitPlan) -> None:
    _verify_keys(
        connection,
        StatementData,
        ("statement_batch_id", "record_type", "sequence_no"),
        STATEMENT_DATA_HEADERS,
        plan.rows,
    )
    _verify_keys(
        connection,
        StatementFinancialComponent,
        ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "component_name"),
        STATEMENT_FINANCIAL_COMPONENT_HEADERS,
        plan.financial_component_rows,
    )
    _verify_keys(
        connection,
        StatementSummary,
        ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "native_label"),
        STATEMENT_SUMMARY_HEADERS,
        plan.summary_rows,
    )
    _verify_keys(
        connection,
        OrderAdjustment,
        ("adjustment_event_fingerprint",),
        ORDER_ADJUSTMENTS_HEADERS,
        tuple(serialize_order_adjustment(event) for event in plan.order_adjustments),
    )


def _verify_keys(
    connection: Connection,
    model: type[Any],
    key_names: Sequence[str],
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
) -> None:
    expected = tuple(rows)
    if not expected:
        return
    table = model.__table__
    for row in expected:
        clauses = [
            table.c[name] == _database_value(table.c[name].type, row[headers.index(name)])
            for name in key_names
        ]
        found = connection.execute(
            select(*(table.c[name] for name in headers)).where(*clauses)
        ).mappings().all()
        if len(found) != 1:
            raise StatementCommitBlocked(
                f"PostgreSQL Statement readback did not verify stable identity {tuple(row[headers.index(name)] for name in key_names)!r}."
            )
        actual = _verification_cells(table, headers, found[0])
        expected_cells = _verification_cells(table, headers, row)
        if actual != expected_cells:
            raise StatementCommitBlocked(
                "PostgreSQL Statement readback did not verify every expected committed fact for "
                f"{tuple(row[headers.index(name)] for name in key_names)!r}."
            )


def _verification_cells(
    table: Any,
    headers: Sequence[str],
    values: Mapping[str, Any] | Sequence[Any],
) -> tuple[object, ...]:
    """Return the strict transactional-readback comparison representation.

    PostgreSQL ``NUMERIC(p, s)`` legitimately reconstructs a source ``12.9``
    as ``Decimal('12.90')``.  That is the same monetary fact, so Numeric
    columns reuse the active Statement Decimal identity primitive.  Every
    non-Numeric field retains the existing exact cell representation.
    """

    return tuple(
        _verification_cell(table.c[name].type, _row_value(values, index, name))
        for index, name in enumerate(headers)
    )


def _verification_cell(column_type: Any, value: Any) -> object:
    if value is None:
        return ""
    if isinstance(column_type, Numeric):
        return _canonicalize_bundle_monetary_value(value)
    return _cell(value)


def _row_value(values: Mapping[str, Any] | Sequence[Any], index: int, name: str) -> Any:
    return values[name] if isinstance(values, Mapping) else values[index]


def _database_value(column_type: Any, value: str) -> Any:
    if value == "":
        return None
    if isinstance(column_type, Numeric):
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise StatementCommitBlocked("Statement monetary value is invalid for PostgreSQL persistence.") from error
    if isinstance(column_type, Integer):
        try:
            return int(value)
        except ValueError as error:
            raise StatementCommitBlocked("Statement integer value is invalid for PostgreSQL persistence.") from error
    return value


def _model_cells(model: Any, headers: Sequence[str]) -> tuple[str, ...]:
    return tuple(_cell(_value(model, name)) for name in headers)


def _value(model: Any, name: str) -> Any:
    return model[name] if isinstance(model, Mapping) else getattr(model, name)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise StatementCommitBlocked(f"PostgreSQL committed Statement has invalid {field}.") from error


def _optional_date(value: str | None, field: str) -> date | None:
    return None if not value else _date(value, field)


def _order_fact(row: Mapping[str, Any] | StatementData) -> CommittedStatementOrderFact:
    if not _value(row, "order_id") or not _value(row, "statement_batch_id") or not _value(row, "statement_file_hash"):
        raise StatementCommitBlocked("PostgreSQL committed ORDER evidence has incomplete identity.")
    return CommittedStatementOrderFact(
        order_id=_value(row, "order_id"),
        payout_completed_date=_optional_date(_value(row, "payout_completed_date"), "payout_completed_date"),
        total_released_amount=_value(row, "total_released_amount"),
        statement_product_price=_value(row, "statement_product_price"),
        statement_batch_id=_value(row, "statement_batch_id"),
        statement_file_hash=_value(row, "statement_file_hash"),
        statement_period_from=_optional_date(_value(row, "statement_period_from"), "statement_period_from"),
        statement_period_to=_optional_date(_value(row, "statement_period_to"), "statement_period_to"),
        source_row_number=int(_value(row, "statement_source_row_number") or 0),
    )


def _component_fact(row: Mapping[str, Any] | StatementFinancialComponent) -> CommittedStatementFinancialComponentFact:
    if not _value(row, "component_name") or _value(row, "component_amount") is None:
        raise StatementCommitBlocked("PostgreSQL committed Statement component is incomplete.")
    return CommittedStatementFinancialComponentFact(
        record_type=_value(row, "record_type") or "",
        order_id=_value(row, "order_id"),
        component_name=_value(row, "component_name"),
        component_amount=_value(row, "component_amount"),
        statement_batch_id=_value(row, "statement_batch_id"),
        statement_file_hash=_value(row, "statement_file_hash") or "",
        statement_source_sheet=_value(row, "statement_source_sheet"),
        statement_source_row_number=_value(row, "statement_source_row_number"),
        sequence_no=_value(row, "sequence_no") or "",
        payout_completed_date=_optional_date(_value(row, "payout_completed_date"), "payout_completed_date"),
    )


def _adjustment_from_model(row: Mapping[str, Any] | OrderAdjustment) -> CanonicalOrderAdjustment:
    if not _value(row, "adjustment_complete_date") or _value(row, "adjustment_amount") is None:
        raise StatementCommitBlocked("PostgreSQL committed Order Adjustment is incomplete.")
    try:
        status = EvidenceStatus(_value(row, "evidence_status") or EvidenceStatus.STATEMENT_ONLY.value)
    except ValueError as error:
        raise StatementCommitBlocked("PostgreSQL committed Order Adjustment has invalid evidence status.") from error
    observed = None
    if _value(row, "first_observed_at"):
        try:
            observed = datetime.fromisoformat(_value(row, "first_observed_at"))
        except ValueError as error:
            raise StatementCommitBlocked("PostgreSQL committed Order Adjustment has invalid first_observed_at.") from error
    return CanonicalOrderAdjustment(
        platform=_value(row, "platform") or "",
        linked_order_id=_value(row, "linked_order_id") or "",
        adjustment_type=_value(row, "adjustment_type") or "",
        adjustment_description=_value(row, "adjustment_description") or "",
        adjustment_reason=_value(row, "adjustment_reason"),
        adjustment_complete_date=_date(_value(row, "adjustment_complete_date"), "adjustment_complete_date"),
        adjustment_amount=_value(row, "adjustment_amount"),
        payout_completed_date=_optional_date(_value(row, "payout_completed_date"), "payout_completed_date"),
        statement_batch_id=_value(row, "statement_batch_id") or "",
        statement_sequence_no=_value(row, "statement_sequence_no") or "",
        statement_source_filename=_value(row, "statement_source_filename") or "",
        statement_file_hash=_value(row, "statement_file_hash") or "",
        adjustment_event_fingerprint=_value(row, "adjustment_event_fingerprint"),
        evidence_status=status,
        invoice_evidence_date=_optional_date(_value(row, "invoice_evidence_date"), "invoice_evidence_date"),
        invoice_evidence_amount=_value(row, "invoice_evidence_amount"),
        invoice_evidence_final_amount=_value(row, "invoice_evidence_final_amount"),
        invoice_evidence_reason=_value(row, "invoice_evidence_reason"),
        invoice_source_pdf=_value(row, "invoice_source_pdf"),
        invoice_source_hash=_value(row, "invoice_source_hash"),
        first_observed_at=observed,
    )
