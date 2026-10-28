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
    STATEMENT_DATA_HEADERS,
    STATEMENT_DATA_TAB,
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
            STATEMENT_DATA_TAB: [
                list(STATEMENT_DATA_HEADERS),
                _sheet_row(
                    STATEMENT_DATA_HEADERS,
                    record_type="ORDER",
                    platform="Shopee",
                    validation_status="PASSED",
                    commit_status="COMMITTED",
                    order_id="SHP-PAYOUT",
                    payout_completed_date="2026-08-09",
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
                STATEMENT_DATA_TAB,
            ),
        )
    ]
    assert snapshot.available_payout_dates("Shopee MY") == (date(2026, 8, 9),)
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


@pytest.mark.parametrize(
    ("market", "platform", "currency"),
    (
        ("shopee_my", "Shopee MY", "MYR"),
        ("shopee_sg", "Shopee SG", "SGD"),
    ),
)
def test_order_created_postgres_reader_uses_only_its_market_full_order_snapshot(
    market, platform, currency
):
    """Order-Created reporting must not even touch legacy Invoice/Statement readers."""

    from datetime import datetime, timezone

    from src.invoice_app.repositories.shopee_full_order_current_repository import (
        FullOrderCurrentSnapshot,
    )
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        PostgresMyCrossPlatformProductSummaryReader,
        build_cross_platform_product_summary,
    )
    from src.invoice_app.services.market_context import resolve_market_context
    from src.invoice_app.services.shopee_full_order_adapter import (
        FullOrderCurrentItem,
        FullOrderCurrentOrder,
    )

    class ForbiddenLegacyGateway:
        requested_tabs = []

        def read_tabs(self, *_args, **_kwargs):
            raise AssertionError("Order-Created reporting must not read Invoice tabs.")

    class ForbiddenStatementRepository:
        def reporting_statement_data_rows(self):
            raise AssertionError("Order-Created reporting must not read Statement rows.")

    class FullOrderRepository:
        calls = 0

        def read_snapshot(self):
            self.calls += 1
            return FullOrderCurrentSnapshot(
                orders=(
                    FullOrderCurrentOrder(
                        market=market,
                        order_id="SAME-ID",
                        order_status="Completed",
                        order_created_date=date(2026, 9, 1),
                        source_file="current.xlsx",
                        snapshot_hash="a" * 64,
                        last_synced_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
                    ),
                ),
                items=(
                    FullOrderCurrentItem(
                        market=market,
                        order_id="SAME-ID",
                        item_index=0,
                        source_parent_sku="PARENT",
                        source_seller_sku="SOURCE-SKU",
                        resolved_seller_sku="RESOLVED-SKU",
                        nav="NAV-1",
                        product_name="Full Order Product",
                        variation="Variant",
                        quantity=2,
                        unit_price=Decimal("7.50"),
                        product_subtotal=Decimal("14.00"),
                        return_refund_status="",
                        resolve_sku="RESOLVE-SKU",
                    ),
                ),
            )

    gateway = ForbiddenLegacyGateway()
    full_order_repository = FullOrderRepository()
    reader = PostgresMyCrossPlatformProductSummaryReader(
        spreadsheet_id="legacy-source-is-for-payout-only",
        gateway=gateway,
        statement_repository=ForbiddenStatementRepository(),
        full_order_repository=full_order_repository,
        market_context=resolve_market_context(market),
    )

    snapshot = reader.load_order_created_snapshot()
    summary = build_cross_platform_product_summary(
        snapshot,
        platform=platform,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 1),
    )

    assert gateway.requested_tabs == []
    assert full_order_repository.calls == 1
    assert snapshot.eligible_items == ()
    assert [(item.platform, item.order_id, item.resolve_sku) for item in summary.source_items] == [
        ("Shopee" if market == "shopee_my" else "Shopee SG", "SAME-ID", "RESOLVE-SKU")
    ]
    assert [
        (row.sku_code, row.nav, row.quantity, row.unit_price, row.amount)
        for row in summary.product_rows
    ] == [("RESOLVED-SKU", "NAV-1", 2, Decimal("7.50"), Decimal("14.00"))]
    assert summary.currency_code == currency


def test_order_created_postgres_reader_fails_closed_on_cross_market_full_order_rows():
    from datetime import datetime, timezone

    from src.invoice_app.repositories.shopee_full_order_current_repository import (
        FullOrderCurrentSnapshot,
    )
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        PostgresMyCrossPlatformProductSummaryReader,
    )
    from src.invoice_app.services.shopee_full_order_adapter import FullOrderCurrentOrder

    class CrossMarketFullOrderRepository:
        def read_snapshot(self):
            return FullOrderCurrentSnapshot(
                orders=(
                    FullOrderCurrentOrder(
                        market="shopee_sg",
                        order_id="SAME-ID",
                        order_status="Completed",
                        order_created_date=date(2026, 9, 1),
                        source_file="current.xlsx",
                        snapshot_hash="a" * 64,
                        last_synced_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
                    ),
                ),
                items=(),
            )

    reader = PostgresMyCrossPlatformProductSummaryReader(
        spreadsheet_id="legacy-source-is-for-payout-only",
        statement_repository=object(),  # type: ignore[arg-type]
        full_order_repository=CrossMarketFullOrderRepository(),
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="market boundary"):
        reader.load_order_created_snapshot()


@pytest.mark.parametrize(
    ("market", "platform", "persisted_platform"),
    (
        ("shopee_my", "Shopee MY", "Shopee"),
        ("shopee_sg", "Shopee SG", "Shopee SG"),
    ),
)
def test_payout_postgres_reader_selects_statement_orders_then_reuses_full_order_summary(
    market, platform, persisted_platform
):
    """Payout mode changes only order selection; product facts stay Full Order."""

    from datetime import datetime, timezone

    from src.invoice_app.repositories.shopee_full_order_current_repository import (
        FullOrderCurrentSnapshot,
    )
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        PostgresMyCrossPlatformProductSummaryReader,
        build_cross_platform_product_summary,
    )
    from src.invoice_app.services.market_context import resolve_market_context
    from src.invoice_app.services.shopee_full_order_adapter import (
        FullOrderCurrentItem,
        FullOrderCurrentOrder,
    )

    class ForbiddenLegacyGateway:
        requested_tabs = []

        def read_tabs(self, *_args, **_kwargs):
            raise AssertionError("Payout Product Summary must not read Invoice tabs.")

    class StatementRepository:
        calls = 0

        def reporting_statement_data_rows(self):
            self.calls += 1
            return (
                tuple(
                    _sheet_row(
                        STATEMENT_DATA_HEADERS,
                        record_type="ORDER",
                        platform=persisted_platform,
                        validation_status="PASSED",
                        commit_status="COMMITTED",
                        order_id="SAME-ID",
                        payout_completed_date="2026-09-08",
                    )
                ),
                tuple(
                    _sheet_row(
                        STATEMENT_DATA_HEADERS,
                        record_type="SKU",
                        platform=persisted_platform,
                        validation_status="PASSED",
                        commit_status="COMMITTED",
                        order_id="SAME-ID",
                        payout_completed_date="2026-09-08",
                    )
                ),
            )

    class FullOrderRepository:
        calls = 0

        def read_snapshot(self):
            self.calls += 1
            return FullOrderCurrentSnapshot(
                orders=(
                    FullOrderCurrentOrder(
                        market=market,
                        order_id="SAME-ID",
                        order_status="Completed",
                        order_created_date=date(2026, 9, 1),
                        source_file="current.xlsx",
                        snapshot_hash="a" * 64,
                        last_synced_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
                    ),
                ),
                items=(
                    FullOrderCurrentItem(
                        market=market,
                        order_id="SAME-ID",
                        item_index=0,
                        source_parent_sku="PARENT",
                        source_seller_sku="SOURCE-SKU",
                        resolved_seller_sku="RESOLVED-SKU",
                        nav="NAV-1",
                        product_name="Full Order Product",
                        variation="Variant",
                        quantity=2,
                        unit_price=Decimal("7.50"),
                        product_subtotal=Decimal("14.00"),
                        return_refund_status="",
                        resolve_sku="RESOLVE-SKU",
                    ),
                ),
            )

    gateway = ForbiddenLegacyGateway()
    statement_repository = StatementRepository()
    full_order_repository = FullOrderRepository()
    reader = PostgresMyCrossPlatformProductSummaryReader(
        spreadsheet_id="legacy-source-is-not-a-product-summary-authority",
        gateway=gateway,
        statement_repository=statement_repository,
        full_order_repository=full_order_repository,
        market_context=resolve_market_context(market),
    )

    payout = build_cross_platform_product_summary(
        reader.load_payout_snapshot(),
        platform=platform,
        date_type=CrossPlatformDateType.PAYOUT,
        from_date=date(2026, 9, 8),
        to_date=date(2026, 9, 8),
    )
    created = build_cross_platform_product_summary(
        reader.load_order_created_snapshot(),
        platform=platform,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 1),
    )

    assert gateway.requested_tabs == []
    assert statement_repository.calls == 1
    assert full_order_repository.calls == 2
    assert [(item.order_id, item.payout_completed_date) for item in payout.source_items] == [
        ("SAME-ID", date(2026, 9, 8))
    ]
    assert payout.product_rows == created.product_rows
    assert payout.source_items[0].resolve_sku == "RESOLVE-SKU"


def test_payout_postgres_reader_fails_closed_for_missing_full_order_coverage():
    from src.invoice_app.repositories.shopee_full_order_current_repository import (
        FullOrderCurrentSnapshot,
    )
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        PostgresMyCrossPlatformProductSummaryReader,
    )

    class StatementRepository:
        def reporting_statement_data_rows(self):
            return (
                tuple(
                    _sheet_row(
                        STATEMENT_DATA_HEADERS,
                        record_type="ORDER",
                        platform="Shopee",
                        validation_status="PASSED",
                        commit_status="COMMITTED",
                        order_id="MISSING-FULL-ORDER",
                        payout_completed_date="2026-09-08",
                    )
                ),
            )

    class EmptyFullOrderRepository:
        def read_snapshot(self):
            return FullOrderCurrentSnapshot((), ())

    reader = PostgresMyCrossPlatformProductSummaryReader(
        spreadsheet_id="legacy-source-is-not-a-product-summary-authority",
        statement_repository=StatementRepository(),
        full_order_repository=EmptyFullOrderRepository(),
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="incomplete Full Order coverage"):
        reader.load_payout_snapshot()


def test_payout_postgres_reader_fails_closed_for_cross_market_statement_rows():
    from src.invoice_app.repositories.shopee_full_order_current_repository import (
        FullOrderCurrentSnapshot,
    )
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        PostgresMyCrossPlatformProductSummaryReader,
    )

    class CrossMarketStatementRepository:
        def reporting_statement_data_rows(self):
            return (
                tuple(
                    _sheet_row(
                        STATEMENT_DATA_HEADERS,
                        record_type="ORDER",
                        platform="Shopee SG",
                        validation_status="PASSED",
                        commit_status="COMMITTED",
                        order_id="SAME-ID",
                        payout_completed_date="2026-09-08",
                    )
                ),
            )

    class EmptyFullOrderRepository:
        def read_snapshot(self):
            return FullOrderCurrentSnapshot((), ())

    reader = PostgresMyCrossPlatformProductSummaryReader(
        spreadsheet_id="legacy-source-is-not-a-product-summary-authority",
        statement_repository=CrossMarketStatementRepository(),
        full_order_repository=EmptyFullOrderRepository(),
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="Statement read crossed"):
        reader.load_payout_snapshot()


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


def test_payout_date_uses_only_committed_statement_order_rows_and_never_invoice_fallback():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_reporting_snapshot,
    )

    tabs = _current_only_tabs(status_by_order={"STATEMENT-ORDER": "Completed"})
    tabs[INVOICE_ORDERS_TAB] = [
        list(INVOICE_ORDERS_HEADERS),
        _sheet_row(
            INVOICE_ORDERS_HEADERS,
            platform="Shopee",
            order_id="STATEMENT-ORDER",
            order_created_date="2026-08-01",
            payout_completed_date="2026-08-05",
            first_imported_at="2026-08-05T00:00:00+00:00",
        ),
        _sheet_row(
            INVOICE_ORDERS_HEADERS,
            platform="Shopee",
            order_id="NO-STATEMENT",
            order_created_date="2026-08-01",
            payout_completed_date="2026-08-06",
            first_imported_at="2026-08-06T00:00:00+00:00",
        ),
    ]
    tabs[INVOICE_ITEMS_TAB] = [
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
            for index, order_id in enumerate(("STATEMENT-ORDER", "NO-STATEMENT"), start=1)
        ],
    ]
    tabs[STATEMENT_DATA_TAB] = [
        list(STATEMENT_DATA_HEADERS),
        _sheet_row(
            STATEMENT_DATA_HEADERS,
            record_type="ORDER", platform="Shopee", order_id=" STATEMENT-ORDER ",
            validation_status="PASSED", commit_status="COMMITTED",
            payout_completed_date="2026-08-09",
        ),
        _sheet_row(
            STATEMENT_DATA_HEADERS,
            record_type="SKU", platform="Shopee", order_id="STATEMENT-ORDER",
            validation_status="PASSED", commit_status="COMMITTED",
            payout_completed_date="2026-08-10",
        ),
        _sheet_row(
            STATEMENT_DATA_HEADERS,
            record_type="ORDER", platform="Shopee", order_id="STATEMENT-ORDER",
            validation_status="PASSED", commit_status="COMMITTED",
            payout_completed_date="2026-08-09",
        ),
    ]

    snapshot = build_cross_platform_reporting_snapshot(tabs)

    assert [(item.order_id, item.order_created_date, item.payout_completed_date) for item in snapshot.eligible_items] == [
        ("STATEMENT-ORDER", date(2026, 10, 22), date(2026, 8, 9)),
        ("NO-STATEMENT", None, None),
    ]
    assert snapshot.available_dates("Shopee MY", CrossPlatformDateType.PAYOUT) == (
        date(2026, 8, 9),
    )


def test_conflicting_committed_statement_payout_dates_fail_closed():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        build_cross_platform_reporting_snapshot,
    )

    tabs = _current_only_tabs(status_by_order={"CONFLICT": "Completed"})
    tabs[STATEMENT_DATA_TAB] = [
        list(STATEMENT_DATA_HEADERS),
        *[
            _sheet_row(
                STATEMENT_DATA_HEADERS,
                record_type="ORDER", platform="Shopee", order_id="CONFLICT",
                validation_status="PASSED", commit_status="COMMITTED",
                payout_completed_date=payout_date,
            )
            for payout_date in ("2026-08-09", "2026-08-10")
        ],
    ]

    with pytest.raises(CrossPlatformProductSummaryError, match="conflicting dates"):
        build_cross_platform_reporting_snapshot(tabs)


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


def test_order_created_summary_consumes_persisted_sg_sku_identity():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    tabs = _current_only_tabs(
        status_by_order={"SG-CURRENT-1": "Completed"},
        order_created_date="2026-09-01",
        market="shopee_sg",
    )
    item = tabs[SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB][1]
    item[SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS.index("resolved_seller_sku")] = "CANONICAL-SG-1"
    item[SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS.index("nav")] = "2000842"
    item[SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS.index("unit_price")] = "9.90"
    item[SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS.index("product_subtotal")] = "19.80"
    item[SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS.index("quantity")] = "2"
    item[SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS.index("resolve_sku")] = "2000842"

    summary = build_cross_platform_product_summary(
        build_cross_platform_reporting_snapshot(tabs),
        platform="Shopee SG",
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 1),
    )

    assert len(summary.product_rows) == 1
    assert (summary.product_rows[0].nav, summary.product_rows[0].sku_code) == (
        "2000842", "CANONICAL-SG-1"
    )
    assert summary.source_items[0].resolve_sku == "2000842"


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


def test_order_created_current_source_uses_the_same_status_rule_for_my_and_sg():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    statuses = {
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
    expected_order_ids = {
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
    for market, platform in (("shopee_my", "Shopee MY"), ("shopee_sg", "Shopee SG")):
        snapshot = build_cross_platform_reporting_snapshot(
            _current_only_tabs(market=market, status_by_order=statuses)
        )
        summary = build_cross_platform_product_summary(
            snapshot,
            platform=platform,
            date_type=CrossPlatformDateType.ORDER_CREATED,
            from_date=date(2026, 10, 22),
            to_date=date(2026, 10, 22),
        )

        assert snapshot.available_dates(platform, CrossPlatformDateType.ORDER_CREATED) == (
            date(2026, 10, 22),
        )
        assert {item.order_id for item in summary.source_items} == expected_order_ids
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


def test_order_created_current_status_transition_keeps_original_created_date_for_my_and_sg():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformDateType,
        build_cross_platform_product_summary,
        build_cross_platform_reporting_snapshot,
    )

    for market, platform in (("shopee_my", "Shopee MY"), ("shopee_sg", "Shopee SG")):
        pending_snapshot = build_cross_platform_reporting_snapshot(
            _current_only_tabs(
                market=market, status_by_order={"SAME-ORDER": "To Ship"}
            )
        )
        shipped_snapshot = build_cross_platform_reporting_snapshot(
            _current_only_tabs(
                market=market, status_by_order={"SAME-ORDER": "Shipped"}
            )
        )

        assert pending_snapshot.available_dates(platform, CrossPlatformDateType.ORDER_CREATED) == ()
        assert build_cross_platform_product_summary(
            pending_snapshot,
            platform=platform,
            date_type=CrossPlatformDateType.ORDER_CREATED,
        ).source_items == ()

        shipped_summary = build_cross_platform_product_summary(
            shipped_snapshot,
            platform=platform,
            date_type=CrossPlatformDateType.ORDER_CREATED,
            from_date=date(2026, 10, 22),
            to_date=date(2026, 10, 22),
        )
        assert [item.order_id for item in shipped_summary.source_items] == ["SAME-ORDER"]
        assert shipped_summary.source_items[0].order_created_date == date(2026, 10, 22)


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
        CrossPlatformProductSummaryError,
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


def test_product_summary_requires_the_persisted_full_order_subtotal_without_allocation():
    from src.invoice_app.services.cross_platform_product_summary import (
        CrossPlatformProductSummaryError,
        CrossPlatformReportingSnapshot,
        build_cross_platform_product_summary,
    )

    snapshot = CrossPlatformReportingSnapshot(
        (
            _item(order_id="SHP-PROMO", item_index=0, payout_date=date(2026, 8, 8), sku="SKU-10", unit_price="10.00", quantity=1, line_subtotal=None, promotion_group_id="promo-1", source_group_total="20.00"),
            _item(order_id="SHP-PROMO", item_index=1, payout_date=date(2026, 8, 8), sku="SKU-20", unit_price="20.00", quantity=1, line_subtotal=None, promotion_group_id="promo-1", source_group_total="20.00"),
        )
    )

    with pytest.raises(CrossPlatformProductSummaryError, match="Full Order Current has no Product Subtotal"):
        build_cross_platform_product_summary(snapshot, platform="Shopee MY")


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
