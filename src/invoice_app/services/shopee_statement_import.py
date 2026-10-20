"""Application orchestration for the Data Import Statement workflow.

The Streamlit UI calls this boundary but does not reproduce reconciliation,
matching, persistence planning, or Google Sheets write rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    HistoricalInvoiceRepository,
)
from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.shopee_full_order_reconciliation import (
    FULL_ORDER_STATEMENT_PROOF_RULE_VERSION,
    prove_full_order_current_against_statement,
)
from src.invoice_app.domain.order_adjustment import CanonicalOrderAdjustment
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
from src.invoice_app.services.market_context import resolve_market_context
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


INVOICE_COVERAGE_INCOMPLETE = "INVOICE_COVERAGE_INCOMPLETE"
ADDITIONAL_EVIDENCE_REQUIRED = "ADDITIONAL_EVIDENCE_REQUIRED"


@dataclass(frozen=True)
class StatementInvoiceCoverage:
    """Fresh persisted Invoice coverage for normal Statement orders only."""

    statement_order_ids: tuple[str, ...]
    invoice_order_ids: tuple[str, ...]
    missing_invoice_order_ids: tuple[str, ...]
    missing_invoice_item_order_ids: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not (
            self.missing_invoice_order_ids
            or self.missing_invoice_item_order_ids
        )


@dataclass(frozen=True)
class StatementEvidenceRequirement:
    """One Statement order that remains outside safe canonical evidence."""

    order_id: str
    order_status: str | None
    reason: str


@dataclass(frozen=True)
class StatementEvidenceCoverage:
    """Statement coverage after canonical and strict Full Order evidence."""

    statement_order_ids: tuple[str, ...]
    already_verified_order_ids: tuple[str, ...]
    ready_from_full_order_order_ids: tuple[str, ...]
    additional_evidence: tuple[StatementEvidenceRequirement, ...]

    @property
    def complete(self) -> bool:
        return not self.additional_evidence


@dataclass(frozen=True)
class StatementReviewEvidenceVersion:
    statement_file_hash: str
    invoice_snapshot_sha256: str
    product_family_snapshot_sha256: str
    order_adjustment_snapshot_sha256: str
    rule_version: str
    full_order_proof_rule_version: str
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
    invoice_coverage: StatementInvoiceCoverage | None = None
    evidence_coverage: StatementEvidenceCoverage | None = None
    full_order_canonical_inserts: tuple[InvoiceBundle, ...] = ()

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
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
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
    invoice_orders, invoice_items, canonical_inserts, full_order_blockers, evidence_coverage = (
        _statement_inputs_with_full_order(
            stage.statement,
            tuple(state.orders.values()),
            _state_statement_items(stage.statement, state.items),
            product_master=product_master,
            full_order_repository=full_order_repository,
            imported_at=uploaded_at,
        )
    )
    return _build_review(
        stage,
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        invoice_orders=invoice_orders,
        invoice_items=invoice_items,
        canonical_adjustments=_cross_period_order_adjustments(
            stage.statement, state.order_adjustments
        ),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
        source_bytes=source_bytes,
        canonical_inserts=canonical_inserts,
        full_order_blockers=full_order_blockers,
        evidence_coverage=evidence_coverage,
    )


def refresh_statement_review(
    review: StatementImportReview,
    *,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    product_master: ProductPriceMaster,
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
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
    invoice_orders, invoice_items, canonical_inserts, full_order_blockers, evidence_coverage = (
        _statement_inputs_with_full_order(
            stage.statement,
            tuple(state.orders.values()),
            _state_statement_items(stage.statement, state.items),
            product_master=product_master,
            full_order_repository=full_order_repository,
            imported_at=review.uploaded_at,
        )
    )
    return _build_review(
        stage,
        batch_id=review.batch_id,
        uploaded_at=review.uploaded_at,
        uploaded_by=review.uploaded_by,
        invoice_orders=invoice_orders,
        invoice_items=invoice_items,
        canonical_adjustments=_cross_period_order_adjustments(
            stage.statement, state.order_adjustments
        ),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
        source_bytes=review.source_bytes,
        canonical_inserts=canonical_inserts,
        full_order_blockers=full_order_blockers,
        evidence_coverage=evidence_coverage,
    )


def commit_statement_review(
    review: StatementImportReview,
    *,
    repository: HistoricalInvoiceRepository,
    writer: GoogleSheetsStatementWriter,
    load_product_master: Callable[[], ProductPriceMaster],
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
) -> StatementCommitAttempt:
    """Under one lock, rerun V2 and commit only exact reviewed evidence."""

    if review.stage.already_imported:
        return StatementCommitAttempt(False, ("ALREADY_IMPORTED",))

    if review.evidence_coverage is not None and not review.evidence_coverage.complete:
        return StatementCommitAttempt(False, (ADDITIONAL_EVIDENCE_REQUIRED,))

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

        current_master = load_product_master()
        current_orders, current_items, canonical_inserts, full_order_blockers, current_evidence_coverage = (
            _statement_inputs_with_full_order(
                statement,
                tuple(state.orders.values()),
                _state_statement_items(statement, state.items),
                product_master=current_master,
                full_order_repository=full_order_repository,
                imported_at=review.uploaded_at,
            )
        )
        current_orders = _statement_orders(statement, current_orders)
        current_coverage = classify_statement_invoice_coverage(
            statement,
            current_orders,
            current_items,
        )
        if not current_evidence_coverage.complete:
            return StatementCommitAttempt(False, (ADDITIONAL_EVIDENCE_REQUIRED,))
        if full_order_blockers:
            return StatementCommitAttempt(False, full_order_blockers)
        current_adjustments = _cross_period_order_adjustments(
            statement, state.order_adjustments
        )
        current_reconciliation = evaluate_statement_reconciliation(
            statement,
            current_orders,
            current_items,
            product_families=product_family_resolver_from_price_master(
                current_master
            ),
            verified_artifact_repairs=repairs,
            canonical_adjustments=current_adjustments,
        )
        current_version = _evidence_version(
            statement,
            current_orders,
            current_items,
            current_reconciliation,
            current_adjustments,
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
                canonical_inserts=canonical_inserts,
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
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
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
    current_orders, current_items, _inserts, full_order_blockers, _evidence_coverage = (
        _statement_inputs_with_full_order(
            statement,
            tuple(state.orders.values()),
            _state_statement_items(statement, state.items),
            product_master=product_master,
            full_order_repository=full_order_repository,
            imported_at=review.uploaded_at,
        )
    )
    if full_order_blockers:
        return StatementReviewCurrency(False, full_order_blockers)
    current_orders = _statement_orders(statement, current_orders)
    current_adjustments = _cross_period_order_adjustments(
        statement, state.order_adjustments
    )
    current_reconciliation = evaluate_statement_reconciliation(
        statement,
        current_orders,
        current_items,
        product_families=product_family_resolver_from_price_master(product_master),
        verified_artifact_repairs=tuple(verified_artifact_repairs),
        canonical_adjustments=current_adjustments,
    )
    current = _evidence_version(
        statement,
        current_orders,
        current_items,
        current_reconciliation,
        current_adjustments,
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
    canonical_adjustments: Iterable[CanonicalOrderAdjustment],
    product_master: ProductPriceMaster,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair],
    now: Callable[[], datetime],
    source_bytes: bytes,
    canonical_inserts: Iterable[InvoiceBundle] = (),
    full_order_blockers: Iterable[str] = (),
    evidence_coverage: StatementEvidenceCoverage | None = None,
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

    canonical_inserts = tuple(canonical_inserts)
    full_order_blockers = tuple(full_order_blockers)
    invoice_items = tuple(invoice_items)
    canonical_adjustments = tuple(canonical_adjustments)
    invoice_orders = _statement_orders(statement, invoice_orders)
    invoice_coverage = classify_statement_invoice_coverage(
        statement,
        invoice_orders,
        invoice_items,
    )
    if (
        evidence_coverage is not None
        and not evidence_coverage.complete
        and not stage.rejection_reasons
        and not stage.validation_issues
        and stage.duplicate_status is None
    ):
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
            persistence_blockers=tuple(
                dict.fromkeys((*full_order_blockers, ADDITIONAL_EVIDENCE_REQUIRED))
            ),
            source_bytes=source_bytes,
            invoice_items=invoice_items,
            invoice_coverage=invoice_coverage,
            evidence_coverage=evidence_coverage,
            full_order_canonical_inserts=canonical_inserts,
        )
    repairs = tuple(verified_artifact_repairs)
    product_families = product_family_resolver_from_price_master(product_master)
    reconciliation_v2 = evaluate_statement_reconciliation(
        statement,
        invoice_orders,
        invoice_items,
        product_families=product_families,
        verified_artifact_repairs=repairs,
        canonical_adjustments=canonical_adjustments,
    )
    sku_matches = match_statement_sku_rows(
        statement.sku_rows,
        invoice_items,
        product_families=product_families,
        verified_artifact_repairs=repairs,
    )
    persistence_blockers = list(stage.rejection_reasons)
    persistence_blockers.extend(full_order_blockers)
    persistence_blockers.extend(
        issue.message for issue in stage.validation_issues
    )
    if stage.duplicate_status:
        persistence_blockers.extend(stage.review_reasons)
    blockers = list(stage.rejection_reasons)
    blockers.extend(full_order_blockers)
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
                canonical_inserts=canonical_inserts,
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
            canonical_adjustments,
        ),
        sku_matches=sku_matches,
        plan=plan,
        blockers=tuple(dict.fromkeys(blockers)),
        limitations=limitations,
        persistence_blockers=tuple(dict.fromkeys(persistence_blockers)),
        source_bytes=source_bytes,
        invoice_items=invoice_items,
        invoice_coverage=invoice_coverage,
        evidence_coverage=evidence_coverage,
        full_order_canonical_inserts=canonical_inserts,
    )


def classify_statement_invoice_coverage(
    statement: ParsedShopeeWeeklyStatement,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
) -> StatementInvoiceCoverage:
    """Classify normal-order coverage without using reconciliation outcomes.

    Adjustment linked order IDs are intentionally absent: an adjustment is
    independent evidence unless its order also appears in ``order_rows``.
    """

    statement_order_ids = tuple(
        dict.fromkeys(row.order_id for row in statement.order_rows if row.order_id)
    )
    required = set(statement_order_ids)
    persisted_orders = {
        order.order_id
        for order in invoice_orders
        if order.platform == "Shopee" and order.order_id in required
    }
    persisted_items = {
        item.order_id
        for item in invoice_items
        if item.platform == "Shopee" and item.order_id in required
    }
    return StatementInvoiceCoverage(
        statement_order_ids=statement_order_ids,
        invoice_order_ids=tuple(
            order_id for order_id in statement_order_ids if order_id in persisted_orders
        ),
        missing_invoice_order_ids=tuple(
            order_id for order_id in statement_order_ids if order_id not in persisted_orders
        ),
        missing_invoice_item_order_ids=tuple(
            order_id
            for order_id in statement_order_ids
            if order_id in persisted_orders and order_id not in persisted_items
        ),
    )


def _statement_inputs_with_full_order(
    statement: ParsedShopeeWeeklyStatement | None,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
    *,
    product_master: ProductPriceMaster,
    full_order_repository: ShopeeFullOrderCurrentRepository | None,
    imported_at: datetime | None = None,
) -> tuple[
    tuple[CanonicalInvoiceOrder, ...],
    tuple[CanonicalInvoiceItem, ...],
    tuple[InvoiceBundle, ...],
    tuple[str, ...],
    StatementEvidenceCoverage | None,
]:
    orders = tuple(invoice_orders)
    items = tuple(invoice_items)
    if statement is None:
        return orders, items, (), (), None
    statement_order_ids = tuple(
        dict.fromkeys(row.order_id for row in statement.order_rows if row.order_id)
    )
    required = set(statement_order_ids)
    canonical_orders = {
        order.order_id: order
        for order in orders
        if order.platform == "Shopee" and order.order_id in required
    }
    canonical_item_ids = {
        item.order_id
        for item in items
        if item.platform == "Shopee" and item.order_id in required
    }
    already_verified = tuple(
        order_id
        for order_id in statement_order_ids
        if order_id in canonical_orders and order_id in canonical_item_ids
    )
    pending = tuple(
        order_id for order_id in statement_order_ids if order_id not in already_verified
    )
    if not pending:
        return (
            orders,
            items,
            (),
            (),
            StatementEvidenceCoverage(statement_order_ids, already_verified, (), ()),
        )
    if full_order_repository is None:
        requirements = tuple(
            StatementEvidenceRequirement(
                order_id,
                canonical_orders.get(order_id).order_status
                if order_id in canonical_orders
                else None,
                (
                    "Canonical Invoice items are incomplete."
                    if order_id in canonical_orders
                    else "Full Order Current evidence is unavailable."
                ),
            )
            for order_id in pending
        )
        return (
            orders,
            items,
            (),
            (),
            StatementEvidenceCoverage(statement_order_ids, already_verified, (), requirements),
        )
    try:
        snapshot = full_order_repository.read_snapshot()
    except Exception as error:
        requirements = tuple(
            StatementEvidenceRequirement(
                order_id,
                canonical_orders.get(order_id).order_status
                if order_id in canonical_orders
                else None,
                "Full Order Current evidence is unavailable.",
            )
            for order_id in pending
        )
        return (
            orders,
            items,
            (),
            (f"FULL_ORDER_CURRENT_UNAVAILABLE:{error}",),
            StatementEvidenceCoverage(statement_order_ids, already_verified, (), requirements),
        )
    current_orders = {
        order.order_id: order
        for order in snapshot.orders
        if resolve_market_context(order.market).persisted_platform == "Shopee"
    }
    current_items = snapshot.items_by_identity()
    statement_order_rows = {row.order_id: row for row in statement.order_rows}
    inserts: list[InvoiceBundle] = []
    blockers: list[str] = []
    ready_from_full_order: list[str] = []
    requirements: list[StatementEvidenceRequirement] = []
    strict_candidates: list[str] = []
    for order_id in pending:
        current = current_orders.get(order_id)
        order_items = (
            current_items.get((current.market, order_id), ()) if current is not None else ()
        )
        statement_row = statement_order_rows[order_id]
        if current is None or not order_items:
            reason = f"{order_id}: Full Order current snapshot is missing."
        elif current.order_status.casefold() in {"cancelled", "canceled"}:
            reason = f"{order_id}: cancelled order remains on the PDF exception path."
        elif any(
            statement_row.financial_components.get(name) is None
            for name in INCOME_COMPONENT_COLUMNS
        ):
            reason = f"{order_id}: Statement financial bridge is incomplete."
        else:
            strict_candidates.append(order_id)
            continue
        blockers.append(reason)
        requirements.append(
            StatementEvidenceRequirement(
                order_id,
                current.order_status if current is not None else None,
                _full_order_evidence_reason((reason,)),
            )
        )
    if strict_candidates:
        batch_proof = prove_full_order_current_against_statement(
            _statement_subset_for_orders(statement, tuple(strict_candidates)),
            snapshot,
            product_master=product_master,
            imported_at=imported_at,
        )
        if batch_proof.ready:
            inserts.extend(batch_proof.bundles)
            ready_from_full_order.extend(strict_candidates)
        else:
            for order_id in strict_candidates:
                proof = prove_full_order_current_against_statement(
                    _statement_subset_for_order(statement, order_id),
                    snapshot,
                    product_master=product_master,
                    imported_at=imported_at,
                )
                if proof.ready:
                    inserts.extend(proof.bundles)
                    ready_from_full_order.append(order_id)
                    continue
                proof_blockers = proof.blockers or (INVOICE_COVERAGE_INCOMPLETE,)
                blockers.extend(proof_blockers)
                requirements.append(
                    StatementEvidenceRequirement(
                        order_id,
                        current_orders.get(order_id).order_status
                        if order_id in current_orders
                        else None,
                        _full_order_evidence_reason(proof_blockers),
                    )
                )
    return (
        orders + tuple(bundle.order for bundle in inserts),
        items + tuple(item for bundle in inserts for item in bundle.items),
        tuple(inserts),
        tuple(dict.fromkeys(blockers)),
        StatementEvidenceCoverage(
            statement_order_ids,
            already_verified,
            tuple(ready_from_full_order),
            tuple(requirements),
        ),
    )


def _statement_subset_for_order(
    statement: ParsedShopeeWeeklyStatement,
    order_id: str,
) -> ParsedShopeeWeeklyStatement:
    """Keep exact Statement evidence for one Current-only order proof."""

    return _statement_subset_for_orders(statement, (order_id,))


def _statement_subset_for_orders(
    statement: ParsedShopeeWeeklyStatement,
    order_ids: tuple[str, ...],
) -> ParsedShopeeWeeklyStatement:
    """Keep exact Statement evidence for one or more Current-only order proofs."""

    selected = set(order_ids)
    income_rows = tuple(row for row in statement.income_rows if row.order_id in selected)
    order_rows = tuple(row for row in income_rows if row.view_by == "Order")
    return replace(
        statement,
        summary_total_released=sum(
            (
                row.total_released_amount
                for row in order_rows
                if row.total_released_amount is not None
            ),
            start=Decimal("0"),
        ),
        adjustment_control_total=Decimal("0"),
        adjustment_footer_total=None,
        income_rows=income_rows,
        service_fee_details=tuple(
            row for row in statement.service_fee_details if row.order_id in selected
        ),
        shipping_fee_discrepancies=tuple(
            row
            for row in statement.shipping_fee_discrepancies
            if row.order_id in selected
        ),
        adjustments=(),
    )


def _full_order_evidence_reason(blockers: Iterable[str]) -> str:
    text = " ".join(blockers).casefold()
    if "return/refund marker" in text:
        return "Return/refund request detected."
    if "cancelled order" in text:
        return "Cancelled order requires existing exception evidence."
    if "snapshot is missing" in text:
        return "Full Order Current evidence is missing."
    if "product master identity" in text:
        return "Product Master identity requires additional evidence."
    if "financial bridge" in text:
        return "Statement financial evidence is incomplete."
    if "product price proof" in text or "non-zero" in text:
        return "Full Order and Statement product evidence does not match."
    return "Additional evidence is required before reconciliation."


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


def _cross_period_order_adjustments(
    statement: ParsedShopeeWeeklyStatement | None,
    adjustments: Iterable[CanonicalOrderAdjustment],
) -> tuple[CanonicalOrderAdjustment, ...]:
    """Return only persisted other-Statement events for current target orders."""

    if statement is None:
        return ()
    order_ids = {row.order_id for row in statement.order_rows}
    return tuple(
        event
        for event in adjustments
        if event.platform == "Shopee"
        and event.linked_order_id in order_ids
        and event.statement_file_hash != statement.file_hash
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
    canonical_adjustments: Iterable[CanonicalOrderAdjustment],
) -> StatementReviewEvidenceVersion:
    invoice_hash = invoice_snapshot_sha256(invoice_orders, invoice_items)
    return StatementReviewEvidenceVersion(
        statement_file_hash=statement.file_hash,
        invoice_snapshot_sha256=invoice_hash,
        product_family_snapshot_sha256=(
            reconciliation.product_family_snapshot.sha256
        ),
        order_adjustment_snapshot_sha256=_stable_hash(
            tuple(asdict(event) for event in canonical_adjustments)
        ),
        rule_version=reconciliation.rule_version,
        full_order_proof_rule_version=FULL_ORDER_STATEMENT_PROOF_RULE_VERSION,
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
    if (
        current.order_adjustment_snapshot_sha256
        != previous.order_adjustment_snapshot_sha256
    ):
        changed.append("ORDER_ADJUSTMENT_SNAPSHOT_CHANGED")
    if current.rule_version != previous.rule_version:
        changed.append("RECONCILIATION_RULE_CHANGED")
    if (
        current.full_order_proof_rule_version
        != getattr(previous, "full_order_proof_rule_version", None)
    ):
        changed.append("FULL_ORDER_PROOF_RULE_CHANGED")
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
