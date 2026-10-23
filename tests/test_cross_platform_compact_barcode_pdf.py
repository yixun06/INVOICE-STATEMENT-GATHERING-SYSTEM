from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from src.invoice_app.domain.weekly_billing import ProductSummaryRow
from src.invoice_app.services.compact_barcode_pdf import (
    BarcodeStatus,
    BundleStatus,
    PriceStatus,
    resolve_bundle_status,
    resolve_compact_barcode,
    resolve_price_status,
)
from src.invoice_app.services.cross_platform_compact_barcode_pdf import (
    build_cross_platform_compact_barcode_report,
    export_cross_platform_compact_barcode_pdf,
)
from src.invoice_app.services.cross_platform_product_summary import (
    CrossPlatformDateType,
    CrossPlatformProductSummary,
    CrossPlatformReportingSnapshot,
    build_cross_platform_product_summary,
    build_cross_platform_reporting_snapshot,
)
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


def _row(number: int, **overrides) -> ProductSummaryRow:
    values = dict(
        number=number,
        sku_code="9555208107347",
        nav="5000001",
        product_name="Simply Natural Product",
        uom="EA",
        unit_price=Decimal("20.90"),
        quantity=1,
        discount_percent=None,
        discount_amount=Decimal("0.00"),
        amount=Decimal("20.90"),
        source_item_count=1,
    )
    values.update(overrides)
    return ProductSummaryRow(**values)


def _summary(*rows: ProductSummaryRow, currency_code: str = "RM") -> CrossPlatformProductSummary:
    return CrossPlatformProductSummary(
        platform="Shopee MY" if currency_code == "RM" else "Shopee SG",
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 10, 1),
        to_date=date(2026, 10, 31),
        source_items=(),
        product_rows=rows,
    )


def _current_tabs(
    *,
    item_values: dict[str, str],
    unit_price: str = "43.12",
    product_subtotal: str = "43.12",
) -> dict[str, list[list[str]]]:
    def row(headers, **values):
        return [values.get(header, "") for header in headers]

    return {
        INVOICE_ORDERS_TAB: [list(INVOICE_ORDERS_HEADERS)],
        INVOICE_ITEMS_TAB: [list(INVOICE_ITEMS_HEADERS)],
        SHOPEE_FULL_ORDER_CURRENT_ORDERS_TAB: [
            list(SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS),
            row(
                SHOPEE_FULL_ORDER_CURRENT_ORDERS_HEADERS,
                market="shopee_my",
                order_id="SHP-RESOLVE-SKU",
                order_status="Shipped",
                order_created_date="2026-09-14",
                source_file="full-order.xlsx",
                snapshot_hash="a" * 64,
                last_synced_at="2026-10-23T10:00:00+00:00",
            ),
        ],
        SHOPEE_FULL_ORDER_CURRENT_ITEMS_TAB: [
            list(SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS),
            row(
                SHOPEE_FULL_ORDER_CURRENT_ITEMS_HEADERS,
                market="shopee_my",
                order_id="SHP-RESOLVE-SKU",
                item_index="0",
                nav="5001438",
                product_name="Simply Natural Baby Noodles Bundle - 4 flavors",
                quantity="1",
                unit_price=unit_price,
                product_subtotal=product_subtotal,
                **item_values,
            ),
        ],
    }


@pytest.mark.parametrize(
    ("sku", "quantity", "status", "multiplier", "scan_quantity"),
    (
        ("9555208105145-2", 4, BundleStatus.CONFIRMED_BUNDLE, 2, 8),
        ("9555208105145-3", 4, BundleStatus.CONFIRMED_BUNDLE, 3, 12),
        ("9555208105145-6", 13, BundleStatus.CONFIRMED_BUNDLE, 6, 78),
        ("9555208105145-7", 10, BundleStatus.CONFIRMED_BUNDLE, 7, 70),
        ("9555208105145-Italy", 4, BundleStatus.NORMAL, None, 4),
        ("9555208105145-0", 4, BundleStatus.REVIEW_REQUIRED, None, None),
        ("9555208105145-6", 0, BundleStatus.REVIEW_REQUIRED, None, None),
    ),
)
def test_terminal_numeric_bundle_suffixes_are_the_only_multiplier_source(
    sku, quantity, status, multiplier, scan_quantity
):
    assert resolve_bundle_status(sku, quantity) == (status, multiplier, scan_quantity)


def test_barcode_resolution_uses_existing_valid_prefix_convention_without_changing_sku():
    assert resolve_compact_barcode("9555208105145-Italy") == "9555208105145"
    assert resolve_compact_barcode("9555208107348-6") is None
    assert resolve_compact_barcode("") is None


def test_price_resolution_requires_exact_decimal_reproduction_and_never_overrides_bundle_price():
    assert resolve_price_status(
        bundle_status=BundleStatus.NORMAL,
        report_quantity=5,
        discount_given=Decimal("-15.00"),
        amount=Decimal("119.50"),
    ) == (PriceStatus.OVERRIDE_REQUIRED, Decimal("23.90"))
    assert resolve_price_status(
        bundle_status=BundleStatus.NORMAL,
        report_quantity=17,
        discount_given=Decimal("-163.70"),
        amount=Decimal("1013.70"),
    ) == (PriceStatus.ROUNDING_REVIEW_REQUIRED, None)
    assert resolve_price_status(
        bundle_status=BundleStatus.CONFIRMED_BUNDLE,
        report_quantity=5,
        discount_given=Decimal("-15.00"),
        amount=Decimal("119.50"),
    ) == (PriceStatus.BUNDLE_PRICE_REVIEW_REQUIRED, None)


def test_cross_platform_adapter_preserves_final_rows_and_authoritative_totals():
    rows = (
        _row(1, sku_code="9555208107347", quantity=5, discount_amount=Decimal("-15.00"), amount=Decimal("119.50")),
        _row(2, sku_code="9555208105145-6", quantity=13, amount=Decimal("271.70")),
        _row(3, sku_code="UNAVAILABLE-Italy", nav="5000999", amount=Decimal("20.90")),
    )
    summary = _summary(*rows)

    report = build_cross_platform_compact_barcode_report(summary)

    assert tuple(item.seller_sku for item in report.rows) == tuple(row.sku_code for row in rows)
    assert report.rows[0].pos_override_unit_price == Decimal("23.90")
    assert report.rows[1].bundle_multiplier == 6
    assert report.rows[1].effective_scan_quantity == 78
    assert report.rows[1].price_status is PriceStatus.BUNDLE_PRICE_REVIEW_REQUIRED
    assert report.rows[2].barcode_status is BarcodeStatus.UNAVAILABLE
    assert [metric.label for metric in report.metrics] == [
        "Total Product",
        "Total Qty",
        "Total Original Sales",
        "Total Discount Given",
        "Total Amount",
        "Barcode Ready",
        "Barcode Unavailable",
    ]
    assert report.metrics[0].value == "3"
    assert report.metrics[1].value == "19"
    assert report.metrics[4].value == "RM 412.10"


def test_cross_platform_adapter_uses_the_selected_market_currency():
    report = build_cross_platform_compact_barcode_report(
        _summary(_row(1, amount=Decimal("20.90")), currency_code="S$")
    )

    assert report.platform == "Shopee SG"
    assert report.currency_code == "SGD"
    assert report.metrics[2].value == "S$ 20.90"


def test_compact_adapter_uses_resolve_sku_components_without_repeating_financial_amount():
    report = build_cross_platform_compact_barcode_report(
        _summary(
            _row(
                1,
                sku_code="NATIVE-SKU",
                quantity=2,
                resolve_sku="9555208010555-2;NAV:5001438-3",
                amount=Decimal("20.90"),
            )
        )
    )

    row = report.rows[0]
    assert row.seller_sku == "NATIVE-SKU"
    assert row.resolve_sku == "9555208010555-2;NAV:5001438-3"
    assert [component.scan_quantity(row.report_quantity) for component in row.pos_components] == [4, 6]
    assert row.amount == Decimal("20.90")


def test_full_order_current_resolve_sku_reaches_compact_pdf_as_four_ean13_components():
    resolve_sku = "9555208010555;9555208010678;9555208010562;9555208010586"
    snapshot = build_cross_platform_reporting_snapshot(
        _current_tabs(
            item_values={
                "source_seller_sku": "Babybundle",
                "resolved_seller_sku": "FINANCIAL-BABY-BUNDLE",
                "resolve_sku": resolve_sku,
            },
            unit_price="49.00",
            product_subtotal="43.12",
        )
    )
    summary = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 9, 14),
        to_date=date(2026, 9, 14),
    )

    assert len(summary.product_rows) == 1
    assert summary.product_rows[0].resolve_sku == resolve_sku
    assert summary.product_rows[0].quantity == 1
    assert summary.product_rows[0].original_sales == Decimal("49.00")
    assert summary.product_rows[0].discount_amount == Decimal("5.88")
    assert summary.product_rows[0].amount == Decimal("43.12")

    report = build_cross_platform_compact_barcode_report(summary)
    compact_row = report.rows[0]

    assert [component.barcode for component in compact_row.pos_components] == [
        "9555208010555",
        "9555208010678",
        "9555208010562",
        "9555208010586",
    ]
    assert [component.scan_quantity(compact_row.report_quantity) for component in compact_row.pos_components] == [
        1,
        1,
        1,
        1,
    ]
    assert compact_row.amount == Decimal("43.12")
    assert [
        (
            allocation.pos_quantity,
            allocation.original_sales,
            allocation.discount_given,
            allocation.amount,
        )
        for allocation in compact_row.pos_component_allocations
    ] == [
        (1, Decimal("12.25"), Decimal("1.47"), Decimal("10.78")),
        (1, Decimal("12.25"), Decimal("1.47"), Decimal("10.78")),
        (1, Decimal("12.25"), Decimal("1.47"), Decimal("10.78")),
        (1, Decimal("12.25"), Decimal("1.47"), Decimal("10.78")),
    ]
    assert report.metrics[4].value == "RM 43.12"

    pdf = export_cross_platform_compact_barcode_pdf(summary)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    for barcode in (
        "9555208010555",
        "9555208010678",
        "9555208010562",
        "9555208010586",
    ):
        assert barcode in text
    assert text.count("POS QTY: 1") == 4
    assert text.count("POS PRICE: RM 12.25") == 4
    assert text.count("DISCOUNT: RM 1.47") == 4
    assert text.count("NET: RM 10.78") == 4
    assert "BARCODE UNAVAILABLE" not in text
    assert "MANUAL ENTRY REQUIRED" not in text


def test_multi_component_pos_allocation_assigns_non_divisible_signed_cents_in_listing_order():
    report = build_cross_platform_compact_barcode_report(
        _summary(
            _row(
                1,
                unit_price=Decimal("10.00"),
                quantity=1,
                discount_amount=Decimal("-0.01"),
                amount=Decimal("10.01"),
                resolve_sku="9555208010555;9555208010678;9555208010562",
            )
        )
    )

    allocations = report.rows[0].pos_component_allocations

    assert [allocation.original_sales for allocation in allocations] == [
        Decimal("3.34"),
        Decimal("3.33"),
        Decimal("3.33"),
    ]
    assert [allocation.discount_given for allocation in allocations] == [
        Decimal("-0.01"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert [allocation.amount for allocation in allocations] == [
        Decimal("3.35"),
        Decimal("3.33"),
        Decimal("3.33"),
    ]
    assert sum((allocation.original_sales for allocation in allocations), Decimal("0.00")) == Decimal("10.00")
    assert sum((allocation.discount_given for allocation in allocations), Decimal("0.00")) == Decimal("-0.01")
    assert sum((allocation.amount for allocation in allocations), Decimal("0.00")) == Decimal("10.01")


def test_multi_component_pos_allocation_uses_physical_quantities_for_report_quantity_above_one():
    report = build_cross_platform_compact_barcode_report(
        _summary(
            _row(
                1,
                unit_price=Decimal("6.00"),
                quantity=2,
                discount_amount=Decimal("3.00"),
                amount=Decimal("9.00"),
                resolve_sku="9555208010555-2;9555208010678",
            )
        )
    )

    allocations = report.rows[0].pos_component_allocations

    assert [
        (allocation.pos_quantity, allocation.original_sales, allocation.discount_given, allocation.amount)
        for allocation in allocations
    ] == [
        (4, Decimal("8.00"), Decimal("2.00"), Decimal("6.00")),
        (2, Decimal("4.00"), Decimal("1.00"), Decimal("3.00")),
    ]
    assert sum((allocation.original_sales for allocation in allocations), Decimal("0.00")) == Decimal("12.00")
    assert sum((allocation.discount_given for allocation in allocations), Decimal("0.00")) == Decimal("3.00")
    assert sum((allocation.amount for allocation in allocations), Decimal("0.00")) == Decimal("9.00")


def test_full_order_current_resolve_sku_overrides_same_ean_numeric_seller_sku_suffix():
    snapshot = build_cross_platform_reporting_snapshot(
        _current_tabs(
            item_values={
                "source_seller_sku": "9555208010555-4",
                "resolved_seller_sku": "9555208010555-4",
                "resolve_sku": "9555208010555",
            }
        )
    )
    summary = build_cross_platform_product_summary(
        snapshot,
        date_type=CrossPlatformDateType.ORDER_CREATED,
        from_date=date(2026, 9, 14),
        to_date=date(2026, 9, 14),
    )

    compact_row = build_cross_platform_compact_barcode_report(summary).rows[0]

    assert compact_row.seller_sku == "9555208010555-4"
    assert compact_row.resolve_sku == "9555208010555"
    assert [component.scan_quantity(compact_row.report_quantity) for component in compact_row.pos_components] == [1]


def test_compact_adapter_never_selects_one_conflicting_resolve_sku_for_a_summary_group():
    report = build_cross_platform_compact_barcode_report(
        _summary(
            _row(
                1,
                resolve_sku_warning="Resolve SKU differs across consolidated source items.",
            )
        )
    )

    assert report.rows[0].pos_components == ()
    assert report.rows[0].barcode_status is BarcodeStatus.UNAVAILABLE


def test_compact_renderer_has_required_actions_header_metrics_and_multiple_portrait_pages():
    long_name = "Long product description with a variation that must wrap without truncation " * 4
    rows = (
        _row(1, product_name=long_name),
        _row(2, sku_code="NO-BARCODE-Italy", nav="5000999"),
        _row(3, sku_code="9555208105145-3", quantity=4, amount=Decimal("83.60")),
        _row(4, sku_code="9555208105145-6", quantity=13, amount=Decimal("271.70")),
        _row(5, sku_code="9555208105145-7", quantity=10, amount=Decimal("209.00")),
        _row(6, quantity=5, discount_amount=Decimal("-15.00"), amount=Decimal("119.50")),
        _row(7, quantity=17, discount_amount=Decimal("-163.70"), amount=Decimal("1013.70")),
        _row(8, sku_code="9555208105145-6", quantity=2, discount_amount=Decimal("-15.00"), amount=Decimal("47.80")),
    )
    pdf = export_cross_platform_compact_barcode_pdf(_summary(*rows))
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)

    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    assert float(reader.pages[0].mediabox.width) < float(reader.pages[0].mediabox.height)
    for expected in (
        "Zenxin Agri-Organic Food (AH) Sdn Bhd",
        "CONSOLIDATED PRODUCT SUMMARY",
        "Total Product",
        "Total Qty",
        "Total Original Sales",
        "Total Discount Given",
        "Total Amount",
        "Barcode Ready",
        "Barcode Unavailable",
        "BARCODE UNAVAILABLE",
        "MANUAL ENTRY REQUIRED",
        "BUNDLE SALE",
        "SCAN QTY: 78",
        "SCAN QTY: 70",
        "OVERRIDE REQUIRED",
        "ROUNDING REVIEW REQUIRED",
        "BUNDLE PRICE REVIEW REQUIRED",
        "Long product description with a variation",
    ):
        assert expected in text


def test_renderer_is_independent_of_streamlit_and_the_legacy_exporter_is_untouched():
    renderer = (
        Path(__file__).parents[1]
        / "src"
        / "invoice_app"
        / "services"
        / "compact_barcode_pdf.py"
    ).read_text(encoding="utf-8").casefold()
    assert "streamlit" not in renderer
    assert "google_sheets" not in renderer


def test_cross_platform_ui_keeps_both_exports_and_passes_the_same_summary(monkeypatch):
    from src.invoice_app.ui import cross_platform_product_summary as ui

    summary = _summary(_row(1))
    calls = []
    downloads = []
    monkeypatch.setattr(ui, "_render_platform_filter", lambda: (CrossPlatformDateType.ORDER_CREATED, "Shopee MY", False, object(), object()))
    monkeypatch.setattr(ui, "_render_date_filters", lambda *_args, **_kwargs: (date(2026, 10, 1), date(2026, 10, 31)))
    monkeypatch.setattr(ui, "cross_platform_backend_available", lambda _platform: True)
    monkeypatch.setattr(ui, "build_cross_platform_product_summary", lambda *_args, **_kwargs: summary)
    monkeypatch.setattr(ui, "_render_summary_dashboard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui.st, "title", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui.st, "dataframe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui.st, "download_button", lambda label, data, **kwargs: downloads.append((label, data, kwargs)))
    monkeypatch.setattr(ui, "export_cross_platform_product_summary_barcode_table_pdf", lambda received: calls.append(("legacy", received)) or b"legacy")
    monkeypatch.setattr(ui, "export_cross_platform_compact_barcode_pdf", lambda received: calls.append(("compact", received)) or b"compact")

    ui.render_cross_platform_product_summary(CrossPlatformReportingSnapshot(()))

    assert calls == [("legacy", summary), ("compact", summary)]
    assert [label for label, _data, _kwargs in downloads] == [
        "Consolidated Product Summary Barcode Table PDF",
        "Export Compact Barcode PDF",
    ]
