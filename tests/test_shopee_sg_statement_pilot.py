from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.invoice_app.parsers.shopee_statement_source_contract import (
    SHOPEE_SG_STATEMENT_SOURCE,
    resolve_statement_source_contract,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    ServiceFeeDetail,
    SettlementAdjustment,
    SettlementIncomeRow,
    StatementSummaryLine,
)
from src.invoice_app.services.shopee_statement_persistence import (
    STATEMENT_DATA_HEADERS,
    StatementBatchAudit,
    StatementCommitBlocked,
    prepare_full_order_existence_statement_commit_plan,
    CommittedStatementReference,
    StatementCommitState,
    preview_active_statement_state,
    validate_active_statement_state,
)
from src.invoice_app.services.shopee_weekly_statement_service import (
    ALREADY_IMPORTED,
    NEEDS_REVIEW,
    READY_TO_COMMIT,
    CommittedOrderSettlementEvidence,
    stage_parsed_shopee_weekly_statement,
)
from src.invoice_app.services.uat2_persistence_schema import (
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
    STATEMENT_SUMMARY_HEADERS,
)


def _income(*, view_by: str, sequence: str, source_row: int) -> SettlementIncomeRow:
    return SettlementIncomeRow(
        sequence_no=sequence,
        view_by=view_by,
        order_id="SG-ORDER-1",
        product_id="SG-PRODUCT-1",
        product_name="Papaya",
        order_creation_date=date(2026, 8, 30),
        payout_completed_date=date(2026, 9, 5),
        release_channel="Seller Wallet",
        order_type="Normal",
        total_released_amount=Decimal("9.90"),
        financial_components={
            "Product Price": Decimal("10.00"),
            "Refund Amount": Decimal("-0.10"),
            "Beverage Container Return Scheme (BCRS) Deposit": Decimal("0.00"),
        },
        source_values={},
        source_row_number=source_row,
    )


def _statement() -> ParsedShopeeWeeklyStatement:
    order = _income(view_by="Order", sequence="1", source_row=2)
    sku = _income(view_by="Sku", sequence="2", source_row=3)
    adjustment = SettlementAdjustment(
        sequence_no="1",
        adjustment_complete_date=date(2026, 9, 5),
        adjustment_type="Adjustment",
        adjustment_reason="Native source fact",
        adjustment_amount=Decimal("-0.10"),
        linked_order_id="SG-ORDER-1",
        payout_completed_date=None,
        source_row_number=4,
    )
    return ParsedShopeeWeeklyStatement(
        source_filename="income.released.sg.xlsx",
        file_hash="sg-pilot-hash",
        statement_period_from=date(2026, 8, 30),
        statement_period_to=date(2026, 9, 5),
        summary_total_released=Decimal("9.90"),
        adjustment_control_total=Decimal("-0.10"),
        adjustment_footer_total=None,
        income_rows=(order, sku),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(adjustment,),
        source_value_issues=(),
        dimension_fallback_sheets=(),
        summary_lines=(
            StatementSummaryLine(
                "Summary", 20, "3. Total Released Amount", "TOTAL", None,
                Decimal("9.90"), "S$",
            ),
        ),
        source_contract_key=SHOPEE_SG_STATEMENT_SOURCE.key,
        income_component_columns=SHOPEE_SG_STATEMENT_SOURCE.income_component_columns,
        service_fee_component_columns=(),
    )


def _audit() -> StatementBatchAudit:
    now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
    return StatementBatchAudit("sg-pilot-batch", now, "pilot@example.test", now)


def test_sg_contract_uses_native_sgd_headers_and_no_shipping_discrepancy_sheet():
    contract = resolve_statement_source_contract("shopee_sg")

    assert contract is SHOPEE_SG_STATEMENT_SOURCE
    assert contract.total_released_header == "Total Released Amount (S$)"
    assert contract.shipping_discrepancy_required is False
    assert "Shipping Fee Discrepancy" not in contract.required_sheets
    assert "Beverage Container Return Scheme (BCRS) Deposit" in contract.income_component_columns


def test_sg_existence_gate_persists_raw_settlement_facts_without_v2_allocation():
    plan = prepare_full_order_existence_statement_commit_plan(
        _statement(),
        audit=_audit(),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
    )
    data_positions = {name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)}
    component_positions = {
        name: index for index, name in enumerate(STATEMENT_FINANCIAL_COMPONENT_HEADERS)
    }
    summary_positions = {name: index for index, name in enumerate(STATEMENT_SUMMARY_HEADERS)}

    assert plan.authority == "FULL_ORDER_CURRENT"
    assert plan.invoice_order_updates == ()
    assert plan.invoice_item_updates == ()
    assert [row[data_positions["record_type"]] for row in plan.rows] == ["ORDER", "SKU", "ADJUSTMENT"]
    assert {row[data_positions["platform"]] for row in plan.rows} == {"Shopee SG"}
    sku_row = plan.rows[1]
    assert sku_row[data_positions["matched_item_index"]] == ""
    assert sku_row[data_positions["match_method"]] == ""
    assert {row[component_positions["platform"]] for row in plan.financial_component_rows} == {"Shopee SG"}
    assert {row[summary_positions["platform"]] for row in plan.summary_rows} == {"Shopee SG"}
    assert {event.platform for event in plan.order_adjustments} == {"Shopee SG"}


def test_sg_existence_gate_fails_closed_when_a_statement_order_is_not_in_full_order():
    with pytest.raises(StatementCommitBlocked, match="missing from Full Order Current"):
        prepare_full_order_existence_statement_commit_plan(
            _statement(),
            audit=_audit(),
            full_order_ids=(),
            validation_passed=True,
            persisted_platform="Shopee SG",
        )


def _committed_order_evidence(statement: ParsedShopeeWeeklyStatement):
    row = statement.order_rows[0]
    return CommittedOrderSettlementEvidence(
        platform="Shopee SG",
        order_id=row.order_id,
        payout_completed_date=row.payout_completed_date,
        total_released_amount=row.total_released_amount,
        release_channel=row.release_channel,
        order_type=row.order_type,
    )


def _committed_state(plan):
    return StatementCommitState(
        {},
        (),
        order_adjustments=plan.order_adjustments,
        committed_statement_rows=plan.rows,
        committed_financial_component_rows=plan.financial_component_rows,
    )


def _with_service_fee_headers(
    statement: ParsedShopeeWeeklyStatement,
    *,
    file_hash: str,
    headers: tuple[str, ...],
    components: dict[str, Decimal],
) -> ParsedShopeeWeeklyStatement:
    return replace(
        statement,
        file_hash=file_hash,
        service_fee_details=(
            ServiceFeeDetail(
                sequence_no="1",
                order_id="SG-ORDER-1",
                components=components,
                source_row_number=9,
            ),
        ),
        service_fee_component_columns=headers,
    )


def _service_fee_plan(statement, batch_id):
    return prepare_full_order_existence_statement_commit_plan(
        statement,
        audit=replace(_audit(), statement_batch_id=batch_id),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )


def test_sg_semantic_overlap_dedupes_exact_facts_and_does_not_block_period_alone():
    base = _statement()
    evidence = (_committed_order_evidence(base),)
    references = (
        CommittedStatementReference("prior-hash", base.statement_period_from, base.statement_period_to),
    )

    duplicate = stage_parsed_shopee_weekly_statement(
        replace(base, file_hash="renamed-but-identical"),
        existing_statements=references,
        existing_order_evidence=evidence,
        evaluate_legacy_order_reconciliation=False,
        semantic_overlap_dedupe=True,
    )
    assert duplicate.result == ALREADY_IMPORTED
    assert duplicate.duplicate_status == "ALREADY_IMPORTED"

    unrelated_rows = tuple(
        replace(row, order_id="SG-ORDER-2") for row in base.income_rows
    )
    overlap_only = stage_parsed_shopee_weekly_statement(
        replace(base, file_hash="overlap-only", income_rows=unrelated_rows),
        existing_statements=references,
        existing_order_evidence=evidence,
        evaluate_legacy_order_reconciliation=False,
        semantic_overlap_dedupe=True,
    )
    assert overlap_only.result == READY_TO_COMMIT
    assert overlap_only.duplicate_status is None


def test_dynamic_service_fee_zero_with_proven_absent_incoming_header_is_duplicate():
    prior = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="prior-dynamic-headers",
            headers=("Current Promo", "PromoXtra 5.45% cap $30"),
            components={
                "Current Promo": Decimal("0.00"),
                "PromoXtra 5.45% cap $30": Decimal("0.00"),
            },
        ),
        "prior-dynamic-headers",
    )
    incoming = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="incoming-header-absent",
            headers=("Current Promo",),
            components={"Current Promo": Decimal("0.00")},
        ),
        "incoming-header-absent",
    )

    result = preview_active_statement_state(incoming, _committed_state(prior))

    assert result.status == "ALREADY_IMPORTED"
    assert result.exact_duplicate_order_ids == ("SG-ORDER-1",)


def test_dynamic_service_fee_zero_equivalence_is_symmetric_only_with_committed_header_provenance():
    prior = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="prior-header-absent",
            headers=("Current Promo",),
            components={"Current Promo": Decimal("0.00")},
        ),
        "prior-header-absent",
    )
    incoming = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="incoming-zero-promo",
            headers=("Current Promo", "PromoXtra 5.45% cap $30"),
            components={
                "Current Promo": Decimal("0.00"),
                "PromoXtra 5.45% cap $30": Decimal("0.00"),
            },
        ),
        "incoming-zero-promo",
    )
    strict = preview_active_statement_state(incoming, _committed_state(prior))
    evidenced_state = replace(
        _committed_state(prior),
        committed_service_fee_component_headers_by_batch={
            "prior-header-absent": ("Current Promo",),
        },
    )
    evidenced = preview_active_statement_state(incoming, evidenced_state)

    assert strict.status == "INCOMPLETE_UNSAFE"
    assert evidenced.status == "ALREADY_IMPORTED"


def test_dynamic_service_fee_overlap_rule_preserves_nonzero_and_present_header_mismatches():
    nonzero_prior = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="prior-nonzero-promo",
            headers=("Current Promo", "PromoXtra 5.45% cap $30"),
            components={
                "Current Promo": Decimal("0.00"),
                "PromoXtra 5.45% cap $30": Decimal("1.00"),
            },
        ),
        "prior-nonzero-promo",
    )
    header_absent = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="incoming-nonzero-header-absent",
            headers=("Current Promo",),
            components={"Current Promo": Decimal("0.00")},
        ),
        "incoming-nonzero-header-absent",
    )
    present_but_missing = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="incoming-header-present-row-missing",
            headers=("Current Promo", "PromoXtra 5.45% cap $30"),
            components={"Current Promo": Decimal("0.00")},
        ),
        "incoming-header-present-row-missing",
    )
    zero_prior = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="prior-zero-promo",
            headers=("Current Promo", "PromoXtra 5.45% cap $30"),
            components={
                "Current Promo": Decimal("0.00"),
                "PromoXtra 5.45% cap $30": Decimal("0.00"),
            },
        ),
        "prior-zero-promo",
    )
    present_nonzero = _service_fee_plan(
        _with_service_fee_headers(
            _statement(),
            file_hash="incoming-present-nonzero",
            headers=("Current Promo", "PromoXtra 5.45% cap $30"),
            components={
                "Current Promo": Decimal("0.00"),
                "PromoXtra 5.45% cap $30": Decimal("1.00"),
            },
        ),
        "incoming-present-nonzero",
    )

    assert preview_active_statement_state(header_absent, _committed_state(nonzero_prior)).status == "INCOMPLETE_UNSAFE"
    assert preview_active_statement_state(present_but_missing, _committed_state(zero_prior)).status == "INCOMPLETE_UNSAFE"
    assert preview_active_statement_state(present_nonzero, _committed_state(zero_prior)).status == "CONFLICT"


def test_dynamic_service_fee_overlap_rule_never_applies_to_an_ordinary_component():
    prior = _service_fee_plan(_statement(), "prior-ordinary-component")
    incoming = replace(
        _service_fee_plan(_statement(), "incoming-ordinary-component"),
        financial_component_rows=tuple(
            row
            for row in _service_fee_plan(_statement(), "incoming-ordinary-component").financial_component_rows
            if row[STATEMENT_FINANCIAL_COMPONENT_HEADERS.index("component_name")] != "Refund Amount"
        ),
    )

    result = preview_active_statement_state(incoming, _committed_state(prior))

    assert result.status == "INCOMPLETE_UNSAFE"


@pytest.mark.parametrize("platform", ["Shopee", "Shopee SG"])
def test_active_preflight_new_and_exact_duplicate_are_market_parity(platform):
    statement = _statement()
    plan = prepare_full_order_existence_statement_commit_plan(
        statement,
        audit=_audit(),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform=platform,
        semantic_overlap_dedupe=True,
    )

    fresh = preview_active_statement_state(plan, StatementCommitState({}, ()))
    duplicate = preview_active_statement_state(plan, _committed_state(plan))

    assert fresh.status == "NEW"
    assert fresh.eligible is True
    assert fresh.new_order_ids == ("SG-ORDER-1",)
    assert duplicate.status == "ALREADY_IMPORTED"
    assert duplicate.eligible is False
    assert duplicate.no_new_facts is True
    assert duplicate.exact_duplicate_order_ids == ("SG-ORDER-1",)


def test_active_preflight_supports_mixed_duplicate_and_new_and_blocks_conflict():
    first = _statement()
    second_order = replace(
        first.order_rows[0],
        order_id="SG-ORDER-2",
        sequence_no="2",
        source_row_number=5,
    )
    mixed = replace(
        first,
        file_hash="mixed-hash",
        income_rows=(*first.income_rows, second_order),
    )
    plan = prepare_full_order_existence_statement_commit_plan(
        mixed,
        audit=_audit(),
        full_order_ids=("SG-ORDER-1", "SG-ORDER-2"),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    first_plan = prepare_full_order_existence_statement_commit_plan(
        first,
        audit=replace(_audit(), statement_batch_id="prior-batch"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    partial = preview_active_statement_state(
        plan,
        _committed_state(first_plan),
    )
    conflicting_statement = replace(
        first,
        file_hash="conflict-hash",
        income_rows=(
            replace(first.order_rows[0], total_released_amount=Decimal("8.88")),
            *first.sku_rows,
        ),
    )
    conflicting_plan = prepare_full_order_existence_statement_commit_plan(
        conflicting_statement,
        audit=replace(_audit(), statement_batch_id="conflict-batch"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    conflict = preview_active_statement_state(
        conflicting_plan,
        _committed_state(first_plan),
    )

    assert partial.status == "MIXED"
    assert partial.exact_duplicate_order_ids == ("SG-ORDER-1",)
    assert partial.new_order_ids == ("SG-ORDER-2",)
    assert partial.eligible is True
    assert partial.proposed_plan is not None
    assert len(partial.proposed_plan.statement.order_rows) == 2
    assert len(partial.proposed_plan.order_comparisons) == 1
    assert conflict.status == "CONFLICT"
    assert conflict.conflict_order_ids == ("SG-ORDER-1",)
    assert conflict.eligible is False


def test_complete_bundle_detects_changed_child_fact_and_incomplete_ledger():
    base = _statement()
    prior_plan = prepare_full_order_existence_statement_commit_plan(
        base,
        audit=replace(_audit(), statement_batch_id="prior-batch"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    changed_components = dict(base.sku_rows[0].financial_components)
    changed_components["Product Price"] = Decimal("10.01")
    changed = replace(
        base,
        file_hash="changed-child",
        income_rows=(
            base.order_rows[0],
            replace(base.sku_rows[0], financial_components=changed_components),
        ),
    )
    changed_plan = prepare_full_order_existence_statement_commit_plan(
        changed,
        audit=replace(_audit(), statement_batch_id="changed-batch"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    changed_result = preview_active_statement_state(
        changed_plan, _committed_state(prior_plan)
    )
    incomplete_state = replace(
        _committed_state(prior_plan),
        committed_financial_component_rows=prior_plan.financial_component_rows[:-1],
    )
    incomplete_result = preview_active_statement_state(prior_plan, incomplete_state)
    data_positions = {
        name: index for index, name in enumerate(STATEMENT_DATA_HEADERS)
    }
    missing_sku_state = replace(
        _committed_state(prior_plan),
        committed_statement_rows=tuple(
            row
            for row in prior_plan.rows
            if row[data_positions["record_type"]] != "SKU"
        ),
    )
    missing_sku_result = preview_active_statement_state(
        prior_plan, missing_sku_state
    )
    component_only_state = replace(
        _committed_state(prior_plan),
        committed_statement_rows=(),
    )
    component_only_result = preview_active_statement_state(
        prior_plan, component_only_state
    )

    assert changed_result.status == "CONFLICT"
    assert changed_result.conflict_order_ids == ("SG-ORDER-1",)
    assert incomplete_result.status == "INCOMPLETE_UNSAFE"
    assert incomplete_result.incomplete_unsafe_order_ids == ("SG-ORDER-1",)
    assert missing_sku_result.status == "INCOMPLETE_UNSAFE"
    assert missing_sku_result.incomplete_unsafe_order_ids == ("SG-ORDER-1",)
    assert component_only_result.status == "INCOMPLETE_UNSAFE"
    assert component_only_result.incomplete_unsafe_order_ids == ("SG-ORDER-1",)


def test_complete_bundle_detects_conflicting_refund_evidence():
    base = _statement()
    prior_plan = prepare_full_order_existence_statement_commit_plan(
        base,
        audit=replace(_audit(), statement_batch_id="refund-prior"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    changed_components = dict(base.sku_rows[0].financial_components)
    changed_components["Refund Amount"] = Decimal("-0.20")
    changed = replace(
        base,
        file_hash="refund-conflict",
        income_rows=(
            base.order_rows[0],
            replace(base.sku_rows[0], financial_components=changed_components),
        ),
    )
    changed_plan = prepare_full_order_existence_statement_commit_plan(
        changed,
        audit=replace(_audit(), statement_batch_id="refund-current"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )

    result = preview_active_statement_state(
        changed_plan, _committed_state(prior_plan)
    )

    assert result.status == "CONFLICT"
    assert result.conflict_order_ids == ("SG-ORDER-1",)


def test_repeated_native_rows_are_compared_as_a_multiset():
    base = _statement()
    sku = base.sku_rows[0]
    repeated_sku = replace(sku, sequence_no="3", source_row_number=8)
    doubled_components = {
        name: (amount * 2 if amount is not None else None)
        for name, amount in base.order_rows[0].financial_components.items()
    }
    repeated = replace(
        base,
        file_hash="repeated-native-rows",
        income_rows=(
            replace(
                base.order_rows[0],
                financial_components=doubled_components,
                total_released_amount=base.order_rows[0].total_released_amount * 2,
            ),
            sku,
            repeated_sku,
        ),
    )
    first_plan = prepare_full_order_existence_statement_commit_plan(
        repeated,
        audit=replace(_audit(), statement_batch_id="repeat-prior"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    reordered = replace(
        repeated,
        file_hash="repeat-reordered",
        income_rows=(repeated.order_rows[0], repeated_sku, sku),
    )
    reordered_plan = prepare_full_order_existence_statement_commit_plan(
        reordered,
        audit=replace(_audit(), statement_batch_id="repeat-current"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )

    result = preview_active_statement_state(
        reordered_plan, _committed_state(first_plan)
    )

    assert result.no_new_facts is True
    assert result.exact_duplicate_order_ids == ("SG-ORDER-1",)


def test_adjustments_classify_independently_and_unlinked_identity_is_preserved():
    base = _statement()
    prior_plan = prepare_full_order_existence_statement_commit_plan(
        base,
        audit=replace(_audit(), statement_batch_id="adjustment-prior"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    new_adjustment = replace(
        base.adjustments[0],
        sequence_no="2",
        source_row_number=9,
        adjustment_complete_date=date(2026, 9, 6),
        linked_order_id="",
    )
    incoming = replace(
        base,
        file_hash="adjustment-mixed",
        adjustments=(*base.adjustments, new_adjustment),
    )
    incoming_plan = prepare_full_order_existence_statement_commit_plan(
        incoming,
        audit=replace(_audit(), statement_batch_id="adjustment-current"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    result = preview_active_statement_state(
        incoming_plan, _committed_state(prior_plan)
    )
    conflicting = replace(
        base,
        file_hash="adjustment-conflict",
        adjustments=(replace(base.adjustments[0], adjustment_amount=Decimal("-0.20")),),
    )
    conflict_plan = prepare_full_order_existence_statement_commit_plan(
        conflicting,
        audit=replace(_audit(), statement_batch_id="adjustment-conflict"),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    conflict = preview_active_statement_state(
        conflict_plan, _committed_state(prior_plan)
    )

    assert result.status == "MIXED"
    assert result.proposed_adjustment_rows == 1
    assert result.skipped_adjustment_rows == 1
    assert result.proposed_plan.order_adjustments[0].linked_order_id == ""
    assert conflict.status == "CONFLICT"
    assert conflict.conflicting_adjustment_rows == 1

def test_sg_conflicting_settlement_evidence_blocks_review_and_fresh_precommit():
    base = _statement()
    changed_order = replace(base.order_rows[0], payout_completed_date=date(2026, 9, 4))
    conflicting = replace(
        base,
        file_hash="conflicting-payout-fact",
        income_rows=(changed_order, *base.sku_rows),
    )
    evidence = (_committed_order_evidence(base),)
    staged = stage_parsed_shopee_weekly_statement(
        conflicting,
        existing_order_evidence=evidence,
        evaluate_legacy_order_reconciliation=False,
        semantic_overlap_dedupe=True,
    )

    assert staged.result == NEEDS_REVIEW
    assert staged.duplicate_status == "SETTLEMENT_EVIDENCE_CONFLICT"
    assert staged.review_reasons == (
        "Conflicting committed settlement evidence for Order ID: SG-ORDER-1",
    )

    plan = prepare_full_order_existence_statement_commit_plan(
        conflicting,
        audit=_audit(),
        full_order_ids=("SG-ORDER-1",),
        validation_passed=True,
        persisted_platform="Shopee SG",
        semantic_overlap_dedupe=True,
    )
    state = StatementCommitState(
        orders={},
        committed_statements=(
            CommittedStatementReference("prior-hash", base.statement_period_from, base.statement_period_to),
        ),
        committed_order_evidence=evidence,
    )
    assert validate_active_statement_state(plan, state) == (
        "SETTLEMENT_EVIDENCE_CONFLICT:SG-ORDER-1",
    )
