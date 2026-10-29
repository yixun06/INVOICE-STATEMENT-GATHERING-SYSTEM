from decimal import Decimal

import pytest

from src.invoice_app.services.product_price_master import ProductPriceMaster, PriceLookupStatus


def row(**changes):
    values = dict(product_name="Product One", variation_name="Red", seller_sku="SKU-1", parent_sku="FAMILY", nav_code="NAV-1", unit_selling_price="10", unit_price_usolf="12", resolve_sku="POS-1")
    values.update(changes)
    return values


def lookup(rows, *, name="Product One", variation="Red", sku="SKU-1", parent="FAMILY"):
    return ProductPriceMaster.from_rows(rows).lookup_full_order(
        product_name=name, variation_name=variation, seller_sku=sku, parent_sku=parent,
        parent_family_variation_disambiguation=True,
    )


@pytest.mark.parametrize(("name", "variation", "sku", "parent", "expected"), [
    ("Product One", "Red", "SKU-1", "FAMILY", "NAME_VARIATION_SKU"),
    ("Product One", "", "SKU-1", "FAMILY", "NAME_SKU"),
    ("Product One", "Red", "WRONG", "WRONG", "NAME_VARIATION"),
    ("Product One", "", "WRONG", "WRONG", "NAME"),
    ("Different", "", "SKU-1", "WRONG", "SELLER_SKU"),
    ("Different", "", "WRONG", "FAMILY", "PARENT_SKU"),
    ("Different", "", "POS-1", "WRONG", "RESOLVE_SKU"),
])
def test_identity_levels(name, variation, sku, parent, expected):
    result = lookup([row()], name=name, variation=variation, sku=sku, parent=parent)
    assert result.status == PriceLookupStatus.MATCHED
    assert result.matched_by == expected
    assert (result.nav_code, result.unit_price_usolf, result.resolve_sku) == ("NAV-1", Decimal("12"), "POS-1")


def test_equal_persisted_tuple_allows_distinct_listing_prices():
    result = lookup([row(unit_selling_price="10"), row(unit_selling_price="99")])
    assert result.matched_by == "full_order_canonical_consensus"
    assert result.source_rows == (1, 2)


@pytest.mark.parametrize("change", [{"nav_code": "NAV-2"}, {"unit_price_usolf": "13"}, {"resolve_sku": "POS-2"}])
def test_identical_identity_with_different_canonical_output_fails_closed(change):
    assert lookup([row(), row(**change)]).status == PriceLookupStatus.PRICING_CONFLICT


def test_no_fuzzy_or_whitespace_deleting_name_match():
    assert lookup([row()], name="ProductOne", variation="", sku="", parent="").status == PriceLookupStatus.PRICE_NOT_FOUND
    assert lookup([row()], name="  PRODUCT   ONE  ", variation="red", sku="SKU-1").matched_by == "NAME_VARIATION_SKU"


def test_resolve_sku_fallback_uses_exact_whole_value_without_bundle_guessing():
    assert lookup([row()], name="Different", variation="", sku="", parent="POS-1").matched_by == "RESOLVE_SKU"
    result = lookup([row(resolve_sku="POS-1;POS-2")], name="Different", variation="", sku="POS-1", parent="")
    assert result.status == PriceLookupStatus.PRICE_NOT_FOUND


def test_cactus_exact_name_sku_not_merged_with_parent_family():
    result = lookup([
        row(product_name="Cactus", variation_name="1 Box-Exp 12/27", seller_sku="SKU-1"),
        row(product_name="Cactus", variation_name="1 Box-Exp 12/26", seller_sku="SKU-1-EXP", parent_sku="SKU-1"),
    ], name="Cactus", variation="1 Box x15'sachet", sku="SKU-1", parent="")
    assert result.matched_by == "NAME_SKU" and result.matched_sku == "SKU-1"


def test_chilli_blank_variation_uses_unique_name_and_sku():
    result = lookup([row(product_name="Chilli", variation_name="Hot"), row(product_name="Chilli", variation_name="Red", seller_sku="OTHER", nav_code="NAV-2")], name="Chilli", variation="")
    assert result.matched_by == "NAME_SKU" and result.nav_code == "NAV-1"
