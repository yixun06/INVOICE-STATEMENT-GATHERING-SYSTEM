"""Application orchestration for the Data Import Statement workflow.

The Streamlit UI calls this boundary but does not reproduce reconciliation,
matching, persistence planning, or Google Sheets write rules.
"""

from __future__ import annotations


from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from src.invoice_app.services.statement_performance import measured, measure
from src.invoice_app.services import shopee_weekly_statement_service as source_rules
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
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
from src.invoice_app.services.market_context import MarketContext, SHOPEE_MY, resolve_market_context
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
    ActiveStatementRepository,
    StatementBatchAudit,
    StatementCommitAttempt,
    StatementCommitBlocked,
    StatementCommitOutcome,
    StatementCommitPlan,
    StatementSourcePlanContext,
    StatementCommitPreflight,
    StatementWriteNotApplied,
    prepare_full_order_statement_commit_plan,
    prepare_full_order_existence_statement_commit_plan,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    StagedShopeeWeeklyStatement,
    stage_parsed_shopee_weekly_statement,
    stage_shopee_weekly_statement,
)


INVOICE_COVERAGE_INCOMPLETE = "INVOICE_COVERAGE_INCOMPLETE"
ADDITIONAL_EVIDENCE_REQUIRED = "ADDITIONAL_EVIDENCE_REQUIRED"
FULL_ORDER_EXISTENCE_PROOF_RULE_VERSION = "FULL_ORDER_EXISTENCE_V1"


def expected_full_order_proof_rule_version(market_context: MarketContext) -> str:
    """Return the active Full Order proof contract for one explicit market."""

    context = resolve_market_context(market_context)
    return (
        FULL_ORDER_EXISTENCE_PROOF_RULE_VERSION
        if context.statement_order_existence_gate
        else FULL_ORDER_STATEMENT_PROOF_RULE_VERSION
    )


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
    full_order_snapshot_sha256: str
    product_family_snapshot_sha256: str
    order_adjustment_snapshot_sha256: str
    rule_version: str
    full_order_proof_rule_version: str
    reconciliation_sha256: str


@dataclass(frozen=True)
class StatementReviewCurrency:
    is_current: bool
    changed_evidence: tuple[str, ...]


# Increment when parser or source validation rules change.
_SOURCE_RULE_VERSION = "STATEMENT_SOURCE_V1"


@dataclass(frozen=True)
class _StatementSourceArtifact:
    key: tuple
    stage: StagedShopeeWeeklyStatement
    plans: StatementSourcePlanContext = field(default_factory=StatementSourcePlanContext, compare=False)


def _source_key(source_bytes: bytes, filename: str, context: MarketContext) -> tuple:
    return (sha256(source_bytes).hexdigest(), filename, context.key,
            context.statement_source_contract, context.statement_semantic_overlap_dedupe,
            _SOURCE_RULE_VERSION, stage_shopee_weekly_statement,
            source_rules.parse_shopee_statement, source_rules.validate_shopee_weekly_statement)


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
    # Session-only handle used to re-read Full Order Current during fresh
    # review/pre-commit validation; never serialized as business data.
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None
    active_gate: str = "V2"
    commit_preflight: StatementCommitPreflight | None = None
    source_artifact: _StatementSourceArtifact | None = None

    @property
    def ready(self) -> bool:
        """Whether the configured active Statement gate is complete."""

        return (
            not self.blockers
            and (
                self.reconciliation_v2 is not None
                or (self.plan is not None and self.plan.authority == "FULL_ORDER_CURRENT")
            )
        )

    @property
    def commit_ready(self) -> bool:
        """Whether V2 business evidence has a safe persistence plan."""

        return (
            self.ready
            and self.plan is not None
            and not self.persistence_blockers
        )


def statement_review_requires_proof_rule_refresh(
    review: StatementImportReview | None,
    market_context: MarketContext,
) -> bool:
    """Return whether a retained review predates the active market proof rule.

    This is deliberately a UI-state guard only. Fresh authority evidence is
    still compared by ``check_statement_review_currency`` immediately before
    commit.
    """

    evidence_version = getattr(review, "evidence_version", None)
    if evidence_version is None:
        return False
    return (
        getattr(evidence_version, "full_order_proof_rule_version", None)
        != expected_full_order_proof_rule_version(market_context)
    )


def review_statement_upload(
    source: str | Path | bytes | bytearray | BinaryIO,
    *,
    source_filename: str,
    batch_id: str,
    uploaded_by: str,
    writer: ActiveStatementRepository,
    product_master: ProductPriceMaster | None,
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
    repository: object | None = None,
    market_context: MarketContext = SHOPEE_MY,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> StatementImportReview:
    """Parse and review one upload against fresh authoritative UAT2 state.

    ``repository`` remains an ignored call-signature compatibility parameter for
    callers that used the retired Invoice repository.  It is deliberately not
    read: Full Order Current is the sole active order/product authority.
    """

    uploaded_at = now()
    source_bytes = _source_bytes(source)
    state = None if market_context.statement_order_existence_gate else writer.reload_active_commit_state()
    stage = stage_shopee_weekly_statement(
        source_bytes,
        source_filename=source_filename,
        existing_statements=(
            () if market_context.statement_order_existence_gate
            else state.committed_statements
        ),
        existing_order_evidence=(
            () if market_context.statement_order_existence_gate
            else state.committed_order_evidence
        ),
        evaluate_legacy_order_reconciliation=False,
        source_contract=market_context.statement_source_contract,
        semantic_overlap_dedupe=market_context.statement_semantic_overlap_dedupe,
    )
    full_order_orders, full_order_items, full_order_blockers, evidence_coverage = (
        ((), (), (), None) if (market_context.statement_order_existence_gate
                              and not stage.validation_issues and not stage.rejection_reasons) else
        _statement_inputs_with_full_order(
            stage.statement,
            product_master=product_master,
            full_order_repository=full_order_repository,
            imported_at=uploaded_at,
            market_context=market_context,
        )
    )
    artifact = (_StatementSourceArtifact(
        _source_key(source_bytes, source_filename, market_context), deepcopy(stage))
        if market_context.statement_order_existence_gate else None)
    result = _build_review(
        stage,
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        invoice_orders=full_order_orders,
        invoice_items=full_order_items,
        canonical_adjustments=(),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
        source_bytes=source_bytes,
        full_order_blockers=full_order_blockers,
        evidence_coverage=evidence_coverage,
        market_context=market_context,
        full_order_repository=full_order_repository,
        source_context=artifact.plans if artifact else None,
        writer=writer,
    )
    return replace(result, source_artifact=artifact)


@measured("review_refresh")
def refresh_statement_review(
    review: StatementImportReview,
    *,
    writer: ActiveStatementRepository,
    product_master: ProductPriceMaster | None,
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
    repository: object | None = None,
    market_context: MarketContext = SHOPEE_MY,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> StatementImportReview:
    """Reconcile the parsed source again after authoritative state changes."""

    if review.source_bytes is None:
        return review
    full_order_repository = full_order_repository or review.full_order_repository
    state = None if market_context.statement_order_existence_gate else writer.reload_active_commit_state()
    artifact = review.source_artifact
    key = _source_key(review.source_bytes, review.stage.source_filename, market_context)
    if market_context.statement_order_existence_gate and artifact is not None and artifact.key == key:
        stage = deepcopy(artifact.stage)
    else:
        stage = stage_shopee_weekly_statement(
            review.source_bytes,
            source_filename=review.stage.source_filename,
            existing_statements=(
                () if market_context.statement_order_existence_gate
                else state.committed_statements
            ),
            existing_order_evidence=(
                () if market_context.statement_order_existence_gate
                else state.committed_order_evidence
            ),
            evaluate_legacy_order_reconciliation=False,
            source_contract=market_context.statement_source_contract,
            semantic_overlap_dedupe=market_context.statement_semantic_overlap_dedupe,
        )
        artifact = (_StatementSourceArtifact(key, deepcopy(stage))
                    if market_context.statement_order_existence_gate else None)
    full_order_orders, full_order_items, full_order_blockers, evidence_coverage = (
        ((), (), (), None) if (market_context.statement_order_existence_gate
                              and not stage.validation_issues and not stage.rejection_reasons) else
        _statement_inputs_with_full_order(
            stage.statement,
            product_master=product_master,
            full_order_repository=full_order_repository,
            imported_at=review.uploaded_at,
            market_context=market_context,
        )
    )
    result = _build_review(
        stage,
        batch_id=review.batch_id,
        uploaded_at=review.uploaded_at,
        uploaded_by=review.uploaded_by,
        invoice_orders=full_order_orders,
        invoice_items=full_order_items,
        canonical_adjustments=(),
        product_master=product_master,
        verified_artifact_repairs=verified_artifact_repairs,
        now=now,
        source_bytes=review.source_bytes,
        full_order_blockers=full_order_blockers,
        evidence_coverage=evidence_coverage,
        market_context=market_context,
        full_order_repository=full_order_repository,
        source_context=artifact.plans if artifact else None,
        writer=writer,
    )
    return replace(result, source_artifact=artifact)


@measured("total_commit")
def commit_statement_review(
    review: StatementImportReview,
    *,
    writer: ActiveStatementRepository,
    load_product_master: Callable[[], ProductPriceMaster],
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
    repository: object | None = None,
    market_context: MarketContext = SHOPEE_MY,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
    transition: Callable[[str], None] | None = None,
) -> StatementCommitAttempt:
    """Under one lock, rebuild the authoritative review and commit it.

    The fresh review uses precisely the same Full Order + Statement builder as
    Validate and Review.  The legacy ``repository`` parameter is intentionally
    ignored for caller compatibility.
    """

    def trace(checkpoint: str) -> None:
        """Keep optional development tracing incapable of changing commit safety."""

        if transition is None:
            return
        try:
            transition(checkpoint)
        except Exception:
            pass

    trace("service_entry")
    if review.stage.already_imported:
        trace("already_imported_noop")
        return StatementCommitAttempt(
            False, ("ALREADY_IMPORTED",), StatementCommitOutcome.ALREADY_IMPORTED
        )
    if (
        review.commit_preflight is not None
        and review.commit_preflight.no_new_facts
    ):
        trace("no_new_facts_noop")
        return StatementCommitAttempt(
            False,
            ("ALREADY_IMPORTED", "NO_NEW_FACTS"),
            StatementCommitOutcome.ALREADY_IMPORTED,
        )

    if not review.commit_ready or review.source_bytes is None:
        trace("review_not_ready")
        return StatementCommitAttempt(
            False,
            review.persistence_blockers
            or review.blockers
            or (
                "REVIEW_SOURCE_UNAVAILABLE"
                if review.source_bytes is None
                else "STATEMENT_NOT_READY",
            ),
            _commit_outcome_from_preflight(review.commit_preflight),
        )
    with commit_lock.acquire():
        trace("before_authoritative_preflight")
        full_order_repository = full_order_repository or review.full_order_repository
        current_review = refresh_statement_review(
            review,
            writer=writer,
            product_master=(
                None if market_context.statement_order_existence_gate
                else load_product_master()
            ),
            full_order_repository=full_order_repository,
            market_context=market_context,
            verified_artifact_repairs=verified_artifact_repairs,
        )
        trace("after_authoritative_preflight")
        changed = _changed_evidence(review.evidence_version, current_review.evidence_version)
        if changed:
            trace("stale_review_blocked")
            return StatementCommitAttempt(
                False,
                tuple(f"STALE_REVIEW:{reason}" for reason in changed),
                StatementCommitOutcome.STALE_REVIEW,
            )
        if not current_review.commit_ready or current_review.plan is None:
            trace("current_review_not_ready")
            return StatementCommitAttempt(
                False,
                current_review.persistence_blockers
                or current_review.blockers
                or ("STATEMENT_NOT_READY",),
                _commit_outcome_from_preflight(current_review.commit_preflight),
            )

        try:
            trace("before_writer_call")
            writer.write_statement_batch(current_review.plan)
        except StatementWriteNotApplied as error:
            trace("writer_not_applied")
            return StatementCommitAttempt(
                False,
                ("WRITE_NOT_APPLIED",),
                StatementCommitOutcome.WRITE_FAILED,
                write_failure_diagnostic=error.diagnostic,
            )
        except StatementCommitBlocked as error:
            trace("writer_blocked")
            return StatementCommitAttempt(
                False, (str(error),), StatementCommitOutcome.BLOCKED
            )
        trace("after_writer_return")
        return StatementCommitAttempt(True, (), StatementCommitOutcome.COMMITTED)


def _commit_outcome_from_preflight(
    preflight: StatementCommitPreflight | None,
) -> StatementCommitOutcome:
    """Map only the writer's authoritative classification to UI navigation."""

    if preflight is None:
        return StatementCommitOutcome.BLOCKED
    if preflight.no_new_facts or preflight.status == "EXACT_DUPLICATE":
        return StatementCommitOutcome.ALREADY_IMPORTED
    if preflight.status == "CONFLICT":
        return StatementCommitOutcome.CONFLICT
    if preflight.status == "INCOMPLETE_UNSAFE":
        return StatementCommitOutcome.INCOMPLETE_UNSAFE
    return StatementCommitOutcome.BLOCKED


@measured("pre_commit_refresh")
def check_statement_review_currency(
    review: StatementImportReview,
    *,
    writer: ActiveStatementRepository,
    product_master: ProductPriceMaster | None,
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
    repository: object | None = None,
    market_context: MarketContext = SHOPEE_MY,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
) -> StatementReviewCurrency:
    """Compare a stored review with the same fresh active authority inputs.

    This read-only guard intentionally ignores the legacy Invoice repository.
    """

    if review.stage.statement is None or review.evidence_version is None:
        return StatementReviewCurrency(False, ("REVIEW_EVIDENCE_UNAVAILABLE",))
    current_review = refresh_statement_review(
        review,
        writer=writer,
        product_master=product_master,
        full_order_repository=full_order_repository,
        market_context=market_context,
        verified_artifact_repairs=verified_artifact_repairs,
    )
    if current_review.evidence_version is None:
        return StatementReviewCurrency(
            False,
            current_review.persistence_blockers
            or current_review.blockers
            or ("REVIEW_EVIDENCE_UNAVAILABLE",),
        )
    changed = _changed_evidence(review.evidence_version, current_review.evidence_version)
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
    product_master: ProductPriceMaster | None,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair],
    now: Callable[[], datetime],
    source_bytes: bytes,
    full_order_blockers: Iterable[str] = (),
    evidence_coverage: StatementEvidenceCoverage | None = None,
    market_context: MarketContext = SHOPEE_MY,
    full_order_repository: ShopeeFullOrderCurrentRepository | None = None,
    writer: ActiveStatementRepository,
    source_context: StatementSourcePlanContext | None = None,
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
        statement_order_ids = tuple(
            dict.fromkeys(row.order_id for row in statement.order_rows if row.order_id)
        )
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
            commit_preflight=StatementCommitPreflight(
                status="EXACT_DUPLICATE",
                eligible=False,
                blocker_codes=("ALREADY_IMPORTED",),
                blocker_reasons=(
                    "This Statement's settlement evidence is already committed; no second write is allowed.",
                ),
                exact_duplicate_order_ids=statement_order_ids,
                statement_batch_id=batch_id,
                statement_file_hash=statement.file_hash,
                source_filename=statement.source_filename,
            ),
        )

    full_order_blockers = tuple(full_order_blockers)
    invoice_items = tuple(invoice_items)
    canonical_adjustments = tuple(canonical_adjustments)
    invoice_coverage = None
    if (
        evidence_coverage is not None
        and not evidence_coverage.complete
        and not market_context.statement_order_existence_gate
        and not stage.rejection_reasons
        and not stage.validation_issues
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
            full_order_canonical_inserts=(),
            full_order_repository=full_order_repository,
            active_gate=(
                "FULL_ORDER_CURRENT_ORDER_EXISTENCE"
                if market_context.statement_order_existence_gate else "V2"
            ),
            commit_preflight=StatementCommitPreflight(
                status="MISSING_FULL_ORDER",
                eligible=False,
                blocker_codes=(ADDITIONAL_EVIDENCE_REQUIRED,),
                blocker_reasons=tuple(
                    requirement.reason
                    for requirement in evidence_coverage.additional_evidence[:10]
                ),
                new_order_ids=evidence_coverage.statement_order_ids,
                statement_batch_id=batch_id,
                statement_file_hash=statement.file_hash,
                source_filename=statement.source_filename,
            ),
        )
    if market_context.statement_order_existence_gate:
        return _build_order_existence_review(
            stage,
            batch_id=batch_id,
            uploaded_at=uploaded_at,
            uploaded_by=uploaded_by,
            source_bytes=source_bytes,
            full_order_blockers=full_order_blockers,
            evidence_coverage=evidence_coverage,
            market_context=market_context,
            full_order_repository=full_order_repository,
            now=now,
            writer=writer,
            source_context=source_context,
        )
    if product_master is None:
        raise StatementCommitBlocked("Product Master is required by the configured Statement gate.")
    repairs = tuple(verified_artifact_repairs)
    product_families = product_family_resolver_from_price_master(product_master)
    reconciliation_v2 = evaluate_statement_reconciliation(
        statement,
        invoice_orders,
        invoice_items,
        product_families=product_families,
        verified_artifact_repairs=repairs,
        canonical_adjustments=canonical_adjustments,
        persisted_platform=market_context.persisted_platform,
    )
    sku_matches = match_statement_sku_rows(
        statement.sku_rows,
        invoice_items,
        product_families=product_families,
        verified_artifact_repairs=repairs,
        persisted_platform=market_context.persisted_platform,
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
            plan = prepare_full_order_statement_commit_plan(
                statement,
                audit=StatementBatchAudit(
                    statement_batch_id=batch_id,
                    uploaded_at=uploaded_at,
                    uploaded_by=uploaded_by,
                    committed_at=now(),
                ),
                full_order_orders=invoice_orders,
                full_order_items=invoice_items,
                reconciliation=reconciliation_v2,
                validation_passed=True,
                persisted_platform=market_context.persisted_platform,
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
        full_order_canonical_inserts=(),
        full_order_repository=full_order_repository,
    )


@measured("review_model_build")
def _build_order_existence_review(
    stage: StagedShopeeWeeklyStatement,
    *,
    batch_id: str,
    uploaded_at: datetime,
    uploaded_by: str,
    source_bytes: bytes,
    full_order_blockers: tuple[str, ...],
    evidence_coverage: StatementEvidenceCoverage | None,
    market_context: MarketContext,
    full_order_repository: ShopeeFullOrderCurrentRepository | None,
    now: Callable[[], datetime],
    writer: ActiveStatementRepository,
    source_context: StatementSourcePlanContext | None = None,
) -> StatementImportReview:
    """Build the exact Current-order existence gate without legacy evidence."""

    statement = stage.statement
    assert statement is not None
    blockers = [*stage.rejection_reasons]
    blockers.extend(issue.message for issue in stage.validation_issues)
    persistence_blockers = list(blockers)
    plan = None
    commit_preflight = None
    filtered_coverage = evidence_coverage
    if not blockers:
        try:
            candidate_plan = prepare_full_order_existence_statement_commit_plan(
                statement,
                audit=StatementBatchAudit(
                    statement_batch_id=batch_id,
                    uploaded_at=uploaded_at,
                    uploaded_by=uploaded_by,
                    committed_at=now(),
                ),
                full_order_ids=tuple(
                    row.order_id for row in statement.order_rows if row.order_id
                ),
                validation_passed=True,
                source_context=source_context,
                persisted_platform=market_context.persisted_platform,
                semantic_overlap_dedupe=market_context.statement_semantic_overlap_dedupe,
            )
            commit_preflight = writer.preview_statement_batch(candidate_plan)
            _, _, full_order_blockers, evidence_coverage = _statement_inputs_with_full_order(
                statement, product_master=None, full_order_repository=full_order_repository,
                imported_at=uploaded_at, market_context=market_context,
                required_order_ids=commit_preflight.new_order_ids,
            )
            filtered_coverage = _coverage_for_order_ids(
                evidence_coverage, commit_preflight.new_order_ids
            )
            if commit_preflight.eligible:
                plan = commit_preflight.proposed_plan
                missing = (
                    filtered_coverage.additional_evidence
                    if filtered_coverage is not None else ()
                )
                if missing:
                    reasons = tuple(requirement.reason for requirement in missing)
                    blockers.extend(reasons)
                    persistence_blockers.extend(reasons)
                    persistence_blockers.append(ADDITIONAL_EVIDENCE_REQUIRED)
                    plan = None
                elif commit_preflight.new_order_ids and full_order_blockers:
                    blockers.extend(full_order_blockers)
                    persistence_blockers.extend(full_order_blockers)
                    plan = None
            elif not commit_preflight.no_new_facts:
                blockers.extend(commit_preflight.blocker_reasons)
                persistence_blockers.extend(commit_preflight.blocker_reasons)
        except StatementCommitBlocked as error:
            blockers.append(str(error))
            persistence_blockers.append(str(error))
    return StatementImportReview(
        batch_id=batch_id,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
        stage=stage,
        reconciliation_v2=None,
        evidence_version=_order_existence_evidence_version(
            statement, filtered_coverage, commit_preflight
        ),
        sku_matches=StatementItemMatchBatch((), False),
        plan=plan,
        blockers=tuple(dict.fromkeys(blockers)),
        limitations=(
            "Statement SKU rows are retained as raw source facts; no product allocation is inferred.",
        ),
        persistence_blockers=tuple(dict.fromkeys(persistence_blockers)),
        source_bytes=source_bytes,
        evidence_coverage=filtered_coverage,
        full_order_repository=full_order_repository,
        active_gate="FULL_ORDER_CURRENT_ORDER_EXISTENCE",
        commit_preflight=commit_preflight,
    )


def _coverage_for_order_ids(
    coverage: StatementEvidenceCoverage | None,
    order_ids: Iterable[str],
) -> StatementEvidenceCoverage | None:
    if coverage is None:
        return None
    required = tuple(dict.fromkeys(str(order_id).strip() for order_id in order_ids if str(order_id).strip()))
    required_set = set(required)
    return StatementEvidenceCoverage(
        statement_order_ids=required,
        already_verified_order_ids=tuple(
            order_id for order_id in coverage.already_verified_order_ids
            if order_id in required_set
        ),
        ready_from_full_order_order_ids=tuple(
            order_id for order_id in coverage.ready_from_full_order_order_ids
            if order_id in required_set
        ),
        additional_evidence=tuple(
            item for item in coverage.additional_evidence
            if item.order_id in required_set
        ),
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


@measured("full_order_coverage")
def _statement_inputs_with_full_order(
    statement: ParsedShopeeWeeklyStatement | None,
    *,
    product_master: ProductPriceMaster | None,
    full_order_repository: ShopeeFullOrderCurrentRepository | None,
    imported_at: datetime | None = None,
    market_context: MarketContext = SHOPEE_MY,
    required_order_ids: tuple[str, ...] | None = None,
) -> tuple[
    tuple[CanonicalInvoiceOrder, ...],
    tuple[CanonicalInvoiceItem, ...],
    tuple[str, ...],
    StatementEvidenceCoverage | None,
]:
    if statement is None:
        return (), (), (), None
    statement_order_ids = tuple(
        dict.fromkeys(row.order_id for row in statement.order_rows if row.order_id)
    )
    if required_order_ids is not None:
        statement_order_ids = tuple(dict.fromkeys(required_order_ids))
    if not statement_order_ids and market_context.statement_order_existence_gate:
        return (), (), (), StatementEvidenceCoverage((), (), (), ())
    if full_order_repository is None:
        requirements = tuple(
            StatementEvidenceRequirement(
                order_id,
                None,
                "Full Order Current evidence is unavailable.",
            )
            for order_id in statement_order_ids
        )
        return (
            (),
            (),
            (),
            StatementEvidenceCoverage(statement_order_ids, (), (), requirements),
        )
    try:
        scoped_reader = getattr(full_order_repository, "read_order_ids", None)
        if market_context.statement_order_existence_gate and callable(scoped_reader):
            available = set(scoped_reader(statement_order_ids, market=market_context.key.value))
        else:
            with measure("full_order_snapshot") as counts:
                snapshot = full_order_repository.read_snapshot()
                counts["full_order_orders_loaded"] = len(snapshot.orders)
                counts["full_order_items_loaded"] = len(snapshot.items)
            available = {order.order_id for order in snapshot.orders
                         if order.market == market_context.key.value}
    except Exception as error:
        requirements = tuple(
            StatementEvidenceRequirement(
                order_id,
                None,
                "Full Order Current evidence is unavailable.",
            )
            for order_id in statement_order_ids
        )
        return (
            (),
            (),
            (f"FULL_ORDER_CURRENT_UNAVAILABLE:{error}",),
            StatementEvidenceCoverage(statement_order_ids, (), (), requirements),
        )
    if market_context.statement_order_existence_gate:
        requirements = tuple(
            StatementEvidenceRequirement(
                order_id,
                None,
                "Full Order Current order is missing for this market.",
            )
            for order_id in statement_order_ids
            if order_id not in available
        )
        return (
            (),
            (),
            (),
            StatementEvidenceCoverage(
                statement_order_ids,
                (),
                tuple(order_id for order_id in statement_order_ids if order_id in available),
                requirements,
            ),
        )
    if product_master is None:
        return (), (), ("PRODUCT_MASTER_UNAVAILABLE",), None
    proof = prove_full_order_current_against_statement(
        statement,
        snapshot,
        product_master=product_master,
        imported_at=imported_at,
        market_context=market_context,
    )
    bundles = proof.bundles
    resolved_ids = {bundle.order.order_id for bundle in bundles}
    blockers = tuple(dict.fromkeys(proof.blockers))
    requirements = tuple(
        StatementEvidenceRequirement(
            order_id,
            None,
            _full_order_evidence_reason(
                tuple(blocker for blocker in blockers if blocker.startswith(f"{order_id}:"))
                or ("Full Order Current evidence is missing.",)
            ),
        )
        for order_id in statement_order_ids
        if order_id not in resolved_ids
    )
    return (
        tuple(bundle.order for bundle in bundles),
        tuple(item for bundle in bundles for item in bundle.items),
        blockers,
        StatementEvidenceCoverage(
            statement_order_ids,
            (),
            tuple(order_id for order_id in statement_order_ids if order_id in resolved_ids),
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
    if "cancelled order" in text:
        return "Cancelled order requires existing exception evidence."
    if "snapshot is missing" in text:
        return "Full Order Current evidence is missing."
    if "product master identity" in text:
        return "Product Master identity requires additional evidence."
    if "financial bridge" in text:
        return "Statement Evidence Review: financial evidence is incomplete or conflicting."
    if "product price proof" in text or "non-zero" in text:
        return "Full Order and Statement product evidence does not match."
    return "Additional evidence is required before reconciliation."


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
            "Statement and Full Order product populations are not fully accounted for."
        )
    for result in batch.orders:
        order_id = result.evidence.order_id
        summary = result.summary
        if order_id not in covered_order_ids:
            blockers.append(
                f"{order_id}: Full Order Current order coverage is unavailable."
            )
        elif order_id not in item_covered_order_ids:
            blockers.append(
                f"{order_id}: Full Order Current order has no product coverage."
            )
        if summary.identity_scope is IdentityScope.UNRESOLVED:
            blockers.append(
                f"{order_id}: product identity is unresolved from the available "
                "Statement, Full Order, and Product Master evidence."
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


def _order_existence_evidence_version(
    statement: ParsedShopeeWeeklyStatement,
    coverage: StatementEvidenceCoverage | None,
    preflight: StatementCommitPreflight | None,
) -> StatementReviewEvidenceVersion:
    """Fingerprint only the evidence consumed by the simplified active gate."""

    covered = coverage.ready_from_full_order_order_ids if coverage else ()
    return StatementReviewEvidenceVersion(
        statement_file_hash=statement.file_hash,
        full_order_snapshot_sha256=_stable_hash(covered),
        product_family_snapshot_sha256="NOT_APPLICABLE",
        order_adjustment_snapshot_sha256="NOT_APPLICABLE",
        rule_version=FULL_ORDER_EXISTENCE_PROOF_RULE_VERSION,
        full_order_proof_rule_version=FULL_ORDER_EXISTENCE_PROOF_RULE_VERSION,
        reconciliation_sha256=_stable_hash({
            "statement_order_ids": tuple(row.order_id for row in statement.order_rows),
            "covered_order_ids": covered,
            "classification": (
                {
                    "status": preflight.status,
                    "new": preflight.new_order_ids,
                    "duplicate": preflight.exact_duplicate_order_ids,
                    "conflict": preflight.conflict_order_ids,
                    "unsafe": preflight.incomplete_unsafe_order_ids,
                    "adjustment_new": preflight.proposed_adjustment_rows,
                    "adjustment_skipped": preflight.skipped_adjustment_rows,
                    "adjustment_conflicts": preflight.conflicting_adjustment_rows,
                }
                if preflight is not None else None
            ),
        }),
    )


def _evidence_version(
    statement: ParsedShopeeWeeklyStatement,
    full_order_orders: Iterable[CanonicalInvoiceOrder],
    full_order_items: Iterable[CanonicalInvoiceItem],
    reconciliation: StatementReconciliationBatch,
    canonical_adjustments: Iterable[CanonicalOrderAdjustment],
) -> StatementReviewEvidenceVersion:
    full_order_hash = _stable_hash(
        {
            "orders": tuple(asdict(order) for order in full_order_orders),
            "items": tuple(asdict(item) for item in full_order_items),
        }
    )
    return StatementReviewEvidenceVersion(
        statement_file_hash=statement.file_hash,
        full_order_snapshot_sha256=full_order_hash,
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
    current: StatementReviewEvidenceVersion | None,
) -> tuple[str, ...]:
    if previous is None or current is None:
        return ("REVIEW_EVIDENCE_UNAVAILABLE",)
    changed: list[str] = []
    if current.statement_file_hash != previous.statement_file_hash:
        changed.append("STATEMENT_FILE_CHANGED")
    if current.full_order_snapshot_sha256 != previous.full_order_snapshot_sha256:
        changed.append("FULL_ORDER_SNAPSHOT_CHANGED")
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
