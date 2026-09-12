from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from src.invoice_app.domain.historical_invoice import map_accepted_shopee_invoice
from src.invoice_app.pdf_document import read_pdf_document
from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_financial_parser import (
    NORMAL_ORDER,
    RETURN_REFUND,
    UNKNOWN_OR_MIXED,
    classify_invoice_financial_layout,
)
from src.invoice_app.parsers.shopee_parser import ShopeeParser
from src.invoice_app.parsers.shopee_product_parser import (
    parse_positioned_products,
    parse_text_products,
    reconcile_product_candidates,
)
from src.invoice_app.parsers.validation import (
    validate_product_items,
    validate_shopee_financial_reconciliation,
    validate_shopee_product_amounts,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    CLOSED_TRANSACTION_SOURCE_CHANGE,
    ImportStatus,
    InMemoryHistoricalInvoiceRepository,
    source_fact_fingerprint,
)
from src.invoice_app.services.product_price_master import (
    PriceLookupStatus,
    ProductPriceMaster,
)
from src.invoice_app.services.uat2_persistence_schema import (
    INVOICE_ITEMS_HEADERS,
    INVOICE_ORDERS_HEADERS,
    STATEMENT_DATA_HEADERS,
    STATEMENT_FINANCIAL_COMPONENT_HEADERS,
)


ROOT = Path(__file__).resolve().parents[1]
DIRECTED_CORPUS = ROOT / "tmp" / "refund_directed_corpus.json"
DIRECTED_ANALYSIS = ROOT / "tmp" / "refund_directed_analysis.json"
SPECIAL_ORDER_ID = "260831RMRYUP6D"


def _text_product(marker_or_name: str, *, ordered_quantity: int) -> dict[str, object]:
    text = f"""
No. Product(s) Unit Price Quantity Subtotal
{marker_or_name}
1 Variation: Original 10.00 {ordered_quantity} {ordered_quantity * 10:.2f}
SKU: SKU-{ordered_quantity}
Total 1 products
Merchandise Subtotal RM{ordered_quantity * 10:.2f}
"""
    items = parse_text_products(text)
    assert len(items) == 1
    return items[0]


def _load_directed_audit() -> tuple[dict[str, object], dict[str, object]]:
    if not DIRECTED_CORPUS.exists() or not DIRECTED_ANALYSIS.exists():
        pytest.skip("Read-only directed Refund audit artifacts are not available.")
    corpus = json.loads(DIRECTED_CORPUS.read_text(encoding="utf-8"))["orders"]
    analysis = json.loads(DIRECTED_ANALYSIS.read_text(encoding="utf-8"))
    return corpus, analysis


def _extract_directed_order(order_id: str):
    corpus, _ = _load_directed_audit()
    snapshots = corpus[order_id]["snapshots"]
    assert len(snapshots) == 1
    pdf_path = ROOT / snapshots[0]["representative_path"]
    document = read_pdf_document(pdf_path)
    extracted = extract_shopee_data(
        document.text,
        pdf_path.name,
        parse_positioned_products(document),
        document=document,
    )
    return pdf_path, document, extracted


@pytest.mark.parametrize(
    ("ordered_quantity", "refund_quantity"),
    ((12, 1), (3, 2), (7, 7)),
)
def test_item_marker_keeps_partial_or_full_source_quantity_and_cleans_name(
    ordered_quantity: int,
    refund_quantity: int,
):
    item = _text_product(
        f"{refund_quantity} Return/Refund Simply Natural Test Item",
        ordered_quantity=ordered_quantity,
    )

    assert item["product_name"] == "Simply Natural Test Item"
    assert item["quantity"] == ordered_quantity
    assert item["source_return_refund_quantity"] == refund_quantity
    assert validate_product_items([item], require_sku=True) == []


@pytest.mark.parametrize("marker", ("4 Return/Refund", "0 Return/Refund", "-1 Return/Refund"))
def test_invalid_item_marker_quantity_is_blocked(marker: str):
    item = _text_product(
        f"{marker} Simply Natural Test Item",
        ordered_quantity=3,
    )

    errors = validate_product_items([item], require_sku=True)
    assert errors
    assert "invalid Return/Refund evidence" in errors[0]


def test_generic_help_text_and_unrelated_return_name_are_not_item_markers():
    generic = classify_invoice_financial_layout(
        "Refund Amount -RM10.00\nBuyer can raise return/refund after delivery."
    )
    item = _text_product("Return to Nature Granola", ordered_quantity=2)

    assert generic == UNKNOWN_OR_MIXED
    assert item["product_name"] == "Return to Nature Granola"
    assert "source_return_refund_quantity" not in item
    assert "_source_return_refund_error" not in item


def test_refund_classifier_requires_nonzero_amount_and_second_typed_source_signal():
    marker_item = {
        "source_return_refund_quantity": 1,
        "quantity": 1,
        "product_name": "Tea",
    }

    assert classify_invoice_financial_layout(
        "Refund Amount -RM10.00", product_items=[marker_item]
    ) == RETURN_REFUND
    assert classify_invoice_financial_layout(
        "Refund Amount RM0.00", product_items=[marker_item]
    ) == UNKNOWN_OR_MIXED
    assert classify_invoice_financial_layout(
        "Refund Amount -RM10.00"
    ) == UNKNOWN_OR_MIXED
    assert classify_invoice_financial_layout(
        "Hide Income Details\nRefund Amount -RM10.00\n"
        "Reverse Shipping Fee -RM4.90\nBuyer Payment"
    ) == RETURN_REFUND


@pytest.mark.parametrize("text_quantity", (2, None))
def test_positioned_and_text_marker_disagreement_does_not_choose_a_quantity(
    text_quantity: int | None,
):
    positioned = [{
        "seller_sku": "SKU-1",
        "product_name": "Tea",
        "quantity": 3,
        "source_return_refund_quantity": 1,
    }]
    text_item = {
        "seller_sku": "SKU-1",
        "product_name": "Tea",
        "quantity": 3,
    }
    if text_quantity is not None:
        text_item["source_return_refund_quantity"] = text_quantity

    item = reconcile_product_candidates(positioned, [text_item])[0]

    assert "source_return_refund_quantity" not in item
    assert "disagree" in item["_source_return_refund_error"]
    assert validate_product_items([item], require_sku=True)


def test_directed_refund_and_early_snapshot_corpus_classifies_from_source():
    corpus, analysis = _load_directed_audit()
    observed: dict[str, str] = {}

    for order_id in (
        *analysis["explicit_refund_order_ids"],
        *analysis["found_without_refund_order_ids"],
    ):
        snapshots = corpus[order_id]["snapshots"]
        assert len(snapshots) == 1
        pdf_path = ROOT / snapshots[0]["representative_path"]
        document = read_pdf_document(pdf_path)
        extracted = extract_shopee_data(
            document.text,
            pdf_path.name,
            parse_positioned_products(document),
            document=document,
        )
        observed[order_id] = extracted.invoice_financial_layout
        if order_id in analysis["explicit_refund_order_ids"]:
            assert extracted.refund_amount is not None
            assert extracted.refund_amount != 0
            assert any(
                item.get("source_return_refund_quantity")
                for item in extracted.product_items
            ) or {
                "reverse_shipping_fee",
                "reverse_shipping_fee_sst",
            }.intersection(extracted.income_label_presence)
        else:
            assert extracted.refund_amount is None
            assert not any(
                item.get("source_return_refund_quantity")
                for item in extracted.product_items
            )

    assert len(analysis["explicit_refund_order_ids"]) == 11
    assert len(analysis["found_without_refund_order_ids"]) == 11
    assert {
        observed[order_id] for order_id in analysis["explicit_refund_order_ids"]
    } == {RETURN_REFUND}
    assert {
        observed[order_id] for order_id in analysis["found_without_refund_order_ids"]
    } == {NORMAL_ORDER}


@pytest.fixture(scope="module")
def special_refund_source():
    return _extract_directed_order(SPECIAL_ORDER_ID)


def test_260831_primary_refund_regression_and_financial_reconciliation(
    special_refund_source,
):
    _, _, extracted = special_refund_source
    products = list(extracted.product_items)

    assert extracted.order_id == SPECIAL_ORDER_ID
    assert extracted.invoice_financial_layout == RETURN_REFUND
    assert len(products) == 2
    assert products[0]["product_name"] == "Better Gourmet Multigrain Ring 40g (HALAL)"
    assert products[0]["quantity"] == 2
    assert "source_return_refund_quantity" not in products[0]
    assert products[1]["product_name"] == (
        "Simply Natural Baked Brown Rice Cracker 140g | Great Snacks | Gluten Free"
    )
    assert products[1]["quantity"] == 1
    assert products[1]["source_return_refund_quantity"] == 1
    assert extracted.income["product_price"] == "43.80"
    assert extracted.refund_amount == Decimal("-36.00")
    assert extracted.income["merchandise_subtotal"] == "7.80"
    assert extracted.income["order_income"] == "2.70"
    assert extracted.income["final_amount"] == "2.70"
    assert validate_shopee_product_amounts(
        products,
        extracted.income["merchandise_subtotal"],
        extracted.refund_amount,
        product_price=extracted.income["product_price"],
    ) is None
    assert validate_shopee_financial_reconciliation(
        extracted.income,
        extracted.refund_amount,
        layout=extracted.invoice_financial_layout,
    ) is None


def test_cleaned_refund_product_keeps_exact_product_master_disambiguation(
    special_refund_source,
):
    _, _, extracted = special_refund_source
    item = extracted.product_items[1]
    master = ProductPriceMaster.from_rows([
        {
            "seller_sku": item["seller_sku"],
            "product_name": item["product_name"],
            "variation_name": item["variation"],
            "unit_selling_price": "36.00",
            "nav_code": "NAV-CLEAN",
        },
        {
            "seller_sku": item["seller_sku"],
            "product_name": "1 Return/Refund " + item["product_name"],
            "variation_name": item["variation"],
            "unit_selling_price": "99.00",
            "nav_code": "NAV-POLLUTED",
        },
    ])

    lookup = master.lookup(
        seller_sku=item["seller_sku"],
        product_name=item["product_name"],
        variation_name=item["variation"],
    )

    assert lookup.status in {
        PriceLookupStatus.MATCHED,
        PriceLookupStatus.MATCHED_BY_SKU_NAME_VARIATION,
    }
    assert lookup.unit_selling_price == Decimal("36.00")
    assert lookup.nav_code == "NAV-CLEAN"


def _special_accepted_records(special_refund_source):
    pdf_path, document, _ = special_refund_source
    orders, products, reviews = ShopeeParser().parse_document(
        document,
        pdf_path.name,
        "refund-v2-regression",
    )
    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 2
    return orders[0], products


def test_runtime_refund_quantity_is_not_persisted_or_fingerprinted(
    special_refund_source,
):
    order, products = _special_accepted_records(special_refund_source)
    enrichment = [
        {"unit_price": product["unit_price"], "nav": f"NAV-{index}"}
        for index, product in enumerate(products)
    ]
    bundle = map_accepted_shopee_invoice(
        order,
        products,
        source_hash="later-refund-source",
        enriched_items=enrichment,
    )
    altered_products = [dict(product) for product in products]
    altered_products[1]["source_return_refund_quantity"] = 999
    altered = map_accepted_shopee_invoice(
        order,
        altered_products,
        source_hash="later-refund-source",
        enriched_items=enrichment,
    )
    polluted = replace(
        bundle,
        items=(
            bundle.items[0],
            replace(
                bundle.items[1],
                product_name="1 Return/Refund " + str(bundle.items[1].product_name),
            ),
        ),
    )

    assert source_fact_fingerprint(bundle) == source_fact_fingerprint(altered)
    assert source_fact_fingerprint(bundle) != source_fact_fingerprint(polluted)
    assert not hasattr(bundle.items[1], "source_return_refund_quantity")
    assert len(INVOICE_ORDERS_HEADERS) == 44
    assert len(INVOICE_ITEMS_HEADERS) == 22
    assert len(STATEMENT_DATA_HEADERS) == 40
    assert len(STATEMENT_FINANCIAL_COMPONENT_HEADERS) == 17


def test_parsed_refund_source_cannot_rewrite_a_closed_invoice(
    special_refund_source,
):
    order, products = _special_accepted_records(special_refund_source)
    later = map_accepted_shopee_invoice(
        order,
        products,
        source_hash="later-refund-source",
        enriched_items=[
            {"unit_price": product["unit_price"], "nav": f"NAV-{index}"}
            for index, product in enumerate(products)
        ],
    )
    original = replace(
        later,
        order=replace(
            later.order,
            fund_transfer_date=date(2026, 9, 5),
            invoice_financial_layout=NORMAL_ORDER,
            merchandise_subtotal=Decimal("43.80"),
            refund_amount=Decimal("0.00"),
            final_amount=Decimal("38.70"),
            order_income=Decimal("38.70"),
        ),
    )
    repository = InMemoryHistoricalInvoiceRepository()
    assert repository.import_invoice(original).status is ImportStatus.NEW
    before_order = repository.get_order("Shopee", SPECIAL_ORDER_ID)
    before_items = repository.get_items_by_order_ids(
        "Shopee", [SPECIAL_ORDER_ID]
    )[SPECIAL_ORDER_ID]

    result = repository.import_invoice(later)

    assert later.order.invoice_financial_layout == RETURN_REFUND
    assert source_fact_fingerprint(later) != source_fact_fingerprint(original)
    assert result.status is ImportStatus.SOURCE_CONFLICT
    assert result.reason_code == CLOSED_TRANSACTION_SOURCE_CHANGE
    assert repository.get_order("Shopee", SPECIAL_ORDER_ID) == before_order
    assert repository.get_items_by_order_ids(
        "Shopee", [SPECIAL_ORDER_ID]
    )[SPECIAL_ORDER_ID] == before_items
