from streamlit.testing.v1 import AppTest

from src.invoice_app.services.manual_review_resolution import (
    CORRECTION_DRAFTS_KEY,
    MISSING_INCOME,
    PRODUCT_COUNT_MISMATCH,
    PROMOTION_SUBTOTAL,
    add_draft_product,
    apply_product_draft,
    apply_resolution,
    clear_correction_draft,
    draft_products,
    draft_summary,
    edit_draft_product,
    promotion_group_options,
    promotion_subtotal_resolution_eligibility,
    promotion_subtotal_groups,
    remove_draft_product,
    resolution_capabilities,
    resolution_plan,
    set_draft_promotion_subtotal,
    synchronize_correction_drafts,
)
from src.invoice_app.review_reason_codes import SKU_RESOLUTION_REQUIRED
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.review_reason_codes import (
    INCOME_COMPLETION_ANCHOR_MISSING,
    INCOME_EXTRACTION_MISSING,
    INCOME_SOURCE_INCOMPLETE,
)


def _master():
    return ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "First", "variation_name": "", "unit_selling_price": "10.00", "nav_code": "NAV-1"},
        {"seller_sku": "SKU-2", "product_name": "Second", "variation_name": "Blue", "unit_selling_price": "20.00", "nav_code": "NAV-2"},
    ])


def _review():
    order = {"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "invoice_financial_layout": "NORMAL_ORDER", "merchandise_subtotal": "30.00", "product_price": "30.00", "shipping_subtotal": "0.00", "shipping_fee_paid_by_buyer": "0.00", "shipping_fee_charged_by_logistic_provider": "0.00", "seller_paid_shipping_fee_sst": "0.00", "fees_charges_total": "0.00", "commission_fee": "0.00", "service_fee": "0.00", "transaction_fee": "0.00", "order_income": "30.00", "income_type": "Estimated"}
    return {"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "status": "Manual Review", "reason_code": PRODUCT_COUNT_MISMATCH, "reason": "Product Count Mismatch: source declares 2 products, but 1 product anchors were extracted.", "order_payload": order, "product_payloads": [{"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "seller_sku": "SKU-1", "product_name": "First", "quantity": 1, "unit_price": "10.00", "line_total": "10.00", "line_subtotal": "10.00", "source_line_subtotal": "10.00"}]}


def _sku_resolution_review():
    review = _review()
    review["reason_code"] = SKU_RESOLUTION_REQUIRED
    review["reason"] = "Seller SKU Resolution Required: Source Missing SKU has no Seller SKU in the original Invoice source."
    review["product_payloads"] = [{
        "batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1",
        "seller_sku": "", "sku_missing_in_source": True, "product_name": "Second", "variation": "Blue",
        "quantity": 1, "unit_price": "20.00", "line_total": "20.00", "line_subtotal": "20.00", "source_line_subtotal": "20.00",
    }]
    review["order_payload"].update({"merchandise_subtotal": "20.00", "product_price": "20.00", "order_income": "20.00"})
    return review


def test_manual_sku_resolution_preserves_blank_source_sku_and_requires_product_master_validation():
    review = _sku_resolution_review()
    plan = resolution_plan(review)
    assert plan is not None
    state = {"orders": [], "products": [], "reviews": [review]}

    rejected = apply_resolution(
        state,
        key=plan.key,
        values={"source_confirmed": True, "resolved_seller_skus": {"0": "UNKNOWN"}},
        price_master=_master(),
    )
    assert rejected.resolved is False
    assert state["reviews"] == [review]

    accepted = apply_resolution(
        state,
        key=plan.key,
        values={"source_confirmed": True, "resolved_seller_skus": {"0": "SKU-2"}},
        price_master=_master(),
    )
    assert accepted.resolved is True
    assert state["reviews"] == []
    assert state["products"][0]["seller_sku"] == ""
    assert state["products"][0]["sku_missing_in_source"] is True
    assert state["products"][0]["resolved_seller_sku"] == "SKU-2"


def _income_review():
    return {
        "batch_id": "batch",
        "source_pdf": "source.pdf",
        "platform": "Shopee",
        "order_id": "SHP-INCOME",
        "status": "Manual Review",
        "reason_code": INCOME_EXTRACTION_MISSING,
        "reason": "Income Completion Anchor Missing: Order Income was not extracted.",
        "order_payload": {
            "batch_id": "batch",
            "source_pdf": "source.pdf",
            "source_hash": "original-source-hash",
            "platform": "Shopee",
            "order_id": "SHP-INCOME",
            "invoice_financial_layout": "NORMAL_ORDER",
            "merchandise_subtotal": "25.00",
            "product_price": "25.00",
            "shipping_subtotal": "0.00",
            "shipping_fee_paid_by_buyer": "0.00",
            "shipping_fee_charged_by_logistic_provider": "0.00",
            "seller_paid_shipping_fee_sst": "0.00",
            "fees_charges_total": "-3.00",
            "commission_fee": "-1.00",
            "service_fee": "-1.00",
            "transaction_fee": "-1.00",
            "vouchers_rebates_total": "N/A",
            "refund_amount": "N/A",
            "order_income": "N/A",
            "estimated_order_income": "N/A",
            "income_type": "N/A",
            "final_amount": "N/A",
            "fund_transfer_date": "",
            "status": "Manual Review",
        },
        "product_payloads": [
            {
                "batch_id": "batch",
                "source_pdf": "source.pdf",
                "platform": "Shopee",
                "order_id": "SHP-INCOME",
                "seller_sku": "SKU-1",
                "product_name": "First",
                "quantity": 2,
                "line_total": "25.00",
                "line_subtotal": "25.00",
                "source_line_subtotal": "25.00",
            }
        ],
    }


def test_product_count_resolution_adds_only_the_missing_staged_product_and_preserves_source_fields():
    review = _review()
    plan = resolution_plan(review)
    assert plan is not None and plan.issue_type == PRODUCT_COUNT_MISMATCH
    state = {"orders": [], "products": [], "reviews": [review]}
    outcome = apply_resolution(state, key=plan.key, values={"seller_sku": "SKU-2", "product_name": "Second", "variation": "Blue", "quantity": 1, "actual_selling_unit_price": "20.00", "line_subtotal": "20.00", "nav": "IGNORED", "unit_price": "IGNORED"}, price_master=_master())
    assert outcome.resolved is True
    assert state["reviews"] == []
    assert [item["seller_sku"] for item in state["products"]] == ["SKU-1", "SKU-2"]
    assert state["products"][0]["source_pdf"] == "source.pdf"
    assert state["products"][1]["nav"] == "NAV-2"
    assert str(state["products"][1]["master_unit_price"]) == "20.00"
    assert state["products"][1]["line_subtotal"] == "20.00"


def test_resolution_keeps_review_when_product_master_remains_unresolved():
    review = _review()
    plan = resolution_plan(review)
    state = {"orders": [], "products": [], "reviews": [review]}
    outcome = apply_resolution(state, key=plan.key, values={"seller_sku": "UNKNOWN", "product_name": "Unknown", "variation": "", "quantity": 1, "actual_selling_unit_price": "20.00", "line_subtotal": "20.00"}, price_master=_master())
    assert outcome.resolved is False
    assert state["reviews"] == [review]


def test_income_extraction_missing_has_a_source_confirmed_resolution_plan_and_revalidates():
    review = _income_review()
    plan = resolution_plan(review)

    assert plan is not None and plan.issue_type == MISSING_INCOME
    state = {"orders": [], "products": [], "reviews": [review]}
    outcome = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "order_income": "RM22.00",
            "income_type": "Estimated",
            "final_amount": "",
        },
        price_master=_master(),
    )

    assert outcome.resolved is True
    assert state["reviews"] == []
    assert state["orders"][0]["order_income"] == "22.00"
    assert state["orders"][0]["income_type"] == "Estimated"
    assert state["orders"][0]["final_amount"] == "N/A"
    assert state["orders"][0]["source_pdf"] == "source.pdf"
    assert state["orders"][0]["source_hash"] == "original-source-hash"


def test_income_resolution_persists_optional_source_visible_financial_enrichment_without_synthetic_zero():
    review = _income_review()
    review["order_payload"].update(
        {
            "service_fee": "N/A",
            "shipping_fee_rebate_from_shopee": "N/A",
            "ads_escrow_top_up_fee": "N/A",
        }
    )
    plan = resolution_plan(review)
    assert plan is not None and plan.issue_type == MISSING_INCOME
    state = {"orders": [], "products": [], "reviews": [review]}

    outcome = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "order_income": "22.00",
            "income_type": "Estimated",
            "final_amount": "",
            "financial_enrichment": {
                "service_fee": "-1.00",
                "shipping_fee_rebate_from_shopee": "",
                "ads_escrow_top_up_fee": "0.00",
            },
        },
        price_master=_master(),
    )

    assert outcome.resolved is True
    assert state["orders"][0]["service_fee"] == "-1.00"
    assert state["orders"][0]["shipping_fee_rebate_from_shopee"] == "N/A"
    assert state["orders"][0]["ads_escrow_top_up_fee"] == "0.00"


def test_income_resolution_requires_source_confirmation_and_keeps_the_review():
    review = _income_review()
    plan = resolution_plan(review)
    assert plan is not None
    state = {"orders": [], "products": [], "reviews": [review]}

    outcome = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": False,
            "order_income": "22.00",
            "income_type": "Estimated",
            "final_amount": "",
        },
        price_master=_master(),
    )

    assert outcome.resolved is False
    assert "Confirm" in str(outcome.reason)
    assert state["reviews"] == [review]


def test_income_resolution_preserves_final_income_type_and_keeps_component_difference_as_note():
    review = _income_review()
    plan = resolution_plan(review)
    assert plan is not None
    differing_state = {"orders": [], "products": [], "reviews": [review]}

    differing = apply_resolution(
        differing_state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "order_income": "23.00",
            "income_type": "Final",
            "final_amount": "23.00",
        },
        price_master=_master(),
    )

    assert differing.resolved is True
    assert differing_state["reviews"] == []
    assert differing_state["orders"][0]["_financial_evidence_notes"] == (
        "Financial Reconciliation Failed: seller components total 22.00, but Order Income is 23.00.",
    )

    state = {"orders": [], "products": [], "reviews": [_income_review()]}
    resolved = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "order_income": "22.00",
            "income_type": "Final",
            "final_amount": "22.00",
        },
        price_master=_master(),
    )

    assert resolved.resolved is True
    assert state["orders"][0]["income_type"] == "Final"
    assert state["orders"][0]["final_amount"] == "22.00"
    assert state["orders"][0]["estimated_order_income"] == "N/A"


def test_proven_income_source_incomplete_has_no_resolution_plan():
    review = _income_review() | {"reason_code": INCOME_SOURCE_INCOMPLETE}

    assert resolution_plan(review) is None


def test_legacy_income_completion_anchor_is_eligible_for_source_confirmation():
    review = _income_review() | {"reason_code": INCOME_COMPLETION_ANCHOR_MISSING}

    plan = resolution_plan(review)

    assert plan is not None
    assert plan.issue_type == MISSING_INCOME


def _draft_review(declared=4):
    review = _review()
    review["reason"] = f"Product Count Mismatch: source declares {declared} products, but 1 product anchors were extracted."
    return review


def _manual_values(sku, name, price="10.00", *, sku_visible=True, promotion_group_id=""):
    return {
        "seller_sku_visible": sku_visible,
        "seller_sku": sku,
        "product_name": name,
        "variation": "",
        "quantity": 1,
        "actual_selling_unit_price": price,
        "line_subtotal": price,
        "promotion_group_id": promotion_group_id,
    }


def test_two_and_three_missing_products_accumulate_before_apply():
    for declared, missing in ((3, 2), (4, 3)):
        review = _draft_review(declared)
        state = {"orders": [], "products": [], "reviews": [review]}
        key = resolution_plan(review).key
        for index in range(missing):
            outcome = add_draft_product(
                state,
                key=key,
                values=_manual_values(f"SKU-{index + 2}", f"Added {index + 2}"),
            )
            assert outcome.resolved is True
        summary = draft_summary(state, review)
        assert summary.manual_count == missing
        assert summary.remaining_missing == 0


def test_below_declared_count_cannot_apply_and_exceeding_count_is_rejected():
    review = _draft_review(3)
    state = {"orders": [], "products": [], "reviews": [review]}
    key = resolution_plan(review).key
    assert add_draft_product(state, key=key, values=_manual_values("SKU-2", "Second", "20.00")).resolved
    outcome = apply_product_draft(state, key=key, price_master=_master())
    assert outcome.resolved is False
    assert "Add 1 more" in outcome.reason
    assert add_draft_product(state, key=key, values=_manual_values("SKU-3", "Third")).resolved
    rejected = add_draft_product(state, key=key, values=_manual_values("SKU-4", "Fourth"))
    assert rejected.resolved is False
    assert "exceed" in rejected.reason


def test_draft_exactly_reaching_declared_count_runs_complete_revalidation():
    review = _review()
    state = {"orders": [], "products": [], "reviews": [review]}
    key = resolution_plan(review).key
    assert add_draft_product(state, key=key, values=_manual_values("SKU-2", "Second", "20.00")).resolved
    outcome = apply_product_draft(state, key=key, price_master=_master())
    assert outcome.resolved is True
    assert state["reviews"] == []
    assert [product["nav"] for product in state["products"]] == ["NAV-1", "NAV-2"]
    assert CORRECTION_DRAFTS_KEY in state and state[CORRECTION_DRAFTS_KEY] == {}


def test_manual_product_can_be_edited_removed_and_draft_cleared():
    review = _draft_review(3)
    state = {"reviews": [review]}
    key = resolution_plan(review).key
    add_draft_product(state, key=key, values=_manual_values("SKU-2", "Before"))
    assert edit_draft_product(state, key=key, index=0, values=_manual_values("SKU-2", "After")).resolved
    assert draft_products(state, review)[0]["product_name"] == "After"
    assert remove_draft_product(state, key=key, index=0).resolved
    assert draft_products(state, review) == ()
    add_draft_product(state, key=key, values=_manual_values("SKU-2", "Again"))
    clear_correction_draft(state, key=key)
    assert draft_products(state, review) == ()


def test_source_or_batch_change_invalidates_stale_draft():
    review = _draft_review(2)
    state = {"reviews": [review]}
    key = resolution_plan(review).key
    add_draft_product(state, key=key, values=_manual_values("SKU-2", "Second", "20.00"))
    changed = dict(review, batch_id="new-batch")
    state["reviews"] = [changed]
    synchronize_correction_drafts(state)
    assert state[CORRECTION_DRAFTS_KEY] == {}


def test_seller_sku_presence_is_explicit_and_missing_source_sku_is_not_guessed():
    review = _draft_review(2)
    state = {"reviews": [review]}
    key = resolution_plan(review).key
    assert add_draft_product(state, key=key, values=_manual_values("SKU-2", "Second", "20.00")).resolved
    visible = draft_products(state, review)[0]
    assert visible["seller_sku"] == "SKU-2"
    assert visible["sku_missing_in_source"] is False
    clear_correction_draft(state, key=key)
    assert add_draft_product(state, key=key, values=_manual_values("", "Second", "20.00", sku_visible=False)).resolved
    missing = draft_products(state, review)[0]
    assert missing["seller_sku"] == ""
    assert missing["sku_missing_in_source"] is True


def test_missing_source_sku_uses_deterministic_name_variation_match_and_conflict_stays_blocking():
    review = _review()
    state = {"orders": [], "products": [], "reviews": [review]}
    key = resolution_plan(review).key
    values = _manual_values("", "Second", "20.00", sku_visible=False)
    values["variation"] = "Blue"
    assert add_draft_product(state, key=key, values=values).resolved
    assert apply_product_draft(state, key=key, price_master=_master()).resolved
    assert state["products"][1]["seller_sku"] == ""
    assert state["products"][1]["nav"] == "NAV-2"

    conflict_review = _review()
    conflict_state = {"orders": [], "products": [], "reviews": [conflict_review]}
    conflict_key = resolution_plan(conflict_review).key
    conflict_master = ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "First", "unit_selling_price": "10.00", "nav_code": "NAV-1"},
        {"seller_sku": "X-1", "product_name": "Second", "variation_name": "Blue", "unit_selling_price": "20.00", "nav_code": "NAV-2"},
        {"seller_sku": "X-2", "product_name": "Second", "variation_name": "Blue", "unit_selling_price": "21.00", "nav_code": "NAV-3"},
    ])
    assert add_draft_product(conflict_state, key=conflict_key, values=values).resolved
    blocked = apply_product_draft(conflict_state, key=conflict_key, price_master=conflict_master)
    assert blocked.resolved is False
    assert conflict_state["reviews"] == [conflict_review]


def _promotion_review(
    *,
    source_status="visible_unresolved",
    reliable=True,
    complete=False,
    advertised_amount="20.00",
):
    review = _review()
    review["reason_code"] = "INCOMPLETE_PROMOTION_EVIDENCE"
    review["reason"] = "INCOMPLETE_PROMOTION_EVIDENCE: subtotal needs review."
    member = review["product_payloads"][0]
    member.update({
        "promotion_group_id": "source-group-1",
        "promotion_label": "Any 2 at RM20.00",
        "promotion_advertised_amount": advertised_amount,
        "promotion_target_qty": 2,
        "promotion_member_qty": 1,
        "_promotion_boundary_status": "reliable" if reliable else "ambiguous",
        "_promotion_member_ownership_status": "reliable" if reliable else "ambiguous",
        "_promotion_subtotal_source_status": source_status,
        "promotion_metadata_status": "incomplete",
        "source_line_subtotal": "N/A",
        "line_total": "N/A",
        "line_subtotal": "N/A",
    })
    if complete:
        member["source_group_total"] = "20.00"
        member["promotion_group_total"] = "20.00"
        member["_promotion_subtotal_source_status"] = "resolved"
        member.pop("promotion_metadata_status")
    review["order_payload"]["merchandise_subtotal"] = "20.00"
    review["order_payload"]["product_price"] = "20.00"
    review["order_payload"]["order_income"] = "20.00"
    return review


def _promotion_master():
    return ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "First", "variation_name": "", "unit_selling_price": "20.00", "nav_code": "NAV-1"},
    ])


def test_only_reliable_existing_promotion_groups_are_selectable_and_metadata_is_preserved():
    review = _promotion_review(complete=True)
    review["reason_code"] = PRODUCT_COUNT_MISMATCH
    review["reason"] = "Product Count Mismatch: source declares 2 products, but 1 product anchors were extracted."
    options = promotion_group_options(review)
    assert [option.group_id for option in options] == ["source-group-1"]
    state = {"reviews": [review]}
    key = resolution_plan(review).key
    values = _manual_values("SKU-2", "Second", "20.00", promotion_group_id="source-group-1")
    assert add_draft_product(state, key=key, values=values).resolved
    added = draft_products(state, review)[0]
    assert added["promotion_label"] == "Any 2 at RM20.00"
    assert added["promotion_advertised_amount"] == "20.00"
    assert added["source_group_total"] == "20.00"
    clear_correction_draft(state, key=key)
    bad = add_draft_product(state, key=key, values={**values, "promotion_group_id": "typed-id"})
    assert bad.resolved is False


def test_no_promotion_is_supported_and_membership_correction_reruns_allocation():
    no_promo_review = _review()
    no_promo_state = {"orders": [], "products": [], "reviews": [no_promo_review]}
    no_promo_key = resolution_plan(no_promo_review).key
    assert add_draft_product(no_promo_state, key=no_promo_key, values=_manual_values("SKU-2", "Second", "20.00")).resolved
    assert "promotion_group_id" not in draft_products(no_promo_state, no_promo_review)[0]

    review = _promotion_review(complete=True)
    review["reason_code"] = PRODUCT_COUNT_MISMATCH
    review["reason"] = "Product Count Mismatch: source declares 2 products, but 1 product anchors were extracted."
    state = {"orders": [], "products": [], "reviews": [review]}
    key = resolution_plan(review).key
    values = _manual_values("SKU-2", "Second", "20.00", promotion_group_id="source-group-1")
    assert add_draft_product(state, key=key, values=values).resolved
    outcome = apply_product_draft(state, key=key, price_master=ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "First", "unit_selling_price": "20.00", "nav_code": "NAV-1"},
        {"seller_sku": "SKU-2", "product_name": "Second", "unit_selling_price": "20.00", "nav_code": "NAV-2"},
    ]))
    assert outcome.resolved is True
    assert {product["promotion_member_qty"] for product in state["products"]} == {1}
    assert {product["participating_qty"] for product in state["products"]} == {2}
    assert {str(product["source_group_total"]) for product in state["products"]} == {"20.00"}


def test_product_count_draft_can_include_safe_case_one_subtotal_before_one_apply():
    review = _promotion_review()
    review["reason_code"] = PRODUCT_COUNT_MISMATCH
    review["reason"] = "Product Count Mismatch: source declares 2 products, but 1 product anchors were extracted."
    state = {"orders": [], "products": [], "reviews": [review]}
    key = resolution_plan(review).key
    assert add_draft_product(
        state,
        key=key,
        values=_manual_values("SKU-2", "Second", "20.00", promotion_group_id="source-group-1"),
    ).resolved
    assert set_draft_promotion_subtotal(
        state,
        key=key,
        group_id="source-group-1",
        value="20.00",
        source_confirmed=True,
    ).resolved
    outcome = apply_product_draft(state, key=key, price_master=ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "First", "unit_selling_price": "20.00", "nav_code": "NAV-1"},
        {"seller_sku": "SKU-2", "product_name": "Second", "unit_selling_price": "20.00", "nav_code": "NAV-2"},
    ]))
    assert outcome.resolved is True
    assert {str(product["source_group_total"]) for product in state["products"]} == {"20.00"}


def test_ambiguous_group_remains_unresolvable_but_label_amount_can_confirm_missing_subtotal():
    ambiguous = _promotion_review(reliable=False)
    absent = _promotion_review(source_status="absent")
    assert promotion_group_options(ambiguous) == ()
    assert promotion_subtotal_groups(ambiguous) == ()
    assert resolution_plan(ambiguous) is None
    assert promotion_subtotal_groups(absent)[0].advertised_amount == "20.00"
    assert resolution_plan(absent).issue_type == PROMOTION_SUBTOTAL


def test_missing_anchors_ambiguous_ownership_and_no_source_amount_fail_closed():
    missing_anchors = _promotion_review(source_status="absent")
    missing_anchors["product_payloads"] = []
    ambiguous_members = _promotion_review(source_status="absent")
    ambiguous_members["product_payloads"][0][
        "_promotion_member_ownership_status"
    ] = "ambiguous"
    no_source_amount = _promotion_review(
        source_status="absent", advertised_amount=None
    )

    for review in (missing_anchors, ambiguous_members, no_source_amount):
        eligibility = promotion_subtotal_resolution_eligibility(review)
        assert eligibility.eligible is False
        assert promotion_subtotal_groups(review) == ()
        assert resolution_plan(review) is None


def test_source_visible_advertised_amount_requires_exact_user_confirmation():
    review = _promotion_review(source_status="absent")
    plan = resolution_plan(review)
    state = {"orders": [], "products": [], "reviews": [review]}

    rejected = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "promotion_group_id": "source-group-1",
            "source_group_total": "16.00",
        },
        price_master=_promotion_master(),
    )

    assert rejected.resolved is False
    assert rejected.reason == (
        "Promotion Subtotal must match the source-visible amount RM20.00."
    )
    assert state["reviews"] == [review]


def test_label_visible_missing_subtotal_reruns_full_revalidation():
    review = _promotion_review(source_status="absent")
    plan = resolution_plan(review)
    state = {"orders": [], "products": [], "reviews": [review]}

    outcome = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "promotion_subtotals": {"source-group-1": "20.00"},
        },
        price_master=_promotion_master(),
    )

    assert outcome.resolved is True
    assert state["reviews"] == []
    assert state["products"][0]["_promotion_subtotal_resolution"] == (
        "source_confirmed_manual"
    )


def test_missing_sku_and_promotion_subtotal_are_applied_in_one_full_revalidation():
    review = _promotion_review(source_status="absent")
    review["reason_code"] = SKU_RESOLUTION_REQUIRED
    review["reason"] = "Seller SKU Resolution Required."
    review["product_payloads"][0].update({"seller_sku": "", "sku_missing_in_source": True})
    assert set(resolution_capabilities(review)) == {"SKU_RESOLUTION", "PROMOTION_SUBTOTAL"}
    state = {"orders": [], "products": [], "reviews": [review]}

    outcome = apply_resolution(
        state,
        key=resolution_plan(review).key,
        values={
            "sku_source_confirmed": True,
            "resolved_seller_skus": {"0": "SKU-1"},
            "promotion_source_confirmed": True,
            "promotion_subtotals": {"source-group-1": "20.00"},
        },
        price_master=_promotion_master(),
    )

    assert outcome.resolved is True
    assert state["products"][0]["seller_sku"] == ""
    assert state["products"][0]["resolved_seller_sku"] == "SKU-1"
    assert str(state["products"][0]["source_group_total"]) == "20.00"


def test_combined_resolution_rejects_invalid_sku_or_wrong_source_subtotal():
    review = _promotion_review(source_status="absent")
    review["reason_code"] = SKU_RESOLUTION_REQUIRED
    review["reason"] = "Seller SKU Resolution Required."
    review["product_payloads"][0].update({"seller_sku": "", "sku_missing_in_source": True})
    key = resolution_plan(review).key
    base = {
        "sku_source_confirmed": True,
        "promotion_source_confirmed": True,
    }

    wrong_subtotal_state = {"orders": [], "products": [], "reviews": [review]}
    wrong_subtotal = apply_resolution(
        wrong_subtotal_state,
        key=key,
        values={**base, "resolved_seller_skus": {"0": "SKU-1"}, "promotion_subtotals": {"source-group-1": "16.00"}},
        price_master=_promotion_master(),
    )
    assert wrong_subtotal.resolved is False
    assert wrong_subtotal_state["reviews"] == [review]

    invalid_sku_state = {"orders": [], "products": [], "reviews": [review]}
    invalid_sku = apply_resolution(
        invalid_sku_state,
        key=key,
        values={**base, "resolved_seller_skus": {"0": "UNKNOWN"}, "promotion_subtotals": {"source-group-1": "20.00"}},
        price_master=_promotion_master(),
    )
    assert invalid_sku.resolved is False
    assert invalid_sku_state["reviews"] == [review]


def test_other_ambiguous_promotion_group_keeps_review_after_valid_subtotal_entry():
    review = _promotion_review(source_status="absent")
    ambiguous = dict(review["product_payloads"][0])
    ambiguous.update(
        {
            "promotion_group_id": "source-group-2",
            "promotion_label": "Any 2 enjoy 10% off",
            "promotion_advertised_amount": None,
            "source_group_total": "20.00",
            "promotion_group_total": "20.00",
            "_promotion_member_ownership_status": "ambiguous",
            "_promotion_subtotal_source_status": "resolved",
            "promotion_metadata_status": "incomplete",
            "promotion_incomplete_reason": "Promotion member ownership is ambiguous.",
        }
    )
    review["product_payloads"].append(ambiguous)
    review["order_payload"].update(
        {"merchandise_subtotal": "40.00", "product_price": "40.00"}
    )
    eligibility = promotion_subtotal_resolution_eligibility(review)
    assert [group.group_id for group in eligibility.groups] == ["source-group-1"]
    state = {"orders": [], "products": [], "reviews": [review]}

    outcome = apply_resolution(
        state,
        key=resolution_plan(review).key,
        values={
            "source_confirmed": True,
            "promotion_subtotals": {"source-group-1": "20.00"},
        },
        price_master=_promotion_master(),
    )

    assert outcome.resolved is False
    assert "INCOMPLETE_PROMOTION_EVIDENCE" in outcome.reason
    assert state["reviews"] == [review]


def test_future_order_with_same_structured_evidence_is_not_id_hardcoded():
    review = _promotion_review(source_status="absent")
    review["order_id"] = "FUTURE-ORDER-999"
    review["source_pdf"] = "future-source.pdf"
    review["order_payload"].update(
        {"order_id": "FUTURE-ORDER-999", "source_pdf": "future-source.pdf"}
    )
    for product in review["product_payloads"]:
        product.update(
            {"order_id": "FUTURE-ORDER-999", "source_pdf": "future-source.pdf"}
        )

    plan = resolution_plan(review)

    assert plan is not None
    assert plan.issue_type == PROMOTION_SUBTOTAL


def _promotion_manual_review_routing_app():
    from copy import deepcopy

    import streamlit as st

    from src.invoice_app.ui import data_import

    eligible = {
        "batch_id": "batch",
        "source_pdf": "eligible.pdf",
        "platform": "Shopee",
        "order_id": "ELIGIBLE",
        "status": "Manual Review",
        "reason_code": "INCOMPLETE_PROMOTION_EVIDENCE",
        "reason": "INCOMPLETE_PROMOTION_EVIDENCE: subtotal missing.",
        "order_payload": {"order_id": "ELIGIBLE"},
        "product_payloads": [
            {
                "product_name": "Product A",
                "seller_sku": "SKU-A",
                "quantity": 1,
                "promotion_group_id": "group-1",
                "promotion_label": "Any 2 at RM20.00",
                "promotion_advertised_amount": "20.00",
                "promotion_target_qty": 2,
                "_promotion_boundary_status": "reliable",
                "_promotion_member_ownership_status": "reliable",
                "_promotion_subtotal_source_status": "absent",
            },
            {
                "product_name": "Product B",
                "seller_sku": "SKU-B",
                "quantity": 1,
                "promotion_group_id": "group-1",
                "promotion_label": "Any 2 at RM20.00",
                "promotion_advertised_amount": "20.00",
                "promotion_target_qty": 2,
                "_promotion_boundary_status": "reliable",
                "_promotion_member_ownership_status": "reliable",
                "_promotion_subtotal_source_status": "absent",
            },
        ],
    }
    unfixable = deepcopy(eligible)
    unfixable.update(
        {
            "source_pdf": "unfixable.pdf",
            "order_id": "UNFIXABLE",
        }
    )
    unfixable["product_payloads"][0]["promotion_advertised_amount"] = None
    unfixable["product_payloads"][1]["promotion_advertised_amount"] = None
    st.session_state.setdefault("batch_id", "batch")
    st.session_state.setdefault("orders", [])
    st.session_state.setdefault("products", [])
    st.session_state.setdefault("reviews", [eligible, unfixable])
    data_import._render_manual_review_resolution()


def test_fixable_promotion_routes_to_online_resolution_tab_with_source_evidence():
    app = AppTest.from_function(_promotion_manual_review_routing_app)

    app.run(timeout=20)

    assert app.exception == []
    assert {tab.label for tab in app.tabs} == {
        "⚠️ Requires Re-upload (1)",
        "📝 Online Resolution (1)",
    }
    captions = {caption.value for caption in app.caption}
    assert "Promotion: Any 2 at RM20.00" in captions
    assert "Source subtotal: Not extracted" in captions
    assert "Source-visible promotion amount: RM20.00" in captions
    assert any(
        checkbox.label == "I confirmed this subtotal from the original Invoice."
        for checkbox in app.checkbox
    )


def _combined_manual_review_routing_app():
    import streamlit as st

    from src.invoice_app.ui import data_import

    review = {
        "batch_id": "batch", "source_pdf": "combined.pdf", "platform": "Shopee",
        "order_id": "COMBINED", "status": "Manual Review",
        "reason_code": "SKU_RESOLUTION_REQUIRED", "reason": "Seller SKU Resolution Required.",
        "order_payload": {"order_id": "COMBINED"},
        "product_payloads": [{
            "product_name": "First", "seller_sku": "", "sku_missing_in_source": True,
            "quantity": 1, "promotion_group_id": "group-1",
            "promotion_label": "Any 1 at RM20.00", "promotion_advertised_amount": "20.00",
            "promotion_target_qty": 1, "_promotion_boundary_status": "reliable",
            "_promotion_member_ownership_status": "reliable",
            "_promotion_subtotal_source_status": "absent",
        }],
    }
    st.session_state.setdefault("batch_id", "batch")
    st.session_state.setdefault("orders", [])
    st.session_state.setdefault("products", [])
    st.session_state.setdefault("reviews", [review])
    data_import._render_manual_review_resolution()


def test_combined_missing_sku_and_promotion_controls_share_one_apply_action():
    app = AppTest.from_function(_combined_manual_review_routing_app)

    app.run(timeout=20)

    assert app.exception == []
    assert {field.label for field in app.text_input} >= {"Resolved Seller SKU", "Promotion Subtotal"}
    assert len([button for button in app.button if button.label == "Apply & Revalidate"]) == 1


def test_case_one_source_visible_subtotal_reruns_full_revalidation():
    review = _promotion_review()
    plan = resolution_plan(review)
    assert plan is not None and plan.issue_type == PROMOTION_SUBTOTAL
    state = {"orders": [], "products": [], "reviews": [review]}
    outcome = apply_resolution(
        state,
        key=plan.key,
        values={"source_confirmed": True, "promotion_group_id": "source-group-1", "source_group_total": "RM20.00"},
        price_master=_promotion_master(),
    )
    assert outcome.resolved is True
    assert str(state["products"][0]["source_group_total"]) == "20.00"
    assert state["products"][0]["nav"] == "NAV-1"


def test_case_one_subtotal_keeps_financial_difference_as_non_blocking_note():
    review = _promotion_review()
    plan = resolution_plan(review)
    review["order_payload"]["order_income"] = "19.00"
    state = {"orders": [], "products": [], "reviews": [review]}
    outcome = apply_resolution(state, key=plan.key, values={"source_confirmed": True, "promotion_group_id": "source-group-1", "source_group_total": "20.00"}, price_master=_promotion_master())
    assert outcome.resolved is True
    assert state["orders"][0]["_financial_evidence_notes"] == (
        "Financial Reconciliation Failed: seller components total 20.00, but Order Income is 19.00.",
    )
