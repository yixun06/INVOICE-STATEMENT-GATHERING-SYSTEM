"""Immutable, persistence-neutral evidence for Shopee Reconciliation V2."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class IdentityScope(str, Enum):
    ITEM = "ITEM"
    GROUP = "GROUP"
    UNRESOLVED = "UNRESOLVED"


class SettlementBasis(str, Enum):
    EXACT = "EXACT"
    EXPLAINED = "EXPLAINED"
    NONE = "NONE"


class ReconciliationReason(str, Enum):
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    PRODUCT_MASTER_IDENTITY_MISSING = "PRODUCT_MASTER_IDENTITY_MISSING"
    ITEM_ALLOCATION_UNRESOLVED = "ITEM_ALLOCATION_UNRESOLVED"
    MERCHANDISE_DIFFERENCE = "MERCHANDISE_DIFFERENCE"
    SETTLEMENT_UNEXPLAINED_DIFFERENCE = "SETTLEMENT_UNEXPLAINED_DIFFERENCE"
    SOURCE_EVIDENCE_INSUFFICIENT = "SOURCE_EVIDENCE_INSUFFICIENT"


class NameConsistency(str, Enum):
    EXACT = "EXACT"
    SAFE_NORMALIZATION_DIFFERENCE = "SAFE_NORMALIZATION_DIFFERENCE"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"


@dataclass(frozen=True, order=True)
class StatementMemberRef:
    statement_file_hash: str
    source_sheet: str
    source_row_number: int
    sequence_no: str


@dataclass(frozen=True, order=True)
class InvoiceItemRef:
    platform: str
    order_id: str
    item_index: int


@dataclass(frozen=True, order=True)
class ProductFamilyIdentity:
    product_id: str
    seller_sku: str
    parent_sku: str
    product_name: str
    variation: str
    nav_code: str = ""
    unit_selling_price: Decimal | None = None


@dataclass(frozen=True)
class ProductFamilySnapshot:
    candidates: tuple[ProductFamilyIdentity, ...]
    sha256: str


@dataclass(frozen=True)
class CompatibleEdgeEvidence:
    statement_member: StatementMemberRef
    invoice_member: InvoiceItemRef
    match_method: str
    name_consistency: NameConsistency


@dataclass(frozen=True)
class IdentityEvidence:
    identity_scope: IdentityScope
    product_id: str
    statement_members: tuple[StatementMemberRef, ...]
    invoice_members: tuple[InvoiceItemRef, ...]
    compatible_edges: tuple[CompatibleEdgeEvidence, ...]
    selected_pairs: tuple[tuple[StatementMemberRef, InvoiceItemRef], ...]
    perfect_matching_count: int
    allocation_resolved: bool
    diagnostic: str


@dataclass(frozen=True)
class CoverageEvidence:
    expected_statement_members: tuple[StatementMemberRef, ...]
    expected_invoice_members: tuple[InvoiceItemRef, ...]
    covered_statement_members: tuple[StatementMemberRef, ...]
    covered_invoice_members: tuple[InvoiceItemRef, ...]
    duplicate_invoice_consumption: tuple[InvoiceItemRef, ...]
    overlapping_item_group_members: tuple[InvoiceItemRef, ...]
    uncovered_statement_members: tuple[StatementMemberRef, ...]
    uncovered_invoice_members: tuple[InvoiceItemRef, ...]

    @property
    def valid(self) -> bool:
        return not (
            self.duplicate_invoice_consumption
            or self.overlapping_item_group_members
            or self.uncovered_statement_members
            or self.uncovered_invoice_members
        )


@dataclass(frozen=True)
class MerchandiseEvidence:
    scope_key: str
    invoice_value: Decimal | None
    statement_value: Decimal | None
    difference: Decimal | None
    tolerance: Decimal
    reconciled: bool
    authority: str


@dataclass(frozen=True)
class SettlementComponentEvidence:
    component: str
    invoice_value: Decimal | None
    statement_value: Decimal | None
    delta: Decimal | None
    treatment: str


@dataclass(frozen=True)
class SettlementEvidence:
    invoice_basis: Decimal | None
    statement_total: Decimal | None
    component_deltas: tuple[SettlementComponentEvidence, ...]
    raw_difference: Decimal | None
    explained_delta: Decimal | None
    unexplained_residual: Decimal | None
    tolerance: Decimal
    basis: SettlementBasis
    source_state: str
    internal_effects: tuple[str, ...]


@dataclass(frozen=True)
class RefundEvidence:
    invoice_source_exists: bool
    statement_amount: Decimal | None
    statement_members: tuple[StatementMemberRef, ...]
    strongest_scope: IdentityScope | None
    allocation_resolved: bool


@dataclass(frozen=True)
class PromotionEvidence:
    promotion_group_ids: tuple[str, ...]
    source_group_total_authority_used: bool
    allocation_resolved: bool


@dataclass(frozen=True)
class QuantityEvidence:
    invoice_quantity_available: bool
    statement_quantity_available: bool


@dataclass(frozen=True)
class ReconciliationSummary:
    identity_scope: IdentityScope
    merchandise_reconciled: bool
    settlement_basis: SettlementBasis
    allocation_resolved: bool
    reasons: tuple[ReconciliationReason, ...]

    @property
    def settlement_reconciled(self) -> bool:
        return self.settlement_basis is not SettlementBasis.NONE


@dataclass(frozen=True)
class ReconciliationEvidence:
    platform: str
    order_id: str
    invoice_source_hash: str | None
    statement_file_hash: str
    statement_period: tuple[str, str]
    product_family_snapshot_hash: str
    identities: tuple[IdentityEvidence, ...]
    coverage: CoverageEvidence
    merchandise: tuple[MerchandiseEvidence, ...]
    settlement: SettlementEvidence
    refund: RefundEvidence
    promotion: PromotionEvidence
    quantity: QuantityEvidence
    rule_version: str


@dataclass(frozen=True)
class OrderReconciliationResult:
    summary: ReconciliationSummary
    evidence: ReconciliationEvidence


@dataclass(frozen=True)
class StatementReconciliationBatch:
    orders: tuple[OrderReconciliationResult, ...]
    coverage: CoverageEvidence
    product_family_snapshot: ProductFamilySnapshot
    statement_adjustment_total: Decimal
    statement_validation_issues: tuple[str, ...]
    rule_version: str
