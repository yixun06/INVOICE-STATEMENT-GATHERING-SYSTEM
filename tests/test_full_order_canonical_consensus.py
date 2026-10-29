from decimal import Decimal

import pytest

from src.invoice_app.services.product_price_master import ProductPriceMaster, PriceLookupStatus
from src.invoice_app.services.shopee_full_order_adapter import adapt_shopee_full_order
from tests.test_shopee_full_order_erp_equivalence import _parsed


def _rows(**changes):
    base = dict(seller_sku="", parent_sku="9555208106937", product_name="Simply Natural Organic Lakadong Turmeric Powder 80g India", unit_selling_price="11.50", unit_price_usolf="18.90", nav_code="5000277", resolve_sku="X")
    return [dict(base, variation_name="02/27"), dict(base, variation_name="31/05/26", **changes)]


def _lookup(master, variation=""):
    return master.lookup_full_order(seller_sku="9555208106937", parent_sku="9555208106937", product_name="Simply Natural Organic Lakadong Turmeric Powder 80g India", variation_name=variation, parent_family_variation_disambiguation=True)


def test_blank_variation_collapses_complete_candidate_outputs():
    master = ProductPriceMaster.from_rows(_rows())
    result = _lookup(master)
    assert result.matched_by == "full_order_canonical_consensus"
    assert result.source_rows == (1, 2)
    assert (result.matched_sku, result.nav_code, result.unit_price_usolf, result.resolve_sku) == ("9555208106937", "5000277", Decimal("18.90"), "X")
    batch = adapt_shopee_full_order(_parsed(), product_master=master)
    assert batch.syncable and batch.items[0].unit_price == Decimal("18.90")


@pytest.mark.parametrize("changes", [dict(nav_code="5000999"), dict(unit_price_usolf="15.20"), dict(resolve_sku="Y"), dict(seller_sku="OTHER")])
def test_different_outputs_fail_closed_and_exact_variation_resolves(changes):
    rows = _rows()
    rows[1].update(changes)
    master = ProductPriceMaster.from_rows(rows)
    assert not adapt_shopee_full_order(_parsed(), product_master=master).syncable
    result = _lookup(master, "02/27")
    assert result.status == PriceLookupStatus.MATCHED
    assert result.source_rows == (1,)


def test_belly_friendly_sku_with_blank_variation_and_two_consistent_rows():
    sku = "9555208011514"
    name = "Simply Natural Organic Belly Friendly Muesli 500g"
    rows = [dict(seller_sku=sku, parent_sku=sku, product_name=name,
                 variation_name=variation, nav_code="5000277", resolve_sku="X",
                 unit_selling_price="18.90", unit_price_usolf="18.90")
            for variation in ("02/27", "31/05/26")]
    result = ProductPriceMaster.from_rows(rows).lookup_full_order(
        seller_sku=sku, parent_sku=sku, product_name=name, variation_name=""
    )
    assert result.matched_by == "full_order_canonical_consensus"
    assert result.source_rows == (1, 2)
    assert (result.matched_sku, result.nav_code, result.resolve_sku, result.unit_price_usolf) == (sku, "5000277", "X", Decimal("18.90"))


def test_incomplete_rows_cannot_be_combined_into_a_complete_result():
    rows = _rows()
    rows[0]["nav_code"] = ""
    rows[1]["unit_price_usolf"] = ""
    assert not adapt_shopee_full_order(_parsed(), product_master=ProductPriceMaster.from_rows(rows)).syncable


def test_same_variation_duplicate_rows_with_different_nav_fail_closed():
    rows = _rows()
    rows[1].update(variation_name="02/27", nav_code="other")
    result = _lookup(ProductPriceMaster.from_rows(rows), "02/27")
    assert result.status == PriceLookupStatus.PRICING_CONFLICT


def test_barley_grass_exact_name_selects_parent_candidate_over_seller_candidate():
    sku = "9555208107378"
    name = "Simply Natural Organic Grade Barley Grass Powder (200g) [Serbuk Rumput Barli]"
    rows = [
        dict(seller_sku=sku, parent_sku="", product_name="Simply Natural Supplement Series",
             variation_name="BARLEY GRASS POWDER", nav_code="3000686", unit_selling_price="54.89",
             unit_price_usolf="49.90", resolve_sku=sku),
        dict(seller_sku="", parent_sku=sku, product_name=name, variation_name="",
             nav_code="3000686", unit_selling_price="49.90", unit_price_usolf="49.90", resolve_sku=sku),
    ]
    result = ProductPriceMaster.from_rows(rows).lookup_full_order(
        seller_sku=sku, parent_sku=sku, product_name=name, variation_name="",
        parent_family_variation_disambiguation=True,
    )
    assert result.matched_by == "NAME"
    assert result.source_rows == (2,)
    assert (result.matched_parent_sku, result.nav_code, result.unit_price_usolf, result.resolve_sku) == (sku, "3000686", Decimal("49.90"), sku)


def test_no_exact_name_and_conflicting_outputs_fail_closed():
    rows = _rows()
    rows[0].update(product_name="Different A", nav_code="NAV-A")
    rows[1].update(product_name="Different B", nav_code="NAV-B")
    result = _lookup(ProductPriceMaster.from_rows(rows))
    assert result.status == PriceLookupStatus.PRICING_CONFLICT
