"""Persistence-neutral planning for a committed Shopee Weekly Statement batch.

This module deliberately does not write to Google Sheets.  A final adapter may
call its atomic writer boundary only after it can prove the locked pre-commit
contract against authoritative persisted state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.domain.order_adjustment import (
    CanonicalOrderAdjustment,
    make_statement_adjustment,
    supported_adjustment_type,
)
from src.invoice_app.domain.statement_reconciliation_v2 import (
    IdentityScope,
    SettlementBasis,
    StatementMemberRef,
    StatementReconciliationBatch,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementAdjustment,
    ShippingFeeDiscrepancy,
    ServiceFeeDetail,
    SettlementIncomeRow,
    StatementSummaryLine,
)
from src.invoice_app.services.shopee_statement_item_matching import (
    StatementItemMatch,
    StatementItemMatchBatch,
    StatementItemMatchStatus,
    business_match_method,
)
from src.invoice_app.services.application_commit_lock import (
    APPLICATION_COMMIT_LOCK,
    ApplicationCommitLock,
)
from src.invoice_app.services.uat2_persistence_schema import (
    ORDER_ADJUSTMENTS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_SUMMARY_HEADERS,
)
from src.invoice_app.services.statement_reconciliation import (
    MISSING_COMPARISON_EVIDENCE,
    UNMATCHED_ORDER,
    compare_statement_order,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    CommittedOrderSettlementEvidence,
)


class StatementCommitBlocked(RuntimeError):
    """The staged Statement cannot safely become a committed batch."""


@dataclass(frozen=True)
class StatementWriteTargetDiagnostic:
    """Redacted shape of one Sheets values range in a Statement write."""

    target: str
    row_count: int
    column_count: int
    cell_count: int
    range_name: str = "unknown"


@dataclass(frozen=True)
class StatementWriteFailureDiagnostic:
    """Payload-free failure facts retained after a verified no-write result."""

    category: str
    exception_type: str
    http_status: int | None
    api_reason: str | None
    message: str
    request_method: str
    operation_type: str
    payload_bytes: int
    value_range_count: int
    total_planned_cells: int
    elapsed_milliseconds: int
    targets: tuple[StatementWriteTargetDiagnostic, ...]

    def trace_summary(self) -> str:
        """Return a bounded redacted summary safe for opt-in session diagnostics."""

        targets = ",".join(
            f"{target.target}[{target.range_name}]:{target.row_count}x{target.column_count}"
            for target in self.targets
        ) or "none"
        return (
            f"category={self.category};exception={self.exception_type};"
            f"http={self.http_status if self.http_status is not None else 'none'};"
            f"api_reason={self.api_reason or 'none'};payload_bytes={self.payload_bytes};"
            f"ranges={self.value_range_count};cells={self.total_planned_cells};"
            f"elapsed_ms={self.elapsed_milliseconds};targets={targets};"
            f"message={self.message}"
        )


class StatementWriteNotApplied(RuntimeError):
    """An uncertain authoritative write was verified to have applied nothing."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic: StatementWriteFailureDiagnostic | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class StatementWriteIntegrityError(RuntimeError):
    """An authoritative write result is mixed or cannot be proven."""


@dataclass(frozen=True)
class StatementBatchAudit:
    statement_batch_id: str
    uploaded_at: datetime
    uploaded_by: str
    committed_at: datetime


@dataclass(frozen=True)
class StatementOrderComparison:
    order_id: str
    comparison_source: str | None
    comparison_amount: Decimal | None
    difference: Decimal | None
    reconciliation_status: str
    payout_completed_date: date | None


@dataclass(frozen=True)
class InvoiceOrderStatementUpdate:
    order_id: str
    payout_completed_date: date | None
    payment_status: str
    difference: Decimal


@dataclass(frozen=True)
class InvoiceItemStatementUpdate:
    order_id: str
    item_index: int
    statement_product_price: Decimal
    statement_refund_amount: Decimal
    statement_net_selling_amount: Decimal


@dataclass(frozen=True)
class ProtectedInvoiceItemStatementFields:
    """GROUP item fields which a Statement commit is forbidden to change."""

    order_id: str
    item_index: int
    statement_product_price: Decimal | None
    statement_refund_amount: Decimal | None
    statement_net_selling_amount: Decimal | None


@dataclass(frozen=True)
class CommittedStatementReference:
    file_hash: str
    statement_period_from: date
    statement_period_to: date
    commit_status: str = "COMMITTED"


@dataclass(frozen=True)
class StatementCommitPlan:
    statement: ParsedShopeeWeeklyStatement
    audit: StatementBatchAudit
    rows: tuple[tuple[str, ...], ...]
    financial_component_rows: tuple[tuple[str, ...], ...]
    order_comparisons: tuple[StatementOrderComparison, ...]
    invoice_order_updates: tuple[InvoiceOrderStatementUpdate, ...]
    invoice_item_updates: tuple[InvoiceItemStatementUpdate, ...]
    summary_rows: tuple[tuple[str, ...], ...] = ()
    order_adjustments: tuple[CanonicalOrderAdjustment, ...] = ()
    protected_invoice_items: tuple[ProtectedInvoiceItemStatementFields, ...] = ()
    invoice_snapshot_sha256: str | None = None
    invoice_order_inserts: tuple[CanonicalInvoiceOrder, ...] = ()
    invoice_item_inserts: tuple[CanonicalInvoiceItem, ...] = ()
    # ``FULL_ORDER_CURRENT`` plans are the active MY/SG Statement contract.
    # The retained default keeps the historical Invoice-centric writer API
    # available only for archived/audit callers.
    authority: str = "LEGACY_INVOICE_V2"
    semantic_overlap_dedupe: bool = False


@dataclass(frozen=True)
class StatementCommitState:
    orders: Mapping[str, CanonicalInvoiceOrder]
    committed_statements: Sequence[CommittedStatementReference]
    items: tuple[CanonicalInvoiceItem, ...] = ()
    order_adjustments: tuple[CanonicalOrderAdjustment, ...] = ()
    committed_order_evidence: tuple[CommittedOrderSettlementEvidence, ...] = ()
    committed_statement_rows: tuple[tuple[str, ...], ...] = ()
    committed_financial_component_rows: tuple[tuple[str, ...], ...] = ()
    # Header presence is optional historical provenance.  A missing mapping
    # entry is intentionally unknown, not evidence of an absent header.
    committed_service_fee_component_headers_by_batch: Mapping[
        str, tuple[str, ...]
    ] = field(default_factory=dict)


class StatementCommitOutcome(str, Enum):
    """Navigation-safe outcomes produced by the existing commit authority."""

    COMMITTED = "COMMITTED"
    ALREADY_IMPORTED = "ALREADY_IMPORTED"
    STALE_REVIEW = "STALE_REVIEW"
    CONFLICT = "CONFLICT"
    INCOMPLETE_UNSAFE = "INCOMPLETE_UNSAFE"
    WRITE_FAILED = "WRITE_FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class StatementCommitAttempt:
    """Authoritative result of one locked Statement commit attempt."""

    committed: bool
    reasons: tuple[str, ...]
    outcome: StatementCommitOutcome = StatementCommitOutcome.BLOCKED
    write_failure_diagnostic: StatementWriteFailureDiagnostic | None = None


@dataclass(frozen=True)
class StatementCommitPreflight:
    """Read-only projection of the active writer's commit eligibility checks."""

    status: str
    eligible: bool
    blocker_codes: tuple[str, ...] = ()
    blocker_reasons: tuple[str, ...] = ()
    new_order_ids: tuple[str, ...] = ()
    exact_duplicate_order_ids: tuple[str, ...] = ()
    conflict_order_ids: tuple[str, ...] = ()
    incomplete_unsafe_order_ids: tuple[str, ...] = ()
    statement_batch_id: str = ""
    statement_file_hash: str = ""
    source_filename: str = ""
    representative_source_rows: tuple[tuple[str, int], ...] = ()
    proposed_statement_rows: int = 0
    proposed_financial_component_rows: int = 0
    proposed_summary_rows: int = 0
    proposed_adjustment_rows: int = 0
    skipped_adjustment_rows: int = 0
    conflicting_adjustment_rows: int = 0
    proposed_new_released_amount: Decimal = Decimal("0.00")
    no_new_facts: bool = False
    proposed_plan: StatementCommitPlan | None = None


class StatementAtomicWriter(Protocol):
    """Implemented only by a storage adapter with an approved atomic contract."""

    def write_statement_batch(self, plan: StatementCommitPlan) -> None: ...


class ActiveStatementRepository(StatementAtomicWriter, Protocol):
    """Storage-only contract shared by MY PostgreSQL and retained SG Sheets."""

    @property
    def persisted_platform(self) -> str: ...

    def reload_active_commit_state(self) -> StatementCommitState: ...

    def preview_statement_batch(self, plan: StatementCommitPlan) -> StatementCommitPreflight: ...


def prepare_statement_commit_plan(
    statement: ParsedShopeeWeeklyStatement,
    *,
    audit: StatementBatchAudit,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    sku_matches: StatementItemMatchBatch,
    validation_passed: bool,
) -> StatementCommitPlan:
    """Build rows and enrichment updates without performing any persistence write."""

    if not validation_passed:
        raise StatementCommitBlocked("Statement internal validation has not passed.")
    if not sku_matches.eligible_for_commit:
        raise StatementCommitBlocked("Statement SKU matching is not ready for commit.")
    if not audit.statement_batch_id.strip():
        raise ValueError("statement_batch_id is required.")
    _validate_statement_row_identity(statement)

    orders_by_id = {order.order_id: order for order in invoice_orders if order.platform == "Shopee"}
    comparisons = tuple(_compare_order(row, orders_by_id.get(row.order_id)) for row in statement.order_rows)
    unmatched = [
        comparison.order_id
        for comparison in comparisons
        if comparison.reconciliation_status == UNMATCHED_ORDER
    ]
    if unmatched:
        raise StatementCommitBlocked(
            "Statement target Order ID coverage is incomplete: "
            + ", ".join(unmatched[:5])
        )
    missing_evidence = [
        comparison.order_id
        for comparison in comparisons
        if comparison.reconciliation_status == MISSING_COMPARISON_EVIDENCE
    ]
    if missing_evidence:
        raise StatementCommitBlocked(
            "Invoice comparison evidence is missing: "
            + ", ".join(missing_evidence[:5])
        )

    match_by_source_row = {match.statement_source_row: match for match in sku_matches.matches}
    if len(match_by_source_row) != len(statement.sku_rows):
        raise StatementCommitBlocked("Every Statement SKU row requires one matching result.")
    if any(match.status is not StatementItemMatchStatus.MATCHED for match in match_by_source_row.values()):
        raise StatementCommitBlocked("An unresolved Statement SKU row cannot be committed.")

    rows = [
        _order_row(statement, audit, row, comparison)
        for row, comparison in zip(statement.order_rows, comparisons)
    ]
    sku_rows = []
    matched_item_keys: list[tuple[str, int]] = []
    for source_row in statement.sku_rows:
        match = match_by_source_row[source_row.source_row_number]
        if match.invoice_item_index is None:
            raise StatementCommitBlocked("A matched Statement SKU row lacks Invoice item identity.")
        product_price = source_row.financial_components.get("Product Price")
        refund_amount = source_row.financial_components.get("Refund Amount")
        if product_price is None or refund_amount is None:
            raise StatementCommitBlocked(
                f"Statement SKU source row {source_row.source_row_number} lacks Product Price or Refund Amount."
            )
        sku_rows.append(_sku_row(statement, audit, source_row, match, product_price, refund_amount))
        matched_item_keys.append((source_row.order_id, match.invoice_item_index))
    rows.extend(sku_rows)
    rows.extend(_adjustment_row(statement, audit, adjustment) for adjustment in statement.adjustments)
    financial_component_rows = _financial_component_rows(statement, audit)
    summary_rows = _summary_rows(statement, audit)

    duplicate_keys = {key for key, count in Counter(matched_item_keys).items() if count > 1}
    item_updates = []
    for source_row in statement.sku_rows:
        match = match_by_source_row[source_row.source_row_number]
        assert match.invoice_item_index is not None
        key = (source_row.order_id, match.invoice_item_index)
        if key in duplicate_keys:
            continue
        product_price = source_row.financial_components["Product Price"]
        refund_amount = source_row.financial_components["Refund Amount"]
        assert product_price is not None and refund_amount is not None
        item_updates.append(InvoiceItemStatementUpdate(
            order_id=source_row.order_id,
            item_index=match.invoice_item_index,
            statement_product_price=product_price,
            statement_refund_amount=refund_amount,
            statement_net_selling_amount=product_price + refund_amount,
        ))

    order_updates = tuple(
        InvoiceOrderStatementUpdate(
            order_id=comparison.order_id,
            payout_completed_date=comparison.payout_completed_date,
            payment_status="RELEASED",
            difference=comparison.difference,
        )
        for comparison in comparisons
    )
    return StatementCommitPlan(
        statement=statement,
        audit=audit,
        rows=tuple(rows),
        financial_component_rows=financial_component_rows,
        order_comparisons=comparisons,
        invoice_order_updates=order_updates,
        invoice_item_updates=tuple(item_updates),
        summary_rows=summary_rows,
        order_adjustments=_order_adjustment_events(statement, audit),
    )


GROUP_MATCH_METHOD = "Product Group Multiset (Unallocated)"


def prepare_v2_statement_commit_plan(
    statement: ParsedShopeeWeeklyStatement,
    *,
    audit: StatementBatchAudit,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
    reconciliation: StatementReconciliationBatch,
    validation_passed: bool,
    canonical_inserts: Iterable[InvoiceBundle] = (),
    persisted_platform: str = "Shopee",
) -> StatementCommitPlan:
    """Build a fail-closed V2 plan without fabricating GROUP allocation."""

    if not validation_passed:
        raise StatementCommitBlocked("Statement internal validation has not passed.")
    if not audit.statement_batch_id.strip():
        raise ValueError("statement_batch_id is required.")
    _validate_statement_row_identity(statement)
    if reconciliation.statement_validation_issues:
        raise StatementCommitBlocked("V2 Statement validation contains blocking issues.")
    if not reconciliation.coverage.valid:
        raise StatementCommitBlocked("V2 product coverage is incomplete or overlapping.")

    invoice_orders = tuple(
        order for order in invoice_orders if order.platform == persisted_platform
    )
    invoice_items = tuple(
        item for item in invoice_items if item.platform == persisted_platform
    )
    orders_by_id = {order.order_id: order for order in invoice_orders}
    items_by_key = {
        (item.order_id, item.item_index): item for item in invoice_items
    }
    results_by_order = {
        result.evidence.order_id: result for result in reconciliation.orders
    }
    statement_order_ids = {row.order_id for row in statement.order_rows}
    if set(results_by_order) != statement_order_ids:
        raise StatementCommitBlocked(
            "V2 order coverage does not equal Statement order coverage."
        )

    comparisons: list[StatementOrderComparison] = []
    for row in statement.order_rows:
        result = results_by_order[row.order_id]
        if row.order_id not in orders_by_id:
            raise StatementCommitBlocked(
                f"Statement target Order ID is missing: {row.order_id}"
            )
        if not result.evidence.coverage.valid:
            raise StatementCommitBlocked(
                f"{row.order_id}: V2 product coverage is invalid."
            )
        if result.summary.identity_scope is IdentityScope.UNRESOLVED:
            raise StatementCommitBlocked(
                f"{row.order_id}: V2 product identity is unresolved."
            )
        if not result.summary.merchandise_reconciled:
            raise StatementCommitBlocked(
                f"{row.order_id}: merchandise is not reconciled."
            )
        if result.summary.settlement_basis is SettlementBasis.NONE:
            raise StatementCommitBlocked(
                f"{row.order_id}: settlement is not reconciled."
            )
        settlement = result.evidence.settlement
        if settlement.invoice_basis is None or settlement.raw_difference is None:
            raise StatementCommitBlocked(
                f"{row.order_id}: settlement source evidence is missing."
            )
        comparisons.append(
            StatementOrderComparison(
                order_id=row.order_id,
                comparison_source="Order Income",
                comparison_amount=settlement.invoice_basis,
                difference=settlement.raw_difference,
                # The legacy Matched/Different vocabulary cannot encode
                # V2 EXACT/EXPLAINED without changing the locked schema.
                reconciliation_status="",
                payout_completed_date=row.payout_completed_date,
            )
        )

    source_rows = {
        StatementMemberRef(
            statement.file_hash,
            "Income",
            row.source_row_number,
            row.sequence_no,
        ): row
        for row in statement.sku_rows
    }
    if len(source_rows) != len(statement.sku_rows):
        raise StatementCommitBlocked(
            "Statement SKU source members are not uniquely identifiable."
        )

    planned_statement_members: set[StatementMemberRef] = set()
    item_targets: set[tuple[str, int]] = set()
    group_targets: dict[
        tuple[str, int], ProtectedInvoiceItemStatementFields
    ] = {}
    serialized_sku_rows: dict[StatementMemberRef, tuple[str, ...]] = {}
    item_updates: list[InvoiceItemStatementUpdate] = []

    for result in reconciliation.orders:
        for identity in result.evidence.identities:
            if identity.identity_scope is IdentityScope.UNRESOLVED:
                raise StatementCommitBlocked(
                    f"{result.evidence.order_id}: unresolved V2 identity cannot commit."
                )
            for member in identity.statement_members:
                if member in planned_statement_members:
                    raise StatementCommitBlocked(
                        "V2 Statement member is consumed more than once."
                    )
                if member not in source_rows:
                    raise StatementCommitBlocked(
                        "V2 Statement source member is missing: "
                        f"{member.source_row_number}."
                    )
                planned_statement_members.add(member)

            if identity.identity_scope is IdentityScope.ITEM:
                if (
                    len(identity.statement_members) != 1
                    or len(identity.invoice_members) != 1
                    or len(identity.selected_pairs) != 1
                    or not identity.allocation_resolved
                ):
                    raise StatementCommitBlocked(
                        "V2 ITEM requires exactly one resolved source pair."
                    )
                statement_member, invoice_member = identity.selected_pairs[0]
                if statement_member != identity.statement_members[0]:
                    raise StatementCommitBlocked(
                        "V2 ITEM selected Statement member is outside its identity."
                    )
                if invoice_member != identity.invoice_members[0]:
                    raise StatementCommitBlocked(
                        "V2 ITEM selected Invoice member is outside its identity."
                    )
                key = (invoice_member.order_id, invoice_member.item_index)
                if key in item_targets:
                    raise StatementCommitBlocked(
                        f"V2 ITEM duplicates Invoice target {key!r}."
                    )
                if key not in items_by_key:
                    raise StatementCommitBlocked(
                        f"V2 ITEM target is missing: {key!r}."
                    )
                edge = next(
                    (
                        edge
                        for edge in identity.compatible_edges
                        if edge.statement_member == statement_member
                        and edge.invoice_member == invoice_member
                    ),
                    None,
                )
                if edge is None:
                    raise StatementCommitBlocked(
                        "V2 ITEM selected pair has no compatible edge evidence."
                    )
                source_row = source_rows[statement_member]
                product_price, refund_amount = _required_sku_money(source_row)
                serialized_sku_rows[statement_member] = _v2_sku_row(
                    statement,
                    audit,
                    source_row,
                    matched_item_index=invoice_member.item_index,
                    match_method=_v2_item_match_method(edge.match_method),
                    product_price=product_price,
                    refund_amount=refund_amount,
                    persisted_platform=persisted_platform,
                )
                item_targets.add(key)
                item_updates.append(
                    InvoiceItemStatementUpdate(
                        order_id=invoice_member.order_id,
                        item_index=invoice_member.item_index,
                        statement_product_price=product_price,
                        statement_refund_amount=refund_amount,
                        statement_net_selling_amount=product_price + refund_amount,
                    )
                )
                continue

            if identity.identity_scope is not IdentityScope.GROUP:
                raise StatementCommitBlocked("Unsupported V2 identity scope.")
            if identity.selected_pairs or identity.allocation_resolved:
                raise StatementCommitBlocked(
                    "V2 GROUP must remain physically unallocated."
                )
            if (
                identity.perfect_matching_count < 2
                or not identity.statement_members
                or len(identity.statement_members) != len(identity.invoice_members)
            ):
                raise StatementCommitBlocked(
                    "V2 GROUP lacks multiple complete valid allocations."
                )
            for invoice_member in identity.invoice_members:
                key = (invoice_member.order_id, invoice_member.item_index)
                if key in group_targets:
                    raise StatementCommitBlocked(
                        f"V2 GROUP duplicates protected target {key!r}."
                    )
                item = items_by_key.get(key)
                if item is None:
                    raise StatementCommitBlocked(
                        f"V2 GROUP protected target is missing: {key!r}."
                    )
                group_targets[key] = ProtectedInvoiceItemStatementFields(
                    order_id=item.order_id,
                    item_index=item.item_index,
                    statement_product_price=item.statement_product_price,
                    statement_refund_amount=item.statement_refund_amount,
                    statement_net_selling_amount=item.statement_net_selling_amount,
                )
            for member in identity.statement_members:
                source_row = source_rows[member]
                product_price, refund_amount = _required_sku_money(source_row)
                serialized_sku_rows[member] = _v2_sku_row(
                    statement,
                    audit,
                    source_row,
                    matched_item_index=None,
                    match_method=GROUP_MATCH_METHOD,
                    product_price=product_price,
                    refund_amount=refund_amount,
                    persisted_platform=persisted_platform,
                )

    if planned_statement_members != set(source_rows):
        raise StatementCommitBlocked(
            "V2 plan does not cover every Statement SKU source member exactly once."
        )
    overlap = item_targets & set(group_targets)
    if overlap:
        raise StatementCommitBlocked(
            f"V2 ITEM and GROUP targets overlap: {sorted(overlap)!r}."
        )

    comparison_tuple = tuple(comparisons)
    rows = [
        _order_row(statement, audit, row, comparison, persisted_platform=persisted_platform)
        for row, comparison in zip(statement.order_rows, comparison_tuple)
    ]
    for source_row in statement.sku_rows:
        member = StatementMemberRef(
            statement.file_hash,
            "Income",
            source_row.source_row_number,
            source_row.sequence_no,
        )
        rows.append(serialized_sku_rows[member])
    rows.extend(
        _adjustment_row(statement, audit, adjustment, persisted_platform=persisted_platform)
        for adjustment in statement.adjustments
    )
    order_updates = tuple(
        InvoiceOrderStatementUpdate(
            order_id=comparison.order_id,
            payout_completed_date=comparison.payout_completed_date,
            payment_status="RELEASED",
            difference=comparison.difference,
        )
        for comparison in comparison_tuple
        if comparison.difference is not None
    )
    if len(order_updates) != len(comparison_tuple):
        raise StatementCommitBlocked(
            "V2 order update lacks a settlement difference."
        )
    insert_bundles = tuple(canonical_inserts)
    insert_orders = tuple(bundle.order for bundle in insert_bundles)
    item_update_by_key = {
        (update.order_id, update.item_index): update for update in item_updates
    }
    insert_items = tuple(
        replace(
            item,
            statement_product_price=update.statement_product_price,
            statement_refund_amount=update.statement_refund_amount,
            statement_net_selling_amount=update.statement_net_selling_amount,
        )
        if (update := item_update_by_key.get((item.order_id, item.item_index)))
        else item
        for bundle in insert_bundles
        for item in bundle.items
    )
    insert_order_ids = {order.order_id for order in insert_orders}
    if len(insert_order_ids) != len(insert_orders):
        raise StatementCommitBlocked("Full Order canonical insert plan duplicates an Order ID.")
    if any(item.order_id not in insert_order_ids for item in insert_items):
        raise StatementCommitBlocked("Full Order canonical insert Item lacks its planned Order.")
    return StatementCommitPlan(
        statement=statement,
        audit=audit,
        rows=tuple(rows),
        financial_component_rows=_financial_component_rows(
            statement, audit, persisted_platform=persisted_platform
        ),
        order_comparisons=comparison_tuple,
        invoice_order_updates=order_updates,
        invoice_item_updates=tuple(item_updates),
        summary_rows=_summary_rows(statement, audit, persisted_platform=persisted_platform),
        order_adjustments=_order_adjustment_events(
            statement, audit, persisted_platform=persisted_platform
        ),
        protected_invoice_items=tuple(
            group_targets[key] for key in sorted(group_targets)
        ),
        invoice_snapshot_sha256=invoice_snapshot_sha256(
            invoice_orders,
            invoice_items,
        ),
        invoice_order_inserts=insert_orders,
        invoice_item_inserts=insert_items,
    )


def prepare_full_order_statement_commit_plan(
    statement: ParsedShopeeWeeklyStatement,
    *,
    audit: StatementBatchAudit,
    full_order_orders: Iterable[CanonicalInvoiceOrder],
    full_order_items: Iterable[CanonicalInvoiceItem],
    reconciliation: StatementReconciliationBatch,
    validation_passed: bool,
    persisted_platform: str = "Shopee",
) -> StatementCommitPlan:
    """Build the active Statement-only persistence projection.

    The V2 evaluator remains a pure deterministic comparison engine, but its
    inputs here are transient projections of Full Order Current.  No persisted
    Invoice row is read, required, inserted, or enriched by this plan.
    """

    legacy_plan = prepare_v2_statement_commit_plan(
        statement,
        audit=audit,
        invoice_orders=full_order_orders,
        invoice_items=full_order_items,
        reconciliation=reconciliation,
        validation_passed=validation_passed,
        persisted_platform=persisted_platform,
    )
    return replace(
        legacy_plan,
        invoice_order_updates=(),
        invoice_item_updates=(),
        protected_invoice_items=(),
        invoice_snapshot_sha256=None,
        invoice_order_inserts=(),
        invoice_item_inserts=(),
        authority="FULL_ORDER_CURRENT",
    )


def prepare_full_order_existence_statement_commit_plan(
    statement: ParsedShopeeWeeklyStatement,
    *,
    audit: StatementBatchAudit,
    full_order_ids: Iterable[str],
    validation_passed: bool,
    persisted_platform: str,
    semantic_overlap_dedupe: bool = False,
) -> StatementCommitPlan:
    """Persist source settlement facts after exact Current-order existence proof.

    This active gate deliberately has no Invoice/PDF, Product Master, SKU, or
    merchandise allocation dependency.  It records unallocated SKU source rows
    intact and leaves legacy Invoice projection fields blank.
    """

    if not validation_passed:
        raise StatementCommitBlocked("Statement internal validation has not passed.")
    if not audit.statement_batch_id.strip():
        raise ValueError("statement_batch_id is required.")
    _validate_statement_row_identity(statement)
    covered = {str(order_id).strip() for order_id in full_order_ids if str(order_id).strip()}
    missing = [row.order_id for row in statement.order_rows if row.order_id not in covered]
    if missing:
        raise StatementCommitBlocked(
            "Statement target Order ID is missing from Full Order Current: " + missing[0]
        )
    comparisons = tuple(
        StatementOrderComparison(
            order_id=row.order_id,
            comparison_source="Full Order Current",
            comparison_amount=None,
            difference=None,
            reconciliation_status="FULL_ORDER_EXISTS",
            payout_completed_date=row.payout_completed_date,
        )
        for row in statement.order_rows
    )
    rows = [
        _order_row(statement, audit, row, comparison, persisted_platform=persisted_platform)
        for row, comparison in zip(statement.order_rows, comparisons)
    ]
    rows.extend(
        _unallocated_sku_row(statement, audit, row, persisted_platform=persisted_platform)
        for row in statement.sku_rows
    )
    rows.extend(
        _adjustment_row(statement, audit, adjustment, persisted_platform=persisted_platform)
        for adjustment in statement.adjustments
    )
    return StatementCommitPlan(
        statement=statement,
        audit=audit,
        rows=tuple(rows),
        financial_component_rows=_financial_component_rows(
            statement, audit, persisted_platform=persisted_platform
        ),
        order_comparisons=comparisons,
        invoice_order_updates=(),
        invoice_item_updates=(),
        summary_rows=_summary_rows(statement, audit, persisted_platform=persisted_platform),
        order_adjustments=_order_adjustment_events(
            statement, audit, persisted_platform=persisted_platform
        ),
        authority="FULL_ORDER_CURRENT",
        semantic_overlap_dedupe=semantic_overlap_dedupe,
    )


def validate_active_statement_state(
    plan: StatementCommitPlan,
    state: StatementCommitState,
) -> tuple[str, ...]:
    """Fresh pre-commit checks that deliberately exclude Invoice/PDF state."""

    return preview_active_statement_state(plan, state).blocker_codes


def preview_active_statement_state(
    plan: StatementCommitPlan,
    state: StatementCommitState,
) -> StatementCommitPreflight:
    """Classify complete order bundles before applying the Full Order gate."""

    statement = plan.statement
    incoming_ids = tuple(
        dict.fromkeys(row.order_id for row in statement.order_rows if row.order_id)
    )
    source_row_by_order = {
        row.order_id: row.source_row_number for row in statement.order_rows if row.order_id
    }

    def provenance(order_ids: Iterable[str]) -> tuple[tuple[str, int], ...]:
        return tuple(
            (order_id, source_row_by_order[order_id])
            for order_id in tuple(dict.fromkeys(order_ids))[:10]
            if order_id in source_row_by_order
        )

    if plan.authority != "FULL_ORDER_CURRENT":
        return StatementCommitPreflight(
            status="CONFLICT",
            eligible=False,
            blocker_codes=("ACTIVE_STATEMENT_AUTHORITY_MISMATCH",),
            blocker_reasons=(
                "The proposed write does not use the active Full Order Current Statement authority.",
            ),
            representative_source_rows=provenance(incoming_ids),
            statement_batch_id=plan.audit.statement_batch_id,
            statement_file_hash=statement.file_hash,
            source_filename=statement.source_filename,
        )

    new_ids, duplicate_ids, conflict_ids, unsafe_ids = _classify_order_bundles(
        plan, state
    )
    new_adjustments, skipped_adjustments, adjustment_conflicts = (
        _classify_adjustment_events(plan.order_adjustments, state.order_adjustments)
    )
    effective_plan = _active_write_plan(
        plan,
        new_order_ids=new_ids,
        new_adjustments=new_adjustments,
    )
    released_by_order = {
        row.order_id: row.total_released_amount or Decimal("0.00")
        for row in statement.order_rows
        if row.order_id
    }
    proposed_released = sum(
        (released_by_order[order_id] for order_id in new_ids), Decimal("0.00")
    )
    common = dict(
        new_order_ids=new_ids,
        exact_duplicate_order_ids=duplicate_ids,
        conflict_order_ids=conflict_ids,
        incomplete_unsafe_order_ids=unsafe_ids,
        statement_batch_id=plan.audit.statement_batch_id,
        statement_file_hash=statement.file_hash,
        source_filename=statement.source_filename,
        representative_source_rows=provenance(
            (*conflict_ids, *unsafe_ids, *duplicate_ids, *new_ids)
        ),
        proposed_statement_rows=len(effective_plan.rows),
        proposed_financial_component_rows=len(
            effective_plan.financial_component_rows
        ),
        proposed_summary_rows=len(effective_plan.summary_rows),
        proposed_adjustment_rows=len(new_adjustments),
        skipped_adjustment_rows=skipped_adjustments,
        conflicting_adjustment_rows=adjustment_conflicts,
        proposed_new_released_amount=proposed_released,
        proposed_plan=effective_plan,
    )
    if conflict_ids or adjustment_conflicts:
        return StatementCommitPreflight(
            status="CONFLICT",
            eligible=False,
            blocker_codes=tuple(
                [f"SETTLEMENT_EVIDENCE_CONFLICT:{order_id}" for order_id in conflict_ids]
                + (["ADJUSTMENT_EVIDENCE_CONFLICT"] if adjustment_conflicts else [])
            ),
            blocker_reasons=(
                "Committed complete evidence conflicts with the incoming source for "
                f"{len(conflict_ids)} order(s) and {adjustment_conflicts} adjustment(s).",
            ),
            **common,
        )
    if unsafe_ids:
        return StatementCommitPreflight(
            status="INCOMPLETE_UNSAFE",
            eligible=False,
            blocker_codes=tuple(
                f"INCOMPLETE_SETTLEMENT_EVIDENCE:{order_id}" for order_id in unsafe_ids
            ),
            blocker_reasons=(
                "Committed evidence is missing, extra, or ambiguous for "
                f"{len(unsafe_ids)} order bundle(s); no facts will be discarded.",
            ),
            **common,
        )
    if not new_ids and not new_adjustments:
        return StatementCommitPreflight(
            status="ALREADY_IMPORTED",
            eligible=False,
            blocker_codes=(),
            blocker_reasons=(),
            no_new_facts=True,
            **common,
        )
    return StatementCommitPreflight(
        status="MIXED" if duplicate_ids or skipped_adjustments else "NEW",
        eligible=True,
        **common,
    )


_ORDER_BUNDLE_FIELDS = (
    "record_type", "order_id", "order_creation_date", "payout_completed_date",
    "release_channel", "order_type", "total_released_amount",
    "statement_product_id", "statement_product_name", "statement_product_price",
    "statement_refund_amount", "statement_net_selling_amount", "platform",
)
_COMPONENT_BUNDLE_FIELDS = (
    "record_type", "statement_source_sheet", "order_id", "statement_product_id",
    "component_name", "component_amount", "component_note", "payout_completed_date",
    "platform",
)
_ORDER_BUNDLE_MONETARY_FIELDS = frozenset(
    {
        "total_released_amount",
        "statement_product_price",
        "statement_refund_amount",
        "statement_net_selling_amount",
    }
)
_COMPONENT_BUNDLE_MONETARY_FIELDS = frozenset({"component_amount"})

_EvidenceBundle = tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]]]


@dataclass(frozen=True)
class _CommittedBundleCandidate:
    bundle: _EvidenceBundle
    service_fee_component_headers: frozenset[str] | None


def _classify_order_bundles(
    plan: StatementCommitPlan,
    state: StatementCommitState,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    incoming = _bundle_map(plan.rows, plan.financial_component_rows)
    positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    incoming_platforms = {
        row[positions["platform"]] for row in plan.rows if row[positions["platform"]]
    }
    if len(incoming_platforms) != 1:
        order_ids = tuple(
            dict.fromkeys(row.order_id for row in plan.statement.order_rows if row.order_id)
        )
        return (), (), (), order_ids
    persisted_platform = next(iter(incoming_platforms))
    committed = _committed_bundle_map(
        state.committed_statement_rows,
        state.committed_financial_component_rows,
        persisted_platform=persisted_platform,
        service_fee_component_headers_by_batch=(
            state.committed_service_fee_component_headers_by_batch
        ),
    )
    incoming_service_fee_component_headers = _service_fee_component_headers(plan.statement)
    new: list[str] = []
    duplicate: list[str] = []
    conflict: list[str] = []
    unsafe: list[str] = []
    for order_id in tuple(
        dict.fromkeys(row.order_id for row in plan.statement.order_rows if row.order_id)
    ):
        source_bundle = incoming.get(order_id)
        candidates = committed.get(order_id, ())
        if source_bundle is None:
            unsafe.append(order_id)
        elif not candidates:
            new.append(order_id)
        elif all(
            _bundles_are_semantically_equivalent(
                source_bundle,
                candidate,
                incoming_service_fee_component_headers=incoming_service_fee_component_headers,
            )
            for candidate in candidates
        ):
            duplicate.append(order_id)
        elif any(
            _bundles_are_semantically_equivalent(
                source_bundle,
                candidate,
                incoming_service_fee_component_headers=incoming_service_fee_component_headers,
            )
            for candidate in candidates
        ):
            unsafe.append(order_id)
        elif any(
            _bundle_shape(candidate.bundle) != _bundle_shape(source_bundle)
            for candidate in candidates
        ):
            unsafe.append(order_id)
        else:
            conflict.append(order_id)
    return tuple(new), tuple(duplicate), tuple(conflict), tuple(unsafe)


def _bundle_map(
    rows: Iterable[tuple[str, ...]],
    components: Iterable[tuple[str, ...]],
) -> dict[str, _EvidenceBundle]:
    data_positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    component_positions = {
        name: index for index, name in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    result: dict[str, tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]]]] = {}
    for row in rows:
        record_type = row[data_positions["record_type"]]
        order_id = row[data_positions["order_id"]]
        if record_type not in {"ORDER", "SKU"} or not order_id:
            continue
        result.setdefault(order_id, (Counter(), Counter()))[0][
            _bundle_values(
                row,
                positions=data_positions,
                fields=_ORDER_BUNDLE_FIELDS,
                monetary_fields=_ORDER_BUNDLE_MONETARY_FIELDS,
            )
        ] += 1
    for row in components:
        order_id = row[component_positions["order_id"]]
        if not order_id:
            continue
        result.setdefault(order_id, (Counter(), Counter()))[1][
            _bundle_values(
                row,
                positions=component_positions,
                fields=_COMPONENT_BUNDLE_FIELDS,
                monetary_fields=_COMPONENT_BUNDLE_MONETARY_FIELDS,
            )
        ] += 1
    return result


def _bundle_values(
    row: Sequence[object],
    *,
    positions: Mapping[str, int],
    fields: Sequence[str],
    monetary_fields: frozenset[str],
) -> tuple[object, ...]:
    """Build one comparison tuple without changing stored source evidence.

    The active Statement duplicate contract compares complete evidence bundles.
    Money has numeric, rather than lexical-scale, identity at that boundary so
    a source ``107.4`` and a PostgreSQL ``NUMERIC(12,2)`` readback of
    ``107.40`` remain the same fact.  All non-monetary provenance remains
    byte-for-byte under the existing serializer contract.
    """

    return tuple(
        _canonicalize_bundle_monetary_value(row[positions[field]])
        if field in monetary_fields
        else row[positions[field]]
        for field in fields
    )


def _canonicalize_bundle_monetary_value(value: object) -> object:
    """Return plain Decimal identity text, preserving null/blank/invalid values.

    This is deliberately comparison-only: it neither rounds nor mutates the
    parser, database representation, persisted values, or display formatting.
    Invalid values retain their existing value so upstream validation and the
    fail-closed bundle comparison keep their current behavior.
    """

    if value is None or value == "":
        return value
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if not amount.is_finite():
        return value
    if amount == 0:
        return "0"
    return format(amount.normalize(), "f")


def _committed_bundle_map(
    rows: Iterable[tuple[str, ...]],
    components: Iterable[tuple[str, ...]],
    *,
    persisted_platform: str,
    service_fee_component_headers_by_batch: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[_CommittedBundleCandidate, ...]]:
    data_positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    component_positions = {
        name: index for index, name in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    batch_rows: dict[str, list[tuple[str, ...]]] = {}
    batch_components: dict[str, list[tuple[str, ...]]] = {}
    for row in rows:
        if (
            row[data_positions["commit_status"]] == "COMMITTED"
            and row[data_positions["platform"]] == persisted_platform
        ):
            batch_rows.setdefault(row[data_positions["statement_batch_id"]], []).append(row)
    for row in components:
        if (
            row[component_positions["commit_status"]] == "COMMITTED"
            and row[component_positions["platform"]] == persisted_platform
        ):
            batch_components.setdefault(
                row[component_positions["statement_batch_id"]], []
            ).append(row)
    result: dict[str, list[_CommittedBundleCandidate]] = {}
    for batch_id in batch_rows.keys() | batch_components.keys():
        header_evidence = service_fee_component_headers_by_batch.get(batch_id)
        normalized_headers = (
            frozenset(header.strip() for header in header_evidence if header.strip())
            if header_evidence is not None
            else None
        )
        for order_id, bundle in _bundle_map(
            batch_rows.get(batch_id, ()), batch_components.get(batch_id, ())
        ).items():
            result.setdefault(order_id, []).append(
                _CommittedBundleCandidate(bundle, normalized_headers)
            )
    return {order_id: tuple(bundles) for order_id, bundles in result.items()}


def _service_fee_component_headers(
    statement: ParsedShopeeWeeklyStatement,
) -> frozenset[str] | None:
    headers = statement.service_fee_component_columns
    if headers is None:
        return None
    return frozenset(header.strip() for header in headers if header.strip())


def _bundles_are_semantically_equivalent(
    incoming: _EvidenceBundle,
    committed: _CommittedBundleCandidate,
    *,
    incoming_service_fee_component_headers: frozenset[str] | None,
) -> bool:
    """Compare complete bundles without turning general missing evidence into zero.

    A source-export schema can legitimately drop a dynamic Service Fee column.
    It is equivalent only to the opposite source's zero-valued detail fact when
    that source's complete header set proves the column is absent.  No stored
    fact is changed, and unknown committed header provenance remains strict.
    """

    incoming_rows, incoming_components = incoming
    committed_rows, committed_components = committed.bundle
    if incoming_rows != committed_rows:
        return False
    return _component_multisets_are_semantically_equivalent(
        incoming_components,
        committed_components,
        incoming_service_fee_component_headers=incoming_service_fee_component_headers,
        committed_service_fee_component_headers=committed.service_fee_component_headers,
    )


def _component_multisets_are_semantically_equivalent(
    incoming: Counter[tuple[str, ...]],
    committed: Counter[tuple[str, ...]],
    *,
    incoming_service_fee_component_headers: frozenset[str] | None,
    committed_service_fee_component_headers: frozenset[str] | None,
) -> bool:
    if incoming == committed:
        return True
    incoming_remaining = incoming.copy()
    committed_remaining = committed.copy()
    _discard_proven_absent_dynamic_service_fee_zeroes(
        committed_remaining,
        incoming_service_fee_component_headers,
    )
    _discard_proven_absent_dynamic_service_fee_zeroes(
        incoming_remaining,
        committed_service_fee_component_headers,
    )
    return incoming_remaining == committed_remaining


def _discard_proven_absent_dynamic_service_fee_zeroes(
    components: Counter[tuple[str, ...]],
    opposite_service_fee_component_headers: frozenset[str] | None,
) -> None:
    """Remove only zero Service Fee detail facts whose opposite header is proven absent."""

    if opposite_service_fee_component_headers is None:
        return
    for component in tuple(components):
        record_type, source_sheet, _, _, component_name, amount, *_ = component
        if (
            record_type == "SERVICE_FEE_DETAIL"
            and source_sheet == "Service Fee Details"
            and component_name not in opposite_service_fee_component_headers
            and _canonicalize_bundle_monetary_value(amount) == "0"
        ):
            del components[component]


def _bundle_shape(
    bundle: tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]]]
) -> tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]]]:
    rows, components = bundle
    row_shape = Counter((value[0], value[1], value[7]) for value, count in rows.items() for _ in range(count))
    component_shape = Counter((value[0], value[1], value[2], value[3], value[4]) for value, count in components.items() for _ in range(count))
    return row_shape, component_shape


def _classify_adjustment_events(
    incoming: Iterable[CanonicalOrderAdjustment],
    committed: Iterable[CanonicalOrderAdjustment],
) -> tuple[tuple[CanonicalOrderAdjustment, ...], int, int]:
    existing_by_fingerprint = {
        event.adjustment_event_fingerprint: event for event in committed
    }
    existing_by_identity: dict[tuple[str, str, str, date], set[str]] = {}
    for event in committed:
        key = (
            event.platform, event.linked_order_id, event.adjustment_type,
            event.adjustment_complete_date,
        )
        existing_by_identity.setdefault(key, set()).add(event.adjustment_event_fingerprint)
    new: list[CanonicalOrderAdjustment] = []
    skipped = 0
    conflicts = 0
    seen: set[str] = set()
    for event in incoming:
        fingerprint = event.adjustment_event_fingerprint
        if fingerprint in seen or fingerprint in existing_by_fingerprint:
            skipped += 1
            continue
        seen.add(fingerprint)
        key = (
            event.platform, event.linked_order_id, event.adjustment_type,
            event.adjustment_complete_date,
        )
        if existing_by_identity.get(key):
            conflicts += 1
        else:
            new.append(event)
    return tuple(new), skipped, conflicts


def _active_write_plan(
    plan: StatementCommitPlan,
    *,
    new_order_ids: Iterable[str],
    new_adjustments: tuple[CanonicalOrderAdjustment, ...],
) -> StatementCommitPlan:
    order_ids = set(new_order_ids)
    adjustment_sequences = {event.statement_sequence_no for event in new_adjustments}
    data_positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    component_positions = {
        name: index for index, name in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    rows = tuple(
        row for row in plan.rows
        if (
            row[data_positions["record_type"]] in {"ORDER", "SKU"}
            and row[data_positions["order_id"]] in order_ids
        ) or (
            row[data_positions["record_type"]] == "ADJUSTMENT"
            and row[data_positions["sequence_no"]] in adjustment_sequences
        )
    )
    components = tuple(
        row for row in plan.financial_component_rows
        if row[component_positions["order_id"]] in order_ids
    )
    has_new_facts = bool(rows or new_adjustments)
    return replace(
        plan,
        rows=rows,
        financial_component_rows=components,
        summary_rows=plan.summary_rows if has_new_facts else (),
        order_adjustments=new_adjustments,
        order_comparisons=tuple(
            comparison for comparison in plan.order_comparisons
            if comparison.order_id in order_ids
        ),
    )


def validate_current_statement_state(
    plan: StatementCommitPlan,
    state: StatementCommitState,
    *,
    sku_matching_is_current: bool,
) -> tuple[str, ...]:
    """Validate a fresh authoritative snapshot immediately before a write."""

    reasons = []
    references = tuple(
        reference
        for reference in state.committed_statements
        if reference.commit_status == "COMMITTED"
    )
    if any(reference.file_hash == plan.statement.file_hash for reference in references):
        reasons.append("ALREADY_IMPORTED")
    elif any(
        reference.statement_period_from == plan.statement.statement_period_from
        and reference.statement_period_to == plan.statement.statement_period_to
        for reference in references
    ):
        reasons.append("POSSIBLE_REVISION")

    if plan.invoice_snapshot_sha256 is not None:
        order_ids = {row.order_id for row in plan.statement.order_rows}
        insert_orders = {order.order_id: order for order in plan.invoice_order_inserts}
        insert_items = tuple(plan.invoice_item_inserts)
        for order_id in insert_orders:
            if order_id in state.orders:
                reasons.append(f"INVOICE_INSERT_CONFLICT:{order_id}")
        current_orders = tuple(
            state.orders.get(order_id) or insert_orders[order_id]
            for order_id in sorted(order_ids)
            if order_id in state.orders or order_id in insert_orders
        )
        current_items = tuple(
            item
            for item in state.items
            if item.platform == "Shopee" and item.order_id in order_ids
        ) + tuple(
            replace(
                item,
                statement_product_price=None,
                statement_refund_amount=None,
                statement_net_selling_amount=None,
            )
            for item in insert_items
            if item.order_id in order_ids
        )
        if len(current_orders) != len(order_ids):
            missing = sorted(order_ids - set(state.orders) - set(insert_orders))
            reasons.extend(f"UNMATCHED_ORDER:{order_id}" for order_id in missing)
        elif invoice_snapshot_sha256(current_orders, current_items) != plan.invoice_snapshot_sha256:
            reasons.append("INVOICE_SNAPSHOT_CHANGED")
    else:
        for comparison in plan.order_comparisons:
            current_order = state.orders.get(comparison.order_id)
            if current_order is None:
                reasons.append(f"UNMATCHED_ORDER:{comparison.order_id}")
                continue
            current = _compare_order_by_id(
                comparison.order_id,
                plan.statement.order_rows,
                current_order,
            )
            if (
                current.comparison_source != comparison.comparison_source
                or current.comparison_amount != comparison.comparison_amount
                or current.difference != comparison.difference
                or current.reconciliation_status != comparison.reconciliation_status
            ):
                reasons.append(f"COMPARISON_STATE_CHANGED:{comparison.order_id}")
    if plan.invoice_snapshot_sha256 is None and not sku_matching_is_current:
        reasons.append("SKU_MATCHING_STATE_CHANGED")
    return tuple(reasons)


def write_statement_plan_if_current(
    plan: StatementCommitPlan,
    *,
    reload_state: Callable[[], StatementCommitState],
    sku_matching_is_current: Callable[[], bool],
    writer: StatementAtomicWriter,
    commit_lock: ApplicationCommitLock = APPLICATION_COMMIT_LOCK,
) -> StatementCommitAttempt:
    """Commit only while the single-instance lock covers preflight and write."""

    with commit_lock.acquire():
        reasons = validate_current_statement_state(
            plan,
            reload_state(),
            sku_matching_is_current=sku_matching_is_current(),
        )
        if reasons:
            return StatementCommitAttempt(False, reasons)
        try:
            writer.write_statement_batch(plan)
        except StatementWriteNotApplied:
            return StatementCommitAttempt(False, ("WRITE_NOT_APPLIED",))
        return StatementCommitAttempt(True, ())


def _compare_order(
    row: SettlementIncomeRow,
    order: CanonicalInvoiceOrder | None,
) -> StatementOrderComparison:
    if row.total_released_amount is None:
        return StatementOrderComparison(
            row.order_id, None, None, None, MISSING_COMPARISON_EVIDENCE,
            row.payout_completed_date,
        )
    decision = compare_statement_order(
        invoice_order_found=order is not None,
        released_amount=row.total_released_amount,
        final_amount=order.final_amount if order else None,
        order_income=order.order_income if order else None,
        income_type=order.income_type if order else None,
    )
    return StatementOrderComparison(
        order_id=row.order_id,
        comparison_source=decision.comparison_source,
        comparison_amount=decision.comparison_amount,
        difference=decision.difference,
        reconciliation_status=decision.reconciliation_status,
        payout_completed_date=row.payout_completed_date,
    )


def _validate_statement_row_identity(statement: ParsedShopeeWeeklyStatement) -> None:
    identities = [
        (record_type, row.sequence_no)
        for record_type, rows in (
            ("ORDER", statement.order_rows),
            ("SKU", statement.sku_rows),
            ("ADJUSTMENT", statement.adjustments),
        )
        for row in rows
    ]
    blank = [record_type for record_type, sequence_no in identities if not sequence_no]
    if blank:
        raise StatementCommitBlocked(
            "Statement source sequence is missing for record type(s): "
            + ", ".join(sorted(set(blank)))
        )
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    if duplicates:
        raise StatementCommitBlocked(
            "Statement stable row identity is duplicated: " + ", ".join(
                f"{record_type}/{sequence_no}" for record_type, sequence_no in duplicates
            )
        )


def _compare_order_by_id(
    order_id: str,
    rows: Sequence[SettlementIncomeRow],
    order: CanonicalInvoiceOrder,
) -> StatementOrderComparison:
    row = next(row for row in rows if row.order_id == order_id)
    return _compare_order(row, order)


def _common(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    record_type: str,
    sequence_no: str,
    source_row: int,
    *,
    persisted_platform: str = "Shopee",
) -> dict[str, object]:
    return {
        "statement_batch_id": audit.statement_batch_id,
        "record_type": record_type,
        "sequence_no": sequence_no,
        "platform": persisted_platform,
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
        "statement_source_row_number": source_row,
        "committed_at": audit.committed_at,
    }


def _order_row(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    row: SettlementIncomeRow,
    comparison: StatementOrderComparison,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[str, ...]:
    values = _common(
        statement, audit, "ORDER", row.sequence_no, row.source_row_number,
        persisted_platform=persisted_platform,
    )
    values.update({
        "order_id": row.order_id, "order_creation_date": row.order_creation_date,
        "payout_completed_date": row.payout_completed_date, "release_channel": row.release_channel,
        "order_type": row.order_type, "total_released_amount": row.total_released_amount,
        "statement_product_id": row.product_id, "statement_product_name": row.product_name,
        "statement_product_price": row.financial_components.get("Product Price"),
        "statement_refund_amount": row.financial_components.get("Refund Amount"),
        "comparison_source": comparison.comparison_source,
        "comparison_amount": comparison.comparison_amount, "difference": comparison.difference,
        "reconciliation_status": comparison.reconciliation_status,
    })
    return _serialize_statement_row(values)


def _sku_row(statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit, row: SettlementIncomeRow, match: StatementItemMatch, product_price: Decimal, refund_amount: Decimal) -> tuple[str, ...]:
    values = _common(statement, audit, "SKU", row.sequence_no, row.source_row_number)
    values.update({
        "order_id": row.order_id, "order_creation_date": row.order_creation_date,
        "payout_completed_date": row.payout_completed_date, "release_channel": row.release_channel,
        "order_type": row.order_type, "total_released_amount": row.total_released_amount,
        "statement_product_id": row.product_id, "statement_product_name": row.product_name,
        "statement_product_price": product_price, "statement_refund_amount": refund_amount,
        "statement_net_selling_amount": product_price + refund_amount,
        "matched_item_index": match.invoice_item_index, "match_method": _required_business_match_method(match.match_method),
    })
    return _serialize_statement_row(values)


def _v2_sku_row(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    row: SettlementIncomeRow,
    *,
    matched_item_index: int | None,
    match_method: str,
    product_price: Decimal,
    refund_amount: Decimal,
    persisted_platform: str = "Shopee",
) -> tuple[str, ...]:
    values = _common(
        statement,
        audit,
        "SKU",
        row.sequence_no,
        row.source_row_number,
        persisted_platform=persisted_platform,
    )
    values.update({
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
        "statement_net_selling_amount": product_price + refund_amount,
        "matched_item_index": matched_item_index,
        "match_method": match_method,
    })
    return _serialize_statement_row(values)


def _unallocated_sku_row(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    row: SettlementIncomeRow,
    *,
    persisted_platform: str,
) -> tuple[str, ...]:
    """Persist raw SKU facts while deliberately leaving allocation fields blank."""

    values = _common(
        statement, audit, "SKU", row.sequence_no, row.source_row_number,
        persisted_platform=persisted_platform,
    )
    product_price = row.financial_components.get("Product Price")
    refund_amount = row.financial_components.get("Refund Amount")
    values.update({
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
            if product_price is not None and refund_amount is not None else None
        ),
    })
    return _serialize_statement_row(values)


def _required_sku_money(row: SettlementIncomeRow) -> tuple[Decimal, Decimal]:
    product_price = row.financial_components.get("Product Price")
    refund_amount = row.financial_components.get("Refund Amount")
    if product_price is None or refund_amount is None:
        raise StatementCommitBlocked(
            f"Statement SKU source row {row.source_row_number} lacks Product Price "
            "or Refund Amount."
        )
    return product_price, refund_amount


def _v2_item_match_method(method: str) -> str:
    labels = {
        "PRODUCT_MASTER_SKU": "Product Master SKU",
        "PRODUCT_MASTER_PARENT_SKU": "Product Master Parent SKU",
        "PRODUCT_MASTER_NAME_VARIATION": "Product Master Name + Variation",
        "EXACT_SELLER_SKU": "Exact Seller SKU",
        "EXACT_PRODUCT_NAME": "Product Name",
        "SAFE_NAME_NORMALIZATION": "Safe Product Name Normalization",
        "VERIFIED_ARTIFACT_REPAIR": "Verified Source Artifact Repair",
        "PRODUCT_MASTER_NAME_CONFIRMATION": "Product Master Name Confirmation",
    }
    tokens = tuple(token for token in method.split("|") if token)
    if not tokens or any(token not in labels for token in tokens):
        raise StatementCommitBlocked(
            f"Unsupported V2 ITEM match method: {method!r}."
        )
    return " + ".join(labels[token] for token in tokens)


def _adjustment_row(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    adjustment: SettlementAdjustment,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[str, ...]:
    values = _common(
        statement, audit, "ADJUSTMENT", adjustment.sequence_no,
        adjustment.source_row_number, persisted_platform=persisted_platform,
    )
    values.update({
        "linked_order_id": adjustment.linked_order_id,
        "payout_completed_date": adjustment.payout_completed_date,
        "adjustment_complete_date": adjustment.adjustment_complete_date,
        "adjustment_type": adjustment.adjustment_type,
        "adjustment_reason": adjustment.adjustment_reason,
        "adjustment_amount": adjustment.adjustment_amount,
    })
    return _serialize_statement_row(values)


def _order_adjustment_events(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[CanonicalOrderAdjustment, ...]:
    """Create canonical events for usable source descriptions; retain every raw row."""
    events: list[CanonicalOrderAdjustment] = []
    for adjustment in statement.adjustments:
        if supported_adjustment_type(adjustment.adjustment_type) is None:
            continue
        if adjustment.adjustment_complete_date is None or adjustment.adjustment_amount is None:
            raise StatementCommitBlocked(
                "Canonical Statement Adjustment lacks required date or amount."
            )
        try:
            event = make_statement_adjustment(
                linked_order_id=adjustment.linked_order_id,
                adjustment_description=adjustment.adjustment_type,
                adjustment_reason=adjustment.adjustment_reason,
                adjustment_complete_date=adjustment.adjustment_complete_date,
                adjustment_amount=adjustment.adjustment_amount,
                payout_completed_date=adjustment.payout_completed_date,
                statement_batch_id=audit.statement_batch_id,
                statement_sequence_no=adjustment.sequence_no,
                statement_source_filename=statement.source_filename,
                statement_file_hash=statement.file_hash,
                first_observed_at=audit.committed_at,
                persisted_platform=persisted_platform,
            )
        except ValueError as error:
            raise StatementCommitBlocked(str(error)) from error
        if event is not None:
            events.append(event)
    return tuple(events)


def serialize_order_adjustment(event: CanonicalOrderAdjustment) -> tuple[str, ...]:
    """Serialize the approved 21-column event schema without Invoice fields."""
    values = {
        "platform": event.platform,
        "linked_order_id": event.linked_order_id,
        "adjustment_type": event.adjustment_type,
        "adjustment_description": event.adjustment_description,
        "adjustment_reason": event.adjustment_reason,
        "adjustment_complete_date": event.adjustment_complete_date,
        "adjustment_amount": event.adjustment_amount,
        "payout_completed_date": event.payout_completed_date,
        "statement_batch_id": event.statement_batch_id,
        "statement_sequence_no": event.statement_sequence_no,
        "statement_source_filename": event.statement_source_filename,
        "statement_file_hash": event.statement_file_hash,
        "adjustment_event_fingerprint": event.adjustment_event_fingerprint,
        "evidence_status": event.evidence_status.value,
        "invoice_evidence_date": event.invoice_evidence_date,
        "invoice_evidence_amount": event.invoice_evidence_amount,
        "invoice_evidence_final_amount": event.invoice_evidence_final_amount,
        "invoice_evidence_reason": event.invoice_evidence_reason,
        "invoice_source_pdf": event.invoice_source_pdf,
        "invoice_source_hash": event.invoice_source_hash,
        "first_observed_at": event.first_observed_at,
    }
    return tuple(_serialize(values.get(header)) for header in ORDER_ADJUSTMENTS_HEADERS)


def _required_business_match_method(method: str | None) -> str:
    display = business_match_method(method)
    if display is None:
        raise StatementCommitBlocked(f"Unsupported business match method: {method!r}.")
    return display


def _serialize_statement_row(values: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(_serialize(values.get(header)) for header in STATEMENT_DATA_HEADERS)


def serialize_statement_values(
    values: Mapping[str, object], headers: Sequence[str]
) -> tuple[str, ...]:
    """Serialize shared Weekly/Monthly source facts with one exact contract."""

    return tuple(_serialize(values.get(header)) for header in headers)


def build_statement_summary_rows(
    statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit
) -> tuple[tuple[str, ...], ...]:
    return _summary_rows(statement, audit)


def build_statement_financial_component_rows(
    statement: ParsedShopeeWeeklyStatement, audit: StatementBatchAudit
) -> tuple[tuple[str, ...], ...]:
    return _financial_component_rows(statement, audit)


_SUMMARY_LINE_TYPES = {
    "TOTAL", "SUBTOTAL", "DETAIL", "REFERENCE", "SECTION_HEADER",
}


def _summary_rows(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[tuple[str, ...], ...]:
    if not statement.summary_lines:
        raise StatementCommitBlocked("Statement has no parsed native Summary lines.")
    rows = tuple(
        _summary_row(statement, audit, line, persisted_platform=persisted_platform)
        for line in statement.summary_lines
    )
    _validate_summary_rows(rows)
    return rows


def _summary_row(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    line: StatementSummaryLine,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[str, ...]:
    values = {
        "statement_batch_id": audit.statement_batch_id,
        "statement_file_hash": statement.file_hash,
        "platform": persisted_platform,
        "statement_period_from": statement.statement_period_from,
        "statement_period_to": statement.statement_period_to,
        "statement_source_sheet": line.statement_source_sheet,
        "statement_source_row_number": line.statement_source_row_number,
        "native_label": line.native_label,
        "line_type": line.line_type,
        "parent_source_row_number": line.parent_source_row_number,
        "component_amount": line.component_amount,
        "currency": line.currency,
        "committed_at": audit.committed_at,
        "commit_status": "COMMITTED",
    }
    return tuple(_serialize(values.get(header)) for header in STATEMENT_SUMMARY_HEADERS)


def _validate_summary_rows(rows: Sequence[tuple[str, ...]]) -> None:
    positions = {
        header: index for index, header in enumerate(STATEMENT_SUMMARY_HEADERS)
    }
    identities: set[tuple[str, str, str]] = set()
    for row in rows:
        if len(row) != len(STATEMENT_SUMMARY_HEADERS):
            raise StatementCommitBlocked(
                "Statement Summary rows do not match the exact approved schema."
            )
        identity = tuple(
            row[positions[header]]
            for header in (
                "statement_batch_id", "statement_source_sheet",
                "statement_source_row_number",
            )
        )
        if not all(identity):
            raise StatementCommitBlocked("Statement Summary row has incomplete stable identity.")
        if identity in identities:
            raise StatementCommitBlocked(
                "Statement Summary plan contains duplicate stable identity: "
                + "/".join(identity)
            )
        identities.add(identity)
        line_type = row[positions["line_type"]]
        amount = row[positions["component_amount"]]
        if line_type not in _SUMMARY_LINE_TYPES:
            raise StatementCommitBlocked(f"Unsupported Statement Summary line type: {line_type!r}.")
        if line_type == "SECTION_HEADER":
            if amount:
                raise StatementCommitBlocked("Statement Summary section header must not contain an amount.")
        elif not amount:
            raise StatementCommitBlocked("Statement Summary monetary row is missing an amount.")
        if not row[positions["native_label"]] or not row[positions["currency"]]:
            raise StatementCommitBlocked("Statement Summary row lacks native label or currency.")


_COMPONENT_RECORD_TYPES = {
    "ORDER",
    "SKU",
    "SERVICE_FEE_DETAIL",
    "SHIPPING_FEE_DISCREPANCY",
}


def _financial_component_rows(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for income_row in statement.income_rows:
        record_type = {"Order": "ORDER", "Sku": "SKU"}.get(income_row.view_by)
        if record_type is None:
            continue
        for component_name in statement.income_component_columns:
            amount = income_row.financial_components.get(component_name)
            if amount is None:
                continue
            rows.append(_financial_component_row(
                statement=statement,
                audit=audit,
                record_type=record_type,
                source_sheet="Income",
                source_row_number=income_row.source_row_number,
                sequence_no=income_row.sequence_no,
                order_id=income_row.order_id,
                statement_product_id=income_row.product_id,
                component_name=component_name,
                component_amount=amount,
                component_note=None,
                payout_completed_date=income_row.payout_completed_date,
                persisted_platform=persisted_platform,
            ))
    for detail in statement.service_fee_details:
        rows.extend(_service_fee_component_rows(
            statement, audit, detail, persisted_platform=persisted_platform
        ))
    for discrepancy in statement.shipping_fee_discrepancies:
        rows.extend(_shipping_discrepancy_component_rows(
            statement, audit, discrepancy, persisted_platform=persisted_platform
        ))
    _validate_financial_component_rows(rows, statement)
    return tuple(rows)


def _service_fee_component_rows(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    detail: ServiceFeeDetail,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        _financial_component_row(
            statement=statement,
            audit=audit,
            record_type="SERVICE_FEE_DETAIL",
            source_sheet="Service Fee Details",
            source_row_number=detail.source_row_number,
            sequence_no=detail.sequence_no,
            order_id=detail.order_id,
            statement_product_id=None,
            component_name=component_name,
            component_amount=amount,
            component_note=None,
            payout_completed_date=None,
            persisted_platform=persisted_platform,
        )
        for component_name, amount in detail.components.items()
        if amount is not None
    )


def _shipping_discrepancy_component_rows(
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    discrepancy: ShippingFeeDiscrepancy,
    *,
    persisted_platform: str = "Shopee",
) -> tuple[tuple[str, ...], ...]:
    values: list[tuple[str, ...]] = []
    for component_name, amount in (
        ("Expected Shipping Fee:", discrepancy.expected_shipping_fee),
        (
            "Actual Shipping Fee Charged by Logistic Provider:",
            discrepancy.actual_shipping_fee,
        ),
    ):
        if amount is not None:
            values.append(_financial_component_row(
                statement=statement,
                audit=audit,
                record_type="SHIPPING_FEE_DISCREPANCY",
                source_sheet="Shipping Fee Discrepancy",
                source_row_number=discrepancy.source_row_number,
                sequence_no=None,
                order_id=discrepancy.order_id,
                statement_product_id=None,
                component_name=component_name,
                component_amount=amount,
                component_note=None,
                payout_completed_date=None,
                persisted_platform=persisted_platform,
            ))
    if discrepancy.reason:
        values.append(_financial_component_row(
            statement=statement,
            audit=audit,
            record_type="SHIPPING_FEE_DISCREPANCY",
            source_sheet="Shipping Fee Discrepancy",
            source_row_number=discrepancy.source_row_number,
            sequence_no=None,
            order_id=discrepancy.order_id,
            statement_product_id=None,
            component_name="Discrepancy reason",
            component_amount=None,
            component_note=discrepancy.reason,
            payout_completed_date=None,
            persisted_platform=persisted_platform,
        ))
    return tuple(values)


def _financial_component_row(
    *,
    statement: ParsedShopeeWeeklyStatement,
    audit: StatementBatchAudit,
    record_type: str,
    source_sheet: str,
    source_row_number: int,
    sequence_no: str | None,
    order_id: str | None,
    statement_product_id: str | None,
    component_name: str,
    component_amount: Decimal | None,
    component_note: str | None,
    payout_completed_date: date | None,
    persisted_platform: str = "Shopee",
) -> tuple[str, ...]:
    values = {
        "statement_batch_id": audit.statement_batch_id,
        "statement_file_hash": statement.file_hash,
        "record_type": record_type,
        "statement_source_sheet": source_sheet,
        "statement_source_row_number": source_row_number,
        "sequence_no": sequence_no,
        "platform": persisted_platform,
        "order_id": order_id,
        "statement_product_id": statement_product_id,
        "component_name": component_name,
        "component_amount": component_amount,
        "component_note": component_note,
        "statement_period_from": statement.statement_period_from,
        "statement_period_to": statement.statement_period_to,
        "payout_completed_date": payout_completed_date,
        "committed_at": audit.committed_at,
        "commit_status": "COMMITTED",
    }
    return tuple(
        _serialize(values.get(header))
        for header in STATEMENT_FINANCIAL_COMPONENT_HEADERS
    )


def _validate_financial_component_rows(
    rows: Sequence[tuple[str, ...]], statement: ParsedShopeeWeeklyStatement
) -> None:
    positions = {
        header: index
        for index, header in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    identities: set[tuple[str, str, str, str]] = set()
    for row in rows:
        if len(row) != len(STATEMENT_FINANCIAL_COMPONENT_HEADERS):
            raise StatementCommitBlocked("Financial component rows do not match the exact ledger schema.")
        record_type = row[positions["record_type"]]
        if record_type not in _COMPONENT_RECORD_TYPES:
            raise StatementCommitBlocked(f"Unsupported financial component record type: {record_type!r}.")
        identity = tuple(
            row[positions[header]]
            for header in (
                "statement_batch_id",
                "statement_source_sheet",
                "statement_source_row_number",
                "component_name",
            )
        )
        if not all(identity):
            raise StatementCommitBlocked("Financial component row has incomplete stable identity.")
        if identity in identities:
            raise StatementCommitBlocked(
                "Financial component plan contains duplicate stable identity: "
                + "/".join(identity)
            )
        identities.add(identity)
        amount = row[positions["component_amount"]]
        note = row[positions["component_note"]]
        if not amount and not note:
            raise StatementCommitBlocked("Financial component row has neither numeric amount nor text evidence.")
        if note and record_type != "SHIPPING_FEE_DISCREPANCY":
            raise StatementCommitBlocked("Text financial component evidence is only supported for Shipping Fee Discrepancy.")
        source_sheet = row[positions["statement_source_sheet"]]
        sequence_no = row[positions["sequence_no"]]
        order_id = row[positions["order_id"]]
        if record_type in {"ORDER", "SKU"}:
            expected_view = "Order" if record_type == "ORDER" else "Sku"
            if source_sheet != "Income" or not sequence_no or not order_id:
                raise StatementCommitBlocked("Income financial component row lacks traceable source identity.")
            if not any(
                source_row.view_by == expected_view
                and source_row.sequence_no == sequence_no
                and str(source_row.source_row_number)
                == row[positions["statement_source_row_number"]]
                for source_row in statement.income_rows
            ):
                raise StatementCommitBlocked("Income financial component row cannot be traced to a Statement source row.")
        elif record_type == "SERVICE_FEE_DETAIL":
            if source_sheet != "Service Fee Details" or not sequence_no or not order_id:
                raise StatementCommitBlocked("Service Fee Detail component row lacks traceable source identity.")
            if not any(
                detail.sequence_no == sequence_no
                and detail.order_id == order_id
                and str(detail.source_row_number)
                == row[positions["statement_source_row_number"]]
                for detail in statement.service_fee_details
            ):
                raise StatementCommitBlocked(
                    "Service Fee Detail component row cannot be traced to a Statement source row."
                )
        elif (
            source_sheet != "Shipping Fee Discrepancy"
            or not order_id
            or not any(
                discrepancy.order_id == order_id
                and str(discrepancy.source_row_number)
                == row[positions["statement_source_row_number"]]
                for discrepancy in statement.shipping_fee_discrepancies
            )
        ):
            raise StatementCommitBlocked(
                "Shipping discrepancy component row cannot be traced to a Statement source row."
            )


def _serialize(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def invoice_snapshot_sha256(
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
) -> str:
    """Hash the exact persisted Invoice evidence used by a V2 review/plan."""

    order_payload = [
        asdict(order)
        for order in sorted(invoice_orders, key=lambda value: value.order_id)
    ]
    item_payload = [
        asdict(item)
        for item in sorted(
            invoice_items,
            key=lambda value: (value.order_id, value.item_index),
        )
    ]
    payload = json.dumps(
        {"orders": order_payload, "items": item_payload},
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()
