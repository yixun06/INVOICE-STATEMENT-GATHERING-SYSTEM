from datetime import datetime
from decimal import Decimal

import pytest

from src.invoice_app.parsers.shopee_full_order_parser import (
    ParsedShopeeFullOrder,
    ShopeeFullOrderRow,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.product_price_master import (
    PriceLookupStatus,
    ProductPriceMaster,
)
from src.invoice_app.services.shopee_full_order_adapter import (
    FULL_ORDER_VERIFIED_ERP_IDENTITIES,
    adapt_shopee_full_order,
)


SKU = "9555208106937"
PRODUCT_NAME = "Simply Natural Organic Lakadong Turmeric Powder 80g India"
CHILI_PARENT_SKU = "9555208107279"
CHILI_NAME = "Simply Natural Organic Red Chilli Powder (Hot) 90g India"


def _record(variation_name, **changes):
    row = {
        "seller_sku": "",
        "parent_sku": SKU,
        "product_name": PRODUCT_NAME,
        "variation_name": variation_name,
        "unit_selling_price": "11.50",
        "unit_price_usolf": "11.50",
        "nav_code": "5000064",
        "product_id": "929433653",
        "usoft_product_description": "SN Org Lakadong Turmeric Powder EA 80g",
        "uom": "EA",
    }
    row.update(changes)
    return row


def _master(*rows):
    return ProductPriceMaster.from_rows(rows, source_filename="synthetic-full-order-master")


def _lakadong_master(*extra_rows):
    return _master(
        _record(""),
        _record("09/2026"),
        _record("09/2027"),
        *extra_rows,
    )


def _parsed(variation=""):
    return ParsedShopeeFullOrder(
        market=SHOPEE_MY,
        source_filename="orders.xlsx",
        source_hash="hash",
        rows=(
            ShopeeFullOrderRow(
                order_id="ORDER-1",
                order_status="COMPLETED",
                order_created_at=datetime(2026, 7, 1),
                parent_sku=SKU,
                seller_sku=SKU,
                product_name=PRODUCT_NAME,
                variation=variation,
                quantity=1,
                product_subtotal=Decimal("11.50"),
                return_refund_status="",
                country="MY",
                currency="MYR",
                source_row_number=2,
            ),
        ),
        dimension_fallback_used=False,
    )


def _chili_master():
    return _master(
        {
            "seller_sku": "9555208106913",
            "parent_sku": CHILI_PARENT_SKU,
            "product_name": CHILI_NAME,
            "variation_name": "Red Chili Powder",
            "unit_selling_price": "6.90",
            "unit_price_usolf": "8.90",
            "nav_code": "5000065",
        },
        {
            "seller_sku": CHILI_PARENT_SKU,
            "parent_sku": CHILI_PARENT_SKU,
            "product_name": CHILI_NAME,
            "variation_name": "Red Chili Powder Hot",
            "unit_selling_price": "6.90",
            "unit_price_usolf": "6.90",
            "nav_code": "5000047",
        },
    )


def _chili_parsed(*, variation, market=SHOPEE_MY, seller_sku=CHILI_PARENT_SKU):
    return ParsedShopeeFullOrder(
        market=market,
        source_filename="chili-orders.xlsx",
        source_hash="chili-hash",
        rows=(
            ShopeeFullOrderRow(
                order_id="CHILI-ORDER",
                order_status="COMPLETED",
                order_created_at=datetime(2026, 7, 1),
                parent_sku=CHILI_PARENT_SKU,
                seller_sku=seller_sku,
                product_name=CHILI_NAME,
                variation=variation,
                quantity=1,
                product_subtotal=Decimal("6.90"),
                return_refund_status="",
                country="MY" if market is SHOPEE_MY else "SG",
                currency="MYR" if market is SHOPEE_MY else "SGD",
                source_row_number=2,
            ),
        ),
        dimension_fallback_used=False,
    )


@pytest.mark.parametrize(
    ("variation", "expected_sku", "expected_nav", "expected_usolf"),
    (
        ("Red Chili Powder", "9555208106913", "5000065", Decimal("8.90")),
        ("Red Chili Powder Hot", CHILI_PARENT_SKU, "5000047", Decimal("6.90")),
    ),
)
def test_my_full_order_uses_exact_variation_within_the_conflicting_parent_family(
    variation, expected_sku, expected_nav, expected_usolf
):
    batch = adapt_shopee_full_order(
        _chili_parsed(variation=variation), product_master=_chili_master()
    )

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == expected_sku
    assert batch.items[0].nav == expected_nav
    assert batch.items[0].unit_price == expected_usolf


def test_my_full_order_conflicting_parent_family_unknown_variation_fails_closed():
    batch = adapt_shopee_full_order(
        _chili_parsed(variation="Unknown Chili Variation"), product_master=_chili_master()
    )

    assert batch.syncable is False
    assert batch.issues[0].code == "PM_ENRICHMENT_REQUIRED"


def test_my_parent_family_variation_rule_preserves_unrelated_seller_sku_resolution():
    master = _master(
        {
            "seller_sku": "UNRELATED-SKU",
            "parent_sku": "UNRELATED-PARENT",
            "product_name": "Unrelated Product",
            "variation_name": "Only",
            "unit_selling_price": "10.00",
            "unit_price_usolf": "10.00",
            "nav_code": "UNRELATED-NAV",
        }
    )

    result = master.lookup_full_order(
        seller_sku="UNRELATED-SKU",
        parent_sku="UNRELATED-PARENT",
        product_name="Unrelated Product",
        variation_name="Only",
        parent_family_variation_disambiguation=True,
    )

    assert result.status is PriceLookupStatus.MATCHED
    assert result.matched_sku == "UNRELATED-SKU"
    assert result.nav_code == "UNRELATED-NAV"


def test_sg_full_order_does_not_enable_the_my_parent_family_variation_rule():
    batch = adapt_shopee_full_order(
        _chili_parsed(variation="Red Chili Powder", market=SHOPEE_SG),
        product_master=_chili_master(),
    )

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == CHILI_PARENT_SKU
    assert batch.items[0].nav == "5000047"
    assert batch.items[0].unit_price == Decimal("6.90")


def test_blank_lakadong_variation_resolves_only_for_the_verified_erp_identity():
    batch = adapt_shopee_full_order(_parsed(), product_master=_lakadong_master())

    assert batch.syncable is True
    assert batch.items[0].nav == "5000064"
    assert batch.items[0].unit_price == Decimal("11.50")
    assert batch.items[0].variation == ""


@pytest.mark.parametrize("variation", ("09/2026", "09/2027"))
def test_exact_lakadong_variations_keep_the_normal_match_path(variation):
    result = _lakadong_master().lookup_full_order(
        seller_sku=SKU,
        parent_sku=SKU,
        product_name=PRODUCT_NAME,
        variation_name=variation,
        verified_erp_identities=FULL_ORDER_VERIFIED_ERP_IDENTITIES,
    )

    assert result.status is PriceLookupStatus.MATCHED
    assert result.matched_variation_name == variation


def test_unknown_same_price_candidates_do_not_gain_verified_equivalence():
    master = _master(
        _record("One", parent_sku="UNKNOWN-SKU", product_id="UNKNOWN"),
        _record("Two", parent_sku="UNKNOWN-SKU", product_id="UNKNOWN"),
    )

    result = master.lookup_full_order(
        seller_sku="UNKNOWN-SKU",
        parent_sku="",
        product_name=PRODUCT_NAME,
        variation_name="",
        verified_erp_identities=FULL_ORDER_VERIFIED_ERP_IDENTITIES,
    )

    assert result.status is PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS


@pytest.mark.parametrize(
    "changes",
    (
        {"usoft_product_description": "Different ERP Description"},
        {"uom": "BOX"},
        {"unit_selling_price": "12.50"},
    ),
)
def test_conflicting_verified_erp_fields_fail_closed(changes):
    result = _lakadong_master(_record("", **changes)).lookup_full_order(
        seller_sku=SKU,
        parent_sku=SKU,
        product_name=PRODUCT_NAME,
        variation_name="",
        verified_erp_identities=FULL_ORDER_VERIFIED_ERP_IDENTITIES,
    )

    assert result.status is not PriceLookupStatus.VERIFIED_ERP_IDENTITY_EQUIVALENCE


def test_unapproved_additional_variation_prevents_verified_equivalence():
    result = _lakadong_master(_record("09/2028")).lookup_full_order(
        seller_sku=SKU,
        parent_sku=SKU,
        product_name=PRODUCT_NAME,
        variation_name="",
        verified_erp_identities=FULL_ORDER_VERIFIED_ERP_IDENTITIES,
    )

    assert result.status is PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS


def test_unapproved_source_variation_prevents_verified_equivalence():
    result = _lakadong_master().lookup_full_order(
        seller_sku=SKU,
        parent_sku=SKU,
        product_name=PRODUCT_NAME,
        variation_name="09/2028",
        verified_erp_identities=FULL_ORDER_VERIFIED_ERP_IDENTITIES,
    )

    assert result.status is PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS


def test_pdf_lookup_has_no_verified_erp_identity_equivalence_path():
    result = _lakadong_master().lookup(
        seller_sku=SKU,
        product_name=PRODUCT_NAME,
        variation_name="",
    )

    assert result.status is PriceLookupStatus.PRICE_CONFIRMED_IDENTITY_AMBIGUOUS
