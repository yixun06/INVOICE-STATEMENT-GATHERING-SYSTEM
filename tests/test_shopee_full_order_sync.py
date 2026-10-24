from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
import pytest

from src.invoice_app.parsers.shopee_full_order_parser import (
    ShopeeFullOrderParseError,
    parse_shopee_full_order,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
    FullOrderSyncStatus,
    ShopeeFullOrderCurrentRepository,
)
from src.invoice_app.services.shopee_full_order_adapter import (
    FullOrderCurrentItem,
    FullOrderCurrentOrder,
    PM_ENRICHMENT_REQUIRED,
    adapt_shopee_full_order,
    full_order_snapshot_hash,
)
from src.invoice_app.services.market_context import SHOPEE_MY, SHOPEE_SG
from src.invoice_app.services.shopee_full_order_import import (
    ShopeeFullOrderMarketMismatch,
    commit_shopee_full_order_review,
    refresh_shopee_full_order_review,
    review_shopee_full_order_upload,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.domain.statement_reconciliation_v2 import IdentityScope
from src.invoice_app.services.shopee_full_order_reconciliation import (
    prove_full_order_current_against_statement,
)
from src.invoice_app.services.uat2_persistence_schema import (
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
)
from src.invoice_app.services.uat2_full_order_schema_migration import (
    FullOrderSchemaState,
    build_full_order_schema_requests,
    classify_full_order_schema,
)
from src.invoice_app.services.uat2_statement_schema_migration import (
    SheetSchema,
    SpreadsheetSchemaSnapshot,
)


HEADERS = (
    "Order ID",
    "Order Status",
    "Return / Refund Status",
    "Order Creation Date",
    "Parent SKU Reference No.",
    "Product Name",
    "SKU Reference No.",
    "Variation Name",
    "Quantity",
    "Product Subtotal",
    "Country",
)


def _workbook_bytes(rows, *, malformed_dimension=False, headers=HEADERS):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "orders"
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    if not malformed_dimension:
        return stream.getvalue()
    source = BytesIO(stream.getvalue())
    target = BytesIO()
    with ZipFile(source) as archive, ZipFile(target, "w", ZIP_DEFLATED) as rebuilt:
        for info in archive.infolist():
            payload = archive.read(info.filename)
            if info.filename.startswith("xl/worksheets/sheet"):
                text = payload.decode("utf-8")
                start = text.index('<dimension ref="') + len('<dimension ref="')
                end = text.index('"', start)
                payload = (text[:start] + "A1:A1" + text[end:]).encode("utf-8")
            rebuilt.writestr(info, payload)
    return target.getvalue()


def _master(*, nav="5001", resolve_sku=None):
    return ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "unit_price_usolf": "6.50",
                "nav_code": nav,
                "product_id": "PID-1",
                "resolve_sku": resolve_sku,
            },
            {
                "seller_sku": "SKU-2",
                "parent_sku": "PARENT-2",
                "product_name": "Second Item",
                "variation_name": "",
                "unit_selling_price": "4.00",
                "unit_price_usolf": "4.00",
                "nav_code": "5002",
                "product_id": "PID-2",
            },
        ]
    )


def _rows():
    return [
        (
            "ORDER-1",
            "Completed",
            "",
            "2026-09-10 12:30",
            "PARENT-1",
            "Organic Item",
            "SKU-1",
            "Red",
            2,
            "10.25",
            "MY",
        ),
        (
            "ORDER-1",
            "Completed",
            "",
            "2026-09-10 12:30",
            "PARENT-2",
            "Second Item",
            "SKU-2",
            "",
            1,
            "4.00",
            "MY",
        ),
    ]


def test_parser_recovers_malformed_dimension_and_preserves_repeated_order_rows():
    parsed = parse_shopee_full_order(
        _workbook_bytes(_rows(), malformed_dimension=True),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )

    assert parsed.dimension_fallback_used is True
    assert parsed.order_ids == ("ORDER-1",)
    assert len(parsed.rows) == 2
    assert parsed.rows[0].quantity == 2
    assert parsed.rows[0].product_subtotal == Decimal("10.25")
    assert parsed.rows[0].variation == "Red"


def test_same_parser_supports_sg_blank_skus_by_exact_name_variation():
    row = list(_rows()[0])
    row[4] = ""
    row[6] = ""
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]),
        source_filename="sg-full-order.xlsx",
        market=SHOPEE_SG,
    )

    batch = adapt_shopee_full_order(
        parsed,
        product_master=_master(resolve_sku="SG-RESOLVE-1"),
        now=lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
    )

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == "SKU-1"
    assert batch.items[0].nav == "5001"
    assert batch.items[0].unit_price == Decimal("6.50")


def test_sg_unique_blank_listing_identity_is_enrichment_required():
    row = list(_rows()[0])
    row[4] = ""
    row[6] = ""
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    base_master = _master(resolve_sku="2000842")
    blank_listing_record = replace(
        base_master.records[0], seller_sku="", parent_sku="", nav_code="2000842",
        unit_price_usolf=Decimal("9.90"), resolve_sku="2000842",
    )
    master = ProductPriceMaster((blank_listing_record,), base_master.metadata)

    batch = adapt_shopee_full_order(parsed, product_master=master)
    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert "canonical Seller SKU" in batch.issues[0].message


def test_sg_missing_same_row_resolve_sku_is_enrichment_required():
    row = list(_rows()[0])
    row[4] = ""
    row[6] = ""
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "CANONICAL-SG-1", "parent_sku": "",
                "product_name": "Organic Item", "variation_name": "Red",
                "unit_selling_price": "9.90", "unit_price_usolf": "9.90",
                "nav_code": "2000842", "resolve_sku": "",
            }
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert "resolve_sku" in batch.issues[0].message


def test_sg_name_variation_persists_same_row_sku_identity_and_enrichment():
    row = list(_rows()[0])
    row[4] = ""
    row[6] = ""
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "",
                "parent_sku": "CANONICAL-SG-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "9.90",
                "unit_price_usolf": "9.90",
                "nav_code": "2000842",
                "resolve_sku": "2000842",
            }
        ]
    )
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="sg-current", gateway=_Gateway())

    batch = adapt_shopee_full_order(parsed, product_master=master)
    repository.sync(batch)

    item = repository.read_snapshot().items[0]
    assert batch.syncable is True
    assert (item.resolved_seller_sku, item.nav, item.unit_price, item.resolve_sku) == (
        "CANONICAL-SG-1", "2000842", Decimal("9.90"), "2000842"
    )


def test_sg_sku_disambiguation_uses_one_matched_row_for_all_persisted_fields():
    row = list(_rows()[0])
    row[4] = ""
    row[6] = "SKU-B"
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-A", "parent_sku": "", "product_name": "Organic Item",
                "variation_name": "Red", "unit_selling_price": "6.50",
                "unit_price_usolf": "6.50", "nav_code": "NAV-A", "resolve_sku": "RESOLVE-A",
            },
            {
                "seller_sku": "SKU-B", "parent_sku": "", "product_name": "Organic Item",
                "variation_name": "Red", "unit_selling_price": "7.75",
                "unit_price_usolf": "7.75", "nav_code": "NAV-B", "resolve_sku": "RESOLVE-B",
            },
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is True
    item = batch.items[0]
    assert (item.resolved_seller_sku, item.nav, item.unit_price, item.resolve_sku) == (
        "SKU-B", "NAV-B", Decimal("7.75"), "RESOLVE-B"
    )


def test_full_order_refresh_revalidates_retained_source_with_new_product_master():
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=_Gateway())
    stale = review_shopee_full_order_upload(
        _workbook_bytes([_rows()[0]]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
        product_master=ProductPriceMaster.from_rows([]),
        repository=repository,
    )

    refreshed = refresh_shopee_full_order_review(
        stale,
        product_master=_master(),
        repository=repository,
    )

    assert parsed.source_hash == refreshed.parsed.source_hash
    assert stale.ready is False
    assert refreshed.ready is True


def test_full_order_refresh_failure_does_not_mutate_the_previous_review():
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=_Gateway())
    review = review_shopee_full_order_upload(
        _workbook_bytes([_rows()[0]]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
        product_master=_master(),
        repository=repository,
    )

    with pytest.raises(ValueError, match="Unreadable or corrupt"):
        refresh_shopee_full_order_review(
            replace(review, source_bytes=b"not-an-xlsx"),
            product_master=_master(),
            repository=repository,
        )

    assert review.ready is True


def test_full_order_keeps_native_seller_sku_and_exposes_listing_resolve_sku():
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )
    batch = adapt_shopee_full_order(parsed, product_master=_master(resolve_sku="NAV:5001438-3"))

    item = batch.items[0]
    assert item.source_seller_sku == "SKU-1"
    assert item.resolved_seller_sku == "SKU-1"
    assert item.resolve_sku == "NAV:5001438-3"


def test_sg_full_order_persists_same_row_usolf_price_not_price_sgd():
    row = list(_rows()[0])
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "unit_price_usolf": "7.75",
                "nav_code": "5001",
                "resolve_sku": "SG-RESOLVE-1",
            }
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == "SKU-1"
    assert batch.items[0].nav == "5001"
    assert batch.items[0].unit_price == Decimal("7.75")


def test_sg_full_order_uses_name_variation_before_seller_sku():
    row = list(_rows()[0])
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1", "parent_sku": "PARENT-1",
                "product_name": "Wrong name", "variation_name": "Red",
                "unit_selling_price": "6.50", "unit_price_usolf": "6.50", "nav_code": "WRONG",
            },
            {
                "seller_sku": "SKU-2", "parent_sku": "PARENT-2",
                "product_name": "Organic Item", "variation_name": "Red",
                "unit_selling_price": "7.25", "unit_price_usolf": "7.75", "nav_code": "RIGHT",
                "resolve_sku": "SG-RESOLVED",
            },
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == "SKU-2"
    assert batch.items[0].nav == "RIGHT"
    assert batch.items[0].unit_price == Decimal("7.75")
    assert batch.items[0].resolve_sku == "SG-RESOLVED"


def test_sg_full_order_uses_seller_sku_only_to_disambiguate_exact_name_variation_rows():
    row = list(_rows()[0])
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1", "parent_sku": "PARENT-1",
                "product_name": "Organic Item", "variation_name": "Red",
                "unit_selling_price": "6.50", "unit_price_usolf": "7.75", "nav_code": "RIGHT",
                "resolve_sku": "SG-RESOLVE-1",
            },
            {
                "seller_sku": "SKU-ALT", "parent_sku": "PARENT-ALT",
                "product_name": "Organic Item", "variation_name": "Red",
                "unit_selling_price": "6.50", "unit_price_usolf": "8.50", "nav_code": "OTHER",
                "resolve_sku": "SG-RESOLVE-ALT",
            },
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == "SKU-1"
    assert batch.items[0].nav == "RIGHT"


def test_sg_full_order_never_selects_duplicate_exact_listing_rows():
    row = list(_rows()[0])
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    record = {
        "seller_sku": "SKU-1", "parent_sku": "PARENT-1",
        "product_name": "Organic Item", "variation_name": "Red",
        "unit_selling_price": "6.50", "unit_price_usolf": "7.75", "nav_code": "5001",
    }

    batch = adapt_shopee_full_order(
        parsed, product_master=ProductPriceMaster.from_rows([record, record])
    )

    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert "multiple Listing rows" in batch.issues[0].message


def test_source_country_is_market_authority_not_requested_context():
    sg_row = list(_rows()[0])
    sg_row[-1] = "SG"

    parsed = parse_shopee_full_order(
        _workbook_bytes([sg_row]), source_filename="sg-full-order.xlsx", market=SHOPEE_MY
    )

    assert parsed.market is SHOPEE_SG


@pytest.mark.parametrize(
    ("source_country", "current_market", "detected_market"),
    (("SG", SHOPEE_MY, SHOPEE_SG), ("MY", SHOPEE_SG, SHOPEE_MY)),
)
def test_market_mismatch_blocks_before_current_write_and_confirmed_switch_is_idempotent(
    source_country, current_market, detected_market
):
    source_row = list(_rows()[0])
    source_row[-1] = source_country
    source = _workbook_bytes([source_row])
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="sg-current", gateway=gateway)

    with pytest.raises(ShopeeFullOrderMarketMismatch) as error:
        review_shopee_full_order_upload(
            source,
            source_filename="market-mismatch.xlsx",
            market=current_market,
            product_master=_master(),
            repository=repository,
        )

    assert error.value.current_market is current_market
    assert error.value.parsed.market is detected_market
    assert gateway.write_count == 0

    matching_master = (
        _master(resolve_sku="SG-RESOLVE-1")
        if detected_market is SHOPEE_SG
        else _master()
    )
    review = review_shopee_full_order_upload(
        error.value.source_bytes,
        source_filename=error.value.parsed.source_filename,
        market=detected_market,
        product_master=matching_master,
        repository=repository,
    )
    first = commit_shopee_full_order_review(
        review,
        load_product_master=(
            (lambda: _master(resolve_sku="SG-RESOLVE-1"))
            if detected_market is SHOPEE_SG
            else _master
        ),
        repository=repository,
    )
    second = commit_shopee_full_order_review(
        review,
        load_product_master=(
            (lambda: _master(resolve_sku="SG-RESOLVE-1"))
            if detected_market is SHOPEE_SG
            else _master
        ),
        repository=repository,
    )

    assert first.new_count == 1
    assert second.unchanged_count == 1
    assert gateway.write_count == 1


@pytest.mark.parametrize(
    ("country", "error_code"),
    (("", "blank Country"), ("XX", "UNSUPPORTED_MARKET_FULL_ORDER_COUNTRY")),
)
def test_missing_or_unsupported_country_blocks_full_order_source(country, error_code):
    row = list(_rows()[0])
    row[-1] = country

    with pytest.raises(ShopeeFullOrderParseError, match=error_code):
        parse_shopee_full_order(
            _workbook_bytes([row]), source_filename="invalid-country.xlsx", market=SHOPEE_MY
        )


def test_mixed_my_and_sg_country_blocks_one_full_order_workbook():
    sg_row = list(_rows()[0])
    sg_row[-1] = "SG"

    with pytest.raises(ShopeeFullOrderParseError, match="MIXED_MARKET_FULL_ORDER_SOURCE"):
        parse_shopee_full_order(
            _workbook_bytes([_rows()[0], sg_row]),
            source_filename="mixed.xlsx",
            market=SHOPEE_MY,
        )


def test_missing_country_header_blocks_full_order_source():
    headers = tuple(header for header in HEADERS if header != "Country")
    row = tuple(_rows()[0][index] for index, header in enumerate(HEADERS) if header != "Country")

    with pytest.raises(ShopeeFullOrderParseError, match="Country"):
        parse_shopee_full_order(
            _workbook_bytes([row], headers=headers),
            source_filename="missing-country-column.xlsx",
            market=SHOPEE_MY,
        )


def test_pm_incomplete_resolution_is_distinct_non_money_blocker():
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    batch = adapt_shopee_full_order(parsed, product_master=_master(nav=""))

    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert batch.orders == ()


def test_exact_seller_sku_precedes_a_different_exact_parent_product_row():
    row = list(_rows()[0])
    row[4] = "PARENT-PRODUCT"
    row[5] = "Child Item"
    row[6] = "CHILD-SKU"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "CHILD-SKU",
                "parent_sku": "PARENT-PRODUCT",
                "product_name": "Child Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "unit_price_usolf": "6.50",
                "nav_code": "NAV-CHILD",
                "product_id": "PID-CHILD",
            },
            {
                "seller_sku": "PARENT-PRODUCT",
                "parent_sku": "",
                "product_name": "Parent Product",
                "variation_name": "",
                "unit_selling_price": "23.90",
                "unit_price_usolf": "23.90",
                "nav_code": "NAV-PARENT",
                "product_id": "PID-PARENT",
            },
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == "CHILD-SKU"
    assert batch.items[0].nav == "NAV-CHILD"
    assert batch.items[0].unit_price == Decimal("6.50")


def test_my_full_order_persists_usolf_price_not_pm_price_and_preserves_subtotal():
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "unit_price_usolf": "7.75",
                "nav_code": "5001",
            }
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is True
    assert batch.items[0].nav == "5001"
    assert batch.items[0].unit_price == Decimal("7.75")
    assert batch.items[0].product_subtotal == Decimal("10.25")


@pytest.mark.parametrize("usolf_price", ("", "not-a-number", "0"))
def test_my_full_order_blocks_when_matched_pm_usolf_is_missing_or_invalid(usolf_price):
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "unit_price_usolf": usolf_price,
                "nav_code": "5001",
            }
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert "UNIT PRICE USOLF" in batch.issues[0].message


@pytest.mark.parametrize("usolf_price", ("", "not-a-number", "0"))
def test_sg_full_order_blocks_when_matched_pm_usolf_is_missing_or_invalid(usolf_price):
    row = list(_rows()[0])
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "unit_price_usolf": usolf_price,
                "nav_code": "5001",
            }
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert "UNIT PRICE USOLF" in batch.issues[0].message


def test_my_full_order_blocks_when_resolved_identity_has_ambiguous_usolf():
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1", "parent_sku": "PARENT-1",
                "product_name": "Organic Item", "variation_name": "Red",
                "unit_selling_price": "6.50", "unit_price_usolf": "7.75",
                "nav_code": "5001",
            },
            {
                "seller_sku": "SKU-1", "parent_sku": "PARENT-1",
                "product_name": "Organic Item", "variation_name": "Red",
                "unit_selling_price": "6.50", "unit_price_usolf": "8.00",
                "nav_code": "5001",
            },
        ]
    )

    batch = adapt_shopee_full_order(parsed, product_master=master)

    assert batch.syncable is False
    assert batch.issues[0].code == PM_ENRICHMENT_REQUIRED
    assert "UNIT PRICE USOLF" in batch.issues[0].message


class _Gateway:
    def __init__(self):
        self.tabs = {
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: [
                list(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS)
            ],
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: [
                list(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS)
            ],
        }
        self.ids = {
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: 101,
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: 102,
        }
        self.row_counts = {
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: 1000,
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: 1000,
        }
        self.write_count = 0
        self.last_requests = []

    def read_tabs(self, _spreadsheet_id, tabs):
        return {tab: [list(row) for row in self.tabs[tab]] for tab in tabs}

    def read_sheet_ids(self, _spreadsheet_id, tabs):
        return {tab: self.ids[tab] for tab in tabs}

    def read_sheet_row_counts(self, _spreadsheet_id, tabs):
        return {tab: self.row_counts[tab] for tab in tabs}

    def batch_update(self, _spreadsheet_id, requests):
        self.write_count += 1
        self.last_requests = list(requests)
        title_by_id = {value: key for key, value in self.ids.items()}
        for request in requests:
            if "appendDimension" in request:
                append = request["appendDimension"]
                self.row_counts[title_by_id[append["sheetId"]]] += append["length"]
                continue
            if "appendCells" in request:
                append = request["appendCells"]
                title = title_by_id[append["sheetId"]]
                self.tabs[title].extend(
                    [
                        [cell["userEnteredValue"]["stringValue"] for cell in row["values"]]
                        for row in append["rows"]
                    ]
                )
                continue
            update = request["updateCells"]
            title = title_by_id[update["range"]["sheetId"]]
            values = [
                [cell["userEnteredValue"]["stringValue"] for cell in row["values"]]
                for row in update["rows"]
            ]
            self.tabs[title] = [self.tabs[title][0]] + [
                row for row in values if any(row)
            ]


def _batch(rows=None, *, now=None, product_master=None):
    parsed = parse_shopee_full_order(
        _workbook_bytes(rows or _rows(), malformed_dimension=True),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    return adapt_shopee_full_order(
        parsed,
        product_master=product_master or _master(),
        now=lambda: now or datetime(2026, 10, 16, tzinfo=timezone.utc),
    )


def test_sync_is_idempotent_replaces_all_items_and_keeps_absent_orders():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(
        spreadsheet_id="uat2", gateway=gateway
    )
    original = _batch()

    first = repository.sync(original)
    second = repository.sync(_batch(now=datetime(2026, 10, 17, tzinfo=timezone.utc)))

    assert [decision.status for decision in first.decisions] == [FullOrderSyncStatus.NEW]
    assert [decision.status for decision in second.decisions] == [
        FullOrderSyncStatus.UNCHANGED
    ]
    assert gateway.write_count == 1

    other_row = list(_rows()[0])
    other_row[0] = "ORDER-2"
    repository.sync(_batch([tuple(other_row)]))

    changed = list(_rows()[0])
    changed[9] = "9.50"
    changed_result = repository.sync(_batch([tuple(changed)]))
    snapshot = repository.read_snapshot()

    assert changed_result.decisions[0].status is FullOrderSyncStatus.UPDATED
    assert {order.order_id for order in snapshot.orders} == {"ORDER-1", "ORDER-2"}
    order_1_items = [item for item in snapshot.items if item.order_id == "ORDER-1"]
    assert len(order_1_items) == 1
    assert order_1_items[0].product_subtotal == Decimal("9.50")
    assert any(item.order_id == "ORDER-2" for item in snapshot.items)


def test_same_workbook_reimport_is_updated_when_my_usolf_price_changes():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )
    old_master = ProductPriceMaster.from_rows(
        [_master().records[0].__dict__ | {"unit_price_usolf": Decimal("6.50")}]
    )
    revised_master = ProductPriceMaster.from_rows(
        [_master().records[0].__dict__ | {"unit_price_usolf": Decimal("7.75")}]
    )

    repository.sync(adapt_shopee_full_order(parsed, product_master=old_master))
    result = repository.sync(adapt_shopee_full_order(parsed, product_master=revised_master))

    assert result.updated_count == 1
    assert repository.read_snapshot().items[0].unit_price == Decimal("7.75")


def test_reupload_preserves_stored_resolve_sku_when_listing_value_changes():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )

    repository.sync(
        adapt_shopee_full_order(parsed, product_master=_master(resolve_sku="9555208010555-2"))
    )
    result = repository.sync(
        adapt_shopee_full_order(parsed, product_master=_master(resolve_sku="NAV:5001438"))
    )

    assert result.unchanged_count == 1
    assert repository.read_snapshot().items[0].resolve_sku == "9555208010555-2"


def test_reupload_preserves_a_stored_blank_resolve_sku_without_backfilling_it():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    parsed = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )

    repository.sync(adapt_shopee_full_order(parsed, product_master=_master()))
    result = repository.sync(
        adapt_shopee_full_order(parsed, product_master=_master(resolve_sku="NAV:5001438-3"))
    )

    assert result.unchanged_count == 1
    assert repository.read_snapshot().items[0].resolve_sku == ""


def test_sg_reupload_refreshes_same_row_resolve_sku_without_changing_my_behavior():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="sg-current", gateway=gateway)
    row = list(_rows()[0])
    row[-1] = "SG"
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]), source_filename="sg-full-order.xlsx", market=SHOPEE_SG
    )

    repository.sync(
        adapt_shopee_full_order(parsed, product_master=_master(resolve_sku="SG-RESOLVE-1"))
    )
    result = repository.sync(
        adapt_shopee_full_order(parsed, product_master=_master(resolve_sku="SG-RESOLVE-2"))
    )

    assert result.updated_count == 1
    assert repository.read_snapshot().items[0].resolve_sku == "SG-RESOLVE-2"


def test_updated_reupload_preserves_raw_existing_resolve_sku_when_listing_is_unavailable():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    original = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )
    stored_value = " 9555208010555-2;\nNAV:5001438-3; MANUAL-A-01 "
    repository.sync(
        adapt_shopee_full_order(original, product_master=_master(resolve_sku=stored_value))
    )

    changed = list(_rows()[0])
    changed[9] = "9.50"
    updated = parse_shopee_full_order(
        _workbook_bytes([tuple(changed)]),
        source_filename="full-order-revised.xlsx",
        market=SHOPEE_MY,
    )
    result = repository.sync(adapt_shopee_full_order(updated, product_master=_master()))

    item = repository.read_snapshot().items[0]
    assert result.updated_count == 1
    assert item.resolve_sku == stored_value
    assert item.resolved_seller_sku == "SKU-1"
    assert item.product_subtotal == Decimal("9.50")


def test_updated_reupload_preserves_a_stored_blank_resolve_sku_without_backfilling_it():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    original = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )
    repository.sync(adapt_shopee_full_order(original, product_master=_master()))

    changed = list(_rows()[0])
    changed[9] = "9.50"
    updated = parse_shopee_full_order(
        _workbook_bytes([tuple(changed)]),
        source_filename="full-order-revised.xlsx",
        market=SHOPEE_MY,
    )
    result = repository.sync(
        adapt_shopee_full_order(updated, product_master=_master(resolve_sku="NAV:5001438-3"))
    )

    assert result.updated_count == 1
    assert repository.read_snapshot().items[0].resolve_sku == ""


def test_updated_reupload_preserves_existing_resolve_sku_when_listing_candidates_conflict():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    original = parse_shopee_full_order(
        _workbook_bytes([_rows()[0]]), source_filename="full-order.xlsx", market=SHOPEE_MY
    )
    repository.sync(
        adapt_shopee_full_order(
            original, product_master=_master(resolve_sku="9555208010555-2")
        )
    )

    changed = list(_rows()[0])
    changed[9] = "9.50"
    conflicting_master = ProductPriceMaster.from_rows(
        [
            _master().records[0].__dict__ | {"resolve_sku": "9555208010555-2"},
            _master().records[0].__dict__ | {"resolve_sku": "NAV:5001438-3"},
        ]
    )
    updated = parse_shopee_full_order(
        _workbook_bytes([tuple(changed)]),
        source_filename="full-order-revised.xlsx",
        market=SHOPEE_MY,
    )
    batch = adapt_shopee_full_order(updated, product_master=conflicting_master)
    result = repository.sync(batch)

    assert batch.items[0].resolve_sku == ""
    assert batch.items[0].resolve_sku_warning == (
        "Resolved SKU conflicts across matched Listing candidates."
    )
    assert result.updated_count == 1
    assert repository.read_snapshot().items[0].resolve_sku == "9555208010555-2"


def test_missing_only_sync_appends_absent_orders_without_rewriting_existing_current_facts():
    gateway = _Gateway()
    repository = ShopeeFullOrderCurrentRepository(spreadsheet_id="uat2", gateway=gateway)
    original = _batch([_rows()[0]])
    repository.sync(original)
    before = repository.read_snapshot()
    changed_existing = list(_rows()[0])
    changed_existing[9] = "9.50"
    new_order = list(_rows()[0])
    new_order[0] = "ORDER-2"
    result = repository.sync_missing_only(_batch([tuple(changed_existing), tuple(new_order)]))
    after = repository.read_snapshot()

    assert result.new_count == 1
    assert result.retained_count == 1
    assert result.updated_count == 0
    assert [item for item in after.items if item.order_id == "ORDER-1"] == [
        item for item in before.items if item.order_id == "ORDER-1"
    ]
    assert {order.order_id for order in after.orders} == {"ORDER-1", "ORDER-2"}
    assert all("updateCells" not in request for request in gateway.last_requests)
    assert repository.sync_missing_only(_batch([tuple(changed_existing), tuple(new_order)])).new_count == 0


def test_sync_expands_only_current_grid_rows_before_writing_a_larger_snapshot():
    gateway = _Gateway()
    gateway.row_counts = {
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: 1,
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: 1,
    }
    repository = ShopeeFullOrderCurrentRepository(
        spreadsheet_id="uat2", gateway=gateway
    )

    result = repository.sync(_batch())

    assert result.new_count == 1
    assert gateway.row_counts == {
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: 2,
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: 3,
    }
    assert [
        request["appendDimension"]["sheetId"]
        for request in gateway.last_requests
        if "appendDimension" in request
    ] == [101, 102]


def test_schema_plan_adds_only_the_two_concrete_current_tabs():
    snapshot = SpreadsheetSchemaSnapshot(
        tabs={"Invoice_Orders": SheetSchema(8, ("existing",))}
    )

    assert classify_full_order_schema(snapshot) is FullOrderSchemaState.ELIGIBLE
    requests = build_full_order_schema_requests(snapshot)

    assert len(requests) == 4
    assert requests[0]["addSheet"]["properties"]["title"] == (
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB
    )
    assert requests[1]["addSheet"]["properties"]["title"] == (
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB
    )
    assert requests[0]["addSheet"]["properties"]["sheetId"] == 9
    assert requests[1]["addSheet"]["properties"]["sheetId"] == 10
    assert SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS == (
        "market", "order_id", "order_status", "order_created_date", "source_file",
        "snapshot_hash", "last_synced_at",
    )
    assert SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS == (
        "market", "order_id", "item_index", "source_parent_sku", "source_seller_sku",
        "resolved_seller_sku", "nav", "product_name", "variation", "quantity",
        "unit_price", "product_subtotal", "return_refund_status", "resolve_sku",
    )


def test_schema_plan_upgrades_only_the_existing_current_items_header_for_resolve_sku():
    snapshot = SpreadsheetSchemaSnapshot(
        tabs={
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: SheetSchema(
                101, SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS
            ),
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: SheetSchema(
                102, SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS[:-1]
            ),
        }
    )

    assert (
        classify_full_order_schema(snapshot)
        is FullOrderSchemaState.RESOLVE_SKU_UPGRADE_REQUIRED
    )
    requests = build_full_order_schema_requests(snapshot)

    assert requests[0]["insertDimension"]["range"] == {
        "sheetId": 102,
        "dimension": "COLUMNS",
        "startIndex": 13,
        "endIndex": 14,
    }
    assert requests[1]["updateCells"]["rows"][0]["values"][0] == {
        "userEnteredValue": {"stringValue": "resolve_sku"}
    }


def test_snapshot_hash_excludes_upload_provenance_and_is_order_scoped():
    first = _batch()
    different_source_file = parse_shopee_full_order(
        _workbook_bytes(_rows()), source_filename="renamed.xlsx", market=SHOPEE_MY
    )
    renamed = adapt_shopee_full_order(
        different_source_file,
        product_master=_master(),
        now=lambda: datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    changed_row = list(_rows()[0])
    changed_row[9] = "9.50"
    changed = _batch([tuple(changed_row)])

    assert first.orders[0].snapshot_hash == renamed.orders[0].snapshot_hash
    assert first.orders[0].snapshot_hash != changed.orders[0].snapshot_hash
    assert not hasattr(first.orders[0], "platform")
    assert not hasattr(first.orders[0], "source_type")
    assert not hasattr(first.items[0], "source_file")


def test_snapshot_hash_orders_items_by_numeric_item_index():
    base = _batch([_rows()[0]]).items[0]
    high = FullOrderCurrentItem(
        market=base.market, order_id=base.order_id, item_index=2,
        source_parent_sku=base.source_parent_sku, source_seller_sku="SECOND",
        resolved_seller_sku="SECOND", nav="5002", product_name="Second",
        variation="", quantity=1, unit_price=Decimal("4.00"),
        product_subtotal=Decimal("4.00"), return_refund_status="",
    )
    low = FullOrderCurrentItem(
        market=base.market, order_id=base.order_id, item_index=1,
        source_parent_sku=base.source_parent_sku, source_seller_sku=base.source_seller_sku,
        resolved_seller_sku=base.resolved_seller_sku, nav=base.nav,
        product_name=base.product_name, variation=base.variation,
        quantity=base.quantity, unit_price=base.unit_price,
        product_subtotal=base.product_subtotal, return_refund_status="",
    )

    assert full_order_snapshot_hash(
        market="shopee_my", order_id="ORDER-1", order_status="Completed",
        order_created_date=datetime(2026, 9, 10).date(), items=[high, low],
    ) == full_order_snapshot_hash(
        market="shopee_my", order_id="ORDER-1", order_status="Completed",
        order_created_date=datetime(2026, 9, 10).date(), items=[low, high],
    )


def _statement(amount: str, *, refund: str = "0.00") -> ParsedShopeeWeeklyStatement:
    value = Decimal(amount)
    refund_amount = Decimal(refund)

    def row(view_by: str, source_row: int) -> SettlementIncomeRow:
        components = {name: Decimal("0.00") for name in INCOME_COMPONENT_COLUMNS}
        components["Product Price"] = value
        components["Refund Amount"] = refund_amount
        return SettlementIncomeRow(
            sequence_no=str(source_row),
            view_by=view_by,
            order_id="ORDER-1",
            product_id="PID-1" if view_by == "Sku" else "",
            product_name="Organic Item" if view_by == "Sku" else "",
            order_creation_date=None,
            payout_completed_date=None,
            release_channel="",
            order_type="Normal Order",
            total_released_amount=sum(components.values(), Decimal("0.00")),
            financial_components=components,
            source_values={},
            source_row_number=source_row,
        )

    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash="s" * 64,
        statement_period_from=datetime(2026, 9, 1).date(),
        statement_period_to=datetime(2026, 9, 30).date(),
        summary_total_released=value + refund_amount,
        adjustment_control_total=Decimal("0.00"),
        adjustment_footer_total=None,
        income_rows=(row("Order", 2), row("Sku", 3)),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )


def test_full_order_statement_proof_requires_exact_item_and_order_cents():
    current = _batch([_rows()[0]])
    snapshot = FullOrderCurrentSnapshot(current.orders, current.items)

    passed = prove_full_order_current_against_statement(
        _statement("10.25"), snapshot, product_master=_master()
    )
    failed = prove_full_order_current_against_statement(
        _statement("10.24"), snapshot, product_master=_master()
    )

    assert passed.ready is True
    assert passed.reconciliation is not None
    assert all(
        evidence.difference == Decimal("0.00")
        for result in passed.reconciliation.orders
        for evidence in result.evidence.merchandise
        if evidence.difference is not None
    )
    assert failed.ready is False
    assert any("exact Full Order/Statement" in blocker for blocker in failed.blockers)


def test_full_order_statement_proof_allows_return_refund_request_without_statement_refund():
    marked = list(_rows()[0])
    marked[2] = "Return / Refund"
    current = _batch([tuple(marked)])

    proof = prove_full_order_current_against_statement(
        _statement("10.25"),
        FullOrderCurrentSnapshot(current.orders, current.items),
        product_master=_master(),
    )

    assert proof.ready is True
    assert proof.reconciliation is not None


def test_full_order_statement_proof_accepts_marked_item_with_exact_refund_attribution():
    marked = list(_rows()[0])
    marked[2] = "Request Approved"
    current = _batch([tuple(marked)])

    proof = prove_full_order_current_against_statement(
        _statement("10.25", refund="-2.00"),
        FullOrderCurrentSnapshot(current.orders, current.items),
        product_master=_master(),
    )

    assert proof.ready is True
    assert proof.reconciliation is not None
    result = proof.reconciliation.orders[0]
    assert result.summary.identity_scope is IdentityScope.ITEM
    assert result.summary.settlement_basis.value == "EXACT"
    assert result.evidence.refund.statement_amount == Decimal("-2.00")
    assert result.evidence.refund.allocation_resolved is True
    assert current.items[0].return_refund_status == "Request Approved"
    assert proof.bundles[0].items[0].quantity == 2
    assert proof.bundles[0].items[0].line_subtotal == Decimal("10.25")


def test_full_order_group_uses_member_subtotals_without_pdf_allocation():
    synced_at = datetime(2026, 10, 16, tzinfo=timezone.utc)
    order = FullOrderCurrentOrder(
        market="shopee_my",
        order_id="ORDER-1",
        order_status="Completed",
        order_created_date=datetime(2026, 9, 10).date(),
        source_file="full-order.xlsx",
        snapshot_hash="f" * 64,
        last_synced_at=synced_at,
    )
    items = tuple(
        FullOrderCurrentItem(
            market="shopee_my",
            order_id="ORDER-1",
            item_index=index,
            source_parent_sku="COMMON-PARENT",
            source_seller_sku="",
            resolved_seller_sku=f"SKU-{index}",
            nav=f"NAV-{index}",
            product_name="Bundle Product",
            variation="",
            quantity=1,
            unit_price=Decimal("6.00"),
            product_subtotal=Decimal("5.00"),
            return_refund_status="",
        )
        for index in range(2)
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": f"SKU-{index}",
                "parent_sku": "COMMON-PARENT",
                "product_name": "Bundle Product",
                "variation_name": "",
                "unit_selling_price": "6.00",
                "nav_code": f"NAV-{index}",
                "product_id": "PID-GROUP",
            }
            for index in range(2)
        ]
    )
    statement = _group_statement()

    proof = prove_full_order_current_against_statement(
        statement,
        FullOrderCurrentSnapshot((order,), items),
        product_master=master,
    )

    assert proof.ready is True
    assert proof.reconciliation is not None
    assert proof.reconciliation.orders[0].summary.identity_scope is IdentityScope.GROUP
    assert all(
        evidence.difference == Decimal("0.00")
        for evidence in proof.reconciliation.orders[0].evidence.merchandise
        if evidence.difference is not None
    )


def test_full_order_statement_proof_allows_marked_refund_with_group_statement_evidence():
    synced_at = datetime(2026, 10, 16, tzinfo=timezone.utc)
    order = FullOrderCurrentOrder(
        market="shopee_my",
        order_id="ORDER-1",
        order_status="Completed",
        order_created_date=datetime(2026, 9, 10).date(),
        source_file="full-order.xlsx",
        snapshot_hash="f" * 64,
        last_synced_at=synced_at,
    )
    items = tuple(
        FullOrderCurrentItem(
            market="shopee_my",
            order_id="ORDER-1",
            item_index=index,
            source_parent_sku="COMMON-PARENT",
            source_seller_sku="",
            resolved_seller_sku=f"SKU-{index}",
            nav=f"NAV-{index}",
            product_name="Bundle Product",
            variation="",
            quantity=1,
            unit_price=Decimal("6.00"),
            product_subtotal=Decimal("5.00"),
            return_refund_status="Request Approved" if index == 0 else "",
        )
        for index in range(2)
    )
    master = ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": f"SKU-{index}",
                "parent_sku": "COMMON-PARENT",
                "product_name": "Bundle Product",
                "variation_name": "",
                "unit_selling_price": "6.00",
                "nav_code": f"NAV-{index}",
                "product_id": "PID-GROUP",
            }
            for index in range(2)
        ]
    )

    proof = prove_full_order_current_against_statement(
        _group_statement(refund="-2.00"),
        FullOrderCurrentSnapshot((order,), items),
        product_master=master,
    )

    assert proof.ready is True
    assert proof.reconciliation is not None
    assert proof.reconciliation.orders[0].summary.identity_scope is IdentityScope.GROUP


def test_full_order_statement_proof_still_blocks_marked_request_when_statement_bridge_is_incomplete():
    marked = list(_rows()[0])
    marked[2] = "Request Approved"
    current = _batch([tuple(marked)])
    statement = _statement("10.25")
    order_row, sku_row = statement.income_rows
    components = dict(order_row.financial_components)
    components["Commission Fee (incl. SST)"] = None
    incomplete = replace(
        statement,
        income_rows=(replace(order_row, financial_components=components), sku_row),
    )

    proof = prove_full_order_current_against_statement(
        incomplete,
        FullOrderCurrentSnapshot(current.orders, current.items),
        product_master=_master(),
    )

    assert proof.ready is False
    assert proof.blockers == ("ORDER-1: Statement financial bridge is incomplete.",)


def _group_statement(*, refund: str = "0.00") -> ParsedShopeeWeeklyStatement:
    def components(amount, refund_amount="0.00"):
        result = {name: Decimal("0.00") for name in INCOME_COMPONENT_COLUMNS}
        result["Product Price"] = Decimal(amount)
        result["Refund Amount"] = Decimal(refund_amount)
        return result

    refund_amount = Decimal(refund)

    order = SettlementIncomeRow(
        "1", "Order", "ORDER-1", "", "", None, None, "", "Normal Order",
        Decimal("10.00") + refund_amount, components("10.00", refund), {}, 2,
    )
    sku_rows = tuple(
        SettlementIncomeRow(
            str(index + 2), "Sku", "ORDER-1", "PID-GROUP", "Bundle Product",
            None, None, "", "Normal Order", Decimal("5.00") + (refund_amount if index == 0 else Decimal("0.00")), components("5.00", refund if index == 0 else "0.00"),
            {}, index + 3,
        )
        for index in range(2)
    )
    return ParsedShopeeWeeklyStatement(
        source_filename="statement.xlsx",
        file_hash="g" * 64,
        statement_period_from=datetime(2026, 9, 1).date(),
        statement_period_to=datetime(2026, 9, 30).date(),
        summary_total_released=Decimal("10.00") + refund_amount,
        adjustment_control_total=Decimal("0.00"),
        adjustment_footer_total=None,
        income_rows=(order, *sku_rows),
        service_fee_details=(),
        shipping_fee_discrepancies=(),
        adjustments=(),
        source_value_issues=(),
        dimension_fallback_sheets=(),
    )
