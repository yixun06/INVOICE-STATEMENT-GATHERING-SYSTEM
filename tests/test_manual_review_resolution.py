from src.invoice_app.services.manual_review_resolution import (
    MISSING_INCOME, PRODUCT_COUNT_MISMATCH, apply_resolution, resolution_plan,
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
    return {"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "status": "Manual Review", "reason_code": PRODUCT_COUNT_MISMATCH, "reason": "Product Count Mismatch: source declares 2 products, but 1 product anchors were extracted.", "order_payload": {"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "order_income": "30.00", "income_type": "Estimated"}, "product_payloads": [{"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "seller_sku": "SKU-1", "product_name": "First", "quantity": 1, "line_total": "10.00", "line_subtotal": "10.00", "source_line_subtotal": "10.00"}]}


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
            "merchandise_subtotal": "25.00",
            "product_price": "25.00",
            "shipping_subtotal": "0.00",
            "fees_charges_total": "-3.00",
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
    assert "nav" not in state["products"][1]
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
