"""Review and commit orchestration for isolated Shopee Monthly Statements."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from ..parsers.shopee_weekly_statement_parser import ParsedShopeeStatement
from .google_sheets_monthly_statement_writer import (
    GoogleSheetsMonthlyStatementWriter,
    MonthlyStatementWriteResult,
)
from .shopee_monthly_statement_persistence import (
    MonthlyStatementCommitPlan,
    prepare_monthly_statement_commit_plan,
)
from .shopee_monthly_statement_service import (
    StagedShopeeMonthlyStatement,
    stage_parsed_shopee_monthly_statement,
    stage_shopee_monthly_statement,
)
from .shopee_statement_persistence import StatementBatchAudit, StatementCommitBlocked


@dataclass(frozen=True)
class MonthlyStatementReview:
    stage: StagedShopeeMonthlyStatement
    audit: StatementBatchAudit
    source_bytes: bytes

    @property
    def commit_ready(self) -> bool:
        return self.stage.commit_ready


def review_monthly_statement_upload(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str,
    batch_id: str,
    uploaded_by: str,
    writer: GoogleSheetsMonthlyStatementWriter,
) -> MonthlyStatementReview:
    source_bytes = _source_bytes(source)
    references = writer.committed_references()
    stage = stage_shopee_monthly_statement(
        source_bytes,
        source_filename=source_filename,
        existing_monthly_statements=references,
    )
    now = datetime.now(timezone.utc)
    return MonthlyStatementReview(
        stage=stage,
        audit=StatementBatchAudit(
            statement_batch_id=batch_id,
            uploaded_at=now,
            uploaded_by=uploaded_by,
            committed_at=now,
        ),
        source_bytes=source_bytes,
    )


def refresh_monthly_statement_review(
    review: MonthlyStatementReview,
    *,
    writer: GoogleSheetsMonthlyStatementWriter,
) -> MonthlyStatementReview:
    statement = review.stage.statement
    if statement is None:
        return review_monthly_statement_upload(
            review.source_bytes,
            source_filename=review.stage.source_filename,
            batch_id=review.audit.statement_batch_id,
            uploaded_by=review.audit.uploaded_by,
            writer=writer,
        )
    stage = stage_parsed_shopee_monthly_statement(
        statement,
        existing_monthly_statements=writer.committed_references(),
    )
    return replace(review, stage=stage)


def build_monthly_statement_plan(
    review: MonthlyStatementReview,
) -> MonthlyStatementCommitPlan:
    return prepare_monthly_statement_commit_plan(
        review.stage,
        audit=replace(review.audit, committed_at=datetime.now(timezone.utc)),
    )


def commit_monthly_statement_review(
    review: MonthlyStatementReview,
    *,
    writer: GoogleSheetsMonthlyStatementWriter,
) -> MonthlyStatementWriteResult:
    statement = review.stage.statement
    if statement is None:
        raise StatementCommitBlocked("Monthly Statement source is unavailable.")
    current_stage = stage_parsed_shopee_monthly_statement(
        statement,
        existing_monthly_statements=writer.committed_references(),
    )
    if not current_stage.commit_ready:
        raise StatementCommitBlocked(
            "Monthly Statement duplicate, revision, or validation state changed before commit."
        )
    current_review = replace(review, stage=current_stage)
    return writer.write_monthly_batch(build_monthly_statement_plan(current_review))


def _source_bytes(
    source: str | Path | bytes | bytearray | BinaryIO,
) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    data = source.read()
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("Monthly Statement source must provide bytes.")
    return bytes(data)
