from decimal import Decimal

from src.invoice_app.pdf_document import PdfWord
from src.invoice_app.parsers.shopee_extractor import ShopeeExtractedData
from src.invoice_app.parsers.shopee_mapper import map_shopee_products
from src.invoice_app.parsers.shopee_product_parser import (
    _Row,
    _apply_group_promotion,
    parse_text_products,
    reconcile_product_candidates,
)


def _promotion_row(label: str) -> _Row:
    return _Row(
        top=1.0,
        bottom=2.0,
        words=(PdfWord(text=label, x0=1.0, x1=100.0, top=1.0, bottom=2.0),),
    )


def _promotion_items() -> list[dict[str, object]]:
    return [
        {
            "product_name": "Synthetic Product A",
            "seller_sku": "SYN-A",
            "quantity": 1,
            "unit_price": Decimal("20.00"),
            "line_total": Decimal("20.00"),
            "source_line_subtotal": Decimal("20.00"),
        },
        {
            "product_name": "Synthetic Product B",
            "seller_sku": "SYN-B",
            "quantity": 2,
            "unit_price": Decimal("20.00"),
            "line_total": Decimal("40.00"),
            "source_line_subtotal": Decimal("40.00"),
        },
    ]


def _extracted_data(items: list[dict[str, object]]) -> ShopeeExtractedData:
    return ShopeeExtractedData(
        source_pdf="synthetic.pdf",
        normalized_text="",
        is_courier_only=False,
        order_id="SYNTHETIC-ORDER",
        order_status="To Ship",
        order_created_date="01/01/2026 09:00",
        delivered_date="",
        completed_date="",
        fund_transfer_date="",
        product_items=tuple(items),
        income={},
        buyer_payment={},
        voucher={},
    )


def test_normal_product_preserves_source_subtotal_without_promotion_metadata():
    items = parse_text_products(
        """
        Product(s) Unit Price Quantity Subtotal
        1 Synthetic Normal Product 9.50 2 19.00
        SKU: SYN-NORMAL
        """
    )

    assert len(items) == 1
    assert items[0]["source_line_subtotal"] == Decimal("19.00")
    assert "promotion_group_id" not in items[0]
    assert "promotion_label" not in items[0]


def test_detected_group_preserves_source_evidence_and_legacy_allocation():
    items = _promotion_items()

    _apply_group_promotion(
        items,
        [_promotion_row("Any 3 at RM45.80")],
        promotion_group_id="shopee-promotion:p1:section1",
    )

    assert {item["promotion_group_id"] for item in items} == {"shopee-promotion:p1:section1"}
    assert {item["promotion_label"] for item in items} == {"Any 3 at RM45.80"}
    assert {item["promotion_group_total"] for item in items} == {Decimal("45.80")}
    assert {item["promotion_target_qty"] for item in items} == {3}
    assert [item["promotion_member_qty"] for item in items] == [1, 2]
    assert [item["source_line_subtotal"] for item in items] == [Decimal("20.00"), Decimal("40.00")]
    assert [item["line_total"] for item in items] == [Decimal("15.27"), Decimal("30.53")]
    assert sum(item["line_total"] for item in items) == Decimal("45.80")


def test_multiple_detected_groups_have_separate_deterministic_ids():
    first_group = _promotion_items()
    second_group = _promotion_items()

    _apply_group_promotion(
        first_group,
        [_promotion_row("Any 3 at RM45.80")],
        promotion_group_id="shopee-promotion:p1:section1",
    )
    _apply_group_promotion(
        second_group,
        [_promotion_row("Any 3 at RM39.90")],
        promotion_group_id="shopee-promotion:p1:section2",
    )

    assert {item["promotion_group_id"] for item in first_group} == {"shopee-promotion:p1:section1"}
    assert {item["promotion_group_id"] for item in second_group} == {"shopee-promotion:p1:section2"}
    assert {item["promotion_group_total"] for item in first_group} == {Decimal("45.80")}
    assert {item["promotion_group_total"] for item in second_group} == {Decimal("39.90")}


def test_incomplete_group_does_not_assign_membership_or_legacy_allocation():
    items = _promotion_items()

    _apply_group_promotion(
        items,
        [_promotion_row("Any 4 at RM45.80")],
        promotion_group_id="shopee-promotion:p1:section1",
    )

    assert all("promotion_group_id" not in item for item in items)
    assert all("promotion_member_qty" not in item for item in items)
    assert {item["promotion_metadata_status"] for item in items} == {"incomplete"}
    assert [item["line_total"] for item in items] == [Decimal("20.00"), Decimal("40.00")]


def test_mapper_preserves_new_source_fields_without_changing_legacy_subtotal():
    items = _promotion_items()
    _apply_group_promotion(
        items,
        [_promotion_row("Any 3 at RM45.80")],
        promotion_group_id="shopee-promotion:p1:section1",
    )

    products = map_shopee_products(_extracted_data(items), "batch-synthetic")

    assert [product["line_subtotal"] for product in products] == ["15.27", "30.53"]
    assert [product["source_line_subtotal"] for product in products] == ["20.00", "40.00"]
    assert {product["promotion_group_id"] for product in products} == {"shopee-promotion:p1:section1"}
    assert {product["promotion_group_total"] for product in products} == {"45.80"}


def test_candidate_reconciliation_preserves_fallback_source_subtotal():
    products = reconcile_product_candidates(
        [
            {
                "product_name": "Synthetic Positioned",
                "seller_sku": "SYN-FALLBACK",
                "quantity": 1,
                "unit_price": Decimal("20.00"),
                "line_total": Decimal("0"),
                "source_line_subtotal": None,
            }
        ],
        [
            {
                "product_name": "Synthetic Text",
                "seller_sku": "SYN-FALLBACK",
                "quantity": 1,
                "unit_price": Decimal("20.00"),
                "line_total": Decimal("20.00"),
                "source_line_subtotal": Decimal("20.00"),
            }
        ],
    )

    assert products[0]["line_total"] == Decimal("20.00")
    assert products[0]["source_line_subtotal"] == Decimal("20.00")