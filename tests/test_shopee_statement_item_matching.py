from decimal import Decimal

from src.invoice_app.domain.historical_invoice import CanonicalInvoiceItem
from src.invoice_app.parsers.shopee_weekly_statement_parser import SettlementIncomeRow
from src.invoice_app.services.shopee_statement_item_matching import (
    MappingProductFamilyResolver,
    ProductFamilyCandidate,
    StatementItemMatchStatus,
    VerifiedArtifactRepair,
    match_statement_sku_rows,
    normalize_statement_product_name,
    product_family_resolver_from_price_master,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


def _row(
    *,
    order_id: str = "ORDER-1",
    product_id: str = "PRODUCT-1",
    product_name: str = "Green Tea",
    product_price: Decimal | None = Decimal("10.00"),
    source_row: int = 2,
) -> SettlementIncomeRow:
    return SettlementIncomeRow(
        sequence_no="1",
        view_by="Sku",
        order_id=order_id,
        product_id=product_id,
        product_name=product_name,
        order_creation_date=None,
        payout_completed_date=None,
        release_channel="",
        order_type="",
        total_released_amount=None,
        financial_components={"Product Price": product_price},
        source_values={},
        source_row_number=source_row,
    )


def _item(
    index: int,
    *,
    order_id: str = "ORDER-1",
    sku: str = "SKU-1",
    name: str = "Green Tea",
    variation: str = "",
    subtotal: str = "10.00",
    promotion: bool = False,
    source_pdf: str = "invoice.pdf",
) -> CanonicalInvoiceItem:
    return CanonicalInvoiceItem(
        platform="Shopee",
        order_id=order_id,
        item_index=index,
        seller_sku=sku,
        product_name=name,
        variation=variation,
        quantity=1,
        actual_selling_unit_price=Decimal(subtotal),
        line_subtotal=Decimal(subtotal),
        promotion_group_id="promo-1" if promotion else None,
        promotion_label="Any 2 at RM18" if promotion else None,
        source_group_total=Decimal("18.00") if promotion else None,
        source_pdf=source_pdf,
    )


def _resolver(*candidates: ProductFamilyCandidate):
    return MappingProductFamilyResolver(
        {candidate.product_id: tuple(
            member for member in candidates if member.product_id == candidate.product_id
        ) for candidate in candidates}
    )


def _family(
    *,
    product_id: str = "PRODUCT-1",
    seller_sku: str = "SKU-1",
    parent_sku: str = "",
    product_name: str = "Green Tea",
    variation: str = "",
) -> ProductFamilyCandidate:
    return ProductFamilyCandidate(
        product_id=product_id,
        seller_sku=seller_sku,
        parent_sku=parent_sku,
        product_name=product_name,
        variation=variation,
    )


def test_name_normalization_is_nfkc_trim_collapse_and_case_insensitive_only():
    assert normalize_statement_product_name("  ＧＲＥＥＮ\t Tea  ") == "green tea"
    assert normalize_statement_product_name("F ree") != normalize_statement_product_name("Free")


def test_unique_identity_matches_even_when_item_has_promotion():
    result = match_statement_sku_rows(
        [_row()],
        [_item(1, promotion=True)],
        product_families=_resolver(_family()),
    )

    assert result.eligible_for_commit is True
    assert result.matches[0].status is StatementItemMatchStatus.MATCHED
    assert result.matches[0].match_method == "EXACT_PRODUCT_NAME"
    assert result.matches[0].invoice_item_index == 1


def test_product_id_family_filters_candidates_but_never_finalizes_by_itself():
    result = match_statement_sku_rows(
        [_row(product_name="Missing Name")],
        [_item(1), _item(2, sku="SKU-OTHER", name="Missing Name")],
        product_families=_resolver(_family()),
    )

    assert result.eligible_for_commit is False
    assert result.matches[0].status is StatementItemMatchStatus.NEEDS_REVIEW


def test_price_master_records_build_exact_product_id_variant_families():
    master = ProductPriceMaster.from_rows(
        [
            {
                "product_id": "PRODUCT-1",
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Green Tea",
                "variation_name": "Original",
                "unit_selling_price": "10.00",
            },
            {
                "product_id": "PRODUCT-1",
                "seller_sku": "SKU-2",
                "parent_sku": "PARENT-1",
                "product_name": "Green Tea",
                "variation_name": "Large",
                "unit_selling_price": "12.00",
            },
        ]
    )

    resolver = product_family_resolver_from_price_master(master)

    assert [candidate.seller_sku for candidate in resolver.resolve("PRODUCT-1")] == [
        "SKU-1",
        "SKU-2",
    ]
    assert resolver.resolve("product-1") == ()


def test_parent_sku_family_evidence_can_narrow_to_exact_name():
    result = match_statement_sku_rows(
        [_row()],
        [_item(1, sku="PARENT-1"), _item(2, sku="OTHER", name="Green Tea")],
        product_families=_resolver(_family(seller_sku="", parent_sku="PARENT-1")),
    )

    assert result.eligible_for_commit is True
    assert result.matches[0].invoice_item_index == 1


def test_non_promotion_same_name_uses_unique_line_subtotal_with_rm002_tolerance():
    result = match_statement_sku_rows(
        [_row(product_price=Decimal("12.02"))],
        [_item(1, sku="PARENT-1", subtotal="10.00"), _item(2, sku="PARENT-1", subtotal="12.00")],
        product_families=_resolver(_family(seller_sku="", parent_sku="PARENT-1")),
    )

    assert result.eligible_for_commit is True
    assert result.matches[0].invoice_item_index == 2
    assert result.matches[0].match_method == "LINE_SUBTOTAL_TIE_BREAK"


def test_ambiguous_promotion_does_not_use_amount_tie_break():
    result = match_statement_sku_rows(
        [_row(product_price=Decimal("12.00"))],
        [
            _item(1, sku="PARENT-1", subtotal="10.00", promotion=True),
            _item(2, sku="PARENT-1", subtotal="12.00", promotion=True),
        ],
        product_families=_resolver(_family(seller_sku="", parent_sku="PARENT-1")),
    )

    assert result.eligible_for_commit is False
    assert "prohibited" in result.matches[0].reason


def test_line_subtotal_tie_break_must_leave_exactly_one_candidate():
    result = match_statement_sku_rows(
        [_row(product_price=Decimal("10.01"))],
        [_item(1, sku="PARENT-1", subtotal="10.00"), _item(2, sku="PARENT-1", subtotal="10.02")],
        product_families=_resolver(_family(seller_sku="", parent_sku="PARENT-1")),
    )

    assert result.eligible_for_commit is False
    assert result.matches[0].status is StatementItemMatchStatus.NEEDS_REVIEW


def test_internal_space_artifact_requires_explicit_pdf_bound_unique_repair():
    item = _item(1, name="F ree Gift")
    without_repair = match_statement_sku_rows(
        [_row(product_name="Free Gift")],
        [item],
        product_families=_resolver(_family(product_name="Free Gift")),
    )
    with_repair = match_statement_sku_rows(
        [_row(product_name="Free Gift")],
        [item],
        product_families=_resolver(_family(product_name="Free Gift")),
        verified_artifact_repairs=[
            VerifiedArtifactRepair("invoice.pdf", "F ree Gift", "Free Gift")
        ],
    )

    assert without_repair.eligible_for_commit is False
    assert with_repair.eligible_for_commit is True
    assert with_repair.matches[0].match_method == "VERIFIED_ARTIFACT_REPAIR"


def test_verified_repair_must_leave_one_candidate_in_order_and_family():
    result = match_statement_sku_rows(
        [_row(product_name="Free Gift")],
        [_item(1, sku="PARENT-1", name="F ree Gift"), _item(2, sku="PARENT-1", name="F ree Gift")],
        product_families=_resolver(_family(seller_sku="", parent_sku="PARENT-1")),
        verified_artifact_repairs=[
            VerifiedArtifactRepair("invoice.pdf", "F ree Gift", "Free Gift")
        ],
    )

    assert result.eligible_for_commit is False
    assert result.matches[0].status is StatementItemMatchStatus.NEEDS_REVIEW


def test_one_unresolved_row_blocks_the_whole_batch():
    result = match_statement_sku_rows(
        [_row(), _row(order_id="ORDER-2", source_row=3)],
        [_item(1)],
        product_families=_resolver(_family()),
    )

    assert [match.status for match in result.matches] == [
        StatementItemMatchStatus.MATCHED,
        StatementItemMatchStatus.NEEDS_REVIEW,
    ]
    assert result.eligible_for_commit is False


def test_duplicate_invoice_item_consumption_alone_does_not_downgrade_matches():
    result = match_statement_sku_rows(
        [_row(source_row=2), _row(source_row=3)],
        [_item(1)],
        product_families=_resolver(_family()),
    )

    assert result.eligible_for_commit is True
    assert all(
        match.status is StatementItemMatchStatus.MATCHED
        for match in result.matches
    )
