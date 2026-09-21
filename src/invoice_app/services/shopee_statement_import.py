"""Application orchestration for the Data Import Statement workflow.

The Streamlit UI calls this boundary but does not reproduce reconciliation,
matching, persistence planning, or Google Sheets write rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
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
from src.invoice_app.domain.statement_reconciliation_v2 import (
    IdentityScope,
    SettlementBasis,
    StatementReconciliationBatch,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.google_sheets_statement_writer import (
    GoogleSheetsStatementWriter,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_statement_item_matching import (
    StatementItemMatchBatch,
    VerifiedArtifactRepair,
    match_statement_sku_rows,
    product_family_resolver_from_price_master,
)
from src.invoice_app.services.shopee_statement_reconciliation_v2 import (
    evaluate_statement_reconciliation,
)
from src.invoice_app.services.shopee_statement_persistence import (
    StatementBatchAudit,
    StatementCommitAttempt,
    StatementCommitBlocked,
    StatementCommitPlan,
    StatementWriteNotApplied,
    invoice_snapshot_sha256,
    prepare_v2_statement_commit_plan,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
    stage_parsed_shopee_weekly_statement,
    stage_shopee_weekly_statement,
)


@dataclass(frozen=True)
class StatementReviewEvidenceVersion:
    statement_file_hash: str
    invoice_snapshot_sha256: str
    product_family_snapshot_sha256: str
    rule_version: str
    reconciliation_sha256: str


@dataclass(frozen=True)
class StatementReviewCurrency:
    is_current: bool
    changed_evidence: tuple[str, ...]


@dataclass(frozen=True)
class StatementImportReview:
    batch_id: str
    uploaded_at: datetime
    uploaded_by: str
    stage: StagedShopeeWeeklyStatement
    reconciliation_v2: StatementReconciliationBatch | None
    evidence_version: StatementReviewEvidenceVersion | None
    # Persistence-only compatibility evidence. It is not review/UI authority.
    sku_matches: StatementItemMatchBatch
    plan: StatementCommitPlan | None
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]
    persistence_blockers: tuple[str, ...]
    source_bytes: bytes | None = None
    # Read-only review evidence for the Data Import presentation. This is not
    # persistence input and never authorizes a source correction or match.
    invoice_items: tuple[CanonicalInvoiceItem, ...] = ()

    @property
    def ready(self) -> bool:
        """Whether the V2 business review is complete and fail-closed."""

        return self.reconciliation_v2 is not None and not self.blockers

    @property
    def commit_ready(self) -> bool:
        """Whether V2 business evidence has a safe persistence plan."""

        return (
            self.ready
            and self.plan is not None
            and not self.persistence_blockers
        )


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
    source_bytes = _source_bytes(source)
    state = writer.reload_commit_state()
    stage = stage_shopee_weekly_statement(
        source_bytes,
        source_filename=source_filename,
        existing_orders=(asdict(order) for order in state.orders.values()),
        existing_statements=state.committed_statements,
    )
    return _build_review(
        stage,
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        invoice_orders=tuple(state.orders.values()),
        invoice_items=_state_statement_items(stage.statement, state.items),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
        source_bytes=source_bytes,
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

    if review.source_bytes is None:
        return review
    state = writer.reload_commit_state()
    stage = stage_shopee_weekly_statement(
        review.source_bytes,
        source_filename=review.stage.source_filename,
        existing_orders=(asdict(order) for order in state.orders.values()),
        existing_statements=state.committed_statements,
    )
    return _build_review(
        stage,
        batch_id=review.batch_id,
        uploaded_at=review.uploaded_at,
        uploaded_by=review.uploaded_by,
        invoice_orders=tuple(state.orders.values()),
        invoice_items=_state_statement_items(stage.statement, state.items),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
        source_bytes=review.source_bytes,
    )


def commit_statement_review(
    review: StatementImportReview,
    *,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    load_product_master: Callable[[], ProductPriceMaster],
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
) -> StatementCommitAttempt:
    """Under one lock, rerun V2 and commit only exact reviewed evidence."""

    if review.stage.already_imported:
        return StatementCommitAttempt(False, ("ALREADY_IMPORTED",))

    if not review.commit_ready or review.source_bytes is None:
        return StatementCommitAttempt(
            False,
            review.persistence_blockers
            or review.blockers
            or (
                "REVIEW_SOURCE_UNAVAILABLE"
                if review.source_bytes is None
                else "STATEMENT_NOT_READY",
            ),
        )
    repairs = tuple(verified_artifact_repairs)
    with commit_lock.acquire():
        state = writer.reload_commit_state()
        stage = stage_shopee_weekly_statement(
            review.source_bytes,
            source_filename=review.stage.source_filename,
            existing_orders=(asdict(order) for order in state.orders.values()),
            existing_statements=state.committed_statements,
        )
        statement = stage.statement
        if statement is None:
            return StatementCommitAttempt(False, ("STATEMENT_SOURCE_CHANGED",))
        if stage.duplicate_status is not None:
            return StatementCommitAttempt(False, (stage.duplicate_status,))
        if stage.rejection_reasons or stage.validation_issues:
            return StatementCommitAttempt(
                False,
                tuple(stage.rejection_reasons)
                + tuple(issue.message for issue in stage.validation_issues),
            )

        current_orders = _statement_orders(statement, state.orders.values())
        current_items = _state_statement_items(statement, state.items)
        current_reconciliation = evaluate_statement_reconciliation(
            statement,
            current_orders,
            current_items,
            product_families=product_family_resolver_from_price_master(
                load_product_master()
            ),
            verified_artifact_repairs=repairs,
        )
        current_version = _evidence_version(
            statement,
            current_orders,
            current_items,
            current_reconciliation,
        )
        changed = _changed_evidence(review.evidence_version, current_version)
        if changed:
            return StatementCommitAttempt(
                False,
                tuple(f"STALE_REVIEW:{reason}" for reason in changed),
            )
        blockers = _v2_blockers(
            current_reconciliation,
            {order.order_id for order in current_orders},
            {item.order_id for item in current_items},
        )
        if blockers:
            return StatementCommitAttempt(False, blockers)

        try:
            plan = prepare_v2_statement_commit_plan(
                statement,
                audit=StatementBatchAudit(
                    statement_batch_id=review.batch_id,
                    uploaded_at=review.uploaded_at,
                    uploaded_by=review.uploaded_by,
                    committed_at=datetime.now(timezone.utc),
                ),
                invoice_orders=current_orders,
                invoice_items=current_items,
                reconciliation=current_reconciliation,
                validation_passed=True,
            )
            writer.write_statement_batch(plan)
        except StatementWriteNotApplied:
            return StatementCommitAttempt(False, ("WRITE_NOT_APPLIED",))
        return StatementCommitAttempt(True, ())


def check_statement_review_currency(
    review: StatementImportReview,
    *,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    product_master: ProductPriceMaster,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
) -> StatementReviewCurrency:
    """Compare a stored V2 review with current Invoice and Product Master evidence.

    This is a read-only review guard. It does not alter the formal Statement
    commit preflight or persistence contract.
    """

    statement = review.stage.statement
    if statement is None or review.evidence_version is None:
        return StatementReviewCurrency(False, ("REVIEW_EVIDENCE_UNAVAILABLE",))
    state = writer.reload_commit_state()
    current_items = _state_statement_items(statement, state.items)
    current_orders = _statement_orders(statement, state.orders.values())
    current_reconciliation = evaluate_statement_reconciliation(
        statement,
        current_orders,
        current_items,
        product_families=product_family_resolver_from_price_master(product_master),
        verified_artifact_repairs=tuple(verified_artifact_repairs),
    )
    current = _evidence_version(
        statement,
        current_orders,
        current_items,
        current_reconciliation,
    )
    changed = _changed_evidence(review.evidence_version, current)
    return StatementReviewCurrency(not changed, changed)


def _build_review(
    stage: StagedShopeeWeeklyStatement,
    *,
    batch_id: str,
    uploaded_at: datetime,
    uploaded_by: str,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
    product_master: ProductPriceMaster,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair],
    now: Callable[[], datetime],
    source_bytes: bytes,
) -> StatementImportReview:
    statement = stage.statement
    empty_matches = StatementItemMatchBatch((), False)
    if statement is None:
        return StatementImportReview(
            batch_id=batch_id,
            uploaded_at=uploaded_at,
            uploaded_by=uploaded_by,
            stage=stage,
            reconciliation_v2=None,
            evidence_version=None,
            sku_matches=empty_matches,
            plan=None,
            blockers=tuple(stage.rejection_reasons) or ("Statement parsing failed.",),
            limitations=(),
            persistence_blockers=(
                tuple(stage.rejection_reasons) or ("Statement parsing failed.",)
            ),
            source_bytes=source_bytes,
        )

    if stage.already_imported:
        return StatementImportReview(
            batch_id=batch_id,
            uploaded_at=uploaded_at,
            uploaded_by=uploaded_by,
            stage=stage,
            reconciliation_v2=None,
            evidence_version=None,
            sku_matches=empty_matches,
            plan=None,
            blockers=(),
            limitations=(),
            persistence_blockers=(),
            source_bytes=source_bytes,
        )

    invoice_items = tuple(invoice_items)
    invoice_orders = _statement_orders(statement, invoice_orders)
    repairs = tuple(verified_artifact_repairs)
    product_families = product_family_resolver_from_price_master(product_master)
    reconciliation_v2 = evaluate_statement_reconciliation(
        statement,
        invoice_orders,
        invoice_items,
        product_families=product_families,
        verified_artifact_repairs=repairs,
    )
    sku_matches = match_statement_sku_rows(
        statement.sku_rows,
        invoice_items,
        product_families=product_families,
        verified_artifact_repairs=repairs,
    )
    persistence_blockers = list(stage.rejection_reasons)
    persistence_blockers.extend(
        issue.message for issue in stage.validation_issues
    )
    if stage.duplicate_status:
        persistence_blockers.extend(stage.review_reasons)
    blockers = list(stage.rejection_reasons)
    blockers.extend(issue.message for issue in stage.validation_issues)
    if stage.duplicate_status:
        blockers.extend(stage.review_reasons)
    blockers.extend(
        _v2_blockers(
            reconciliation_v2,
            {order.order_id for order in invoice_orders},
            {item.order_id for item in invoice_items},
        )
    )
    limitations = _v2_limitations(reconciliation_v2)
    plan = None
    if (
        not blockers
        and not persistence_blockers
        and stage.duplicate_status is None
    ):
        try:
            plan = prepare_v2_statement_commit_plan(
                statement,
                audit=StatementBatchAudit(
                    statement_batch_id=batch_id,
                    uploaded_at=uploaded_at,
                    uploaded_by=uploaded_by,
                    committed_at=now(),
                ),
                invoice_orders=invoice_orders,
                invoice_items=invoice_items,
                reconciliation=reconciliation_v2,
                validation_passed=True,
            )
        except StatementCommitBlocked as error:
            persistence_blockers.append(str(error))
    return StatementImportReview(
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        stage=stage,
        reconciliation_v2=reconciliation_v2,
        evidence_version=_evidence_version(
            statement,
            invoice_orders,
            invoice_items,
            reconciliation_v2,
        ),
        sku_matches=sku_matches,
        plan=plan,
        blockers=tuple(dict.fromkeys(blockers)),
        limitations=limitations,
        persistence_blockers=tuple(dict.fromkeys(persistence_blockers)),
        source_bytes=source_bytes,
        invoice_items=invoice_items,
    )


def _statement_items(
    statement: ParsedShopeeWeeklyStatement,
    repository: HistoricalInvoiceRepository,
) -> tuple[CanonicalInvoiceItem, ...]:
    order_ids = tuple(dict.fromkeys(row.order_id for row in statement.order_rows))
    by_order = repository.get_items_by_order_ids("Shopee", order_ids)
    return tuple(item for order_id in order_ids for item in by_order.get(order_id, ()))


def _state_statement_items(
    statement: ParsedShopeeWeeklyStatement | None,
    invoice_items: Iterable[CanonicalInvoiceItem],
) -> tuple[CanonicalInvoiceItem, ...]:
    if statement is None:
        return ()
    order_ids = {row.order_id for row in statement.order_rows}
    return tuple(
        item
        for item in invoice_items
        if item.platform == "Shopee" and item.order_id in order_ids
    )


def _statement_orders(
    statement: ParsedShopeeWeeklyStatement,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
) -> tuple[CanonicalInvoiceOrder, ...]:
    order_ids = {row.order_id for row in statement.order_rows}
    return tuple(
        order
        for order in invoice_orders
        if order.platform == "Shopee" and order.order_id in order_ids
    )


def _v2_blockers(
    batch: StatementReconciliationBatch,
    covered_order_ids: set[str],
    item_covered_order_ids: set[str],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not batch.coverage.valid:
        blockers.append(
            "Statement and Invoice product populations are not fully accounted for."
        )
    for result in batch.orders:
        order_id = result.evidence.order_id
        summary = result.summary
        if order_id not in covered_order_ids:
            blockers.append(
                f"{order_id}: no persisted Invoice order coverage is available."
            )
        elif order_id not in item_covered_order_ids:
            blockers.append(
                f"{order_id}: persisted Invoice order exists but has no "
                "Invoice_Items coverage."
            )
        if summary.identity_scope is IdentityScope.UNRESOLVED:
            blockers.append(
                f"{order_id}: product identity is unresolved from the available "
                "Statement, Invoice, and Product Master evidence."
            )
        if not summary.merchandise_reconciled:
            blockers.append(
                f"{order_id}: merchandise Product Price is not reconciled."
            )
        if summary.settlement_basis is SettlementBasis.NONE:
            blockers.append(
                f"{order_id}: final seller settlement has an unexplained difference "
                "or insufficient source evidence."
            )
    return tuple(dict.fromkeys(blockers))


def _v2_limitations(batch: StatementReconciliationBatch) -> tuple[str, ...]:
    group_orders = sum(
        result.summary.identity_scope is IdentityScope.GROUP
        for result in batch.orders
    )
    limitations: list[str] = []
    if group_orders:
        limitations.append(
            f"{group_orders} order(s) reconcile at product-group scope; individual "
            "Statement row allocation cannot be proven from source evidence."
        )
    if any(
        not result.evidence.quantity.statement_quantity_available
        for result in batch.orders
    ):
        limitations.append(
            "Statement quantity is not provided by the source; no quantity-match "
            "claim is made."
        )
    return tuple(limitations)


def _evidence_version(
    statement: ParsedShopeeWeeklyStatement,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
    reconciliation: StatementReconciliationBatch,
) -> StatementReviewEvidenceVersion:
    invoice_hash = invoice_snapshot_sha256(invoice_orders, invoice_items)
    return StatementReviewEvidenceVersion(
        statement_file_hash=statement.file_hash,
        invoice_snapshot_sha256=invoice_hash,
        product_family_snapshot_sha256=(
            reconciliation.product_family_snapshot.sha256
        ),
        rule_version=reconciliation.rule_version,
        reconciliation_sha256=_stable_hash(asdict(reconciliation)),
    )


def _changed_evidence(
    previous: StatementReviewEvidenceVersion | None,
    current: StatementReviewEvidenceVersion,
) -> tuple[str, ...]:
    if previous is None:
        return ("REVIEW_EVIDENCE_UNAVAILABLE",)
    changed: list[str] = []
    if current.statement_file_hash != previous.statement_file_hash:
        changed.append("STATEMENT_FILE_CHANGED")
    if current.invoice_snapshot_sha256 != previous.invoice_snapshot_sha256:
        changed.append("INVOICE_SNAPSHOT_CHANGED")
    if (
        current.product_family_snapshot_sha256
        != previous.product_family_snapshot_sha256
    ):
        changed.append("PRODUCT_MASTER_SNAPSHOT_CHANGED")
    if current.rule_version != previous.rule_version:
        changed.append("RECONCILIATION_RULE_CHANGED")
    if current.reconciliation_sha256 != previous.reconciliation_sha256:
        changed.append("RECONCILIATION_RESULT_CHANGED")
    return tuple(dict.fromkeys(changed))


def _source_bytes(source: str | Path | bytes | bytearray | BinaryIO) -> bytes:
    """Retain the exact uploaded Statement bytes for commit-time reparse."""

    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "getvalue"):
        return bytes(source.getvalue())
    position = source.tell() if hasattr(source, "tell") else None
    data = source.read()
    if position is not None and hasattr(source, "seek"):
        source.seek(position)
    return bytes(data)


def _stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()
