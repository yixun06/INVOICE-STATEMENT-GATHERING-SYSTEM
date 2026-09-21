from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest

from src.invoice_app.pdf_document import read_pdf_document
from src.invoice_app.domain.historical_invoice import map_accepted_shopee_invoice
from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_financial_parser import (
    NORMAL_ORDER,
    RETURN_REFUND_AFTER_ORDER_COMPLETED,
)
from src.invoice_app.parsers.shopee_mapper import map_shopee_records
from src.invoice_app.parsers.shopee_parser import ShopeeParser
from src.invoice_app.parsers.shopee_product_parser import parse_positioned_products
from src.invoice_app.parsers.shopee_review_policy import find_shopee_review_issue
from src.invoice_app.repositories.historical_invoice_repository import InMemoryHistoricalInvoiceRepository
from src.invoice_app.services import batch_service
from src.invoice_app.services.batch_service import (
    append_batch_results_with_metadata,
    prepare_uploaded_invoice_files,
    process_pdf_file_with_outcome,
)
from src.invoice_app.services.historical_invoice_intake import (
    IntakeStatus,
    build_current_batch_staging,
    classify_staging,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.shopee_invoice_revalidation import revalidate_shopee_invoice
from src.invoice_app.services.uat2_persistence_schema import INVOICE_ORDERS_HEADERS


ADJUSTMENT_ARCHIVE = Path(r"D:\download material\ADJUSTMENT EXP.zip")


class _UploadedFile:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


def _upload_through_invoice_flow(uploaded_files, batch_id: str):
    """Run the same archive -> parse -> batch-rules path used by app.process_uploads."""
    archived, preparation_reviews = prepare_uploaded_invoice_files(uploaded_files, batch_id)
    orders: list[dict] = []
    products: list[dict] = []
    reviews = list(preparation_reviews)
    for source in archived:
        result = process_pdf_file_with_outcome(
            source.source_pdf, source.archive_path, batch_id
        )
        appended = append_batch_results_with_metadata(
            orders,
            products,
            reviews,
            result.orders,
            result.products,
            result.reviews,
        )
        orders, products, reviews = appended.orders, appended.products, appended.reviews
    return archived, orders, products, reviews


def _real_product_master(products: list[dict]) -> ProductPriceMaster:
    rows_by_identity: dict[tuple[str, str, str], dict] = {}
    for product in products:
        identity = (
            str(product["seller_sku"]),
            str(product["product_name"]),
            str(product.get("variation") or ""),
        )
        rows_by_identity.setdefault(
            identity,
            {
                "seller_sku": identity[0],
                "parent_sku": "",
                "product_name": identity[1],
                "variation_name": identity[2],
                "unit_selling_price": "1.00",
                "nav_code": f"NAV-{len(rows_by_identity) + 1}",
            },
        )
    return ProductPriceMaster.from_rows(rows_by_identity.values())


def _order_text(
    *,
    order_income: str = "50.03",
    final_amount: str = "32.36",
    adjustment_reason: str = "Return Refund Adjustment After Order\nCompleted",
    adjustment_amount: str = "-17.67",
    include_adjustment: bool = True,
    product_return_marker: bool = True,
) -> str:
    adjustment = ""
    if include_adjustment:
        adjustment = f"""
Order Adjustment
Adjustment Complete Date Adjustment Reason Released Amount
01/08/2026 {adjustment_reason} RM{adjustment_amount}
Total Adjustment Amount RM{adjustment_amount}
"""
    return f"""
Order Received Add a Note
Order ID: POST-ORDER-1
POST-ORDER-1 07/08/2026
Hide Income Details
No. Product(s) Unit Price Quantity Subtotal
{"1 Return/Refund Test Product" if product_return_marker else "Test Product"}
1 Variation: Original 25.00 2 50.00
SKU: ADJ-001
Total 1 products
Merchandise Subtotal RM50.00
Product Price RM50.00
Shipping Subtotal RM0.00
Fees & Charges -RM0.00
Order Income RM{order_income}
{adjustment}
Final Amount RM{final_amount}
"""


def test_completed_return_refund_adjustment_is_transient_evidence_not_original_refund():
    extracted = extract_shopee_data(_order_text(), "post-order-adjustment.pdf")
    order, _ = map_shopee_records(extracted, "adjustment-test")

    assert extracted.invoice_financial_layout == NORMAL_ORDER
    assert extracted.refund_amount is None
    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_type == RETURN_REFUND_AFTER_ORDER_COMPLETED
    assert extracted.post_order_adjustment_reason == "Return Refund Adjustment After Order Completed"
    assert extracted.post_order_adjustment_date == "01/08/2026"
    assert extracted.post_order_adjustment_amount == Decimal("-17.67")
    assert extracted.post_order_adjustment_final_amount_consistent is True
    assert find_shopee_review_issue(extracted) is None
    assert order["order_income"] == "50.03"
    assert order["final_amount"] == "32.36"
    assert order["refund_amount"] == "N/A"
    assert order["post_order_adjustment_observed"] is True
    assert order["post_order_adjustment_reason"] == "Return Refund Adjustment After Order Completed"
    assert order["post_order_adjustment_amount"] == "-17.67"


def test_completed_adjustment_with_inconsistent_final_amount_fails_closed():
    extracted = extract_shopee_data(
        _order_text(final_amount="32.35"), "adjustment-conflict.pdf"
    )

    issue = find_shopee_review_issue(extracted)

    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_final_amount_consistent is False
    assert issue is not None
    assert issue.reason_code == "POST_ORDER_ADJUSTMENT_EVIDENCE_CONFLICT"


def test_post_order_adjustment_evidence_is_not_added_to_the_canonical_invoice_schema():
    extracted = extract_shopee_data(_order_text(), "staged-adjustment.pdf")
    order, products = map_shopee_records(extracted, "adjustment-persistence-boundary")
    bundle = map_accepted_shopee_invoice(
        order,
        products,
        source_hash="staged-adjustment-source",
        enriched_items=[
            {"unit_price": product["unit_price"], "nav": "NAV-ADJ"}
            for product in products
        ],
    )

    assert bundle.order.order_income == Decimal("50.03")
    assert bundle.order.final_amount == Decimal("32.36")
    assert bundle.order.refund_amount is None
    assert not hasattr(bundle.order, "post_order_adjustment_observed")
    assert not hasattr(bundle.order, "post_order_adjustment_amount")
    assert "post_order_adjustment_observed" not in INVOICE_ORDERS_HEADERS
    assert "post_order_adjustment_type" not in INVOICE_ORDERS_HEADERS
    assert "post_order_adjustment_date" not in INVOICE_ORDERS_HEADERS
    assert "post_order_adjustment_amount" not in INVOICE_ORDERS_HEADERS


@pytest.mark.parametrize(
    "text",
    (
        _order_text(include_adjustment=False),
        _order_text(
            adjustment_reason="No adjustment has been made to this order yet",
            adjustment_amount="0.00",
        ),
        _order_text(
            adjustment_reason="Shopee Voucher Adjustment After Order\nCompleted",
            adjustment_amount="-17.67",
        ),
    ),
)
def test_non_completed_return_refund_adjustment_rows_do_not_trigger_detection(text: str):
    extracted = extract_shopee_data(text, "no-post-order-adjustment.pdf")

    assert extracted.post_order_adjustment_observed is False
    assert extracted.post_order_adjustment_type is None
    assert extracted.post_order_adjustment_date is None
    assert extracted.post_order_adjustment_amount is None


def test_empty_adjustment_section_does_not_block_an_otherwise_normal_invoice():
    extracted = extract_shopee_data(
        _order_text(
            adjustment_reason="No adjustment has been made to this order yet",
            adjustment_amount="0.00",
            product_return_marker=False,
        ),
        "empty-adjustment-section.pdf",
    )

    assert extracted.invoice_financial_layout == NORMAL_ORDER
    assert extracted.post_order_adjustment_observed is False
    assert find_shopee_review_issue(extracted) is None


@pytest.mark.skipif(
    not ADJUSTMENT_ARCHIVE.exists(),
    reason="The user-supplied post-order adjustment PDF archive is not available.",
)
def test_user_supplied_completed_adjustment_pdf_corpus():
    expected = {
        "260722BVJQ6RRF": ("01/08/2026", "50.03", "-17.67", "32.36"),
        "2607302A9FMUBM": ("06/08/2026", "46.58", "-43.61", "2.97"),
        "260807P7MNPU1X": ("15/08/2026", "65.29", "-6.25", "59.04"),
        "260808RW7RVBGY": ("16/08/2026", "107.35", "-129.06", "-21.71"),
        "2608245W7CGC2C": ("26/08/2026", "31.14", "-12.85", "18.29"),
    }

    with TemporaryDirectory(prefix="post-order-adjustment-test-") as directory:
        root = Path(directory)
        with ZipFile(ADJUSTMENT_ARCHIVE) as archive:
            archive.extractall(root)
        pdfs = sorted(root.rglob("*.pdf"))
        assert len(pdfs) == len(expected)

        observed: dict[str, tuple[str, str, str, str]] = {}
        for pdf_path in pdfs:
            document = read_pdf_document(pdf_path)
            extracted = extract_shopee_data(
                document.text,
                pdf_path.name,
                parse_positioned_products(document),
                document=document,
            )
            orders, _, reviews = ShopeeParser().parse_document(
                document, pdf_path.name, "post-order-adjustment-corpus"
            )

            assert extracted.invoice_financial_layout == NORMAL_ORDER
            assert extracted.refund_amount is None
            assert extracted.post_order_adjustment_observed is True
            assert extracted.post_order_adjustment_type == RETURN_REFUND_AFTER_ORDER_COMPLETED
            assert extracted.post_order_adjustment_final_amount_consistent is True
            assert reviews == []
            assert len(orders) == 1
            assert orders[0]["order_income"] == extracted.income["order_income"]
            assert orders[0]["final_amount"] == extracted.income["final_amount"]
            assert orders[0]["refund_amount"] == "N/A"
            observed[extracted.order_id] = (
                str(extracted.post_order_adjustment_date),
                extracted.income["order_income"],
                str(extracted.post_order_adjustment_amount),
                extracted.income["final_amount"],
            )

    assert observed == expected


@pytest.mark.skipif(
    not ADJUSTMENT_ARCHIVE.exists(),
    reason="The user-supplied post-order adjustment PDF archive is not available.",
)
@pytest.mark.parametrize("upload_kind", ("direct", "zip"))
def test_real_adjustment_upload_preserves_sidecar_through_staging_and_revalidation(
    tmp_path, monkeypatch, upload_kind: str
):
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", tmp_path / "archive")
    expected = {
        "260722BVJQ6RRF": ("01/08/2026", "50.03", "-17.67", "32.36"),
        "2607302A9FMUBM": ("06/08/2026", "46.58", "-43.61", "2.97"),
        "260807P7MNPU1X": ("15/08/2026", "65.29", "-6.25", "59.04"),
        "260808RW7RVBGY": ("16/08/2026", "107.35", "-129.06", "-21.71"),
        "2608245W7CGC2C": ("26/08/2026", "31.14", "-12.85", "18.29"),
    }
    if upload_kind == "direct":
        with ZipFile(ADJUSTMENT_ARCHIVE) as archive:
            uploads = [
                _UploadedFile(Path(member.filename).name, archive.read(member))
                for member in archive.infolist()
                if not member.is_dir() and member.filename.lower().endswith(".pdf")
            ]
    else:
        uploads = [_UploadedFile(ADJUSTMENT_ARCHIVE.name, ADJUSTMENT_ARCHIVE.read_bytes())]

    archived, orders, products, reviews = _upload_through_invoice_flow(
        uploads, f"post-order-upload-{upload_kind}"
    )

    assert len(archived) == len(expected)
    assert reviews == []
    assert {order["order_id"] for order in orders} == set(expected)
    assert all(order["status"] == "Accepted" for order in orders)
    assert all(order["invoice_financial_layout"] == NORMAL_ORDER for order in orders)
    assert all(order["post_order_adjustment_observed"] is True for order in orders)
    assert all(order["refund_amount"] == "N/A" for order in orders)

    master = _real_product_master(products)
    entries = classify_staging(
        build_current_batch_staging(
            batch_id=f"post-order-upload-{upload_kind}",
            orders=orders,
            products=products,
            reviews=reviews,
            price_master=master,
        ),
        InMemoryHistoricalInvoiceRepository(),
    )
    assert all(entry.status is IntakeStatus.NEW for entry in entries)

    for order in orders:
        order_id = order["order_id"]
        complete_date, income, adjustment, final_amount = expected[order_id]
        entry = next(entry for entry in entries if entry.order_id == order_id)
        assert entry.post_order_adjustment is not None
        assert entry.source_filename == order["source_pdf"]
        assert len(entry.source_hash) == 64
        assert entry.post_order_adjustment.adjustment_type == RETURN_REFUND_AFTER_ORDER_COMPLETED
        assert entry.post_order_adjustment.adjustment_reason == "Return Refund Adjustment After Order Completed"
        assert entry.post_order_adjustment.adjustment_complete_date == complete_date
        assert entry.post_order_adjustment.released_amount == Decimal(adjustment)
        assert entry.post_order_adjustment.final_amount_consistent is True
        assert entry.bundle is not None
        assert entry.bundle.order.order_income == Decimal(income)
        assert entry.bundle.order.final_amount == Decimal(final_amount)
        assert entry.bundle.order.refund_amount is None
        related_products = [
            product
            for product in products
            if product["order_id"] == order_id
            and product["source_pdf"] == order["source_pdf"]
        ]
        revalidated = revalidate_shopee_invoice(
            order,
            related_products,
            price_master=master,
        )
        assert revalidated.error is None
        assert order["post_order_adjustment_amount"] == adjustment
