from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
    map_accepted_shopee_invoice,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    ImportStatus,
    InMemoryHistoricalInvoiceRepository,
    source_fact_fingerprint,
)


def _bundle(*, imported_at: datetime | None = None) -> InvoiceBundle:
    order = CanonicalInvoiceOrder(
        platform="Shopee", order_id="000123456789", order_created_date=date(2026, 8, 7),
        income_type="Final", order_income=Decimal("352.79"), refund_amount=Decimal("-27.67"),
        payment_status="Released", source_pdf="upload-a.pdf", source_hash="bytes-a",
        first_imported_at=imported_at or datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    item = CanonicalInvoiceItem(
        platform="Shopee", order_id="000123456789", item_index=0, seller_sku="000SKU-01",
        nav="NAV-01", product_name="Tea", variation="Large", quantity=2, unit_price=Decimal("190.23"),
        actual_selling_unit_price=Decimal("190.23"), line_subtotal=Decimal("380.46"), source_pdf="upload-a.pdf", source_hash="bytes-a",
    )
    return InvoiceBundle(order=order, items=(item,))


def test_canonical_models_preserve_text_ids_and_decimal_money():
    bundle = _bundle()
    assert isinstance(bundle.order.order_id, str) and bundle.order.order_id == "000123456789"
    assert isinstance(bundle.items[0].seller_sku, str) and bundle.items[0].seller_sku == "000SKU-01"
    assert isinstance(bundle.order.refund_amount, Decimal)
    assert isinstance(bundle.items[0].line_subtotal, Decimal)


def test_none_and_explicit_decimal_zero_have_distinct_fingerprints():
    missing = replace(_bundle(), order=replace(_bundle().order, refund_amount=None))
    explicit_zero = replace(_bundle(), order=replace(_bundle().order, refund_amount=Decimal("0.00")))
    assert source_fact_fingerprint(missing) != source_fact_fingerprint(explicit_zero)


def test_fingerprint_ignores_filename_source_hash_and_import_timestamp_only():
    original = _bundle()
    changed_meta = replace(
        original,
        order=replace(original.order, source_pdf="C:/temp/renamed.pdf", source_hash="different-bytes", payment_status="Pending", first_imported_at=datetime(2026, 8, 9, tzinfo=timezone.utc)),
        items=(replace(original.items[0], source_pdf="C:/temp/renamed.pdf", source_hash="different-bytes", nav="NAV-CHANGED", unit_price=Decimal("999")),),
    )
    assert source_fact_fingerprint(original) == source_fact_fingerprint(changed_meta)


def test_material_refund_income_quantity_and_sku_changes_change_fingerprint():
    original = _bundle()
    variants = (
        replace(original, order=replace(original.order, refund_amount=Decimal("-20.00"))),
        replace(original, order=replace(original.order, order_income=Decimal("350.00"))),
        replace(original, items=(replace(original.items[0], quantity=3),)),
        replace(original, items=(replace(original.items[0], seller_sku="SKU-DIFFERENT"),)),
    )
    assert all(source_fact_fingerprint(original) != source_fact_fingerprint(variant) for variant in variants)


def test_repository_import_statuses_no_duplicate_and_no_conflict_overwrite():
    repository = InMemoryHistoricalInvoiceRepository()
    original = _bundle()
    changed = replace(original, order=replace(original.order, refund_amount=Decimal("-20.00")))
    assert repository.import_invoice(original).status is ImportStatus.NEW
    assert repository.import_invoice(original).status is ImportStatus.ALREADY_IMPORTED
    assert repository.import_invoice(changed).status is ImportStatus.SOURCE_CONFLICT
    assert repository.get_order("Shopee", "000123456789").refund_amount == Decimal("-27.67")
    assert len(repository.get_items_by_order_ids("Shopee", ["000123456789"])["000123456789"]) == 1


def test_repository_batch_lookups_and_logical_bundle_retention():
    repository = InMemoryHistoricalInvoiceRepository()
    first = _bundle()
    second = replace(first, order=replace(first.order, order_id="000000000002"), items=(replace(first.items[0], order_id="000000000002"),))
    repository.import_invoice(first)
    repository.import_invoice(second)
    assert set(repository.get_orders_by_ids("Shopee", ["000000000002", "missing", "000123456789"])) == {"000123456789", "000000000002"}
    assert set(repository.get_items_by_order_ids("Shopee", ["000123456789", "000000000002"])) == {"000123456789", "000000000002"}
    assert [order.order_id for order in repository.list_orders(platform="Shopee")] == ["000000000002", "000123456789"]


def test_in_memory_bulk_mixed_classification_is_all_new_or_zero_write():
    repository = InMemoryHistoricalInvoiceRepository()
    existing = _bundle()
    new_bundle = replace(
        existing,
        order=replace(existing.order, order_id="NEW"),
        items=(replace(existing.items[0], order_id="NEW"),),
    )
    repository.import_invoice(existing)

    result = repository.import_invoices((existing, new_bundle))

    assert [item.status for item in result.results] == [
        ImportStatus.ALREADY_IMPORTED,
        ImportStatus.NEW,
    ]
    assert result.chunk_sizes == ()
    assert repository.get_order("Shopee", "NEW") is None


def test_mapper_explicitly_converts_accepted_shopee_rows_without_parser_changes():
    bundle = map_accepted_shopee_invoice(
        {"platform": "Shopee", "order_id": "000123456789", "order_created_date": "07/08/2026", "income_type": "Final", "order_income": "352.79", "refund_amount": "-27.67", "payment_status": "Released", "source_pdf": "original.pdf", "status": "Accepted"},
        [{"platform": "Shopee", "order_id": "000123456789", "seller_sku": "000SKU-01", "product_name": "Tea", "variation_name": "Large", "quantity": "2", "unit_price": "190.23", "line_subtotal": "380.46", "status": "Accepted"}],
        source_hash="content-sha256", enriched_items=[{"unit_price": "12.50", "nav": "NAV-01"}],
    )
    assert bundle.order.order_created_date == date(2026, 8, 7)
    assert bundle.order.refund_amount == Decimal("-27.67")
    assert bundle.items[0].actual_selling_unit_price == Decimal("190.23")
    assert bundle.items[0].unit_price == Decimal("12.50")
    assert bundle.items[0].nav == "NAV-01"


def test_mapper_accepts_authoritative_shopee_date_with_minutes_as_a_canonical_date():
    bundle = map_accepted_shopee_invoice(
        {"platform": "Shopee", "order_id": "DATE-1", "order_created_date": "19/08/2026 14:32", "status": "Accepted"},
        [{"platform": "Shopee", "order_id": "DATE-1", "status": "Accepted", "quantity": 1}],
        source_hash="content-sha256", enriched_items=[{"unit_price": "1.00", "nav": "NAV"}],
    )
    assert bundle.order.order_created_date == date(2026, 8, 19)


def test_domain_and_repository_modules_have_no_streamlit_or_google_api_dependency():
    root = Path(__file__).parents[1] / "src" / "invoice_app"
    contents = "\n".join((root / path).read_text(encoding="utf-8").casefold() for path in ("domain/historical_invoice.py", "repositories/historical_invoice_repository.py"))
    assert "streamlit" not in contents
    assert "gspread" not in contents
    assert "google." not in contents


def test_mapper_preserves_explicit_zero_money_facts_without_truthiness_fallback():
    bundle = map_accepted_shopee_invoice(
        {"platform": "Shopee", "order_id": "ORDER-0", "status": "Accepted"},
        [{
            "platform": "Shopee", "order_id": "ORDER-0", "status": "Accepted",
            "quantity": "1", "source_line_subtotal": Decimal("0.00"), "unit_price": Decimal("0.00"),
            "line_subtotal": Decimal("99.99"),
            "source_group_total": Decimal("0.00"), "promotion_group_total": Decimal("77.77"),
        }],
        source_hash="content-sha256", enriched_items=[{"unit_price": "5.00", "nav": "NAV"}],
    )

    item = bundle.items[0]
    assert item.line_subtotal == Decimal("0.00")
    assert item.actual_selling_unit_price == Decimal("0.00")
    assert item.source_group_total == Decimal("0.00")


def test_mapper_keeps_actual_selling_unit_price_missing_when_no_explicit_evidence_exists():
    bundle = map_accepted_shopee_invoice(
        {"platform": "Shopee", "order_id": "ORDER-NONE", "status": "Accepted"},
        [{
            "platform": "Shopee", "order_id": "ORDER-NONE", "status": "Accepted",
            "quantity": "1", "source_line_subtotal": Decimal("12.34"),
        }],
        source_hash="content-sha256", enriched_items=[{"unit_price": "5.00", "nav": "NAV"}],
    )

    assert bundle.items[0].line_subtotal == Decimal("12.34")
    assert bundle.items[0].actual_selling_unit_price is None


def test_empty_invoice_bundle_is_rejected():
    try:
        InvoiceBundle(order=_bundle().order, items=())
    except ValueError as error:
        assert "at least one item" in str(error)
    else:
        raise AssertionError("An empty InvoiceBundle must be rejected.")
