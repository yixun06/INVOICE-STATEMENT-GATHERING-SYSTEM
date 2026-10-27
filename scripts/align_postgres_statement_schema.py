"""Widen only MY Statement PostgreSQL varchar columns to the ORM contract.

This migration is intentionally additive/non-destructive: it refuses a target
that would narrow a physical column and verifies every Statement table row count
before and after the transaction.  It never touches business rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, select, text, func
from sqlalchemy.engine import Connection, Engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.invoice_app.pg_orm.pg_models import (
    OrderAdjustment,
    StatementData,
    StatementFinancialComponent,
    StatementSummary,
)
from src.invoice_app.pg_orm.runtime import PostgresRuntimeConfig


_MODELS = (
    StatementData,
    StatementFinancialComponent,
    StatementSummary,
    OrderAdjustment,
)


def align_statement_schema(engine: Engine) -> dict[str, int]:
    """Apply only safe widening and return verified unchanged row counts."""

    before = _counts(engine)
    with engine.begin() as connection:
        for model in _MODELS:
            _widen_to_model_contract(connection, model)
    after = _counts(engine)
    if before != after:
        raise RuntimeError(
            "Statement schema alignment changed row counts; transaction must be investigated."
        )
    return after


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            model.__table__.name: int(
                connection.execute(
                    select(func.count()).select_from(model.__table__)
                ).scalar_one()
            )
            for model in _MODELS
        }


def _widen_to_model_contract(connection: Connection, model: type[object]) -> None:
    table = model.__table__
    actual = {
        column["name"]: column
        for column in inspect(connection).get_columns(table.name)
    }
    for column in table.columns:
        if column.name not in actual:
            raise RuntimeError(f"PostgreSQL Statement table {table.name} lacks {column.name}.")
        desired_length = getattr(column.type, "length", None)
        actual_type = actual[column.name]["type"]
        actual_length = getattr(actual_type, "length", None)
        if desired_length is None:
            continue
        if actual_length is None:
            # TEXT is already less restrictive than varchar; another type is unsafe.
            if actual_type.__class__.__name__.upper() == "TEXT":
                continue
            raise RuntimeError(
                f"PostgreSQL Statement column {table.name}.{column.name} is not a compatible text type."
            )
        if actual_length > desired_length:
            raise RuntimeError(
                f"Refusing to narrow {table.name}.{column.name} from {actual_length} to {desired_length}."
            )
        if actual_length < desired_length:
            connection.execute(
                text(
                    f'ALTER TABLE "{table.name}" ALTER COLUMN "{column.name}" '
                    f"TYPE VARCHAR({desired_length})"
                )
            )


def main() -> None:
    config = PostgresRuntimeConfig.from_environment()
    counts = align_statement_schema(config.engine())
    print("STATEMENT_SCHEMA_ALIGNED " + ", ".join(
        f"{name}={count}" for name, count in sorted(counts.items())
    ))


if __name__ == "__main__":
    main()
