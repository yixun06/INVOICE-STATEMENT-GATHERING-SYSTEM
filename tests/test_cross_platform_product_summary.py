from datetime import date
from decimal import Decimal

import pytest

from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ITEMS_TAB,
    INVOICE_ORDERS_HEADERS,
    INVOICE_ORDERS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
    SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
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


def _current_only_tabs(
    *, status_by_order, order_created_date="2026-10-22", market="shopee_my"
):
    return {
        INVOICE_ORDERS_TAB: [list(INVOICE_ORDERS_HEADERS)],
        INVOICE_ITEMS_TAB: [list(INVOICE_ITEMS_HEADERS)],
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: [
            list(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS),
            *[
                _sheet_row(
                    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
                    market=market,
                    order_id=order_id,
                    order_status=status,
                    order_created_date=order_created_date,
                    source_file="full-order.xlsx",
                    snapshot_hash=(str(index) * 64)[:64],
                    last_synced_at="2026-10-22T10:00:00+00:00",
                )
                for index, (order_id, status) in enumerate(status_by_order.items(), start=1)
            ],
        ],
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: [
            list(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS),
            *[
                _sheet_row(
                    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
                    market=market,
                    order_id=order_id,
                    item_index="0",
                    resolved_seller_sku=f"SKU-{index}",
                    nav=f"NAV-{index}",
                    product_name=f"Product {index}",
                    quantity="1",
                    unit_price="10.00",
                    product_subtotal="8.00",
                )
                for index, order_id in enumerate(status_by_order, start=1)
            ],
        ],
    }


def test_live_reader_keeps_committed_shopee_rows_for_the_selected_date_type():
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
                    order_created_date="2026-08-01",
                    payout_completed_date="2026-08-08",
                    first_imported_at="2026-08-08T10:00:00+00:00",
                ),
                _sheet_row(
                    INVOICE_ORDERS_HEADERS,
                    platform="Shopee",
                    order_id="SHP-NO-PAYOUT",
                    order_created_date="2026-08-02",
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
            SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: [
                list(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS),
                _sheet_row(
                    SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
                    market="shopee_my",
                    order_id="SHP-CURRENT-ONLY",
                    order_status="Shipping",
                    order_created_date="2026-08-03",
                    source_file="full-order.xlsx",
                    snapshot_hash="a" * 64,
                    last_synced_at="2026-08-08T10:00:00+00:00",
                ),
            ],
            SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: [
                list(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS),
                _sheet_row(
                    SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
                    market="shopee_my",
                    order_id="SHP-CURRENT-ONLY",
                    item_index="0",
                    resolved_seller_sku="9555208107000",
                    nav="300003",
                    product_name="Current only",
                    quantity="1",
                    unit_price="12.00",
                    product_subtotal="10.00",
                ),
            ],
        }
    )

    snapshot = GoogleSheetsCrossPlatformProductSummaryReader(
        spreadsheet_id="uat2", gateway=gateway
    ).load_snapshot()
    summary = build_cross_platform_product_summary(snapshot, platform="Shopee MY")

    assert gateway.read_calls == [
        (
            "uat2",
            (
                INVOICE_ORDERS_TAB,
                INVOICE_ITEMS_TAB,
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB,
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB,
            ),
        )
    ]
    assert snapshot.available_payout_dates("Shopee MY") == (date(2026, 8, 8),)
    assert [item.order_id for item in snapshot.eligible_items] == [
        "SHP-PAYOUT",
        "SHP-NO-PAYOUT",
    ]
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


def test_order_created_date_uses_explicit_current_rows_without_payout_evidence():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(
                order_id="SHP-PAYOUT",
                item_index=0,
                payout_date=date(2026, 8, 8),
                order_created_date=date(2026, 8, 1),
                sku="SKU-1",
                unit_price="10.00",
                quantity=1,
                line_subtotal="10.00",
            ),
            _item(
                order_id="SHP-PENDING",
                item_index=0,
                payout_date=None,
                order_created_date=date(2026, 8, 2),
                sku="SKU-2",
                unit_price="10.00",
                quantity=1,
                line_subtotal="10.00",
            ),
        ),
        order_created_items=(
            _item(
                order_id="SHP-CURRENT-PENDING",
                item_index=0,
                payout_date=None,
                order_created_date=date(2026, 8, 2),
                sku="SKU-CURRENT",
                unit_price="10.00",
                quantity=1,
                line_subtotal="8.00",
            ),
        ),
    )

    summary = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 8, 2),
        to_date=date(2026, 8, 2),
    )

    assert snapshot.available_dates("Shopee MY", CrossPlatformDateType.ORDER_CREATED) == (
        date(2026, 8, 2),
    )
    assert {item.order_id for item in summary.source_items} == {
        "SHP-CURRENT-PENDING",
    }


def test_order_created_summary_uses_persisted_current_price_and_source_subtotal():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (),
        order_created_items=(
            _item(
                order_id="SHP-CURRENT-USOLF", item_index=0, payout_date=None,
                order_created_date=date(2026, 8, 2), sku="SKU-USOLF",
                unit_price="7.75", quantity=2, line_subtotal="10.25",
            ),
        ),
    )

    summary = build_cross_platform_product_summary(
        snapshot, date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 8, 2), to_date=date(2026, 8, 2),
    )

    row = summary.product_rows[0]
    assert (row.unit_price, row.original_sales, row.amount, row.discount_amount) == (
        Decimal("7.75"), Decimal("15.50"), Decimal("10.25"), Decimal("5.25"),
    )


def test_order_created_current_source_excludes_cancelled_and_never_uses_canonical_fallback():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    tabs = {
        INVOICE_ORDERS_TAB: [
            list(INVOICE_ORDERS_HEADERS),
            _sheet_row(
                INVOICE_ORDERS_HEADERS,
                platform="Shopee",
                order_id="CANONICAL-ONLY",
                payout_completed_date="2026-08-08",
                first_imported_at="2026-08-08T10:00:00+00:00",
            ),
        ],
        INVOICE_ITEMS_TAB: [
            list(INVOICE_ITEMS_HEADERS),
            _sheet_row(
                INVOICE_ITEMS_HEADERS,
                platform="Shopee",
                order_id="CANONICAL-ONLY",
                item_index="0",
                seller_sku="CANONICAL",
                nav="NAV-C",
                product_name="Canonical only",
                quantity="1",
                unit_price="9.00",
                line_subtotal="9.00",
            ),
        ],
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: [
            list(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS),
            _sheet_row(
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
                market="shopee_my", order_id="CURRENT-SHIPPED", order_status="Shipped",
                order_created_date="2026-08-02", source_file="current.xlsx",
                snapshot_hash="b" * 64, last_synced_at="2026-10-20T00:00:00+00:00",
            ),
            _sheet_row(
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
                market="shopee_my", order_id="CURRENT-CANCELLED", order_status="Cancelled",
                order_created_date="2026-08-02", source_file="current.xlsx",
                snapshot_hash="c" * 64, last_synced_at="2026-10-20T00:00:00+00:00",
            ),
        ],
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: [
            list(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS),
            _sheet_row(
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
                market="shopee_my", order_id="CURRENT-SHIPPED", item_index="0",
                resolved_seller_sku="CURRENT", nav="NAV-1", product_name="Current Shipped",
                quantity="2", unit_price="10.00", product_subtotal="18.00",
            ),
            _sheet_row(
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
                market="shopee_my", order_id="CURRENT-CANCELLED", item_index="0",
                resolved_seller_sku="CANCELLED", nav="NAV-2", product_name="Cancelled",
                quantity="1", unit_price="8.00", product_subtotal="8.00",
            ),
        ],
    }

    snapshot = build_cross_platform_reporting_snapshot(tabs)
    created = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 8, 2),
        to_date=date(2026, 8, 2),
    )
    payout = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.PAYOUT,
        from_date=date(2026, 8, 8),
        to_date=date(2026, 8, 8),
    )

    assert [item.order_id for item in created.source_items] == ["CURRENT-SHIPPED"]
    assert created.product_rows[0].amount == Decimal("18.00")
    assert created.product_rows[0].discount_amount == Decimal("2.00")
    assert [item.order_id for item in payout.source_items] == ["CANONICAL-ONLY"]


def test_order_created_current_source_excludes_only_final_po_status_classes():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    snapshot = build_cross_platform_reporting_snapshot(
        _current_only_tabs(
            status_by_order={
                "TO-SHIP-TITLE": "To Ship",
                "TO-SHIP-SOURCE": "To ship",
                "CANCELLED": "Cancelled",
                "SHIPPING": "Shipping",
                "SHIPPED": "Shipped",
                "DELIVERED": "Delivered",
                "RECEIVED": "Order Received",
                "COMPLETED": "Completed",
                "RECEIVED-SEMICOLON": "Order Received; refund window until 2026-10-29",
                "RECEIVED-LIVE": (
                    "Order Received, but buyer can raise return/refund until 2026-10-29"
                ),
                "SINGLE-L-CANCEL": "Canceled",
                "FUTURE": "Some Future Shopee Status",
            }
        )
    )
    summary = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 10, 22),
        to_date=date(2026, 10, 22),
    )

    assert snapshot.available_dates("Shopee MY", CrossPlatformDateType.ORDER_CREATED) == (
        date(2026, 10, 22),
    )
    assert {item.order_id for item in summary.source_items} == {
        "SHIPPING",
        "SHIPPED",
        "DELIVERED",
        "RECEIVED",
        "COMPLETED",
        "RECEIVED-SEMICOLON",
        "RECEIVED-LIVE",
        "SINGLE-L-CANCEL",
        "FUTURE",
    }
    assert [(row.quantity, row.unit_price, row.original_sales, row.amount, row.discount_amount)
            for row in summary.product_rows] == [
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
        (1, Decimal("10.00"), Decimal("10.00"), Decimal("8.00"), Decimal("2.00")),
    ]


def test_order_received_annotation_remains_selectable_and_visible_by_created_date():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    snapshot = build_cross_platform_reporting_snapshot(
        _current_only_tabs(
            order_created_date="2026-10-13",
            status_by_order={
                "261013GMN0W5QP": (
                    "Order Received, but buyer can raise return/refund until 2026-10-29"
                )
            },
        )
    )
    options = snapshot.available_dates("Shopee MY", CrossPlatformDateType.ORDER_CREATED)
    summary = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 10, 13),
        to_date=date(2026, 10, 13),
    )

    assert options == (date(2026, 10, 13),)
    assert [item.order_id for item in summary.source_items] == ["261013GMN0W5QP"]
    assert summary.source_items[0].order_created_date == date(2026, 10, 13)


def test_dispatched_current_statuses_keep_each_order_created_date_selectable():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    october_21 = build_cross_platform_reporting_snapshot(
        _current_only_tabs(
            order_created_date="2026-10-21",
            status_by_order={
                "26102166D148YS": "Shipping",
                "2610216AUW12KS": "Delivered",
                "OCT21-TO-SHIP": "To ship",
                "OCT21-CANCELLED": "Cancelled",
            },
        )
    )
    october_22 = build_cross_platform_reporting_snapshot(
        _current_only_tabs(
            order_created_date="2026-10-22",
            status_by_order={
                "2610228PUG84DX": "Shipping",
                "OCT22-TO-SHIP": "To ship",
                "OCT22-CANCELLED": "Cancelled",
            },
        )
    )

    for snapshot, selected_date, expected_order_ids in (
        (october_21, date(2026, 10, 21), ["26102166D148YS", "2610216AUW12KS"]),
        (october_22, date(2026, 10, 22), ["2610228PUG84DX"]),
    ):
        assert snapshot.available_dates("Shopee MY", CrossPlatformDateType.ORDER_CREATED) == (
            selected_date,
        )
        summary = build_cross_platform_product_summary(
            snapshot,
            date_type=CrossPlatformDateType.ORDER_CREATED,
            from_date=selected_date,
            to_date=selected_date,
        )
        assert [item.order_id for item in summary.source_items] == expected_order_ids


def test_order_created_current_status_transition_keeps_original_created_date():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    pending_snapshot = build_cross_platform_reporting_snapshot(
        _current_only_tabs(status_by_order={"SAME-ORDER": "To Ship"})
    )
    shipped_snapshot = build_cross_platform_reporting_snapshot(
        _current_only_tabs(status_by_order={"SAME-ORDER": "Shipped"})
    )

    assert pending_snapshot.available_dates(
        "Shopee MY", CrossPlatformDateType.ORDER_CREATED
    ) == ()
    assert build_cross_platform_product_summary(
        pending_snapshot, date_type=CrossPlatformDateType.ORDER_CREATED
    ).source_items == ()

    shipped_summary = build_cross_platform_product_summary(
        shipped_snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 10, 22),
        to_date=date(2026, 10, 22),
    )
    assert [item.order_id for item in shipped_summary.source_items] == ["SAME-ORDER"]
    assert shipped_summary.source_items[0].order_created_date == date(2026, 10, 22)


def test_order_created_my_status_exclusions_do_not_change_sg_current_behavior():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    snapshot = build_cross_platform_reporting_snapshot(
        _current_only_tabs(
            market="shopee_sg",
            status_by_order={"SG-TO-SHIP": "To Ship", "SG-CANCELLED": "Cancelled"},
        )
    )
    summary = build_cross_platform_product_summary(
        snapshot,
        platform="Shopee SG",
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 10, 22),
        to_date=date(2026, 10, 22),
    )

    assert [item.order_id for item in summary.source_items] == ["SG-TO-SHIP"]


def test_cross_product_summary_excludes_cancelled_parent_by_status_or_layout():
    from src.invoice_app.services.cross_platform_product_summary import (
        build_cross_platform_reporting_snapshot,
    )

    order_ids = ("SHP-NORMAL", "SHP-CANCELLED-STATUS", "SHP-CANCELLED-LAYOUT")
    tabs = {
        INVOICE_ORDERS_TAB: [
            list(INVOICE_ORDERS_HEADERS),
            *[
                _sheet_row(
                    INVOICE_ORDERS_HEADERS,
                    platform="Shopee",
                    order_id=order_id,
                    order_status=("Cancelled" if order_id.endswith("STATUS") else "Completed"),
                    invoice_financial_layout=(
                        "CANCELLED_ORDER" if order_id.endswith("LAYOUT") else "NORMAL_ORDER"
                    ),
                    payout_completed_date="2026-08-08",
                    first_imported_at="2026-08-08T10:00:00+00:00",
                )
                for order_id in order_ids
            ],
        ],
        INVOICE_ITEMS_TAB: [
            list(INVOICE_ITEMS_HEADERS),
            *[
                _sheet_row(
                    INVOICE_ITEMS_HEADERS,
                    platform="Shopee",
                    order_id=order_id,
                    item_index="0",
                    seller_sku=f"SKU-{index}",
                    nav=f"NAV-{index}",
                    product_name=f"Product {index}",
                    quantity="1",
                    unit_price="10.00",
                    line_subtotal="10.00",
                )
                for index, order_id in enumerate(order_ids, start=1)
            ],
        ],
    }

    snapshot = build_cross_platform_reporting_snapshot(tabs)

    assert [(item.order_id, item.item_index) for item in snapshot.eligible_items] == [
        ("SHP-NORMAL", 0)
    ]


def _item(
    *,
    order_id,
    item_index,
    payout_date,
    order_created_date=None,
    sku,
    unit_price,
    quantity,
    line_subtotal,
    nav="300001",
    description="Persisted product",
    variation="500ml",
    promotion_group_id=None,
    source_group_total=None,
    platform="Shopee",
    currency="MYR",
):
    from src.invoice_app.services.cross_platform_product_summary import CommittedReportingItem

    return CommittedReportingItem(
        platform=platform,
        currency=currency,
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
        order_created_date=(
            payout_date if order_created_date is None else order_created_date
        ),
    )


def test_payout_date_options_are_platform_linked_and_ordinary_range_filters_before_aggregation():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(order_id="SHP-1", item_index=0, payout_date=date(2026, 8, 1), sku="SKU-1", unit_price="10.00", quantity=1, line_subtotal="9.00"),
            _item(order_id="SHP-2", item_index=0, payout_date=date(2026, 8, 3), sku="SKU-1", unit_price="10.00", quantity=2, line_subtotal="18.00"),
            _item(order_id="SHP-3", item_index=0, payout_date=date(2026, 8, 5), sku="SKU-1", unit_price="10.00", quantity=3, line_subtotal="27.00"),
        )
    )

    filtered = build_cross_platform_product_summary(
        snapshot,
        platform="Shopee MY",
        from_date=date(2026, 8, 3),
        to_date=date(2026, 8, 5),
    )

    assert snapshot.available_payout_dates("Shopee MY") == (
        date(2026, 8, 1),
        date(2026, 8, 3),
        date(2026, 8, 5),
    )
    assert [(row.quantity, row.amount) for row in filtered.product_rows] == [
        (5, Decimal("45.00"))
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

    summary = build_cross_platform_product_summary(snapshot, platform="Shopee MY")

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

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(order_id="SHP-1", item_index=0, payout_date=date(2026, 8, 8), sku="SKU-1", unit_price="10.00", quantity=1, line_subtotal="10.00"),
            _item(order_id="SHP-2", item_index=0, payout_date=date(2026, 8, 10), sku="SKU-2", unit_price="10.00", quantity=1, line_subtotal="10.00"),
        )
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="From Date must be"):
        build_cross_platform_product_summary(
            snapshot,
            from_date=date(2026, 8, 10),
            to_date=date(2026, 8, 8),
        )


def test_payout_filter_rejects_dates_without_committed_payout_evidence():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(order_id="SHP-1", item_index=0, payout_date=date(2026, 8, 3), sku="SKU-1", unit_price="10.00", quantity=1, line_subtotal="10.00"),
            _item(order_id="SHP-2", item_index=0, payout_date=date(2026, 8, 5), sku="SKU-2", unit_price="10.00", quantity=1, line_subtotal="10.00"),
        )
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="From Date must be a committed payout date"):
        build_cross_platform_product_summary(
            snapshot,
            from_date=date(2026, 8, 4),
            to_date=date(2026, 8, 5),
        )

    with pytest.raises(CrossPlatformProductSummaryError, match="must both be selected"):
        build_cross_platform_product_summary(
            snapshot,
            from_date=date(2026, 8, 3),
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
        CrossPlatformReportingSnapshot(source_items), platform="Shopee MY"
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


def test_shopee_sg_persisted_rows_use_their_own_market_identity_at_cross_service_boundary():
    from src.invoice_app.services.cross_platform_product_summary import (
        CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS,
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    tabs = {
        INVOICE_ORDERS_TAB: [
            list(INVOICE_ORDERS_HEADERS),
            _sheet_row(
                INVOICE_ORDERS_HEADERS,
                platform="Shopee SG",
                order_id="SG-PAYOUT",
                payout_completed_date="2026-08-08",
                first_imported_at="2026-08-08T10:00:00+00:00",
            ),
        ],
        INVOICE_ITEMS_TAB: [
            list(INVOICE_ITEMS_HEADERS),
            _sheet_row(
                INVOICE_ITEMS_HEADERS,
                platform="Shopee SG",
                order_id="SG-PAYOUT",
                item_index="0",
                seller_sku="SG-SKU",
                nav="SG-NAV",
                product_name="SG-only item",
                quantity="1",
                unit_price="10.00",
                line_subtotal="10.00",
            ),
        ],
    }

    snapshot = build_cross_platform_reporting_snapshot(tabs)

    assert CROSS_PLATFORM_INCLUDED_PERSISTED_PLATFORMS == frozenset({"Shopee", "Shopee SG"})
    assert [(item.platform, item.currency, item.order_id) for item in snapshot.eligible_items] == [
        ("Shopee SG", "SGD", "SG-PAYOUT")
    ]
    summary = build_cross_platform_product_summary(
        snapshot,
        platform="Shopee SG",
        date_type=CrossPlatformDateType.PAYOUT,
        from_date=date(2026, 8, 8),
        to_date=date(2026, 8, 8),
    )
    assert [item.order_id for item in summary.source_items] == ["SG-PAYOUT"]
    assert summary.currency_code == "SGD"
