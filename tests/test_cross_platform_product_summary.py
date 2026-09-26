from datetime import date
from decimal import Decimal

import pytest

from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
)


def _sheet_row(headers, **values):
    return [values.get(header, "") for header in headers]


class _Gateway:
    def __init__(self, tabs):
        self.tabs = tabs
        self.read_calls = []

    def read_tabs(self, spreadsheet_id, tabs):
        self.read_calls.append((spreadsheet_id, tuple(tabs)))
        return {
            tab: [list(row) for row in self.tabs[tab]]
            for tab in tabs
        }


def test_live_reader_uses_one_committed_snapshot_and_only_payout_eligible_shopee_rows():
    from src.invoice_app.services.cross_platform_product_summary import (
        GoogleSheetsCrossPlatformProductSummaryReader,
        build_cross_platform_product_summary,
    )

    gateway = _Gateway(
        {
            INVOICE_ORDERS_TAB: [
                list(INVOICE_ORDERS_HEADERS),
                _sheet_row(
                    INVOICE_ORDERS_HEADERS,
                    platform="Shopee",
                    order_id="SHP-PAYOUT",
                    payout_completed_date="2026-08-08",
                    first_imported_at="2026-08-08T10:00:00+00:00",
                ),
                _sheet_row(
                    INVOICE_ORDERS_HEADERS,
                    platform="Shopee",
                    order_id="SHP-NO-PAYOUT",
                    first_imported_at="2026-08-08T10:00:00+00:00",
                ),
            ],
            INVOICE_ITEMS_TAB: [
                list(INVOICE_ITEMS_HEADERS),
                _sheet_row(
                    INVOICE_ITEMS_HEADERS,
                    platform="Shopee",
                    order_id="SHP-PAYOUT",
                    item_index="0",
                    seller_sku="9555208107347",
                    nav="300001",
                    product_name="Persisted Tea",
                    variation="500ml",
                    quantity="2",
                    unit_price="10.00",
                    line_subtotal="18.00",
                ),
                _sheet_row(
                    INVOICE_ITEMS_HEADERS,
                    platform="Shopee",
                    order_id="SHP-NO-PAYOUT",
                    item_index="0",
                    seller_sku="9555208109999",
                    nav="300002",
                    product_name="Not eligible",
                    quantity="1",
                    unit_price="5.00",
                    line_subtotal="5.00",
                ),
            ],
        }
    )

    snapshot = GoogleSheetsCrossPlatformProductSummaryReader(
        spreadsheet_id="uat2", gateway=gateway
    ).load_snapshot()
    summary = build_cross_platform_product_summary(snapshot, platform="Shopee")

    assert gateway.read_calls == [
        ("uat2", (INVOICE_ORDERS_TAB, INVOICE_ITEMS_TAB))
    ]
    assert snapshot.available_payout_dates("Shopee") == (date(2026, 8, 8),)
    assert [(row.sku_code, row.nav, row.uom, row.unit_price, row.quantity, row.original_sales, row.discount_amount, row.amount) for row in summary.product_rows] == [
        (
            "9555208107347",
            "300001",
            "EA",
            Decimal("10.00"),
            2,
            Decimal("20.00"),
            Decimal("2.00"),
            Decimal("18.00"),
        )
    ]


def _item(
    *,
    order_id,
    item_index,
    payout_date,
    sku,
    unit_price,
    quantity,
    line_subtotal,
    nav="300001",
    description="Persisted product",
    variation="500ml",
    promotion_group_id=None,
    source_group_total=None,
):
    from src.invoice_app.services.cross_platform_product_summary import CommittedReportingItem

    return CommittedReportingItem(
        platform="Shopee",
        currency="RM",
        order_id=order_id,
        item_index=item_index,
        payout_completed_date=payout_date,
        seller_sku=sku,
        nav=nav,
        description=description,
        variation=variation,
        quantity=quantity,
        unit_price=Decimal(unit_price),
        line_subtotal=None if line_subtotal is None else Decimal(line_subtotal),
        promotion_group_id=promotion_group_id,
        promotion_label="Any promotion" if promotion_group_id else None,
        source_group_total=(
            None if source_group_total is None else Decimal(source_group_total)
        ),
    )


def test_payout_date_options_are_platform_linked_and_filter_before_aggregation():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(order_id="SHP-1", item_index=0, payout_date=date(2026, 8, 8), sku="SKU-1", unit_price="10.00", quantity=1, line_subtotal="9.00"),
            _item(order_id="SHP-2", item_index=0, payout_date=date(2026, 8, 10), sku="SKU-1", unit_price="10.00", quantity=2, line_subtotal="18.00"),
        )
    )

    filtered = build_cross_platform_product_summary(
        snapshot,
        platform="Shopee",
        from_date=date(2026, 8, 10),
    )

    assert snapshot.available_payout_dates("All") == (date(2026, 8, 8), date(2026, 8, 10))
    assert snapshot.available_payout_dates("Lazada") == ()
    assert [(row.quantity, row.amount) for row in filtered.product_rows] == [
        (2, Decimal("18.00"))
    ]


def test_persisted_promotion_facts_allocate_amount_without_product_master_lookup():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(order_id="SHP-PROMO", item_index=0, payout_date=date(2026, 8, 8), sku="SKU-10", unit_price="10.00", quantity=1, line_subtotal=None, promotion_group_id="promo-1", source_group_total="20.00"),
            _item(order_id="SHP-PROMO", item_index=1, payout_date=date(2026, 8, 8), sku="SKU-20", unit_price="20.00", quantity=1, line_subtotal=None, promotion_group_id="promo-1", source_group_total="20.00"),
        )
    )

    summary = build_cross_platform_product_summary(snapshot, platform="Shopee")

    assert [(row.sku_code, row.original_sales, row.discount_amount, row.amount) for row in summary.product_rows] == [
        ("SKU-10", Decimal("10.00"), Decimal("3.33"), Decimal("6.67")),
        ("SKU-20", Decimal("20.00"), Decimal("6.67"), Decimal("13.33")),
    ]


def test_invalid_payout_range_has_no_report():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="From Date must be"):
        build_cross_platform_product_summary(
            CrossPlatformReportingSnapshot(()),
            from_date=date(2026, 8, 10),
            to_date=date(2026, 8, 8),
        )


def test_cross_product_summary_uses_weekly_canonical_identity_and_display_policy():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    payout_date = date(2026, 8, 8)
    source_items = (
        _item(
            order_id="ORDER-1", item_index=0, payout_date=payout_date,
            nav="NAV-1", sku="SKU-1", unit_price="10.00", quantity=2,
            line_subtotal="16.00", description="First description", variation="Small",
        ),
        _item(
            order_id="ORDER-2", item_index=0, payout_date=payout_date,
            nav="NAV-1", sku="SKU-1", unit_price="10.00", quantity=3,
            line_subtotal="21.00", description="Second description", variation="Large",
        ),
        _item(
            order_id="ORDER-3", item_index=0, payout_date=payout_date,
            nav="NAV-2", sku="SKU-2", unit_price="12.00", quantity=1,
            line_subtotal="12.00", description="Gluten F ree", variation="Original",
        ),
        _item(
            order_id="ORDER-4", item_index=0, payout_date=payout_date,
            nav="NAV-2", sku="SKU-2", unit_price="12.00", quantity=1,
            line_subtotal="12.00", description="Gluten Free", variation="Original",
        ),
        _item(
            order_id="ORDER-5", item_index=0, payout_date=payout_date,
            nav="3000209", sku="9555208107347", unit_price="54.90", quantity=1,
            line_subtotal="54.90",
            description="Pre-Order Simply Natural Fresh Raw Honey Malaysia [Madu Asli Segar]",
            variation="1KG",
        ),
        _item(
            order_id="ORDER-6", item_index=0, payout_date=payout_date,
            nav="3000209", sku="9555208107347", unit_price="54.90", quantity=1,
            line_subtotal="54.90",
            description="Simply Natural Fresh Raw Honey Malaysia [Madu Asli Segar]",
            variation="Fresh Raw Honey 1kg",
        ),
        _item(
            order_id="ORDER-7", item_index=0, payout_date=payout_date,
            nav="NAV-3A", sku="SKU-3", unit_price="8.00", quantity=1,
            line_subtotal="8.00", description="Same SKU, first NAV", variation="A",
        ),
        _item(
            order_id="ORDER-8", item_index=0, payout_date=payout_date,
            nav="NAV-3B", sku="SKU-3", unit_price="8.00", quantity=1,
            line_subtotal="8.00", description="Same SKU, second NAV", variation="B",
        ),
    )

    summary = build_cross_platform_product_summary(
        CrossPlatformReportingSnapshot(source_items), platform="Shopee"
    )

    assert len(summary.product_rows) == 5
    rows = {(row.nav, row.sku_code, row.unit_price): row for row in summary.product_rows}
    assert rows[("NAV-1", "SKU-1", Decimal("10.00"))].product_name == (
        "First description | Small | Second description | Large"
    )
    assert rows[("NAV-1", "SKU-1", Decimal("10.00"))].quantity == 5
    assert rows[("NAV-2", "SKU-2", Decimal("12.00"))].product_name == (
        "Gluten Free | Original"
    )
    assert rows[("3000209", "9555208107347", Decimal("54.90"))].product_name == (
        "Simply Natural Fresh Raw Honey Malaysia [Madu Asli Segar] | 1KG"
    )
    assert {(row.nav, row.sku_code) for row in summary.product_rows if row.sku_code == "SKU-3"} == {
        ("NAV-3A", "SKU-3"),
        ("NAV-3B", "SKU-3"),
    }
    assert {
        (item.order_id, item.item_index) for item in summary.source_items
    } == {
        (item.order_id, item.item_index) for item in source_items
    }
    assert sum(item.quantity for item in summary.source_items) == summary.total_quantity
    assert sum(item.line_subtotal for item in source_items) == sum(
        row.amount for row in summary.product_rows
    )


def test_cross_persisted_item_missing_nav_is_blocking():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        build_cross_platform_reporting_snapshot,
    )

    tabs = {
        INVOICE_ORDERS_TAB: [
            list(INVOICE_ORDERS_HEADERS),
            _sheet_row(
                INVOICE_ORDERS_HEADERS,
                platform="Shopee",
                order_id="SHP-MISSING-NAV",
                payout_completed_date="2026-08-08",
                first_imported_at="2026-08-08T10:00:00+00:00",
            ),
        ],
        INVOICE_ITEMS_TAB: [
            list(INVOICE_ITEMS_HEADERS),
            _sheet_row(
                INVOICE_ITEMS_HEADERS,
                platform="Shopee",
                order_id="SHP-MISSING-NAV",
                item_index="0",
                seller_sku="9555208107347",
                nav="   ",
                product_name="Persisted Tea",
                quantity="1",
                unit_price="10.00",
                line_subtotal="10.00",
            ),
        ],
    }

    with pytest.raises(
        CrossPlatformProductSummaryError,
        match=r"SHP-MISSING-NAV/0: missing persisted NAV",
    ):
        build_cross_platform_reporting_snapshot(tabs)
