from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from src.invoice_app.parsers.shopee_full_order_parser import parse_shopee_full_order
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
)


def _workbook_bytes(rows, *, malformed_dimension=False):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "orders"
    worksheet.append(HEADERS)
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


def _master(*, nav="5001"):
    return ProductPriceMaster.from_rows(
        [
            {
                "seller_sku": "SKU-1",
                "parent_sku": "PARENT-1",
                "product_name": "Organic Item",
                "variation_name": "Red",
                "unit_selling_price": "6.50",
                "nav_code": nav,
                "product_id": "PID-1",
            },
            {
                "seller_sku": "SKU-2",
                "parent_sku": "PARENT-2",
                "product_name": "Second Item",
                "variation_name": "",
                "unit_selling_price": "4.00",
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
    parsed = parse_shopee_full_order(
        _workbook_bytes([row]),
        source_filename="sg-full-order.xlsx",
        market=SHOPEE_SG,
    )

    batch = adapt_shopee_full_order(
        parsed,
        product_master=_master(),
        now=lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
    )

    assert batch.syncable is True
    assert batch.items[0].resolved_seller_sku == "SKU-1"
    assert batch.items[0].nav == "5001"
    assert batch.items[0].unit_price == Decimal("6.50")


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
                "nav_code": "NAV-CHILD",
                "product_id": "PID-CHILD",
            },
            {
                "seller_sku": "PARENT-PRODUCT",
                "parent_sku": "",
                "product_name": "Parent Product",
                "variation_name": "",
                "unit_selling_price": "23.90",
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
            update = request["updateCells"]
            title = title_by_id[update["range"]["sheetId"]]
            values = [
                [cell["userEnteredValue"]["stringValue"] for cell in row["values"]]
                for row in update["rows"]
            ]
            self.tabs[title] = [self.tabs[title][0]] + [
                row for row in values if any(row)
            ]


def _batch(rows=None, *, now=None):
    parsed = parse_shopee_full_order(
        _workbook_bytes(rows or _rows(), malformed_dimension=True),
        source_filename="full-order.xlsx",
        market=SHOPEE_MY,
    )
    return adapt_shopee_full_order(
        parsed,
        product_master=_master(),
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
        "unit_price", "product_subtotal", "return_refund_status",
    )


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


def test_full_order_statement_proof_blocks_return_refund_marker_without_nonzero_statement_refund():
    marked = list(_rows()[0])
    marked[2] = "Return / Refund"
    current = _batch([tuple(marked)])

    proof = prove_full_order_current_against_statement(
        _statement("10.25"),
        FullOrderCurrentSnapshot(current.orders, current.items),
        product_master=_master(),
    )

    assert proof.ready is False
    assert any("return/refund marker" in blocker for blocker in proof.blockers)


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


def test_full_order_statement_proof_blocks_marked_refund_with_ambiguous_item_allocation():
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

    assert proof.ready is False
    assert any("deterministically ITEM-resolved" in blocker for blocker in proof.blockers)


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
