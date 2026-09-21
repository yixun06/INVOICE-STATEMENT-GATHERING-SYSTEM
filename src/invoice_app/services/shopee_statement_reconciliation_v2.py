"""Pure, read-only Shopee Statement reconciliation evaluator.

The evaluator deliberately has no repository, UI, or persistence dependency.  It
returns thin public status plus immutable evidence and never invents an item
allocation when the sources only prove an unordered group.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable, Mapping, Sequence

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.domain.statement_reconciliation_v2 import (
    CompatibleEdgeEvidence,
    CoverageEvidence,
    IdentityEvidence,
    IdentityScope,
    InvoiceItemRef,
    MerchandiseEvidence,
    NameConsistency,
    OrderReconciliationResult,
    ProductFamilyIdentity,
    ProductFamilySnapshot,
    PromotionEvidence,
    QuantityEvidence,
    ReconciliationEvidence,
    ReconciliationReason,
    ReconciliationSummary,
    RefundEvidence,
    SettlementBasis,
    SettlementComponentEvidence,
    SettlementEvidence,
    StatementMemberRef,
    StatementReconciliationBatch,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.services.shopee_statement_item_matching import (
    MONEY_TOLERANCE,
    ProductFamilyCandidate,
    ProductFamilyResolver,
    VerifiedArtifactRepair,
    normalize_statement_product_name,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    validate_shopee_weekly_statement,
)
from src.invoice_app.utils.normalize import normalize_sku_text


RULE_VERSION = "SHOPEE_RECONCILIATION_V2_3"

_SHIPPING_COMPONENTS = (
    "Shipping Fee Paid by Buyer (excl. SST)",
    "Shipping Fee Charged by Logistic Provider",
    "Seller Paid Shipping Fee SST",
    "Shipping Rebate From Shopee",
    "Reverse Shipping Fee",
    "Reverse Shipping Fee SST",
    "Saver Programme Shipping Fee Savings",
    "Return to Seller Fee",
)
_VOUCHER_COMPONENTS = (
    "Rebate Provided by Shopee",
    "Voucher Sponsored by Seller",
    "Cofund Voucher Sponsored by Seller",
    "Coin Cashback Sponsored by Seller",
    "Cofund Coin Cashback Sponsored by Seller",
)
_FEE_COMPONENTS = (
    "Commission Fee (incl. SST)",
    "Service Fee (Incl. SST)",
    "Transaction Fee (Incl. SST)",
    "AMS Commission Fee",
    "Saver Programme Fee (Incl. SST)",
    "Ads Escrow Top Up Fee",
)


@dataclass(frozen=True)
class _CandidateEdge:
    item: CanonicalInvoiceItem
    evidence: CompatibleEdgeEvidence
    name_method: str


@dataclass(frozen=True)
class _RowContext:
    row: SettlementIncomeRow
    member: StatementMemberRef
    family: tuple[ProductFamilyCandidate, ...]
    edges: tuple[_CandidateEdge, ...]
    preferred: _CandidateEdge | None
    diagnostic: str


def evaluate_statement_reconciliation(
    statement: ParsedShopeeWeeklyStatement,
    invoice_orders: Iterable[CanonicalInvoiceOrder],
    invoice_items: Iterable[CanonicalInvoiceItem],
    *,
    product_families: ProductFamilyResolver,
    verified_artifact_repairs: Iterable[VerifiedArtifactRepair] = (),
    tolerance: Decimal = MONEY_TOLERANCE,
) -> StatementReconciliationBatch:
    """Evaluate one parsed Statement against persisted Invoice-derived facts.

    All inputs are read-only values.  No match result is eligible for persistence
    merely because this function can prove it.
    """

    orders = tuple(invoice_orders)
    items = tuple(item for item in invoice_items if item.platform == "Shopee")
    order_map = _unique_orders(orders)
    items_by_order: dict[str, list[CanonicalInvoiceItem]] = defaultdict(list)
    for item in items:
        items_by_order[item.order_id].append(item)
    for values in items_by_order.values():
        values.sort(key=lambda value: value.item_index)

    validation = tuple(
        f"{issue.code}: {issue.message}"
        for issue in validate_shopee_weekly_statement(statement)
    )
    repairs = tuple(verified_artifact_repairs)
    family_cache: dict[str, tuple[ProductFamilyCandidate, ...]] = {}
    contexts_by_order: dict[str, list[_RowContext]] = defaultdict(list)
    for row in statement.sku_rows:
        if row.product_id not in family_cache:
            family_cache[row.product_id] = tuple(
                candidate
                for candidate in product_families.resolve(row.product_id)
                if candidate.product_id == row.product_id
            )
        family = family_cache[row.product_id]
        contexts_by_order[row.order_id].append(
            _build_row_context(
                statement.file_hash,
                row,
                items_by_order.get(row.order_id, ()),
                family,
                repairs,
                tolerance,
            )
        )

    snapshot = _product_family_snapshot(family_cache.values())
    order_rows = _order_rows_by_id(statement.order_rows)
    order_ids = sorted(set(order_map) | set(contexts_by_order) | set(order_rows))
    results: list[OrderReconciliationResult] = []
    for order_id in order_ids:
        order = order_map.get(order_id)
        order_items = tuple(items_by_order.get(order_id, ()))
        contexts = tuple(contexts_by_order.get(order_id, ()))
        identities = _evaluate_identity(contexts, order_items)
        coverage = _coverage_for_order(contexts, order_items, identities)
        merchandise = _evaluate_merchandise(
            order,
            order_rows.get(order_id),
            contexts,
            order_items,
            identities,
            tolerance,
        )
        settlement = _evaluate_settlement(
            order,
            order_rows.get(order_id),
            tolerance=tolerance,
        )
        refund = _refund_evidence(order, order_rows.get(order_id), contexts, identities)
        promotion = _promotion_evidence(order_items, identities)
        quantity = QuantityEvidence(
            invoice_quantity_available=bool(order_items)
            and all(item.quantity is not None for item in order_items),
            statement_quantity_available=False,
        )
        summary = _summarize(
            order=order,
            contexts=contexts,
            identities=identities,
            coverage=coverage,
            merchandise=merchandise,
            settlement=settlement,
            validation_issues=validation,
        )
        results.append(
            OrderReconciliationResult(
                summary=summary,
                evidence=ReconciliationEvidence(
                    platform=order.platform if order is not None else "Shopee",
                    order_id=order_id,
                    invoice_source_hash=order.source_hash if order is not None else None,
                    statement_file_hash=statement.file_hash,
                    statement_period=(
                        statement.statement_period_from.isoformat(),
                        statement.statement_period_to.isoformat(),
                    ),
                    product_family_snapshot_hash=snapshot.sha256,
                    identities=identities,
                    coverage=coverage,
                    merchandise=merchandise,
                    settlement=settlement,
                    refund=refund,
                    promotion=promotion,
                    quantity=quantity,
                    rule_version=RULE_VERSION,
                ),
            )
        )

    batch_coverage = _merge_coverage(tuple(result.evidence.coverage for result in results))
    return StatementReconciliationBatch(
        orders=tuple(results),
        coverage=batch_coverage,
        product_family_snapshot=snapshot,
        statement_adjustment_total=statement.adjustment_control_total,
        statement_validation_issues=validation,
        rule_version=RULE_VERSION,
    )


def _unique_orders(
    orders: Sequence[CanonicalInvoiceOrder],
) -> dict[str, CanonicalInvoiceOrder]:
    result: dict[str, CanonicalInvoiceOrder] = {}
    for order in orders:
        if order.platform != "Shopee":
            continue
        if order.order_id in result:
            raise ValueError(f"Duplicate Shopee Invoice order: {order.order_id}")
        result[order.order_id] = order
    return result


def _order_rows_by_id(
    rows: Sequence[SettlementIncomeRow],
) -> dict[str, SettlementIncomeRow]:
    result: dict[str, SettlementIncomeRow] = {}
    for row in rows:
        result.setdefault(row.order_id, row)
    return result


def _statement_ref(file_hash: str, row: SettlementIncomeRow) -> StatementMemberRef:
    return StatementMemberRef(file_hash, "Income", row.source_row_number, row.sequence_no)


def _invoice_ref(item: CanonicalInvoiceItem) -> InvoiceItemRef:
    return InvoiceItemRef(item.platform, item.order_id, item.item_index)


def _build_row_context(
    file_hash: str,
    row: SettlementIncomeRow,
    order_items: Sequence[CanonicalInvoiceItem],
    family: Sequence[ProductFamilyCandidate],
    repairs: Sequence[VerifiedArtifactRepair],
    tolerance: Decimal,
) -> _RowContext:
    member = _statement_ref(file_hash, row)
    if not row.product_id:
        return _RowContext(row, member, (), (), None, "Statement Product ID is missing.")
    if not family:
        return _RowContext(
            row,
            member,
            (),
            (),
            None,
            "Exact Product Master Product ID lookup returned no family candidates.",
        )

    candidate_items = _family_items(order_items, family)
    edges: list[_CandidateEdge] = []
    semantic_conflicts = 0
    for item in candidate_items:
        linked_family = _family_candidates_for_item(item, family)
        consistency, name_method = _name_consistency(
            row, item, linked_family, repairs
        )
        if consistency is NameConsistency.SEMANTIC_CONFLICT:
            semantic_conflicts += 1
            continue
        identity_method = _identity_method(item, linked_family)
        evidence = CompatibleEdgeEvidence(
            statement_member=member,
            invoice_member=_invoice_ref(item),
            match_method=f"{identity_method}|{name_method}",
            name_consistency=consistency,
        )
        edges.append(_CandidateEdge(item, evidence, name_method))

    compatible = tuple(edges)
    preferred = _preferred_edge(row, compatible, tolerance)
    if preferred is not None:
        diagnostic = "Deterministic item identity."
    elif compatible:
        diagnostic = "More than one compatible item remains; group proof is required."
    elif semantic_conflicts:
        diagnostic = "Strong family identity conflicts with Product Name evidence."
    elif not order_items:
        diagnostic = "Exact Order ID has no persisted Invoice_Items."
    else:
        diagnostic = "No persisted Invoice item matches the exact Product Master family."
    return _RowContext(row, member, tuple(family), compatible, preferred, diagnostic)


def _family_items(
    items: Sequence[CanonicalInvoiceItem],
    family: Sequence[ProductFamilyCandidate],
) -> tuple[CanonicalInvoiceItem, ...]:
    exact_skus = {
        normalize_sku_text(candidate.seller_sku)
        for candidate in family
        if normalize_sku_text(candidate.seller_sku)
    }
    exact_matches = tuple(
        item
        for item in items
        if normalize_sku_text(item.seller_sku) in exact_skus
        and normalize_sku_text(item.seller_sku)
    )
    if exact_matches:
        return exact_matches

    parent_skus = {
        normalize_sku_text(candidate.parent_sku)
        for candidate in family
        if normalize_sku_text(candidate.parent_sku)
    }
    parent_matches = tuple(
        item
        for item in items
        if normalize_sku_text(item.seller_sku) in parent_skus
        and normalize_sku_text(item.seller_sku)
    )
    if parent_matches:
        return parent_matches
    return tuple(
        item
        for item in items
        if any(
            normalize_statement_product_name(item.product_name)
            == normalize_statement_product_name(candidate.product_name)
            and normalize_statement_product_name(item.variation)
            == normalize_statement_product_name(candidate.variation)
            for candidate in family
        )
    )


def _family_candidates_for_item(
    item: CanonicalInvoiceItem,
    family: Sequence[ProductFamilyCandidate],
) -> tuple[ProductFamilyCandidate, ...]:
    item_sku = normalize_sku_text(item.seller_sku)
    exact_candidates = tuple(
        candidate
        for candidate in family
        if item_sku
        and item_sku == normalize_sku_text(candidate.seller_sku)
    )
    if exact_candidates:
        return exact_candidates

    parent_candidates = tuple(
        candidate
        for candidate in family
        if item_sku and item_sku == normalize_sku_text(candidate.parent_sku)
    )
    if parent_candidates:
        return parent_candidates
    return tuple(
        candidate
        for candidate in family
        if normalize_statement_product_name(item.product_name)
        == normalize_statement_product_name(candidate.product_name)
        and normalize_statement_product_name(item.variation)
        == normalize_statement_product_name(candidate.variation)
    )


def _identity_method(
    item: CanonicalInvoiceItem,
    candidates: Sequence[ProductFamilyCandidate],
) -> str:
    item_sku = normalize_sku_text(item.seller_sku)
    if item_sku and any(
        item_sku == normalize_sku_text(candidate.seller_sku)
        for candidate in candidates
    ):
        return "PRODUCT_MASTER_SKU"
    if item_sku and any(
        item_sku == normalize_sku_text(candidate.parent_sku)
        for candidate in candidates
    ):
        return "PRODUCT_MASTER_PARENT_SKU"
    return "PRODUCT_MASTER_NAME_VARIATION"


def _name_consistency(
    row: SettlementIncomeRow,
    item: CanonicalInvoiceItem,
    linked_family: Sequence[ProductFamilyCandidate],
    repairs: Sequence[VerifiedArtifactRepair],
) -> tuple[NameConsistency, str]:
    statement_raw = (row.product_name or "").strip()
    invoice_raw = (item.product_name or "").strip()
    if statement_raw and statement_raw == invoice_raw:
        return NameConsistency.EXACT, "EXACT_PRODUCT_NAME"
    statement_name = normalize_statement_product_name(statement_raw)
    invoice_name = normalize_statement_product_name(invoice_raw)
    if statement_name and statement_name == invoice_name:
        return (
            NameConsistency.SAFE_NORMALIZATION_DIFFERENCE,
            "SAFE_NAME_NORMALIZATION",
        )
    if _verified_repair(row, item, repairs):
        return (
            NameConsistency.SAFE_NORMALIZATION_DIFFERENCE,
            "VERIFIED_ARTIFACT_REPAIR",
        )
    linked_names = {
        normalize_statement_product_name(candidate.product_name)
        for candidate in linked_family
        if normalize_statement_product_name(candidate.product_name)
    }
    if statement_name and statement_name in linked_names:
        return (
            NameConsistency.SAFE_NORMALIZATION_DIFFERENCE,
            "PRODUCT_MASTER_NAME_CONFIRMATION",
        )
    return NameConsistency.SEMANTIC_CONFLICT, "SEMANTIC_CONFLICT"


def _verified_repair(
    row: SettlementIncomeRow,
    item: CanonicalInvoiceItem,
    repairs: Sequence[VerifiedArtifactRepair],
) -> bool:
    target = normalize_statement_product_name(row.product_name)
    return bool(target) and any(
        item.source_pdf
        and repair.source_pdf == item.source_pdf
        and repair.raw_extracted_name == (item.product_name or "")
        and normalize_statement_product_name(repair.repaired_name) == target
        for repair in repairs
    )


def _preferred_edge(
    row: SettlementIncomeRow,
    edges: Sequence[_CandidateEdge],
    tolerance: Decimal,
) -> _CandidateEdge | None:
    if len(edges) == 1:
        return edges[0]
    source_name_edges = tuple(
        edge
        for edge in edges
        if edge.name_method
        in {
            "EXACT_PRODUCT_NAME",
            "SAFE_NAME_NORMALIZATION",
            "VERIFIED_ARTIFACT_REPAIR",
        }
    )
    if len(source_name_edges) == 1:
        return source_name_edges[0]
    candidates = source_name_edges or tuple(edges)
    if not candidates or any(_has_promotion(edge.item) for edge in candidates):
        return None
    statement_price = row.financial_components.get("Product Price")
    if statement_price is None:
        return None
    amount_edges = tuple(
        edge
        for edge in candidates
        if edge.item.line_subtotal is not None
        and abs(statement_price - edge.item.line_subtotal) <= tolerance
    )
    return amount_edges[0] if len(amount_edges) == 1 else None


def _evaluate_identity(
    contexts: Sequence[_RowContext],
    order_items: Sequence[CanonicalInvoiceItem],
) -> tuple[IdentityEvidence, ...]:
    identities: list[IdentityEvidence] = []
    selected_contexts: set[StatementMemberRef] = set()
    consumed: list[InvoiceItemRef] = []

    source_proven_groups = _source_proven_promotion_groups(contexts, order_items)
    for identity in source_proven_groups.values():
        identities.append(identity)
        selected_contexts.update(identity.statement_members)
        consumed.extend(identity.invoice_members)

    for context in contexts:
        if context.member in selected_contexts or context.preferred is None:
            continue
        edge = context.preferred.evidence
        selected_contexts.add(context.member)
        consumed.append(edge.invoice_member)
        identities.append(
            IdentityEvidence(
                identity_scope=IdentityScope.ITEM,
                product_id=context.row.product_id,
                statement_members=(context.member,),
                invoice_members=(edge.invoice_member,),
                compatible_edges=(edge,),
                selected_pairs=((context.member, edge.invoice_member),),
                perfect_matching_count=1,
                allocation_resolved=True,
                diagnostic="Deterministic item identity.",
            )
        )

    consumed_set = set(consumed)
    remaining = tuple(
        context for context in contexts if context.member not in selected_contexts
    )
    by_product_id: dict[str, list[_RowContext]] = defaultdict(list)
    for context in remaining:
        if context.family and context.edges:
            by_product_id[context.row.product_id].append(context)
        else:
            identities.append(_unresolved_identity(context))

    for product_id, group_contexts in sorted(by_product_id.items()):
        available_edges = {
            context.member: tuple(
                edge
                for edge in context.edges
                if edge.evidence.invoice_member not in consumed_set
            )
            for context in group_contexts
        }
        invoice_refs = tuple(
            sorted(
                {
                    edge.evidence.invoice_member
                    for edges in available_edges.values()
                    for edge in edges
                }
            )
        )
        count, first_matching = _perfect_matchings(available_edges, invoice_refs)
        if count == 1 and first_matching is not None:
            for context in group_contexts:
                invoice_ref = first_matching[context.member]
                edge = next(
                    edge.evidence
                    for edge in available_edges[context.member]
                    if edge.evidence.invoice_member == invoice_ref
                )
                identities.append(
                    IdentityEvidence(
                        identity_scope=IdentityScope.ITEM,
                        product_id=product_id,
                        statement_members=(context.member,),
                        invoice_members=(invoice_ref,),
                        compatible_edges=tuple(
                            value.evidence for value in available_edges[context.member]
                        ),
                        selected_pairs=((context.member, invoice_ref),),
                        perfect_matching_count=1,
                        allocation_resolved=True,
                        diagnostic="Compatibility graph has one complete allocation.",
                    )
                )
                consumed_set.add(invoice_ref)
            continue

        statement_refs = tuple(sorted(context.member for context in group_contexts))
        group_edges = tuple(
            sorted(
                (
                    edge.evidence
                    for context in group_contexts
                    for edge in available_edges[context.member]
                ),
                key=lambda value: (value.statement_member, value.invoice_member),
            )
        )
        if (
            count >= 2
            and len(statement_refs) == len(invoice_refs)
            and statement_refs
            and all(available_edges[context.member] for context in group_contexts)
        ):
            identities.append(
                IdentityEvidence(
                    identity_scope=IdentityScope.GROUP,
                    product_id=product_id,
                    statement_members=statement_refs,
                    invoice_members=invoice_refs,
                    compatible_edges=group_edges,
                    selected_pairs=(),
                    perfect_matching_count=count,
                    allocation_resolved=False,
                    diagnostic=(
                        "Complete compatible multiset is proven, but multiple physical "
                        "row-to-item allocations remain."
                    ),
                )
            )
            consumed_set.update(invoice_refs)
        else:
            diagnostic = (
                "Compatible family does not have a complete unique or aggregate-valid "
                "allocation."
            )
            for context in group_contexts:
                identities.append(
                    IdentityEvidence(
                        identity_scope=IdentityScope.UNRESOLVED,
                        product_id=product_id,
                        statement_members=(context.member,),
                        invoice_members=tuple(
                            edge.evidence.invoice_member
                            for edge in available_edges[context.member]
                        ),
                        compatible_edges=tuple(
                            edge.evidence for edge in available_edges[context.member]
                        ),
                        selected_pairs=(),
                        perfect_matching_count=count,
                        allocation_resolved=False,
                        diagnostic=diagnostic,
                    )
                )
    return tuple(
        sorted(
            identities,
            key=lambda value: (
                value.statement_members[0] if value.statement_members else StatementMemberRef("", "", 0, ""),
                value.identity_scope.value,
            ),
        )
    )


def _source_proven_promotion_groups(
    contexts: Sequence[_RowContext],
    order_items: Sequence[CanonicalInvoiceItem],
) -> dict[str, IdentityEvidence]:
    """Prove aggregate identity when source rows cannot prove physical ownership."""

    by_product_id: dict[str, list[_RowContext]] = defaultdict(list)
    for context in contexts:
        if context.family:
            by_product_id[context.row.product_id].append(context)

    result: dict[str, IdentityEvidence] = {}
    for product_id, group_contexts in by_product_id.items():
        family = group_contexts[0].family
        group_items = _family_items(order_items, family)
        if not _source_proven_promotion_group_eligible(
            product_id,
            group_contexts,
            group_items,
            by_product_id,
        ):
            continue

        statement_refs = tuple(sorted(context.member for context in group_contexts))
        invoice_refs = tuple(sorted(_invoice_ref(item) for item in group_items))
        available_edges = {
            context.member: tuple(context.edges) for context in group_contexts
        }
        edge_invoice_refs = tuple(
            sorted(
                {
                    edge.evidence.invoice_member
                    for edges in available_edges.values()
                    for edge in edges
                }
            )
        )
        count, first_matching = _perfect_matchings(available_edges, edge_invoice_refs)

        # Preserve existing complete deterministic ITEM allocation and complete
        # ambiguous multiset behavior.  This route only repairs the evidence gap.
        preferred_refs = tuple(
            context.preferred.evidence.invoice_member
            for context in group_contexts
            if context.preferred is not None
        )
        complete_preferred = (
            len(preferred_refs) == len(statement_refs) == len(invoice_refs)
            and len(set(preferred_refs)) == len(preferred_refs)
            and set(preferred_refs) == set(invoice_refs)
        )
        complete_existing_group = (
            count >= 2
            and len(statement_refs) == len(edge_invoice_refs) == len(invoice_refs)
            and set(edge_invoice_refs) == set(invoice_refs)
            and all(available_edges[context.member] for context in group_contexts)
        )
        if (
            complete_preferred
            or (
                count == 1
                and first_matching is not None
                and len(statement_refs) == len(edge_invoice_refs) == len(invoice_refs)
                and set(edge_invoice_refs) == set(invoice_refs)
            )
            or (complete_existing_group and not preferred_refs)
        ):
            continue

        compatible_edges = tuple(
            sorted(
                (
                    edge.evidence
                    for context in group_contexts
                    for edge in context.edges
                ),
                key=lambda value: (value.statement_member, value.invoice_member),
            )
        )
        result[product_id] = IdentityEvidence(
            identity_scope=IdentityScope.GROUP,
            product_id=product_id,
            statement_members=statement_refs,
            invoice_members=invoice_refs,
            compatible_edges=compatible_edges,
            selected_pairs=(),
            perfect_matching_count=count,
            allocation_resolved=False,
            diagnostic=(
                "Source-proven promotion product group is complete; physical "
                "Statement-to-Invoice row ownership remains unallocated."
            ),
        )
    return result


def _source_proven_promotion_group_eligible(
    product_id: str,
    contexts: Sequence[_RowContext],
    group_items: Sequence[CanonicalInvoiceItem],
    contexts_by_product_id: Mapping[str, Sequence[_RowContext]],
) -> bool:
    if not contexts or len(group_items) < 2:
        return False
    if any(not context.edges for context in contexts):
        return False

    promotion_items = tuple(item for item in group_items if _has_promotion(item))
    if not promotion_items:
        return False
    if any(
        not (item.promotion_group_id or "").strip()
        or item.source_group_total is None
        or item.quantity is None
        or item.quantity <= 0
        for item in promotion_items
    ):
        return False
    normal_items = tuple(item for item in group_items if not _has_promotion(item))
    if any(
        item.line_subtotal is None
        or item.quantity is None
        or item.quantity <= 0
        for item in normal_items
    ):
        return False

    skus = {normalize_sku_text(item.seller_sku) for item in group_items}
    navs = {(item.nav or "").strip().casefold() for item in group_items}
    variations = {
        normalize_statement_product_name(item.variation) for item in group_items
    }
    prices = {item.unit_price for item in group_items}
    if "" in skus or "" in navs or len(skus) != 1 or len(navs) != 1:
        return False
    if len(variations) != 1 or None in prices or len(prices) != 1:
        return False

    expected_nav = next(iter(navs))
    expected_variation = next(iter(variations))
    expected_price = next(iter(prices))
    for item in group_items:
        linked = _family_candidates_for_item(item, contexts[0].family)
        if not linked:
            return False
        candidate_identities = {
            (
                (candidate.nav_code or "").strip().casefold(),
                normalize_statement_product_name(candidate.variation),
                candidate.unit_selling_price,
            )
            for candidate in linked
        }
        if candidate_identities != {
            (expected_nav, expected_variation, expected_price)
        }:
            return False

    # A member that fits another Product ID family has ambiguous group ownership.
    for item in group_items:
        eligible_product_ids = {
            other_product_id
            for other_product_id, other_contexts in contexts_by_product_id.items()
            if other_contexts
            and item in _family_items((item,), other_contexts[0].family)
        }
        if eligible_product_ids != {product_id}:
            return False
    return True


def _unresolved_identity(context: _RowContext) -> IdentityEvidence:
    return IdentityEvidence(
        identity_scope=IdentityScope.UNRESOLVED,
        product_id=context.row.product_id,
        statement_members=(context.member,),
        invoice_members=tuple(
            sorted(edge.evidence.invoice_member for edge in context.edges)
        ),
        compatible_edges=tuple(edge.evidence for edge in context.edges),
        selected_pairs=(),
        perfect_matching_count=0,
        allocation_resolved=False,
        diagnostic=context.diagnostic,
    )


def _perfect_matchings(
    edges: Mapping[StatementMemberRef, Sequence[_CandidateEdge]],
    invoice_refs: Sequence[InvoiceItemRef],
) -> tuple[int, dict[StatementMemberRef, InvoiceItemRef] | None]:
    statements = tuple(sorted(edges, key=lambda member: (len(edges[member]), member)))
    if not statements or len(statements) != len(invoice_refs):
        return 0, None
    count = 0
    first: dict[StatementMemberRef, InvoiceItemRef] | None = None

    def visit(
        index: int,
        used: set[InvoiceItemRef],
        current: dict[StatementMemberRef, InvoiceItemRef],
    ) -> None:
        nonlocal count, first
        if index == len(statements):
            count += 1
            if first is None:
                first = dict(current)
            return
        statement_member = statements[index]
        for edge in sorted(
            edges[statement_member], key=lambda value: value.evidence.invoice_member
        ):
            invoice_member = edge.evidence.invoice_member
            if invoice_member in used:
                continue
            used.add(invoice_member)
            current[statement_member] = invoice_member
            visit(index + 1, used, current)
            current.pop(statement_member, None)
            used.remove(invoice_member)

    visit(0, set(), {})
    return count, first


def _coverage_for_order(
    contexts: Sequence[_RowContext],
    items: Sequence[CanonicalInvoiceItem],
    identities: Sequence[IdentityEvidence],
) -> CoverageEvidence:
    expected_statement = tuple(sorted(context.member for context in contexts))
    expected_invoice = tuple(sorted(_invoice_ref(item) for item in items))
    covered_statement = tuple(
        sorted({member for identity in identities for member in identity.statement_members})
    )
    item_consumption = Counter(
        invoice
        for identity in identities
        if identity.identity_scope is IdentityScope.ITEM
        for _, invoice in identity.selected_pairs
    )
    item_members = set(item_consumption)
    group_members = {
        member
        for identity in identities
        if identity.identity_scope is IdentityScope.GROUP
        for member in identity.invoice_members
    }
    covered_invoice = tuple(sorted(item_members | group_members))
    return CoverageEvidence(
        expected_statement_members=expected_statement,
        expected_invoice_members=expected_invoice,
        covered_statement_members=covered_statement,
        covered_invoice_members=covered_invoice,
        duplicate_invoice_consumption=tuple(
            sorted(member for member, count in item_consumption.items() if count > 1)
        ),
        overlapping_item_group_members=tuple(sorted(item_members & group_members)),
        uncovered_statement_members=tuple(
            sorted(set(expected_statement) - set(covered_statement))
        ),
        uncovered_invoice_members=tuple(
            sorted(set(expected_invoice) - set(covered_invoice))
        ),
    )


def _evaluate_merchandise(
    order: CanonicalInvoiceOrder | None,
    order_row: SettlementIncomeRow | None,
    contexts: Sequence[_RowContext],
    items: Sequence[CanonicalInvoiceItem],
    identities: Sequence[IdentityEvidence],
    tolerance: Decimal,
) -> tuple[MerchandiseEvidence, ...]:
    context_by_ref = {context.member: context for context in contexts}
    item_by_ref = {_invoice_ref(item): item for item in items}
    comparisons: list[MerchandiseEvidence] = []
    graph: dict[tuple[str, object], set[tuple[str, object]]] = defaultdict(set)
    identity_by_statement: dict[StatementMemberRef, IdentityEvidence] = {}
    for identity in identities:
        if identity.identity_scope is IdentityScope.UNRESOLVED:
            continue
        statement_nodes = tuple(("S", member) for member in identity.statement_members)
        invoice_nodes = tuple(("I", member) for member in identity.invoice_members)
        for member in identity.statement_members:
            identity_by_statement[member] = identity
        for statement_node in statement_nodes:
            for invoice_node in invoice_nodes:
                graph[statement_node].add(invoice_node)
                graph[invoice_node].add(statement_node)

    promotion_members: dict[str, list[InvoiceItemRef]] = defaultdict(list)
    for item in items:
        if item.promotion_group_id:
            promotion_members[item.promotion_group_id].append(_invoice_ref(item))
    for members in promotion_members.values():
        for member in members:
            graph[("I", member)].update(("I", other) for other in members if other != member)

    visited: set[tuple[str, object]] = set()
    handled_statement: set[StatementMemberRef] = set()
    handled_invoice: set[InvoiceItemRef] = set()
    for start in sorted(graph, key=lambda node: (node[0], node[1])):
        if start in visited:
            continue
        pending = [start]
        component: set[tuple[str, object]] = set()
        while pending:
            node = pending.pop()
            if node in component:
                continue
            component.add(node)
            pending.extend(graph[node] - component)
        visited.update(component)
        statement_refs = {
            value for kind, value in component if kind == "S" and isinstance(value, StatementMemberRef)
        }
        invoice_refs = {
            value for kind, value in component if kind == "I" and isinstance(value, InvoiceItemRef)
        }
        group_items = tuple(item_by_ref[ref] for ref in invoice_refs if ref in item_by_ref)
        invoice_value, authority = _invoice_group_value(group_items)
        statement_value = _statement_value_for_refs(statement_refs, context_by_ref)
        promotion_ids = sorted(
            {item.promotion_group_id for item in group_items if item.promotion_group_id}
        )
        product_ids = sorted(
            {
                identity_by_statement[member].product_id
                for member in statement_refs
                if member in identity_by_statement
            }
        )
        if promotion_ids:
            scope_key = "PROMOTION_COMPONENT:" + ",".join(promotion_ids)
        elif len(statement_refs) > 1 or len(invoice_refs) > 1:
            scope_key = "FAMILY:" + ",".join(product_ids)
        else:
            only = next(iter(invoice_refs), None)
            scope_key = f"ITEM:{only.item_index}" if only is not None else "UNPAIRED"
        comparisons.append(
            _money_comparison(
                scope_key,
                invoice_value,
                statement_value,
                tolerance,
                authority,
            )
        )
        handled_invoice.update(invoice_refs)
        handled_statement.update(statement_refs)

    statement_order_value = (
        order_row.financial_components.get("Product Price")
        if order_row is not None
        else None
    )
    expected_statement = {context.member for context in contexts}
    expected_invoice = {_invoice_ref(item) for item in items}
    residual_statement = expected_statement - handled_statement
    residual_invoice = expected_invoice - handled_invoice
    if len(residual_statement) == 1 and len(residual_invoice) == 1:
        residual_item = item_by_ref[next(iter(residual_invoice))]
        residual_invoice_value, residual_authority = _invoice_group_value(
            (residual_item,)
        )
        comparisons.append(
            _money_comparison(
                "UNRESOLVED_ORDER_RESIDUAL",
                residual_invoice_value,
                _statement_value_for_refs(residual_statement, context_by_ref),
                tolerance,
                (
                    f"{residual_authority}; financial residual only and it does not "
                    "assign the Statement row to the remaining Invoice item"
                ),
            )
        )
        handled_statement.update(residual_statement)
        handled_invoice.update(residual_invoice)
    comparisons.append(
        _money_comparison(
            "ORDER_CONTROL",
            order.product_price if order is not None else None,
            statement_order_value,
            tolerance,
            "Invoice product_price vs Statement Order View Product Price",
        )
    )
    if handled_statement != expected_statement or handled_invoice != expected_invoice:
        comparisons.append(
            MerchandiseEvidence(
                scope_key="COVERAGE_CONTROL",
                invoice_value=None,
                statement_value=None,
                difference=None,
                tolerance=tolerance,
                reconciled=False,
                authority="Merchandise members require complete ITEM or GROUP coverage",
            )
        )
    return tuple(comparisons)


def _invoice_group_value(
    items: Sequence[CanonicalInvoiceItem],
) -> tuple[Decimal | None, str]:
    if not items:
        return None, "No Invoice members"
    if any(_has_promotion(item) for item in items):
        promotion_totals: dict[str, Decimal] = {}
        nonpromotion_total = Decimal("0")
        for item in items:
            if _has_promotion(item):
                key = item.promotion_group_id or f"SOURCE:{item.item_index}"
                if item.source_group_total is None:
                    return None, "Promotion source_group_total is missing"
                existing = promotion_totals.setdefault(key, item.source_group_total)
                if existing != item.source_group_total:
                    return None, "Promotion source_group_total conflicts within source group"
            else:
                if item.line_subtotal is None:
                    return None, "Invoice line_subtotal is missing"
                nonpromotion_total += item.line_subtotal
        return (
            nonpromotion_total + sum(promotion_totals.values(), Decimal("0")),
            "Invoice source_group_total promotion authority",
        )
    if any(item.line_subtotal is None for item in items):
        return None, "Invoice line_subtotal is missing"
    return (
        sum((item.line_subtotal for item in items if item.line_subtotal is not None), Decimal("0")),
        "Aggregate Invoice line_subtotal vs Statement SKU Product Price",
    )


def _statement_value_for_refs(
    refs: Iterable[StatementMemberRef],
    contexts: Mapping[StatementMemberRef, _RowContext],
) -> Decimal | None:
    values = tuple(
        contexts[ref].row.financial_components.get("Product Price")
        for ref in refs
        if ref in contexts
    )
    if not values or any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), Decimal("0"))


def _money_comparison(
    scope_key: str,
    invoice_value: Decimal | None,
    statement_value: Decimal | None,
    tolerance: Decimal,
    authority: str,
) -> MerchandiseEvidence:
    difference = (
        statement_value - invoice_value
        if invoice_value is not None and statement_value is not None
        else None
    )
    return MerchandiseEvidence(
        scope_key=scope_key,
        invoice_value=invoice_value,
        statement_value=statement_value,
        difference=difference,
        tolerance=tolerance,
        reconciled=difference is not None and abs(difference) <= tolerance,
        authority=authority,
    )


def _evaluate_settlement(
    order: CanonicalInvoiceOrder | None,
    order_row: SettlementIncomeRow | None,
    *,
    tolerance: Decimal,
) -> SettlementEvidence:
    source_state = (order.income_type or "").strip() if order is not None else ""
    if order is None or order_row is None:
        return _empty_settlement(order, order_row, tolerance, source_state)
    if source_state.casefold() not in {"final", "estimated"}:
        return _empty_settlement(order, order_row, tolerance, source_state)
    values = tuple(order_row.financial_components.get(name) for name in INCOME_COMPONENT_COLUMNS)
    if order.order_income is None or any(value is None for value in values):
        return _empty_settlement(order, order_row, tolerance, source_state)
    if (
        source_state.casefold() == "final"
        and order.final_amount is not None
        and abs(order.final_amount - order.order_income) > tolerance
    ):
        return _empty_settlement(order, order_row, tolerance, source_state)

    statement_values = {
        "merchandise": _component_sum(order_row, ("Product Price",)),
        "refund": _component_sum(order_row, ("Refund Amount",)),
        "shipping": _component_sum(order_row, _SHIPPING_COMPONENTS),
        "voucher_rebate": _component_sum(order_row, _VOUCHER_COMPONENTS),
        "fees": _component_sum(order_row, _FEE_COMPONENTS),
    }
    invoice_values = {
        "merchandise": order.product_price,
        "refund": order.refund_amount,
        "shipping": order.shipping_subtotal,
        "voucher_rebate": order.vouchers_rebates_total,
        "fees": order.fees_charges_total,
    }
    statement_total = sum(statement_values.values(), Decimal("0"))
    raw_difference = statement_total - order.order_income
    components: list[SettlementComponentEvidence] = []
    explained_parts: list[Decimal] = []
    invalid_missing = False
    effects: list[str] = []
    for component in ("merchandise", "refund", "shipping", "voucher_rebate", "fees"):
        statement_value = statement_values[component]
        invoice_value = invoice_values[component]
        if invoice_value is not None:
            delta = statement_value - invoice_value
            explained_parts.append(delta)
            treatment = "SOURCE_COMPONENT_DELTA"
        elif statement_value == 0:
            delta = None
            treatment = "INVOICE_SOURCE_ABSENT_STATEMENT_NEUTRAL"
        elif component == "refund" and _late_statement_refund_allowed(order, statement_value):
            delta = statement_value
            explained_parts.append(delta)
            treatment = "LATE_STATEMENT_REFUND_EFFECT"
            effects.append("LATE_STATEMENT_REFUND_EFFECT")
        else:
            delta = None
            treatment = "INVOICE_SOURCE_EVIDENCE_MISSING"
            invalid_missing = True
        components.append(
            SettlementComponentEvidence(
                component=component,
                invoice_value=invoice_value,
                statement_value=statement_value,
                delta=delta,
                treatment=treatment,
            )
        )
    explained_delta = sum(explained_parts, Decimal("0"))
    residual = raw_difference - explained_delta
    if abs(raw_difference) <= tolerance:
        basis = SettlementBasis.EXACT
    elif not invalid_missing and abs(residual) <= tolerance:
        basis = SettlementBasis.EXPLAINED
    else:
        basis = SettlementBasis.NONE
    return SettlementEvidence(
        invoice_basis=order.order_income,
        statement_total=statement_total,
        component_deltas=tuple(components),
        raw_difference=raw_difference,
        explained_delta=explained_delta,
        unexplained_residual=residual,
        tolerance=tolerance,
        basis=basis,
        source_state=source_state,
        internal_effects=tuple(effects),
    )


def _empty_settlement(
    order: CanonicalInvoiceOrder | None,
    row: SettlementIncomeRow | None,
    tolerance: Decimal,
    source_state: str,
) -> SettlementEvidence:
    return SettlementEvidence(
        invoice_basis=order.order_income if order is not None else None,
        statement_total=row.total_released_amount if row is not None else None,
        component_deltas=(),
        raw_difference=None,
        explained_delta=None,
        unexplained_residual=None,
        tolerance=tolerance,
        basis=SettlementBasis.NONE,
        source_state=source_state,
        internal_effects=(),
    )


def _component_sum(
    row: SettlementIncomeRow,
    names: Sequence[str],
) -> Decimal:
    return sum(
        (
            row.financial_components[name]
            for name in names
            if row.financial_components[name] is not None
        ),
        Decimal("0"),
    )


def _late_statement_refund_allowed(
    order: CanonicalInvoiceOrder,
    statement_refund: Decimal,
) -> bool:
    return (
        (order.income_type or "").strip().casefold() == "estimated"
        and order.invoice_financial_layout == "NORMAL_ORDER"
        and order.refund_amount is None
        and statement_refund != 0
    )


def _refund_evidence(
    order: CanonicalInvoiceOrder | None,
    order_row: SettlementIncomeRow | None,
    contexts: Sequence[_RowContext],
    identities: Sequence[IdentityEvidence],
) -> RefundEvidence:
    refund_contexts = tuple(
        context
        for context in contexts
        if (context.row.financial_components.get("Refund Amount") or Decimal("0")) != 0
    )
    scopes = []
    for context in refund_contexts:
        identity = next(
            (
                value
                for value in identities
                if context.member in value.statement_members
            ),
            None,
        )
        scopes.append(identity.identity_scope if identity is not None else IdentityScope.UNRESOLVED)
    if IdentityScope.UNRESOLVED in scopes:
        strongest = IdentityScope.UNRESOLVED
    elif IdentityScope.GROUP in scopes:
        strongest = IdentityScope.GROUP
    elif IdentityScope.ITEM in scopes:
        strongest = IdentityScope.ITEM
    else:
        strongest = None
    return RefundEvidence(
        invoice_source_exists=order is not None and order.refund_amount is not None,
        statement_amount=(
            order_row.financial_components.get("Refund Amount")
            if order_row is not None
            else None
        ),
        statement_members=tuple(context.member for context in refund_contexts),
        strongest_scope=strongest,
        allocation_resolved=strongest in {None, IdentityScope.ITEM},
    )


def _promotion_evidence(
    items: Sequence[CanonicalInvoiceItem],
    identities: Sequence[IdentityEvidence],
) -> PromotionEvidence:
    group_ids = tuple(
        sorted(
            {
                item.promotion_group_id or f"SOURCE:{item.item_index}"
                for item in items
                if _has_promotion(item)
            }
        )
    )
    unresolved_allocation = any(
        identity.identity_scope is IdentityScope.GROUP
        and any(
            member.item_index
            in {item.item_index for item in items if _has_promotion(item)}
            for member in identity.invoice_members
        )
        for identity in identities
    )
    return PromotionEvidence(
        promotion_group_ids=group_ids,
        source_group_total_authority_used=bool(group_ids),
        allocation_resolved=not unresolved_allocation,
    )


def _summarize(
    *,
    order: CanonicalInvoiceOrder | None,
    contexts: Sequence[_RowContext],
    identities: Sequence[IdentityEvidence],
    coverage: CoverageEvidence,
    merchandise: Sequence[MerchandiseEvidence],
    settlement: SettlementEvidence,
    validation_issues: Sequence[str],
) -> ReconciliationSummary:
    reasons: set[ReconciliationReason] = set()
    unresolved = tuple(
        identity
        for identity in identities
        if identity.identity_scope is IdentityScope.UNRESOLVED
    )
    if order is None or not contexts or validation_issues:
        reasons.add(ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT)
    for identity in unresolved:
        if "Product Master" in identity.diagnostic:
            reasons.add(ReconciliationReason.PRODUCT_MASTER_IDENTITY_MISSING)
        elif not identity.product_id:
            reasons.add(ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT)
        elif "conflict" in identity.diagnostic.casefold():
            reasons.add(ReconciliationReason.IDENTITY_CONFLICT)
        else:
            reasons.add(ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT)
    if not coverage.valid:
        reasons.add(ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT)
    has_group = any(
        identity.identity_scope is IdentityScope.GROUP for identity in identities
    )
    if has_group:
        reasons.add(ReconciliationReason.ITEM_ALLOCATION_UNRESOLVED)
    merchandise_reconciled = bool(merchandise) and all(
        comparison.reconciled for comparison in merchandise
    )
    if not merchandise_reconciled:
        reasons.add(ReconciliationReason.MERCHANDISE_DIFFERENCE)
    if settlement.basis is SettlementBasis.NONE:
        if settlement.unexplained_residual is not None:
            reasons.add(ReconciliationReason.SETTLEMENT_UNEXPLAINED_DIFFERENCE)
        else:
            reasons.add(ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT)
    if unresolved or not coverage.valid:
        identity_scope = IdentityScope.UNRESOLVED
    elif has_group:
        identity_scope = IdentityScope.GROUP
    else:
        identity_scope = IdentityScope.ITEM
    allocation_resolved = (
        identity_scope is IdentityScope.ITEM
        and all(identity.allocation_resolved for identity in identities)
    )
    return ReconciliationSummary(
        identity_scope=identity_scope,
        merchandise_reconciled=merchandise_reconciled,
        settlement_basis=settlement.basis,
        allocation_resolved=allocation_resolved,
        reasons=tuple(sorted(reasons, key=lambda value: value.value)),
    )


def _product_family_snapshot(
    families: Iterable[Sequence[ProductFamilyCandidate]],
) -> ProductFamilySnapshot:
    candidates = tuple(
        sorted(
            {
                ProductFamilyIdentity(
                    product_id=candidate.product_id,
                    seller_sku=candidate.seller_sku,
                    parent_sku=candidate.parent_sku,
                    product_name=candidate.product_name,
                    variation=candidate.variation,
                    nav_code=candidate.nav_code,
                    unit_selling_price=candidate.unit_selling_price,
                )
                for family in families
                for candidate in family
            },
            key=lambda candidate: (
                candidate.product_id,
                candidate.seller_sku,
                candidate.parent_sku,
                candidate.product_name,
                candidate.variation,
                candidate.nav_code,
                ""
                if candidate.unit_selling_price is None
                else str(candidate.unit_selling_price),
            ),
        )
    )
    payload = [
        {
            "parent_sku": candidate.parent_sku,
            "product_id": candidate.product_id,
            "product_name": candidate.product_name,
            "seller_sku": candidate.seller_sku,
            "variation": candidate.variation,
            "nav_code": candidate.nav_code,
            "unit_selling_price": (
                str(candidate.unit_selling_price)
                if candidate.unit_selling_price is not None
                else None
            ),
        }
        for candidate in candidates
    ]
    digest = sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return ProductFamilySnapshot(candidates=candidates, sha256=digest)


def _merge_coverage(values: Sequence[CoverageEvidence]) -> CoverageEvidence:
    def combined(field: str) -> tuple:
        return tuple(sorted(value for item in values for value in getattr(item, field)))

    return CoverageEvidence(
        expected_statement_members=combined("expected_statement_members"),
        expected_invoice_members=combined("expected_invoice_members"),
        covered_statement_members=combined("covered_statement_members"),
        covered_invoice_members=combined("covered_invoice_members"),
        duplicate_invoice_consumption=combined("duplicate_invoice_consumption"),
        overlapping_item_group_members=combined("overlapping_item_group_members"),
        uncovered_statement_members=combined("uncovered_statement_members"),
        uncovered_invoice_members=combined("uncovered_invoice_members"),
    )


def _has_promotion(item: CanonicalInvoiceItem) -> bool:
    return any(
        value is not None and value != ""
        for value in (
            item.promotion_group_id,
            item.promotion_label,
            item.source_group_total,
        )
    )
