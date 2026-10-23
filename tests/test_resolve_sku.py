from src.invoice_app.services.resolve_sku import (
    ResolveSkuIdentifierType,
    ResolveSkuParseStatus,
    parse_resolve_sku,
)


def test_ean_nav_manual_and_multiple_components_preserve_listing_order():
    components = parse_resolve_sku(
        "9555208010555-2; NAV:5001438-3; 5003117"
    )

    assert [component.identifier_type for component in components] == [
        ResolveSkuIdentifierType.EAN13,
        ResolveSkuIdentifierType.NAV,
        ResolveSkuIdentifierType.MANUAL_CODE,
    ]
    assert [component.scan_quantity(4) for component in components] == [8, 12, 4]
    assert components[0].barcode == "9555208010555"
    assert components[1].status is ResolveSkuParseStatus.MANUAL_ENTRY_REQUIRED
    assert components[2].identifier == "5003117"


def test_non_numeric_suffix_keeps_a_valid_ean_barcode_without_creating_a_bundle():
    component = parse_resolve_sku("9555208108993-btl 250g")[0]

    assert component.barcode == "9555208108993"
    assert component.units_per_listing_item == 1
    assert component.scan_quantity(2) == 2


def test_embedded_barcode_never_becomes_a_numeric_multiplier():
    component = parse_resolve_sku("btl 250g-9555208108993")[0]

    assert component.identifier == "btl 250g-9555208108993"
    assert component.barcode is None
    assert component.units_per_listing_item == 1
    assert component.scan_quantity(2) == 2


def test_invalid_checksum_and_empty_component_fail_closed_per_component():
    invalid, empty = parse_resolve_sku("9555208010556-2;")

    assert invalid.barcode is None
    assert invalid.status is ResolveSkuParseStatus.MANUAL_ENTRY_REQUIRED
    assert empty.status is ResolveSkuParseStatus.REVIEW_REQUIRED
    assert empty.scan_quantity(1) is None


def test_newlines_and_whitespace_are_supported_around_semicolon_separators():
    components = parse_resolve_sku(" 9555208010555 ;\n NAV:5001438-1 ")

    assert [component.identifier for component in components] == [
        "9555208010555",
        "5001438",
    ]
