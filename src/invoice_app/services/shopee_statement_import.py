"""Application orchestration for the Data Import Statement workflow.

The Streamlit UI calls this boundary but does not reproduce reconciliation,
matching, persistence planning, or Google Sheets write rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    HistoricalInvoiceRepository,
)
from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
    write_google_statement_plan_if_current,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_statement_item_matching import (
    StatementItemMatchBatch,
    VerifiedArtifactRepair,
    match_statement_sku_rows,
    product_family_resolver_from_price_master,
)
from src.invoice_app.services.shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitAttempt,
    StatementCommitBlocked,
    StatementCommitPlan,
    prepare_statement_commit_plan,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
    stage_parsed_shopee_weekly_statement,
    stage_shopee_weekly_statement,
)


@dataclass(frozen=True)
class StatementImportReview:
    batch_id: str
    uploaded_at: datetime
    uploaded_by: str
    stage: StagedShopeeWeeklyStatement
    sku_matches: StatementItemMatchBatch
    plan: StatementCommitPlan | None
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.plan is not None and not self.blockers


def review_statement_upload(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str,
    batch_id: str,
    uploaded_by: str,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    product_master: ProductPriceMaster,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> StatementImportReview:
    """Parse and review one upload against fresh authoritative UAT2 state."""

    uploaded_at = now()
    state = writer.reload_commit_state()
    stage = stage_shopee_weekly_statement(
        source,
        source_filename=source_filename,
        existing_orders=(asdict(order) for order in state.orders.values()),
        existing_statements=state.committed_statements,
    )
    return _build_review(
        stage,
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        repository=repository,
        invoice_orders=tuple(state.orders.values()),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
    )


def refresh_statement_review(
    review: StatementImportReview,
    *,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    product_master: ProductPriceMaster,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> StatementImportReview:
    """Reconcile the parsed source again after authoritative state changes."""

    statement = review.stage.statement
    if statement is None:
        return review
    state = writer.reload_commit_state()
    stage = stage_parsed_shopee_weekly_statement(
        statement,
        existing_orders=(asdict(order) for order in state.orders.values()),
        existing_statements=state.committed_statements,
    )
    return _build_review(
        stage,
        batch_id=review.batch_id,
        uploaded_at=review.uploaded_at,
        uploaded_by=review.uploaded_by,
        repository=repository,
        invoice_orders=tuple(state.orders.values()),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
    )


def commit_statement_review(
    review: StatementImportReview,
    *,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    load_product_master: Callable[[], ProductPriceMaster],
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
) -> StatementCommitAttempt:
    """Commit through the existing lock/preflight writer boundary only."""

    if review.plan is None:
        return StatementCommitAttempt(False, review.blockers or ("STATEMENT_NOT_READY",))
    repairs = tuple(verified_artifact_repairs)

    def sku_matching_is_current() -> bool:
        statement = review.stage.statement
        if statement is None:
            return False
        repository.refresh()
        current_items = _statement_items(statement, repository)
        current_matches = match_statement_sku_rows(
            statement.sku_rows,
            current_items,
            product_families=product_family_resolver_from_price_master(
                load_product_master()
            ),
            verified_artifact_repairs=repairs,
        )
        return current_matches == review.sku_matches

    return write_google_statement_plan_if_current(
        review.plan,
        writer=writer,
        sku_matching_is_current=sku_matching_is_current,
    )


def _build_review(
    stage: StagedShopeeWeeklyStatement,
    *,
    batch_id: str,
    uploaded_at: datetime,
    uploaded_by: str,
    repository: HistoricalInvoiceRepository,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    product_master: ProductPriceMaster,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair],
    now: Callable[[], datetime],
) -> StatementImportReview:
    statement = stage.statement
    empty_matches = StatementItemMatchBatch((), False)
    if statement is None:
        return StatementImportReview(
            batch_id, uploaded_at, uploaded_by, stage, empty_matches, None,
            tuple(stage.rejection_reasons) or ("Statement parsing failed.",),
        )

    repository.refresh()
    invoice_items = _statement_items(statement, repository)
    sku_matches = match_statement_sku_rows(
        statement.sku_rows,
        invoice_items,
        product_families=product_family_resolver_from_price_master(product_master),
        verified_artifact_repairs=verified_artifact_repairs,
    )
    blockers = list(stage.rejection_reasons)
    blockers.extend(issue.message for issue in stage.validation_issues)
    blockers.extend(stage.review_reasons)
    blockers.extend(
        match.reason for match in sku_matches.matches if match.status.value == "NEEDS_REVIEW"
    )
    plan = None
    if not blockers and stage.eligible_for_future_atomic_commit and sku_matches.eligible_for_commit:
        try:
            plan = prepare_statement_commit_plan(
                statement,
                audit=StatementBatchAudit(
                    statement_batch_id=batch_id,
                    uploaded_at=uploaded_at,
                    uploaded_by=uploaded_by,
                    committed_at=now(),
                ),
                invoice_orders=invoice_orders,
                sku_matches=sku_matches,
                validation_passed=True,
            )
        except StatementCommitBlocked as error:
            blockers.append(str(error))
    return StatementImportReview(
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        stage=stage,
        sku_matches=sku_matches,
        plan=plan,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def _statement_items(
    statement: ParsedShopeeWeeklyStatement,
    repository: HistoricalInvoiceRepository,
) -> tuple[CanonicalInvoiceItem, ...]:
    order_ids = tuple(dict.fromkeys(row.order_id for row in statement.order_rows))
    by_order = repository.get_items_by_order_ids("Shopee", order_ids)
    return tuple(item for order_id in order_ids for item in by_order.get(order_id, ()))
