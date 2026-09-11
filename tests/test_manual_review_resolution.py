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
    promotion_subtotal_groups,
    remove_draft_product,
    resolution_plan,
    set_draft_promotion_subtotal,
    synchronize_correction_drafts,
)
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


def test_income_resolution_preserves_final_income_type_and_rejects_unreconciled_amounts():
    review = _income_review()
    plan = resolution_plan(review)
    assert plan is not None
    state = {"orders": [], "products": [], "reviews": [review]}

    rejected = apply_resolution(
        state,
        key=plan.key,
        values={
            "source_confirmed": True,
            "order_income": "23.00",
            "income_type": "Final",
            "final_amount": "23.00",
        },
        price_master=_master(),
    )

    assert rejected.resolved is False
    assert "Financial Reconciliation Failed" in str(rejected.reason)
    assert state["reviews"] == [review]

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


def _promotion_review(*, source_status="visible_unresolved", reliable=True, complete=False):
    review = _review()
    review["reason_code"] = "INCOMPLETE_PROMOTION_EVIDENCE"
    review["reason"] = "INCOMPLETE_PROMOTION_EVIDENCE: subtotal needs review."
    member = review["product_payloads"][0]
    member.update({
        "promotion_group_id": "source-group-1",
        "promotion_label": "Any 2 at RM20.00",
        "promotion_advertised_amount": "20.00",
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


def test_ambiguous_group_and_source_absent_subtotal_are_not_resolvable():
    ambiguous = _promotion_review(reliable=False)
    absent = _promotion_review(source_status="absent")
    assert promotion_group_options(ambiguous) == ()
    assert promotion_subtotal_groups(ambiguous) == ()
    assert resolution_plan(ambiguous) is None
    assert promotion_subtotal_groups(absent) == ()
    assert resolution_plan(absent) is None


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


def test_case_one_subtotal_cannot_bypass_financial_or_product_master_failure():
    review = _promotion_review()
    plan = resolution_plan(review)
    review["order_payload"]["order_income"] = "19.00"
    state = {"orders": [], "products": [], "reviews": [review]}
    failed = apply_resolution(state, key=plan.key, values={"source_confirmed": True, "promotion_group_id": "source-group-1", "source_group_total": "20.00"}, price_master=_promotion_master())
    assert failed.resolved is False
    assert "Financial Reconciliation Failed" in failed.reason
