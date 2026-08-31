from decimal import Decimal

from openpyxl import Workbook

from src.invoice_app.services.product_price_master import (
    PriceLookupStatus,
    ProductPriceMaster,
    load_shopee_product_price_master,
)


def _master(rows):
    return ProductPriceMaster.from_rows(
        rows,
        source_filename="synthetic-price-master.xlsx",
    )


def _row(
    seller_sku="SKU-1",
    parent_sku="PARENT-1",
    product_name="Product One",
    variation_name="Blue",
    unit_selling_price="12.50",
):
    return {
        "seller_sku": seller_sku,
        "parent_sku": parent_sku,
        "product_name": product_name,
        "variation_name": variation_name,
        "unit_selling_price": unit_selling_price,
    }


def test_exact_sku_with_unique_price_matches():
    result = _master([_row()]).lookup(seller_sku="SKU-1")

    assert result.status is PriceLookupStatus.MATCHED
    assert result.unit_selling_price == Decimal("12.50")
    assert result.matched_by == "matched"


def test_duplicate_sku_with_same_price_is_conflict_without_identity_evidence():
    result = _master([
        _row(product_name="Product One"),
        _row(product_name="Product Two"),
    ]).lookup(seller_sku="SKU-1")

    assert result.status is PriceLookupStatus.PRICING_CONFLICT
    assert result.unit_selling_price is None


def test_multiple_sku_prices_resolve_by_product_name():
    result = _master([
        _row(product_name="Product One", unit_selling_price="12.50"),
        _row(product_name="Product Two", unit_selling_price="16.00"),
    ]).lookup(seller_sku="SKU-1", product_name=" product two ")

    assert result.status is PriceLookupStatus.MATCHED
    assert result.unit_selling_price == Decimal("16.00")
    assert result.matched_product_name == "Product Two"


def test_multiple_sku_prices_without_unique_name_variation_is_conflict():
    result = _master([
        _row(product_name="Product One", unit_selling_price="12.50"),
        _row(product_name="Product Two", unit_selling_price="16.00"),
    ]).lookup(seller_sku="SKU-1")

    assert result.status is PriceLookupStatus.PRICING_CONFLICT
    assert result.unit_selling_price is None


def test_present_seller_sku_not_found_does_not_fallback_to_parent_sku():
    result = _master([
        _row(seller_sku="OTHER-SKU", parent_sku="PARENT-1"),
    ]).lookup(seller_sku="MISSING-SKU", parent_sku="PARENT-1")

    assert result.status is PriceLookupStatus.PRICE_NOT_FOUND
    assert result.unit_selling_price is None
    assert "MISSING-SKU" in result.reason


def test_blank_seller_sku_requires_exact_name_variation_identity():
    result = _master([
        _row(seller_sku="CHILD-SKU", parent_sku="PARENT-1"),
    ]).lookup(
        seller_sku="",
        parent_sku="PARENT-1",
        product_name="Product One",
        variation_name="Blue",
    )

    assert result.status is PriceLookupStatus.MATCHED_BY_NAME_VARIATION
    assert result.unit_selling_price == Decimal("12.50")


def test_invalid_sku_name_variation_requires_one_identity_not_one_price():
    result = _master([
        _row(
            seller_sku="CHILD-BLUE",
            variation_name="Blue",
            unit_selling_price="12.50",
        ),
        _row(
            seller_sku="CHILD-RED",
            variation_name="Red",
            unit_selling_price="16.00",
        ),
    ]).lookup(
        seller_sku="exp",
        product_name="Product One",
        variation_name="red",
    )

    assert result.status is PriceLookupStatus.MATCHED_BY_NAME_VARIATION
    assert result.unit_selling_price == Decimal("16.00")
    assert result.matched_sku == "CHILD-RED"


def test_missing_price_is_not_zero_and_returns_not_found():
    master = _master([_row(unit_selling_price="")])
    result = master.lookup(seller_sku="SKU-1")

    assert master.metadata.invalid_price_row_count == 1
    assert result.status is PriceLookupStatus.PRICE_NOT_FOUND
    assert result.unit_selling_price is None


def test_sku_is_preserved_as_string_when_loaded(tmp_path):
    path = tmp_path / "synthetic_product_listing.xlsx"
    _write_listing(path, seller_sku="00123", price="9.90")

    master = load_shopee_product_price_master(path)
    result = master.lookup(seller_sku="00123")

    assert master.records[0].seller_sku == "00123"
    assert result.status is PriceLookupStatus.MATCHED
    assert result.unit_selling_price == Decimal("9.90")


def test_loader_ignores_malformed_price_safely(tmp_path):
    path = tmp_path / "synthetic_malformed_price.xlsx"
    _write_listing(path, seller_sku="SKU-1", price="not a price")

    master = ProductPriceMaster.from_xlsx(path)
    result = master.lookup(seller_sku="SKU-1")

    assert master.metadata.candidate_row_count == 1
    assert master.metadata.invalid_price_row_count == 1
    assert master.records == ()
    assert result.status is PriceLookupStatus.PRICE_NOT_FOUND
    assert result.unit_selling_price is None


def _write_listing(path, *, seller_sku, price):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["et_title_product_id", "et_title_product_name"])
    worksheet.append([])
    worksheet.append([
        "Product ID",
        "Product Name",
        "Variation ID",
        "Variation Name",
        "Parent SKU",
        "SKU",
        "Price",
    ])
    worksheet.append([
        "SYNTHETIC-PRODUCT",
        "Synthetic Product",
        "SYNTHETIC-VARIATION",
        "Blue",
        "PARENT-1",
        seller_sku,
        price,
    ])
    workbook.save(path)

def test_seller_sku_matches_master_parent_sku_without_column_priority():
    result = _master([
        _row(seller_sku="CHILD", parent_sku="SOURCE-SKU", unit_selling_price="12.50"),
    ]).lookup(seller_sku="SOURCE-SKU")

    assert result.status is PriceLookupStatus.MATCHED
    assert result.unit_selling_price == Decimal("12.50")
    assert result.matched_parent_sku == "SOURCE-SKU"


def test_less_alias_is_only_used_after_the_original_sku_has_no_candidates():
    result = _master([
        _row(seller_sku="9555208106654", variation_name="25% Less Sweet"),
    ]).lookup(
        seller_sku="9555208106654-Less",
        product_name="Product One",
        variation_name="25%LessSweet",
    )

    assert result.status is PriceLookupStatus.MATCHED_BY_ALIAS
    assert result.alias_rule == "REMOVE_SUFFIX_LESS"
    assert result.unit_selling_price == Decimal("12.50")


def test_unknown_sku_suffix_is_not_an_alias():
    result = _master([_row(seller_sku="SKU-1")]).lookup(seller_sku="SKU-1-Other")

    assert result.status is PriceLookupStatus.PRICE_NOT_FOUND


def test_numeric_master_sku_is_normalized_without_inventing_leading_zero():
    master = _master([_row(seller_sku=9555208106364.0)])
    result = master.lookup(seller_sku=9555208106364.0)

    assert master.records[0].seller_sku == "9555208106364"
    assert result.status is PriceLookupStatus.MATCHED
