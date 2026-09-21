from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
)
from src.invoice_app.domain.statement_reconciliation_v2 import (
    IdentityScope,
    NameConsistency,
    ReconciliationReason,
    SettlementBasis,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    StatementSummaryLine,
    SettlementAdjustment,
    SettlementIncomeRow,
    SourceValueIssue,
)
from src.invoice_app.services.shopee_statement_item_matching import (
    MappingProductFamilyResolver,
    ProductFamilyCandidate,
    VerifiedArtifactRepair,
)
from src.invoice_app.services.shopee_statement_reconciliation_v2 import (
    evaluate_statement_reconciliation,
)


ZERO = Decimal("0")


def _components(
    *,
    product: str,
    refund: str = "0",
    shipping: str = "0",
    voucher: str = "0",
    fees: str = "0",
) -> dict[str, Decimal]:
    result = {name: ZERO for name in INCOME_COMPONENT_COLUMNS}
    result["Product Price"] = Decimal(product)
    result["Refund Amount"] = Decimal(refund)
    result["Shipping Fee Paid by Buyer (excl. SST)"] = Decimal(shipping)
    result["Voucher Sponsored by Seller"] = Decimal(voucher)
    result["Commission Fee (incl. SST)"] = Decimal(fees)
    return result


def _sku_row(
    *,
    source_row: int = 3,
    sequence: str = "1",
    order_id: str = "ORDER-1",
    product_id: str = "PRODUCT-1",
    name: str = "Green Tea",
    components: dict[str, Decimal] | None = None,
) -> SettlementIncomeRow:
    values = components or _components(product="10.00")
    return SettlementIncomeRow(
        sequence_no=sequence,
        view_by="Sku",
        order_id=order_id,
        product_id=product_id,
        product_name=name,
        order_creation_date=None,
        payout_completed_date=None,
        release_channel="",
        order_type="Normal Order",
        total_released_amount=sum(values.values(), ZERO),
        financial_components=values,
        source_values={},
        source_row_number=source_row,
    )


def _statement(
    rows: tuple[SettlementIncomeRow, ...],
    *,
    order_components: dict[str, Decimal] | None = None,
    adjustments: tuple[SettlementAdjustment, ...] = (),
) -> ParsedShopeeWeeklyStatement:
    order_id = rows[0].order_id
    components = order_components or {
        name: sum((row.financial_components[name] for row in rows), ZERO)
        for name in INCOME_COMPONENT_COLUMNS
    }
    total = sum(components.values(), ZERO)
    order_row = SettlementIncomeRow(
        sequence_no="ORDER",
        view_by="Order",
        order_id=order_id,
        product_id="",
        product_name="",
        order_creation_date=None,
        payout_completed_date=None,
        release_channel="",
        order_type="Normal Order",
        total_released_amount=total,
        financial_components=components,
        source_values={},
        source_row_number=2,
    )
    adjustment_total = sum(
        (item.adjustment_amount or ZERO for item in adjustments), ZERO
    )
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash="f" * 64,
        statement_period_from=date(2026, 9, 1),
        statement_period_to=date(2026, 9, 7),
        summary_total_released=total,
        adjustment_control_total=adjustment_total,
        adjustment_footer_total=adjustment_total,
        income_rows=(order_row, *rows),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=adjustments,
        source_value_issues=(),
        dimension_fallback_sheets=(),
        summary_lines=(
            StatementSummaryLine(
                "Summary", 40, "3. Total Released Amount", "TOTAL", None,
                total, "RM",
            ),
        ),
    )


def _order(
    *,
    order_id: str = "ORDER-1",
    product: str = "10.00",
    refund: str | None = "0",
    shipping: str | None = "0",
    voucher: str | None = "0",
    fees: str | None = "0",
    income: str = "10.00",
    income_type: str = "Final",
    final_amount: str | None = "10.00",
    layout: str = "NORMAL_ORDER",
) -> CanonicalInvoiceOrder:
    decimal = lambda value: Decimal(value) if value is not None else None
    return CanonicalInvoiceOrder(
        platform="Shopee",
        order_id=order_id,
        invoice_financial_layout=layout,
        product_price=Decimal(product),
        refund_amount=decimal(refund),
        shipping_subtotal=decimal(shipping),
        vouchers_rebates_total=decimal(voucher),
        fees_charges_total=decimal(fees),
        order_income=Decimal(income),
        income_type=income_type,
        final_amount=decimal(final_amount),
        source_hash="invoice-hash",
    )


def _item(
    index: int,
    *,
    order_id: str = "ORDER-1",
    sku: str = "SKU-1",
    name: str = "Green Tea",
    subtotal: str | None = "10.00",
    promotion_group: str | None = None,
    source_group_total: str | None = None,
    source_pdf: str = "invoice.pdf",
) -> CanonicalInvoiceItem:
    return CanonicalInvoiceItem(
        platform="Shopee",
        order_id=order_id,
        item_index=index,
        seller_sku=sku,
        product_name=name,
        quantity=1,
        line_subtotal=Decimal(subtotal) if subtotal is not None else None,
        promotion_group_id=promotion_group,
        promotion_label="Any 2" if promotion_group else None,
        source_group_total=(
            Decimal(source_group_total) if source_group_total is not None else None
        ),
        source_pdf=source_pdf,
    )


def _family(
    *,
    product_id: str = "PRODUCT-1",
    sku: str = "SKU-1",
    parent: str = "",
    name: str = "Green Tea",
    variation: str = "",
) -> ProductFamilyCandidate:
    return ProductFamilyCandidate(product_id, sku, parent, name, variation)


def _resolver(*families: ProductFamilyCandidate) -> MappingProductFamilyResolver:
    grouped: dict[str, list[ProductFamilyCandidate]] = {}
    for family in families:
        grouped.setdefault(family.product_id, []).append(family)
    return MappingProductFamilyResolver(
        {product_id: tuple(values) for product_id, values in grouped.items()}
    )


def _evaluate(
    rows: tuple[SettlementIncomeRow, ...],
    items: tuple[CanonicalInvoiceItem, ...],
    *,
    order: CanonicalInvoiceOrder | None = None,
    resolver: MappingProductFamilyResolver | None = None,
    order_components: dict[str, Decimal] | None = None,
    repairs: tuple[VerifiedArtifactRepair, ...] = (),
    adjustments: tuple[SettlementAdjustment, ...] = (),
):
    order = order or _order()
    return evaluate_statement_reconciliation(
        _statement(rows, order_components=order_components, adjustments=adjustments),
        (order,),
        items,
        product_families=resolver or _resolver(_family()),
        verified_artifact_repairs=repairs,
    )


def test_unique_strong_identity_with_verified_name_artifact_is_item():
    row = _sku_row(name="Sugar Free Tea")
    item = _item(0, name="Suga r Free Tea")
    result = _evaluate(
        (row,),
        (item,),
        repairs=(
            VerifiedArtifactRepair("invoice.pdf", "Suga r Free Tea", "Sugar Free Tea"),
        ),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.ITEM
    assert result.evidence.identities[0].compatible_edges[0].name_consistency is NameConsistency.SAFE_NORMALIZATION_DIFFERENCE


def test_unicode_compatibility_difference_is_item():
    result = _evaluate(
        (_sku_row(name="Green Tea"),),
        (_item(0, name="Ｇｒｅｅｎ　Ｔｅａ"),),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.ITEM
    assert result.evidence.identities[0].compatible_edges[0].name_consistency is NameConsistency.SAFE_NORMALIZATION_DIFFERENCE


def test_unique_strong_identity_with_semantic_name_conflict_is_unresolved():
    result = _evaluate(
        (_sku_row(name="Red Apples"),),
        (_item(0),),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.UNRESOLVED
    assert ReconciliationReason.IDENTITY_CONFLICT in result.summary.reasons


def test_duplicate_item_consumption_is_blocked_by_coverage_validator():
    rows = (
        _sku_row(source_row=3, sequence="1", components=_components(product="5")),
        _sku_row(source_row=4, sequence="2", components=_components(product="5")),
    )
    result = _evaluate(rows, (_item(0, subtotal="10"),)).orders[0]

    assert result.evidence.coverage.duplicate_invoice_consumption
    assert result.summary.identity_scope is IdentityScope.UNRESOLVED


def test_inverse_invoice_coverage_gap_is_not_silently_accepted():
    result = _evaluate(
        (_sku_row(),),
        (_item(0), _item(1, sku="OTHER", subtotal="1")),
    ).orders[0]

    assert result.evidence.coverage.uncovered_invoice_members
    assert result.summary.identity_scope is IdentityScope.UNRESOLVED


def test_clean_ambiguous_multiset_is_group_without_physical_allocation():
    rows = (
        _sku_row(source_row=3, sequence="1", components=_components(product="10")),
        _sku_row(source_row=4, sequence="2", components=_components(product="10")),
    )
    items = (_item(0), _item(1))
    result = _evaluate(rows, items, order=_order(product="20", income="20", final_amount="20")).orders[0]

    assert result.summary.identity_scope is IdentityScope.GROUP
    assert result.summary.allocation_resolved is False
    assert result.evidence.identities[0].perfect_matching_count == 2
    assert not result.evidence.identities[0].selected_pairs


def test_group_merchandise_mismatch_is_not_reconciled():
    rows = (
        _sku_row(source_row=3, sequence="1", components=_components(product="9")),
        _sku_row(source_row=4, sequence="2", components=_components(product="9")),
    )
    result = _evaluate(
        rows,
        (_item(0), _item(1)),
        order=_order(product="20", income="20", final_amount="20"),
    ).orders[0]

    assert result.summary.merchandise_reconciled is False
    assert ReconciliationReason.MERCHANDISE_DIFFERENCE in result.summary.reasons


def test_promotion_group_uses_source_group_total_and_keeps_allocation_unknown():
    rows = (
        _sku_row(source_row=3, sequence="1", components=_components(product="9")),
        _sku_row(source_row=4, sequence="2", components=_components(product="9")),
    )
    items = (
        _item(0, subtotal="10", promotion_group="P1", source_group_total="18"),
        _item(1, subtotal="12", promotion_group="P1", source_group_total="18"),
    )
    result = _evaluate(
        rows,
        items,
        order=_order(product="18", income="18", final_amount="18"),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.GROUP
    assert result.summary.merchandise_reconciled is True
    assert result.summary.allocation_resolved is False
    assert result.evidence.promotion.source_group_total_authority_used is True
    assert result.evidence.promotion.allocation_resolved is False


def test_refund_sensitive_group_does_not_fabricate_variation_or_allocation():
    rows = (
        _sku_row(source_row=3, sequence="1", components=_components(product="10", refund="-2")),
        _sku_row(source_row=4, sequence="2", components=_components(product="10")),
    )
    result = _evaluate(
        rows,
        (_item(0), _item(1)),
        order=_order(product="20", refund=None, income="20", income_type="Estimated", final_amount=None),
    ).orders[0]

    assert result.evidence.refund.strongest_scope is IdentityScope.GROUP
    assert result.evidence.refund.allocation_resolved is False
    assert result.evidence.identities[0].selected_pairs == ()


def test_normal_product_price_exact_is_merchandise_reconciled():
    result = _evaluate((_sku_row(),), (_item(0),)).orders[0]
    assert result.summary.merchandise_reconciled is True


def test_equal_order_total_does_not_hide_opposite_family_offsets():
    rows = (
        _sku_row(product_id="P1", name="Tea", source_row=3, components=_components(product="11")),
        _sku_row(product_id="P2", name="Coffee", source_row=4, components=_components(product="9")),
    )
    items = (
        _item(0, sku="S1", name="Tea", subtotal="10"),
        _item(1, sku="S2", name="Coffee", subtotal="10"),
    )
    result = _evaluate(
        rows,
        items,
        order=_order(product="20", income="20", final_amount="20"),
        resolver=_resolver(
            _family(product_id="P1", sku="S1", name="Tea"),
            _family(product_id="P2", sku="S2", name="Coffee"),
        ),
    ).orders[0]

    assert next(value for value in result.evidence.merchandise if value.scope_key == "ORDER_CONTROL").reconciled is True
    assert result.summary.merchandise_reconciled is False


def test_final_and_estimated_exact_settlement_both_report_exact():
    final = _evaluate((_sku_row(),), (_item(0),)).orders[0]
    estimated = _evaluate(
        (_sku_row(),),
        (_item(0),),
        order=_order(income_type="Estimated", final_amount=None),
    ).orders[0]

    assert final.summary.settlement_basis is SettlementBasis.EXACT
    assert estimated.summary.settlement_basis is SettlementBasis.EXACT
    assert final.summary.settlement_reconciled is True


def test_estimated_component_difference_fully_explained():
    components = _components(product="10", fees="-2")
    result = _evaluate(
        (_sku_row(components=components),),
        (_item(0),),
        order=_order(fees="-1", income="9", income_type="Estimated", final_amount=None),
    ).orders[0]

    assert result.summary.settlement_basis is SettlementBasis.EXPLAINED
    assert result.evidence.settlement.unexplained_residual == ZERO


def test_unrelated_statement_source_issue_does_not_erase_valid_order_settlement():
    statement = replace(
        _statement((_sku_row(),)),
        source_value_issues=(
            SourceValueIssue(
                code="unrecognized_income_row",
                sheet="Income",
                row_number=742,
                column="View By",
                message="Income row 742 has invalid or missing View By: ''.",
            ),
        ),
    )

    batch = evaluate_statement_reconciliation(
        statement,
        (_order(),),
        (_item(0),),
        product_families=_resolver(_family()),
    )
    result = batch.orders[0]

    assert batch.statement_validation_issues
    assert result.summary.settlement_basis is SettlementBasis.EXACT
    assert result.evidence.settlement.unexplained_residual == ZERO
    assert ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT in result.summary.reasons


def test_late_statement_refund_explains_estimated_settlement_without_mutation():
    order = _order(refund=None, income="10", income_type="Estimated", final_amount=None)
    result = _evaluate(
        (_sku_row(components=_components(product="10", refund="-2")),),
        (_item(0),),
        order=order,
    ).orders[0]

    assert result.summary.settlement_basis is SettlementBasis.EXPLAINED
    assert result.evidence.settlement.internal_effects == ("LATE_STATEMENT_REFUND_EFFECT",)
    assert order.refund_amount is None


def test_unexplained_residual_boundary_rm002_passes_and_rm003_fails():
    row = _sku_row(components=_components(product="9"))
    passing = _evaluate(
        (row,),
        (_item(0, subtotal="9"),),
        order=_order(product="10", income="9.98", final_amount="9.98"),
    ).orders[0]
    failing = _evaluate(
        (row,),
        (_item(0, subtotal="9"),),
        order=_order(product="10", income="9.97", final_amount="9.97"),
    ).orders[0]

    assert passing.evidence.settlement.unexplained_residual == Decimal("0.02")
    assert passing.summary.settlement_basis is SettlementBasis.EXPLAINED
    assert failing.evidence.settlement.unexplained_residual == Decimal("0.03")
    assert failing.summary.settlement_basis is SettlementBasis.NONE
    assert ReconciliationReason.SETTLEMENT_UNEXPLAINED_DIFFERENCE in failing.summary.reasons


def test_missing_invoice_component_is_not_silently_zero():
    result = _evaluate(
        (_sku_row(components=_components(product="10", voucher="-1")),),
        (_item(0),),
        order=_order(voucher=None, income="10", income_type="Estimated", final_amount=None),
    ).orders[0]

    voucher = next(
        value
        for value in result.evidence.settlement.component_deltas
        if value.component == "voucher_rebate"
    )
    assert voucher.invoice_value is None
    assert voucher.delta is None
    assert result.summary.settlement_basis is SettlementBasis.NONE


def test_adjustment_is_excluded_from_original_settlement_formula():
    adjustment = SettlementAdjustment(
        sequence_no="A1",
        adjustment_complete_date=None,
        adjustment_type="Other",
        adjustment_reason="Separate",
        adjustment_amount=Decimal("122.20"),
        linked_order_id="ORDER-1",
        payout_completed_date=None,
        source_row_number=2,
    )
    without = _evaluate((_sku_row(),), (_item(0),)).orders[0]
    with_adjustment = _evaluate(
        (_sku_row(),), (_item(0),), adjustments=(adjustment,)
    ).orders[0]

    assert without.evidence.settlement == with_adjustment.evidence.settlement


def test_statement_quantity_absence_does_not_fail_reconciliation_or_claim_match():
    result = _evaluate((_sku_row(),), (_item(0),)).orders[0]
    assert result.summary.merchandise_reconciled is True
    assert result.evidence.quantity.invoice_quantity_available is True
    assert result.evidence.quantity.statement_quantity_available is False


def test_product_family_snapshot_hash_is_deterministic():
    first = _evaluate(
        (_sku_row(),),
        (_item(0),),
        resolver=_resolver(_family(), _family(sku="SKU-2")),
    )
    second = _evaluate(
        (_sku_row(),),
        (_item(0),),
        resolver=_resolver(_family(sku="SKU-2"), _family()),
    )

    assert first.product_family_snapshot.sha256 == second.product_family_snapshot.sha256
    assert first.product_family_snapshot.candidates == second.product_family_snapshot.candidates


def test_product_master_identity_missing_is_unresolved():
    result = _evaluate(
        (_sku_row(product_id="MISSING"),),
        (_item(0),),
        resolver=_resolver(_family()),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.UNRESOLVED
    assert ReconciliationReason.PRODUCT_MASTER_IDENTITY_MISSING in result.summary.reasons
    assert result.summary.merchandise_reconciled is True
    residual = next(
        value
        for value in result.evidence.merchandise
        if value.scope_key == "UNRESOLVED_ORDER_RESIDUAL"
    )
    assert residual.reconciled is True
    assert result.evidence.identities[0].selected_pairs == ()


def test_exact_seller_sku_does_not_merge_parent_fallback_candidates():
    result = _evaluate(
        (_sku_row(name="Parent Product"),),
        (
            _item(0, sku="EXACT-SKU", name="Exact Product"),
            _item(1, sku="PARENT-SKU", name="Parent Product"),
        ),
        resolver=_resolver(
            _family(sku="EXACT-SKU", name="Exact Product"),
            _family(sku="OTHER-SKU", parent="PARENT-SKU", name="Parent Product"),
        ),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.UNRESOLVED
    assert result.evidence.identities[0].invoice_members == ()


def test_strong_identity_accepts_pm_confirmed_source_name_difference():
    result = _evaluate(
        (_sku_row(name="Current Product Name"),),
        (_item(0, name="Historical PDF Name"),),
        resolver=_resolver(_family(name="Current Product Name")),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.ITEM
    edge = result.evidence.identities[0].compatible_edges[0]
    assert edge.match_method.endswith("PRODUCT_MASTER_NAME_CONFIRMATION")


def test_future_order_uses_general_ampersand_name_normalization():
    order_id = "FUTURE-VITEX-ORDER"
    statement_name = (
        "Simply Natural Organic Vitex Honey 1kg | Remedy for fatigue and "
        "menstrual stress"
    )
    invoice_name = (
        "Simply Natural Organic Vitex Honey 1kg | Remedy for fatigue and "
        "menst rual stress"
    )
    result = _evaluate(
        (
            _sku_row(
                order_id=order_id,
                product_id="VITEX-PRODUCT",
                name=statement_name,
                components=_components(product="49.90"),
            ),
        ),
        (
            _item(
                0,
                order_id=order_id,
                sku="9555208105411",
                name=invoice_name,
                subtotal="49.90",
            ),
        ),
        order=_order(
            order_id=order_id,
            product="49.90",
            income="49.90",
            final_amount="49.90",
        ),
        resolver=_resolver(
            _family(
                product_id="VITEX-PRODUCT",
                sku="",
                parent="9555208105411",
                name=(
                    "Simply Natural Organic Vitex Honey 1kg | Remedy for fatigue "
                    "& menstrual stress"
                ),
            ),
            _family(
                product_id="VITEX-PRODUCT",
                sku="",
                parent="9555208105411",
                name=(
                    "Simply Natural Organic Vitex Honey 1kg | Remedy for fatigue "
                    "& menst rual stress"
                ),
            ),
        ),
    ).orders[0]

    assert result.summary.identity_scope is IdentityScope.ITEM
    edge = result.evidence.identities[0].compatible_edges[0]
    assert edge.match_method == (
        "PRODUCT_MASTER_PARENT_SKU|PRODUCT_MASTER_NAME_CONFIRMATION"
    )


def test_final_amount_conflict_with_final_order_income_fails_closed():
    result = _evaluate(
        (_sku_row(),),
        (_item(0),),
        order=_order(final_amount="9.00", income="10.00", income_type="Final"),
    ).orders[0]

    assert result.summary.settlement_basis is SettlementBasis.NONE


def test_missing_income_source_state_fails_closed():
    result = _evaluate(
        (_sku_row(),),
        (_item(0),),
        order=_order(income_type="", final_amount=None),
    ).orders[0]

    assert result.summary.settlement_basis is SettlementBasis.NONE
    assert ReconciliationReason.SOURCE_EVIDENCE_INSUFFICIENT in result.summary.reasons
