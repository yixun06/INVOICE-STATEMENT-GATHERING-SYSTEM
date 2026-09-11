from src.invoice_app.services.manual_review_resolution import (
    PRODUCT_COUNT_MISMATCH, apply_resolution, resolution_plan,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


def _master():
    return ProductPriceMaster.from_rows([
        {"seller_sku": "SKU-1", "product_name": "First", "variation_name": "", "unit_selling_price": "10.00", "nav_code": "NAV-1"},
        {"seller_sku": "SKU-2", "product_name": "Second", "variation_name": "Blue", "unit_selling_price": "20.00", "nav_code": "NAV-2"},
    ])


def _review():
    return {"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "status": "Manual Review", "reason_code": PRODUCT_COUNT_MISMATCH, "reason": "Product Count Mismatch: source declares 2 products, but 1 product anchors were extracted.", "order_payload": {"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "order_income": "30.00", "income_type": "Estimated"}, "product_payloads": [{"batch_id": "batch", "source_pdf": "source.pdf", "platform": "Shopee", "order_id": "SHP-1", "seller_sku": "SKU-1", "product_name": "First", "quantity": 1, "line_total": "10.00", "line_subtotal": "10.00", "source_line_subtotal": "10.00"}]}


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
