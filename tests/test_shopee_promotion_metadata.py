from decimal import Decimal

from src.invoice_app.parsers.shopee_product_parser import _apply_group_promotion
from src.invoice_app.parsers.validation import count_product_anchor_items, validate_shopee_product_amounts, validate_shopee_promotion_evidence


def _item(sku, quantity, subtotal, promotion=""):
    return {
        "product_name": sku,
        "seller_sku": sku,
        "quantity": quantity,
        "unit_price": Decimal("20.00"),
        "line_total": subtotal,
        "source_line_subtotal": subtotal,
        "promotion": promotion,
    }


def test_normal_product_has_no_promotion_metadata():
    item = _item("NORMAL", 1, Decimal("20.00"))

    _apply_group_promotion([item], promotion_group_id="p1")

    assert "promotion_group_id" not in item
    assert item["source_line_subtotal"] == Decimal("20.00")


def test_container_membership_is_not_limited_by_any_target_quantity():
    first = _item("A", 5, None, "Any 4 at RM176.40")
    first["promotion_container_subtotal"] = Decimal("294.00")
    second = _item("B", 2, None)
    normal = _item("NORMAL", 1, Decimal("10.00"))
    normal["unit_price"] = Decimal("10.00")

    _apply_group_promotion([first, second, normal], promotion_group_id="p1")

    assert first["promotion_group_id"] == second["promotion_group_id"]
    assert first["participating_qty"] == second["participating_qty"] == 7
    assert first["promotion_target_qty"] == 4
    assert first["promotion_advertised_amount"] == Decimal("176.40")
    assert first["source_group_total"] == second["source_group_total"] == Decimal("294.00")
    assert first["source_line_subtotal"] is None
    assert second["source_line_subtotal"] is None
    assert normal.get("promotion_group_id") is None
    assert count_product_anchor_items([first, second], require_sku=True) == 2
    assert validate_shopee_product_amounts([first, second, normal], Decimal("304.00")) is None
    assert first["promotion_target_qty"] != first["participating_qty"]


def test_multiple_visual_containers_do_not_mix():
    first = _item("A", 1, None, "Any 2 at RM20.00")
    first["promotion_container_subtotal"] = Decimal("20.00")
    second = _item("B", 1, None)
    third = _item("C", 1, None, "Any 2 at RM30.00")
    third["promotion_container_subtotal"] = Decimal("30.00")
    fourth = _item("D", 1, None)

    _apply_group_promotion([first, second, third, fourth], promotion_group_id="p1")

    assert first["promotion_group_id"] == second["promotion_group_id"]
    assert third["promotion_group_id"] == fourth["promotion_group_id"]
    assert first["promotion_group_id"] != third["promotion_group_id"]


def test_missing_container_subtotal_is_incomplete_and_does_not_guess_members():
    item = _item("A", 1, None, "Any 2 at RM20.00")
    next_item = _item("B", 1, None)

    _apply_group_promotion([item, next_item], promotion_group_id="p1")

    assert item["promotion_metadata_status"] == "incomplete"
    assert item["promotion_incomplete_reason"]
    assert "promotion_group_id" not in item
    assert validate_shopee_promotion_evidence([item, next_item]).startswith("INCOMPLETE_PROMOTION_EVIDENCE:")
    assert "promotion_group_id" not in next_item

def test_percent_off_label_preserves_percent_and_container_total_separately():
    first = _item("A", 1, None, "Any 3 enjoy 33% off")
    first["promotion_container_subtotal"] = Decimal("159.01")
    second = _item("B", 1, None)
    third = _item("C", 1, None)

    _apply_group_promotion([first, second, third], promotion_group_id="p1")

    assert {item["promotion_label"] for item in (first, second, third)} == {"Any 3 enjoy 33% off"}
    assert {item["promotion_target_qty"] for item in (first, second, third)} == {3}
    assert {item["promotion_discount_percent"] for item in (first, second, third)} == {Decimal("33")}
    assert {item["source_group_total"] for item in (first, second, third)} == {Decimal("159.01")}
    assert validate_shopee_product_amounts([first, second, third], Decimal("159.01")) is None
    assert all("promotion_advertised_amount" not in item for item in (first, second, third))
    assert all(item["source_line_subtotal"] is None for item in (first, second, third))

def test_container_subtotal_may_be_on_a_later_member_sku_row():
    first = _item("A", 1, None, "Any 4 at RM15.00")
    second = _item("B", 1, None)
    second["positioned_sku_row_subtotal"] = Decimal("15.00")
    third = _item("C", 1, None)
    fourth = _item("D", 1, None)
    normal = _item("NORMAL", 1, Decimal("4.80"))

    _apply_group_promotion(
        [first, second, third, fourth, normal],
        promotion_group_id="p1",
    )

    assert {item["source_group_total"] for item in (first, second, third, fourth)} == {Decimal("15.00")}
    assert {item["participating_qty"] for item in (first, second, third, fourth)} == {4}
    assert all(item["source_line_subtotal"] is None for item in (first, second, third, fourth))
    assert normal.get("promotion_group_id") is None
