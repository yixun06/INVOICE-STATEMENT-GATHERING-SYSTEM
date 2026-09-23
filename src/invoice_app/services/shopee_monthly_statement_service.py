"""Source-only staging for Shopee Monthly Statements.

Monthly Statements share the native Shopee parser and internal controls with
Weekly Statements.  They intentionally have no Invoice reconciliation,
product matching, enrichment, or operational Adjustment authority.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping

from ..parsers.shopee_weekly_statement_parser import (
    ParsedShopeeStatement,
    ShopeeStatementParseError,
    parse_shopee_statement,
)
from .shopee_weekly_statement_service import (
    ALREADY_IMPORTED,
    NEEDS_REVIEW,
    READY_TO_COMMIT,
    REJECTED,
    StatementSourceError,
    ValidationIssue,
    validate_shopee_weekly_statement,
)


@dataclass(frozen=True)
class MonthlyStatementReference:
    file_hash: str
    statement_period_from: date
    statement_period_to: date
    commit_status: str = "COMMITTED"


@dataclass(frozen=True)
class StagedShopeeMonthlyStatement:
    result: str
    source_filename: str
    file_hash: str
    statement: ParsedShopeeStatement | None
    validation_issues: tuple[ValidationIssue, ...]
    review_reasons: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    duplicate_status: str | None
    source_error: StatementSourceError | None = None

    @property
    def commit_ready(self) -> bool:
        return self.result == READY_TO_COMMIT and self.duplicate_status is None

    @property
    def already_imported(self) -> bool:
        return self.duplicate_status == "ALREADY_IMPORTED"


def stage_shopee_monthly_statement(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str | None = None,
    existing_monthly_statements: Iterable[
        MonthlyStatementReference | Mapping[str, Any]
    ] = (),
) -> StagedShopeeMonthlyStatement:
    try:
        statement = parse_shopee_statement(
            source,
            source_filename=source_filename,
            source_label="Shopee Monthly Statement",
        )
    except (ShopeeStatementParseError, OSError) as exc:
        source_error = StatementSourceError(
            code=getattr(exc, "code", "OTHER_SOURCE_CONTRACT_FAILURE"),
            technical_message=str(exc),
            source_filename=getattr(exc, "source_filename", source_filename or ""),
            sheet_name=getattr(exc, "sheet_name", None),
            expected=tuple(getattr(exc, "expected", ()) or ()),
            available_sheets=tuple(getattr(exc, "available_sheets", ()) or ()),
        )
        return StagedShopeeMonthlyStatement(
            result=REJECTED,
            source_filename=source_error.source_filename,
            file_hash=getattr(exc, "file_hash", ""),
            statement=None,
            validation_issues=(),
            review_reasons=(),
            rejection_reasons=(str(exc),),
            duplicate_status=None,
            source_error=source_error,
        )
    return stage_parsed_shopee_monthly_statement(
        statement,
        existing_monthly_statements=existing_monthly_statements,
    )


def stage_parsed_shopee_monthly_statement(
    statement: ParsedShopeeStatement,
    *,
    existing_monthly_statements: Iterable[
        MonthlyStatementReference | Mapping[str, Any]
    ] = (),
) -> StagedShopeeMonthlyStatement:
    references = tuple(
        reference
        for reference in existing_monthly_statements
        if _reference_value(reference, "commit_status") in (None, "COMMITTED")
    )
    if any(
        _reference_value(reference, "file_hash") == statement.file_hash
        for reference in references
    ):
        return StagedShopeeMonthlyStatement(
            result=ALREADY_IMPORTED,
            source_filename=statement.source_filename,
            file_hash=statement.file_hash,
            statement=statement,
            validation_issues=(),
            review_reasons=(),
            rejection_reasons=(),
            duplicate_status="ALREADY_IMPORTED",
        )

    if not is_full_calendar_month(
        statement.statement_period_from, statement.statement_period_to
    ):
        message = (
            "This Statement covers "
            f"{statement.statement_period_from:%d/%m/%Y}–"
            f"{statement.statement_period_to:%d/%m/%Y} and is not a full monthly "
            "Statement. Upload it using Shopee Weekly Statement."
        )
        return StagedShopeeMonthlyStatement(
            result=REJECTED,
            source_filename=statement.source_filename,
            file_hash=statement.file_hash,
            statement=statement,
            validation_issues=(),
            review_reasons=(),
            rejection_reasons=(message,),
            duplicate_status=None,
            source_error=StatementSourceError(
                code="WRONG_STATEMENT_GRANULARITY",
                technical_message=message,
                source_filename=statement.source_filename,
            ),
        )

    issues = validate_shopee_weekly_statement(statement)
    same_month = next(
        (
            reference
            for reference in references
            if _same_calendar_month(statement, reference)
        ),
        None,
    )
    if same_month is not None:
        month_label = statement.statement_period_from.strftime("%B %Y")
        return StagedShopeeMonthlyStatement(
            result=NEEDS_REVIEW,
            source_filename=statement.source_filename,
            file_hash=statement.file_hash,
            statement=statement,
            validation_issues=issues,
            review_reasons=(
                f"A different Monthly Statement is already committed for {month_label}.",
            ),
            rejection_reasons=(),
            duplicate_status="POSSIBLE_REVISION",
        )

    return StagedShopeeMonthlyStatement(
        result=NEEDS_REVIEW if issues else READY_TO_COMMIT,
        source_filename=statement.source_filename,
        file_hash=statement.file_hash,
        statement=statement,
        validation_issues=issues,
        review_reasons=(),
        rejection_reasons=(),
        duplicate_status=None,
    )


def is_full_calendar_month(period_from: date, period_to: date) -> bool:
    return (
        period_from.day == 1
        and period_from.year == period_to.year
        and period_from.month == period_to.month
        and period_to.day == monthrange(period_to.year, period_to.month)[1]
    )


def _same_calendar_month(
    statement: ParsedShopeeStatement,
    reference: MonthlyStatementReference | Mapping[str, Any],
) -> bool:
    period_from = _reference_value(reference, "statement_period_from")
    period_to = _reference_value(reference, "statement_period_to")
    return (
        period_from == statement.statement_period_from
        and period_to == statement.statement_period_to
    )


def _reference_value(
    reference: MonthlyStatementReference | Mapping[str, Any], key: str
) -> Any:
    if isinstance(reference, Mapping):
        return reference.get(key)
    return getattr(reference, key)
