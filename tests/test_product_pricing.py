from copy import deepcopy
from decimal import Decimal

from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.product_pricing import (
    ProductPricingStatus,
    calculate_shopee_product_pricing,
)


def _master(*rows):
    return ProductPriceMaster.from_rows(rows, source_filename="synthetic-pricing-master.xlsx")


def _master_row(sku, price, *, parent_sku="", product_name="", variation_name=""):
    return {
        "seller_sku": sku,
        "parent_sku": parent_sku,
        "product_name": product_name or sku,
        "variation_name": variation_name,
        "unit_selling_price": price,
    }


def _row(sku="SKU-1", *, quantity=2, source_line_subtotal="18.00", **metadata):
    return {
        "seller_sku": sku,
        "product_name": sku,
        "quantity": quantity,
        "source_line_subtotal": source_line_subtotal,
        **metadata,
    }


def _promotion_row(sku, *, quantity, source_line_subtotal, group_id="group-1", total="45.80", target=3, member_qty=None):
    return _row(
        sku,
        quantity=quantity,
        source_line_subtotal=source_line_subtotal,
        promotion_group_id=group_id,
        promotion_label="Any 3 at RM45.80",
        promotion_group_total=total,
        promotion_target_qty=target,
        promotion_member_qty=quantity if member_qty is None else member_qty,
    )


def test_normal_row_uses_master_price_and_source_subtotal():
    result = calculate_shopee_product_pricing(
        [_row()],
        _master(_master_row("SKU-1", "10.00")),
    )[0]

    assert result.unit_selling_price == Decimal("10.00")
    assert result.normal_selling_value == Decimal("20.00")
    assert result.actual_selling_value == Decimal("18.00")
    assert result.discount_given == Decimal("2.00")
    assert result.pricing_status is ProductPricingStatus.NORMAL_PRICED
    assert result.allocation_method == "source_line_subtotal"


def test_normal_row_with_equal_source_value_has_zero_discount():
    result = calculate_shopee_product_pricing(
        [_row(source_line_subtotal="20.00")],
        _master(_master_row("SKU-1", "10.00")),
    )[0]

    assert result.discount_given == Decimal("0.00")
    assert result.pricing_status is ProductPricingStatus.NORMAL_PRICED


def test_price_not_found_never_uses_zero_or_derived_values():
    result = calculate_shopee_product_pricing(
        [_row(sku="MISSING")],
        _master(_master_row("SKU-1", "10.00")),
    )[0]

    assert result.pricing_status is ProductPricingStatus.PRICE_NOT_FOUND
    assert result.unit_selling_price is None
    assert result.normal_selling_value is None
    assert result.actual_selling_value == Decimal("18.00")
    assert result.discount_given is None


def test_complete_promotion_with_missing_price_preserves_group_actual_values():
    results = calculate_shopee_product_pricing(
        [
            _promotion_row("SKU-A", quantity=1, source_line_subtotal="20.00"),
            _promotion_row("MISSING", quantity=2, source_line_subtotal="40.00"),
        ],
        _master(_master_row("SKU-A", "20.00")),
    )

    assert [result.actual_selling_value for result in results] == [
        Decimal("15.27"), Decimal("30.53")
    ]
    assert sum(result.actual_selling_value for result in results) == Decimal("45.80")
    assert results[0].pricing_status is ProductPricingStatus.PROMOTION_UNSUPPORTED
    assert results[1].pricing_status is ProductPricingStatus.PRICE_NOT_FOUND
    assert all(result.discount_given is None for result in results)

def test_pricing_conflict_never_guesses_a_price():
    result = calculate_shopee_product_pricing(
        [_row()],
        _master(
            _master_row("SKU-1", "10.00", product_name="First"),
            _master_row("SKU-1", "12.00", product_name="Second"),
        ),
    )[0]

    assert result.pricing_status is ProductPricingStatus.PRICING_CONFLICT
    assert result.unit_selling_price is None
    assert result.discount_given is None


def test_same_price_promotion_allocates_by_member_quantity_and_preserves_source_values():
    rows = [
        _promotion_row("SKU-A", quantity=1, source_line_subtotal="20.00"),
        _promotion_row("SKU-B", quantity=2, source_line_subtotal="40.00"),
    ]
    source_before = deepcopy(rows)

    results = calculate_shopee_product_pricing(
        rows,
        _master(_master_row("SKU-A", "20.00"), _master_row("SKU-B", "20.00")),
    )

    assert [result.actual_selling_value for result in results] == [Decimal("15.27"), Decimal("30.53")]
    assert sum(result.actual_selling_value for result in results) == Decimal("45.80")
    assert [result.normal_selling_value for result in results] == [Decimal("20.00"), Decimal("40.00")]
    assert [result.discount_given for result in results] == [Decimal("4.73"), Decimal("9.47")]
    assert all(result.pricing_status is ProductPricingStatus.PROMOTION_ALLOCATED for result in results)
    assert rows == source_before
    assert [result.source_line_subtotal for result in results] == [Decimal("20.00"), Decimal("40.00")]


def test_decimal_remainder_is_deterministic_and_reconciles_exactly():
    results = calculate_shopee_product_pricing(
        [
            _promotion_row("SKU-A", quantity=1, source_line_subtotal="10.00", total="10.00"),
            _promotion_row("SKU-B", quantity=2, source_line_subtotal="20.00", total="10.00"),
        ],
        _master(_master_row("SKU-A", "10.00"), _master_row("SKU-B", "10.00")),
    )

    assert [result.actual_selling_value for result in results] == [Decimal("3.33"), Decimal("6.67")]
    assert sum(result.actual_selling_value for result in results) == Decimal("10.00")
    assert results[-1].allocation_method == "promotion_group_last_member_remainder"


def test_multiple_promotion_groups_are_calculated_independently():
    results = calculate_shopee_product_pricing(
        [
            _promotion_row("SKU-A", quantity=1, source_line_subtotal="20.00", group_id="group-1"),
            _promotion_row("SKU-B", quantity=2, source_line_subtotal="40.00", group_id="group-1"),
            _promotion_row("SKU-C", quantity=1, source_line_subtotal="10.00", group_id="group-2", total="12.00", target=2),
            _promotion_row("SKU-D", quantity=1, source_line_subtotal="10.00", group_id="group-2", total="12.00", target=2),
        ],
        _master(
            _master_row("SKU-A", "20.00"),
            _master_row("SKU-B", "20.00"),
            _master_row("SKU-C", "10.00"),
            _master_row("SKU-D", "10.00"),
        ),
    )

    assert sum(result.actual_selling_value for result in results[:2]) == Decimal("45.80")
    assert sum(result.actual_selling_value for result in results[2:]) == Decimal("12.00")
    assert {result.promotion_group_id for result in results[:2]} == {"group-1"}
    assert {result.promotion_group_id for result in results[2:]} == {"group-2"}


def test_incomplete_promotion_evidence_does_not_allocate():
    result = calculate_shopee_product_pricing(
        [
            _row(
                quantity=1,
                source_line_subtotal="10.00",
                promotion_metadata_status="incomplete",
                promotion_label="Any 3 at RM45.80",
                promotion_group_total="45.80",
                promotion_target_qty=3,
            )
        ],
        _master(_master_row("SKU-1", "10.00")),
    )[0]

    assert result.pricing_status is ProductPricingStatus.PROMOTION_EVIDENCE_INCOMPLETE
    assert result.actual_selling_value is None
    assert result.discount_given is None
    assert result.allocation_method is None


def test_mixed_resolved_prices_are_not_allocated():
    results = calculate_shopee_product_pricing(
        [
            _promotion_row("SKU-A", quantity=1, source_line_subtotal="10.00"),
            _promotion_row("SKU-B", quantity=2, source_line_subtotal="20.00"),
        ],
        _master(_master_row("SKU-A", "10.00"), _master_row("SKU-B", "12.00")),
    )

    assert all(result.pricing_status is ProductPricingStatus.PROMOTION_UNSUPPORTED for result in results)
    assert all(result.actual_selling_value is None for result in results)


def test_negative_discount_is_retained_as_pricing_anomaly():
    result = calculate_shopee_product_pricing(
        [_row(quantity=1, source_line_subtotal="10.03")],
        _master(_master_row("SKU-1", "10.00")),
    )[0]

    assert result.discount_given == Decimal("-0.03")
    assert result.pricing_status is ProductPricingStatus.PRICING_ANOMALY
    assert "without clamping" in result.reason