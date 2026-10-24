#!/usr/bin/env python
"""One-way Google Sheets -> verified local Docker PostgreSQL migration.

This utility is isolated from InvoiceGather repositories. It reads Google
Sheets, validates the complete snapshot, and synchronizes it to the dedicated
local Docker database without truncating or deleting rows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import Integer, Numeric, String, URL, create_engine, func, inspect, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.invoice_app.pg_orm.pg_models import (  # noqa: E402
    Base, FullOrderCurrentItem, FullOrderCurrentOrder, InvoiceItem, InvoiceOrder,
    OrderAdjustment, StatementData, StatementFinancialComponent, StatementSummary,
)
from src.invoice_app.repositories.google_sheets_historical_invoice_repository import (  # noqa: E402
    GoogleApiHistoricalInvoiceGateway,
)
from src.invoice_app.services.uat2_data_settings import configured_uat2_data_settings  # noqa: E402
from src.invoice_app.services.uat2_persistence_schema import (  # noqa: E402
    INVOICE_ITEMS_HEADERS, INVOICE_ITEMS_TAB, INVOICE_ORDERS_HEADERS, INVOICE_ORDERS_TAB,
    ORDER_ADJUSTMENTS_HEADERS, ORDER_ADJUSTMENTS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS, SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS, SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
    STATEMENT_DATA_HEADERS, STATEMENT_DATA_TAB, STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_FINANCIAL_COMPONENTS_TAB, STATEMENT_SUMMARY_HEADERS, STATEMENT_SUMMARY_TAB,
)


@dataclass(frozen=True)
class TableSpec:
    tab: str
    headers: tuple[str, ...]
    model: type[Base]
    key_columns: tuple[str, ...]

    @property
    def table_name(self) -> str:
        return self.model.__table__.name


TABLE_SPECS = (
    TableSpec(INVOICE_ORDERS_TAB, tuple(INVOICE_ORDERS_HEADERS), InvoiceOrder, ("platform", "order_id")),
    TableSpec(INVOICE_ITEMS_TAB, tuple(INVOICE_ITEMS_HEADERS), InvoiceItem, ("platform", "order_id", "item_index")),
    TableSpec(SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB, tuple(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS), FullOrderCurrentOrder, ("market", "order_id")),
    TableSpec(SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB, tuple(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS), FullOrderCurrentItem, ("market", "order_id", "item_index")),
    TableSpec(STATEMENT_DATA_TAB, tuple(STATEMENT_DATA_HEADERS), StatementData, ("statement_batch_id", "record_type", "sequence_no")),
    TableSpec(STATEMENT_FINANCIAL_COMPONENTS_TAB, tuple(STATEMENT_FINANCIAL_COMPONENT_HEADERS), StatementFinancialComponent, ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "component_name")),
    TableSpec(STATEMENT_SUMMARY_TAB, tuple(STATEMENT_SUMMARY_HEADERS), StatementSummary, ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "native_label")),
    TableSpec(ORDER_ADJUSTMENTS_TAB, tuple(ORDER_ADJUSTMENTS_HEADERS), OrderAdjustment, ("adjustment_event_fingerprint",)),
)


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    container: str

    @classmethod
    def from_environment(cls) -> "PostgresConfig":
        user = os.environ.get("INVOICEGATHER_PG_USER", "")
        password = os.environ.get("INVOICEGATHER_PG_PASSWORD", "")
        if not user or not password:
            raise RuntimeError(
                "INVOICEGATHER_PG_USER and INVOICEGATHER_PG_PASSWORD are required; "
                "credentials are never stored in source code."
            )
        config = cls(
            host=os.environ.get("INVOICEGATHER_PG_HOST", "127.0.0.1"),
            port=int(os.environ.get("INVOICEGATHER_PG_PORT", "5433")),
            database=os.environ.get("INVOICEGATHER_PG_DATABASE", "invoicegather"),
            user=user,
            password=password,
            container=os.environ.get("INVOICEGATHER_PG_CONTAINER", "invoice_postgres"),
        )
        config.assert_authorized_target()
        return config

    def assert_authorized_target(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(f"Refusing non-local PostgreSQL host: {self.host!r}")
        if self.port != 5433 or self.database != "invoicegather":
            raise RuntimeError(
                "Refusing unapproved PostgreSQL target; expected localhost:5433/invoicegather."
            )

    def engine(self) -> Engine:
        return create_engine(
            URL.create("postgresql+psycopg2", username=self.user, password=self.password,
                       host=self.host, port=self.port, database=self.database),
            pool_pre_ping=True,
        )


def verify_local_docker_target(config: PostgresConfig, engine: Engine) -> None:
    """Fail closed unless the endpoint is the expected running Docker container."""
    completed = subprocess.run(
        ["docker", "inspect", config.container], check=False, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Cannot inspect required Docker container {config.container!r}.")
    payload = json.loads(completed.stdout)[0]
    image = str(payload.get("Config", {}).get("Image", ""))
    running = bool(payload.get("State", {}).get("Running"))
    bindings = payload.get("HostConfig", {}).get("PortBindings", {}).get("5432/tcp") or []
    published_ports = {str(binding.get("HostPort", "")) for binding in bindings}
    container_env = dict(entry.split("=", 1) for entry in payload.get("Config", {}).get("Env", []) if "=" in entry)
    if not running or not image.startswith("postgres:") or str(config.port) not in published_ports:
        raise RuntimeError("PostgreSQL target is not the approved running local Docker container.")
    if container_env.get("POSTGRES_DB") != config.database or container_env.get("POSTGRES_USER") != config.user:
        raise RuntimeError("Configured database/user does not match the local Docker container.")
    with engine.connect() as connection:
        database, user = connection.execute(text("SELECT current_database(), current_user")).one()
    if database != config.database or user != config.user:
        raise RuntimeError("Connected PostgreSQL identity does not match the verified Docker target.")


def _text_cell(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value)
    return None if not result.strip() else result


def _decimal_cell(value: Any, *, label: str, precision: int, scale: int) -> Decimal | None:
    raw = _text_cell(value)
    if raw is None:
        return None
    try:
        result = Decimal(raw.strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal at {label}: {raw!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite decimal at {label}: {raw!r}")
    actual_scale = max(0, -result.as_tuple().exponent)
    integer_digits = max(0, len(result.as_tuple().digits) - actual_scale)
    if actual_scale > scale or integer_digits > precision - scale:
        raise ValueError(f"Decimal exceeds Numeric({precision}, {scale}) at {label}: {raw!r}")
    return result


def _integer_cell(value: Any, *, label: str) -> int | None:
    raw = _text_cell(value)
    if raw is None:
        return None
    try:
        decimal_value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid integer at {label}: {raw!r}") from exc
    if not decimal_value.is_finite() or decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"Invalid integer at {label}: {raw!r}")
    return int(decimal_value)


def build_records(spec: TableSpec, raw_rows: list[list[Any]]) -> list[dict[str, Any]]:
    if not raw_rows:
        raise ValueError(f"Required source tab is empty: {spec.tab}")
    actual_headers = tuple(str(value).strip() for value in raw_rows[0])
    if actual_headers != spec.headers:
        raise ValueError(f"Header mismatch for {spec.tab}: expected {spec.headers!r}, got {actual_headers!r}")
    table = spec.model.__table__
    records: list[dict[str, Any]] = []
    for row_number, source_row in enumerate(raw_rows[1:], start=2):
        values = list(source_row)
        if len(values) > len(spec.headers) and any(_text_cell(v) is not None for v in values[len(spec.headers):]):
            raise ValueError(f"Unexpected populated cells after schema in {spec.tab} row {row_number}")
        values = (values + [None] * len(spec.headers))[:len(spec.headers)]
        if not any(_text_cell(value) is not None for value in values):
            continue
        record: dict[str, Any] = {}
        for column_name, value in zip(spec.headers, values):
            column = table.c[column_name]
            label = f"{spec.tab}!{column_name} row {row_number}"
            if isinstance(column.type, Numeric):
                record[column_name] = _decimal_cell(value, label=label, precision=column.type.precision, scale=column.type.scale)
            elif isinstance(column.type, Integer):
                record[column_name] = _integer_cell(value, label=label)
            else:
                parsed = _text_cell(value)
                if isinstance(column.type, String) and column.type.length and parsed is not None and len(parsed) > column.type.length:
                    raise ValueError(f"Text exceeds {column.type.length} characters at {label}: {len(parsed)}")
                record[column_name] = parsed
        missing_keys = [column for column in spec.key_columns if record[column] is None]
        if missing_keys:
            raise ValueError(f"Blank key fields in {spec.tab} row {row_number}: {missing_keys}")
        records.append(record)
    keys = [tuple(record[column] for column in spec.key_columns) for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate business keys in source tab {spec.tab}")
    return records


def read_source_snapshot() -> dict[str, list[dict[str, Any]]]:
    settings = configured_uat2_data_settings()
    gateway = GoogleApiHistoricalInvoiceGateway(settings._credentials_source())
    tabs = gateway.read_tabs(settings.google_spreadsheet_id, [spec.tab for spec in TABLE_SPECS])
    return {spec.table_name: build_records(spec, tabs.get(spec.tab, [])) for spec in TABLE_SPECS}


def existing_table_counts(engine: Engine) -> dict[str, int]:
    existing = set(inspect(engine).get_table_names(schema="public"))
    with engine.connect() as connection:
        return {spec.table_name: (int(connection.execute(select(func.count()).select_from(spec.model.__table__)).scalar_one()) if spec.table_name in existing else 0) for spec in TABLE_SPECS}


def ensure_compatible_schema(connection: Connection) -> None:
    """Apply only additive constraints and safe text widening; never drop data."""
    connection.execute(text("ALTER TABLE invoice_orders ALTER COLUMN order_status TYPE VARCHAR(256)"))
    connection.execute(text("ALTER TABLE full_order_current_orders ALTER COLUMN order_status TYPE VARCHAR(256)"))
    connection.execute(text("ALTER TABLE full_order_current_items ALTER COLUMN resolve_sku TYPE TEXT"))
    requirements = {
        "statement_data": ("statement_batch_id", "record_type", "sequence_no"),
        "statement_financial_components": ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "component_name"),
        "statement_summary": ("statement_batch_id", "statement_source_sheet", "statement_source_row_number", "native_label"),
    }
    for table_name, columns in requirements.items():
        for column in columns:
            connection.execute(text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{column}" SET NOT NULL'))
    constraints = {
        "uq_statement_data_source_row": ("statement_data", '"statement_batch_id", "record_type", "sequence_no"'),
        "uq_sfc_source_component": ("statement_financial_components", '"statement_batch_id", "statement_source_sheet", "statement_source_row_number", "component_name"'),
        "uq_statement_summary_source_row": ("statement_summary", '"statement_batch_id", "statement_source_sheet", "statement_source_row_number", "native_label"'),
    }
    for name, (table_name, columns_sql) in constraints.items():
        exists = connection.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": name}).scalar_one_or_none()
        if exists is None:
            connection.execute(text(f'ALTER TABLE "{table_name}" ADD CONSTRAINT "{name}" UNIQUE ({columns_sql})'))


def _target_key_set(connection: Connection, spec: TableSpec) -> set[tuple[Any, ...]]:
    columns = [spec.model.__table__.c[name] for name in spec.key_columns]
    return {tuple(row) for row in connection.execute(select(*columns))}


def assert_no_target_only_rows(connection: Connection, spec: TableSpec, records: list[dict[str, Any]]) -> None:
    source_keys = {tuple(record[column] for column in spec.key_columns) for record in records}
    extras = _target_key_set(connection, spec) - source_keys
    if extras:
        raise RuntimeError(f"Refusing implicit deletion: {spec.table_name} has {len(extras)} target-only keys.")


def upsert_records(session: Session, spec: TableSpec, records: list[dict[str, Any]], *, chunk_size: int = 2000) -> int:
    table = spec.model.__table__
    affected = 0
    for offset in range(0, len(records), chunk_size):
        statement = postgres_insert(table).values(records[offset:offset + chunk_size])
        mutable = [name for name in spec.headers if name not in spec.key_columns]
        statement = statement.on_conflict_do_update(
            index_elements=[table.c[name] for name in spec.key_columns],
            set_={name: statement.excluded[name] for name in mutable},
            where=or_(*(table.c[name].is_distinct_from(statement.excluded[name]) for name in mutable)),
        )
        affected += int(session.execute(statement).rowcount or 0)
    return affected


def _representatives(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return records if len(records) <= 3 else [records[0], records[len(records) // 2], records[-1]]


def _resolve_sku_digest(rows: list[tuple[Any, ...]]) -> str:
    digest = sha256()
    for row in rows:
        digest.update(json.dumps(tuple(row), default=str, ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def verify_table(connection: Connection, spec: TableSpec, records: list[dict[str, Any]]) -> dict[str, Any]:
    table = spec.model.__table__
    target_count = int(connection.execute(select(func.count()).select_from(table)).scalar_one())
    if target_count != len(records):
        raise AssertionError(f"Count mismatch for {spec.table_name}: source={len(records)}, target={target_count}")
    target_keys = _target_key_set(connection, spec)
    source_keys = {tuple(record[column] for column in spec.key_columns) for record in records}
    if target_keys != source_keys or len(target_keys) != target_count:
        raise AssertionError(f"Key uniqueness/equality failed for {spec.table_name}")
    for record in _representatives(records):
        predicates = [table.c[name] == record[name] for name in spec.key_columns]
        target = connection.execute(select(*(table.c[name] for name in spec.headers)).where(*predicates)).mappings().one()
        for name in spec.headers:
            if target[name] != record[name]:
                raise AssertionError(f"Representative mismatch for {spec.table_name}.{name}: source={record[name]!r}, target={target[name]!r}")
    count_columns = [table.c[name] for name in spec.headers]
    db_non_null = connection.execute(select(*(func.count(column).label(column.name) for column in count_columns))).mappings().one()
    for name in spec.headers:
        if int(db_non_null[name]) != sum(record[name] is not None for record in records):
            raise AssertionError(f"NULL preservation failed for {spec.table_name}.{name}")
    numeric_columns = [column for column in count_columns if isinstance(column.type, Numeric)]
    if numeric_columns:
        aggregates = connection.execute(select(*(func.sum(column).label(column.name) for column in numeric_columns))).mappings().one()
        for column in numeric_columns:
            values = [record[column.name] for record in records if record[column.name] is not None]
            source_sum = sum(values, Decimal("0")) if values else None
            if aggregates[column.name] != source_sum:
                raise AssertionError(f"Financial aggregate mismatch for {spec.table_name}.{column.name}")
    resolve_sku_verified = False
    if "resolve_sku" in spec.headers:
        source_rows = sorted((*[record[name] for name in spec.key_columns], record["resolve_sku"]) for record in records)
        target_rows = [
            tuple(row)
            for row in connection.execute(
                select(*(table.c[name] for name in (*spec.key_columns, "resolve_sku"))).order_by(
                    *(table.c[name] for name in spec.key_columns)
                )
            )
        ]
        if _resolve_sku_digest(source_rows) != _resolve_sku_digest(target_rows):
            raise AssertionError("resolve_sku preservation failed")
        resolve_sku_verified = True
    return {"rows": target_count, "representative_rows": min(3, len(records)), "financial_columns": len(numeric_columns), "null_columns": len(spec.headers), "resolve_sku": resolve_sku_verified}


def main() -> None:
    started = time.monotonic()
    config = PostgresConfig.from_environment()
    engine = config.engine()
    verify_local_docker_target(config, engine)
    before = existing_table_counts(engine)
    print("EXISTING_COUNTS " + json.dumps(before, sort_keys=True))
    print("Reading and validating Google Sheets snapshot (read-only) ...")
    source = read_source_snapshot()
    print("SOURCE_COUNTS " + json.dumps({name: len(rows) for name, rows in source.items()}, sort_keys=True))
    with engine.begin() as schema_connection:
        Base.metadata.create_all(schema_connection)
        ensure_compatible_schema(schema_connection)
    affected: dict[str, int] = {}
    verification: dict[str, dict[str, Any]] = {}
    with Session(engine) as session:
        with session.begin():
            connection = session.connection()
            for spec in TABLE_SPECS:
                records = source[spec.table_name]
                assert_no_target_only_rows(connection, spec, records)
                affected[spec.table_name] = upsert_records(session, spec, records, chunk_size=1000 if spec.table_name == "statement_financial_components" else 2000)
                print(f"SYNC {spec.table_name}: affected={affected[spec.table_name]} rows={len(records)}")
            for spec in TABLE_SPECS:
                verification[spec.table_name] = verify_table(connection, spec, source[spec.table_name])
    print("AFFECTED " + json.dumps(affected, sort_keys=True))
    print("VERIFICATION " + json.dumps(verification, sort_keys=True))
    print(f"COMPLETE elapsed_seconds={time.monotonic() - started:.1f}")


if __name__ == "__main__":
    main()
