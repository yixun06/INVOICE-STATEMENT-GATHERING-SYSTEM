"""Persistence-neutral plan for one isolated Shopee Monthly Statement."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from ..parsers.shopee_weekly_statement_parser import (
    ParsedShopeeStatement,
    SettlementAdjustment,
    SettlementIncomeRow,
)
from .shopee_monthly_statement_service import StagedShopeeMonthlyStatement
from .shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitBlocked,
    build_statement_financial_component_rows,
    build_statement_summary_rows,
    serialize_statement_values,
)
from .uat2_persistence_schema import MONTHLY_STATEMENT_DATA_HEADERS


@dataclass(frozen=True)
class MonthlyStatementCommitPlan:
    statement_batch_id: str
    statement_file_hash: str
    statement_period_from: str
    statement_period_to: str
    rows: tuple[tuple[str, ...], ...]
    financial_component_rows: tuple[tuple[str, ...], ...]
    summary_rows: tuple[tuple[str, ...], ...]

    @property
    def order_row_count(self) -> int:
        return sum(row[1] == "ORDER" for row in self.rows)

    @property
    def sku_row_count(self) -> int:
        return sum(row[1] == "SKU" for row in self.rows)

    @property
    def adjustment_row_count(self) -> int:
        return sum(row[1] == "ADJUSTMENT" for row in self.rows)


def prepare_monthly_statement_commit_plan(
    stage: StagedShopeeMonthlyStatement,
    *,
    audit: StatementBatchAudit,
) -> MonthlyStatementCommitPlan:
    statement = stage.statement
    if statement is None or not stage.commit_ready:
        raise StatementCommitBlocked(
            "Monthly Statement is not ready for source-only commit."
        )
    _validate_audit(statement, audit)
    rows = tuple(
        [
            *(_income_row(statement, audit, row) for row in statement.order_rows),
            *(_income_row(statement, audit, row) for row in statement.sku_rows),
            *(
                _adjustment_row(statement, audit, adjustment)
                for adjustment in statement.adjustments
            ),
        ]
    )
    _validate_monthly_rows(rows)
    return MonthlyStatementCommitPlan(
        statement_batch_id=audit.statement_batch_id,
        statement_file_hash=statement.file_hash,
        statement_period_from=statement.statement_period_from.isoformat(),
        statement_period_to=statement.statement_period_to.isoformat(),
        rows=rows,
        financial_component_rows=build_statement_financial_component_rows(
            statement, audit
        ),
        summary_rows=build_statement_summary_rows(statement, audit),
    )


def _common(
    statement: ParsedShopeeStatement,
    audit: StatementBatchAudit,
    *,
    record_type: str,
    sequence_no: str,
    source_row_number: int,
) -> dict[str, object]:
    return {
        "statement_batch_id": audit.statement_batch_id,
        "record_type": record_type,
        "sequence_no": sequence_no,
        "platform": "Shopee",
        "statement_source_filename": statement.source_filename,
        "statement_file_hash": statement.file_hash,
        "statement_period_from": statement.statement_period_from,
        "statement_period_to": statement.statement_period_to,
        "statement_uploaded_at": audit.uploaded_at,
        "statement_uploaded_by": audit.uploaded_by,
        "statement_order_count": len(statement.order_rows),
        "statement_sku_count": len(statement.sku_rows),
        "statement_summary_total_released": statement.summary_total_released,
        "statement_adjustment_control_total": statement.adjustment_control_total,
        "validation_status": "PASSED",
        "commit_status": "COMMITTED",
        "statement_source_row_number": source_row_number,
        "committed_at": audit.committed_at,
    }


def _income_row(
    statement: ParsedShopeeStatement,
    audit: StatementBatchAudit,
    row: SettlementIncomeRow,
) -> tuple[str, ...]:
    record_type = {"Order": "ORDER", "Sku": "SKU"}.get(row.view_by)
    if record_type is None:
        raise StatementCommitBlocked(
            f"Monthly Income row {row.source_row_number} has unsupported View By."
        )
    product_price = row.financial_components.get("Product Price")
    refund_amount = row.financial_components.get("Refund Amount")
    if record_type == "SKU" and (product_price is None or refund_amount is None):
        raise StatementCommitBlocked(
            f"Monthly SKU source row {row.source_row_number} lacks Product Price or Refund Amount."
        )
    values = _common(
        statement,
        audit,
        record_type=record_type,
        sequence_no=row.sequence_no,
        source_row_number=row.source_row_number,
    )
    values.update(
        {
            "order_id": row.order_id,
            "order_creation_date": row.order_creation_date,
            "payout_completed_date": row.payout_completed_date,
            "release_channel": row.release_channel,
            "order_type": row.order_type,
            "total_released_amount": row.total_released_amount,
            "statement_product_id": row.product_id,
            "statement_product_name": row.product_name,
            "statement_product_price": product_price,
            "statement_refund_amount": refund_amount,
            "statement_net_selling_amount": (
                product_price + refund_amount
                if product_price is not None and refund_amount is not None
                else None
            ),
        }
    )
    return serialize_statement_values(values, MONTHLY_STATEMENT_DATA_HEADERS)


def _adjustment_row(
    statement: ParsedShopeeStatement,
    audit: StatementBatchAudit,
    adjustment: SettlementAdjustment,
) -> tuple[str, ...]:
    values = _common(
        statement,
        audit,
        record_type="ADJUSTMENT",
        sequence_no=adjustment.sequence_no,
        source_row_number=adjustment.source_row_number,
    )
    values.update(
        {
            "linked_order_id": adjustment.linked_order_id,
            "payout_completed_date": adjustment.payout_completed_date,
            "adjustment_complete_date": adjustment.adjustment_complete_date,
            "adjustment_type": adjustment.adjustment_type,
            "adjustment_reason": adjustment.adjustment_reason,
            "adjustment_amount": adjustment.adjustment_amount,
        }
    )
    return serialize_statement_values(values, MONTHLY_STATEMENT_DATA_HEADERS)


def _validate_audit(
    statement: ParsedShopeeStatement, audit: StatementBatchAudit
) -> None:
    if not audit.statement_batch_id.strip():
        raise StatementCommitBlocked("Monthly statement_batch_id must not be blank.")
    if not audit.uploaded_by.strip():
        raise StatementCommitBlocked("Monthly uploaded_by must not be blank.")
    if not statement.file_hash:
        raise StatementCommitBlocked("Monthly Statement file hash is missing.")


def _validate_monthly_rows(rows: Sequence[tuple[str, ...]]) -> None:
    positions = {
        header: index for index, header in enumerate(MONTHLY_STATEMENT_DATA_HEADERS)
    }
    identities: set[tuple[str, str, str]] = set()
    for row in rows:
        if len(row) != len(MONTHLY_STATEMENT_DATA_HEADERS):
            raise StatementCommitBlocked(
                "Monthly Statement rows do not match the exact 34-column schema."
            )
        identity = tuple(
            row[positions[header]]
            for header in ("statement_batch_id", "record_type", "sequence_no")
        )
        if not all(identity):
            raise StatementCommitBlocked(
                "Monthly Statement row has incomplete stable identity."
            )
        if identity in identities:
            raise StatementCommitBlocked(
                "Monthly Statement plan contains duplicate stable identity: "
                + "/".join(identity)
            )
        identities.add(identity)
