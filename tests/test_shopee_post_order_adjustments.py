from __future__ import annotations

from dataclasses import replace
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
    ORDER_ADJUSTMENT,
    RETURN_REFUND,
    extract_invoice_adjustment_evidence,
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
from src.invoice_app.review_reason_codes import (
    POST_ORDER_ADJUSTMENT_AMOUNT_MISSING,
    POST_ORDER_ADJUSTMENT_EVIDENCE_CONFLICT,
    POST_ORDER_ADJUSTMENT_SOURCE_MISSING,
)


ADJUSTMENT_ARCHIVE = Path(r"D:\download material\ADJUSTMENT EXP.zip")
REAL_COMPENSATION_PDF = Path(
    "archive/20260925032034-b51a0273/26082480BKAV7A.pdf"
)
FALSE_POSITIVE_ADJUSTMENT_PDFS = (
    Path(
        "archive/20260925062354-720637ce/"
        "NOT IN STATEMENT.zip_Invoice Shopee&Lazada_ACCOUNT HR_30072026_2607302669GA03.pdf"
    ),
    Path(
        "archive/20260925062354-720637ce/"
        "NOT IN STATEMENT.zip_Invoice Shopee&Lazada_ACCOUNT HR_27072026_260727PS44109J.pdf"
    ),
)


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
    product_return_marker: bool = False,
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


def test_completed_adjustment_keeps_transient_evidence_and_normal_identity_blocker():
    extracted = extract_shopee_data(_order_text(), "post-order-adjustment.pdf")
    order, _ = map_shopee_records(extracted, "adjustment-test")

    assert extracted.invoice_financial_layout == NORMAL_ORDER
    assert extracted.refund_amount is None
    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_type == ORDER_ADJUSTMENT
    assert extracted.post_order_adjustment_reason == "Return Refund Adjustment After Order Completed"
    assert extracted.post_order_adjustment_date == "01/08/2026"
    assert extracted.post_order_adjustment_amount == Decimal("-17.67")
    assert extracted.post_order_adjustment_final_amount_consistent is True
    issue = find_shopee_review_issue(extracted)
    assert issue is not None
    assert issue.reason == (
        "Financial Reconciliation Failed: seller components total 50.00, "
        "but Order Income is 50.03."
    )
    assert order["order_income"] == "50.03"
    assert order["final_amount"] == "32.36"
    assert order["refund_amount"] == "N/A"
    assert order["post_order_adjustment_observed"] is True
    assert order["post_order_adjustment_reason"] == "Return Refund Adjustment After Order Completed"
    assert order["post_order_adjustment_amount"] == "-17.67"


def test_compensation_adjustment_reason_is_explicit_source_evidence():
    extracted = extract_shopee_data(
        _order_text(
            order_income="-6.88",
            final_amount="130.36",
            adjustment_reason="Return Refund Adjustment/Compensation",
            adjustment_amount="137.24",
        ),
        "26082480BKAV7A.pdf",
    )

    issue = find_shopee_review_issue(extracted)

    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_reason == "Return Refund Adjustment/Compensation"
    assert extracted.post_order_adjustment_amount == Decimal("137.24")
    assert extracted.post_order_adjustment_final_amount_consistent is True
    assert issue is None or issue.reason_code != POST_ORDER_ADJUSTMENT_SOURCE_MISSING


def test_multiple_structured_adjustments_sum_without_fake_legacy_projection():
    text = _order_text(
        order_income="50.00",
        final_amount="32.33",
        include_adjustment=False,
    ).replace(
        "Final Amount RM32.33",
        """Order Adjustment
Adjustment Complete Date Adjustment Reason Released Amount
01/08/2026 Return Refund Adjustment After Order Completed RM-10.00
02/08/2026 Return Refund Adjustment/Compensation RM-7.67
Total Adjustment Amount RM-17.67
Final Amount RM32.33""",
    )

    extracted = extract_shopee_data(text, "multiple-adjustments.pdf")

    assert [event.signed_amount for event in extracted.invoice_adjustments] == [
        Decimal("-10.00"), Decimal("-7.67")
    ]
    assert all(event.is_complete for event in extracted.invoice_adjustments)
    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_type is None
    assert extracted.post_order_adjustment_reason is None
    assert extracted.post_order_adjustment_amount is None
    assert extracted.post_order_adjustment_final_amount_consistent is True
    assert find_shopee_review_issue(extracted) is None


def test_adjustment_section_total_mismatch_fails_closed():
    text = _order_text(order_income="50.00", final_amount="32.33").replace(
        "Total Adjustment Amount RM-17.67", "Total Adjustment Amount RM-17.60"
    )

    issue = find_shopee_review_issue(
        extract_shopee_data(text, "adjustment-total-mismatch.pdf")
    )

    assert issue is not None
    assert issue.reason_code == POST_ORDER_ADJUSTMENT_EVIDENCE_CONFLICT
    assert "Total Adjustment Amount" in issue.reason


def test_instructional_return_refund_text_is_not_adjustment_evidence():
    text = _order_text(
        order_income="50.00",
        final_amount="50.00",
        include_adjustment=False,
    ).replace(
        "Final Amount RM50.00",
        "Buyer can raise return/refund requests from Order History\nFinal Amount RM50.00",
    )

    assert extract_invoice_adjustment_evidence(text) == ()
    assert find_shopee_review_issue(
        extract_shopee_data(text, "instructional-return-refund.pdf")
    ) is None


def test_page_boundary_does_not_become_part_of_adjustment_reason():
    text = _order_text(
        order_income="50.00",
        final_amount="32.33",
        adjustment_reason="Return Refund Adjustment After Order\nPage 1 of 2\nCompleted",
    )

    event = extract_shopee_data(text, "adjustment-page-boundary.pdf").invoice_adjustments[0]

    assert event.source_reason == "Return Refund Adjustment After Order Completed"
    assert event.signed_amount == Decimal("-17.67")


def test_complete_adjustment_does_not_hide_an_independent_promotion_blocker():
    extracted = extract_shopee_data(
        _order_text(order_income="50.00", final_amount="32.33"),
        "adjustment-plus-promotion.pdf",
    )
    product = dict(extracted.product_items[0])
    product.update(
        {
            "promotion_group_id": "group-1",
            "promotion_label": "Any 2 at RM50.00",
            "promotion_metadata_status": "incomplete",
            "_promotion_boundary_status": "ambiguous",
        }
    )

    issue = find_shopee_review_issue(
        replace(extracted, product_items=(product,))
    )

    assert issue is not None
    assert issue.reason_code == "INCOMPLETE_PROMOTION_EVIDENCE"


@pytest.mark.skipif(
    not REAL_COMPENSATION_PDF.exists(),
    reason="Current 26082480BKAV7A source fixture is not available.",
)
def test_real_26082480bkav7a_adjustment_is_complete_without_layout_false_blocker():
    document = read_pdf_document(REAL_COMPENSATION_PDF)
    extracted = extract_shopee_data(
        document.text,
        REAL_COMPENSATION_PDF.name,
        parse_positioned_products(document),
        document=document,
    )

    assert extracted.invoice_financial_layout == RETURN_REFUND
    assert extracted.invoice_adjustments[0].source_reason == "Return Refund Adjustment/Compensation"
    assert extracted.invoice_adjustments[0].signed_amount == Decimal("137.24")
    assert extracted.post_order_adjustment_final_amount_consistent is True
    issue = find_shopee_review_issue(extracted)
    assert issue is None or issue.reason_code != "FINANCIAL_LAYOUT_UNRESOLVED"


def test_completed_adjustment_with_inconsistent_final_amount_fails_closed():
    extracted = extract_shopee_data(
        _order_text(order_income="50.00", final_amount="32.30"),
        "adjustment-conflict.pdf",
    )

    issue = find_shopee_review_issue(extracted)

    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_final_amount_consistent is False
    assert issue is not None
    assert issue.reason_code == "POST_ORDER_ADJUSTMENT_EVIDENCE_CONFLICT"


def test_final_amount_equal_to_order_income_without_adjustment_passes():
    extracted = extract_shopee_data(
        _order_text(
            order_income="50.00",
            final_amount="50.00",
            include_adjustment=False,
            product_return_marker=False,
        ),
        "no-adjustment-equal-final.pdf",
    )

    assert find_shopee_review_issue(extracted) is None


def test_incomplete_adjustment_marker_does_not_require_an_amount_when_final_equals_income():
    extracted = extract_shopee_data(
        _order_text(
            order_income="50.00",
            final_amount="50.00",
            adjustment_amount="",
        ),
        "incomplete-adjustment-equal-final.pdf",
    )

    issue = find_shopee_review_issue(extracted)

    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_amount is None
    assert issue is None or issue.reason_code != POST_ORDER_ADJUSTMENT_AMOUNT_MISSING


@pytest.mark.parametrize("source_pdf", FALSE_POSITIVE_ADJUSTMENT_PDFS)
def test_real_no_adjustment_marker_does_not_require_an_amount_when_final_equals_income(
    source_pdf: Path,
):
    document = read_pdf_document(source_pdf)
    extracted = extract_shopee_data(
        document.text,
        source_pdf.name,
        parse_positioned_products(document),
        document=document,
    )

    issue = find_shopee_review_issue(extracted)

    assert extracted.income["final_amount"] == extracted.income["order_income"]
    assert extracted.post_order_adjustment_amount is None
    assert issue is None or issue.reason_code != POST_ORDER_ADJUSTMENT_AMOUNT_MISSING


def test_final_amount_difference_without_adjustment_evidence_fails_closed():
    extracted = extract_shopee_data(
        _order_text(
            order_income="50.00",
            final_amount="45.00",
            include_adjustment=False,
            product_return_marker=False,
        ),
        "no-adjustment-different-final.pdf",
    )

    issue = find_shopee_review_issue(extracted)

    assert issue is not None
    assert issue.reason_code == POST_ORDER_ADJUSTMENT_SOURCE_MISSING
    assert extracted.post_order_adjustment_amount is None


def test_visible_supported_adjustment_with_missing_amount_is_distinct_review():
    extracted = extract_shopee_data(
        _order_text(
            order_income="50.00",
            final_amount="45.00",
            adjustment_amount="",
        ),
        "adjustment-amount-missing.pdf",
    )

    issue = find_shopee_review_issue(extracted)

    assert extracted.post_order_adjustment_observed is True
    assert extracted.post_order_adjustment_amount is None
    assert issue is not None
    assert issue.reason_code == POST_ORDER_ADJUSTMENT_AMOUNT_MISSING


def test_revalidation_enforces_shared_adjustment_contract():
    accepted = extract_shopee_data(
        _order_text(order_income="50.00", final_amount="32.33"),
        "adjustment-revalidation.pdf",
    )
    order, products = map_shopee_records(accepted, "adjustment-revalidation")
    master = _real_product_master(products)

    assert revalidate_shopee_invoice(order, products, price_master=master).error is None

    conflict = dict(order)
    conflict["_invoice_adjustment_evidence"] = ({
        **order["_invoice_adjustment_evidence"][0],
        "signed_amount": "-17.60",
    },)
    result = revalidate_shopee_invoice(conflict, products, price_master=master)
    assert result.error is not None
    assert "do not equal the explicit Total Adjustment Amount" in result.error

    amount_missing = dict(order)
    amount_missing["_invoice_adjustment_evidence"] = ({
        **order["_invoice_adjustment_evidence"][0],
        "signed_amount": "N/A",
        "completeness": "INCOMPLETE",
    },)
    result = revalidate_shopee_invoice(amount_missing, products, price_master=master)
    assert result.error is not None
    assert "could not be extracted" in result.error

    source_missing = dict(order, post_order_adjustment_observed=False)
    source_missing["_invoice_adjustment_evidence"] = ()
    result = revalidate_shopee_invoice(source_missing, products, price_master=master)
    assert result.error is not None
    assert "Adjustment evidence is not available" in result.error


def test_revalidation_uses_the_same_optional_component_visibility_contract():
    text = _order_text(
        order_income="50.00",
        final_amount="50.00",
        include_adjustment=False,
        product_return_marker=False,
    ).replace("Shipping Subtotal RM0.00\n", "").replace(
        "Fees & Charges -RM0.00\n", ""
    )
    extracted = extract_shopee_data(text, "optional-absent-revalidation.pdf")
    order, products = map_shopee_records(extracted, "optional-absent-revalidation")
    master = _real_product_master(products)

    assert order["shipping_subtotal"] == "N/A"
    assert order["vouchers_rebates_total"] == "N/A"
    assert order["fees_charges_total"] == "N/A"
    assert revalidate_shopee_invoice(order, products, price_master=master).error is None

    visible_missing = dict(order)
    visible_missing["_income_label_presence"] = tuple(
        sorted({*order["_income_label_presence"], "shipping_subtotal"})
    )
    result = revalidate_shopee_invoice(
        visible_missing,
        products,
        price_master=master,
    )
    assert result.error is not None
    assert "Missing: Shipping Subtotal" in result.error


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


def test_no_adjustment_section_does_not_trigger_detection():
    text = _order_text(include_adjustment=False).replace(
        "Final Amount RM32.36",
        """Order Adjustment
Adjustment Complete Date Adjustment Reason Released Amount
No adjustment has been made to this order yet
Final Amount RM32.36""",
    )
    extracted = extract_shopee_data(text, "no-post-order-adjustment.pdf")

    assert extracted.post_order_adjustment_observed is False
    assert extracted.post_order_adjustment_type is None
    assert extracted.post_order_adjustment_date is None
    assert extracted.post_order_adjustment_amount is None


def test_empty_adjustment_section_does_not_bypass_normal_financial_validation():
    extracted = extract_shopee_data(
        _order_text(include_adjustment=False).replace(
            "Final Amount RM32.36",
            """Order Adjustment
Adjustment Complete Date Adjustment Reason Released Amount
No adjustment has been made to this order yet
Final Amount RM32.36""",
        ),
        "empty-adjustment-section.pdf",
    )

    assert extracted.invoice_financial_layout == NORMAL_ORDER
    assert extracted.post_order_adjustment_observed is False
    issue = find_shopee_review_issue(extracted)
    assert issue is not None
    assert issue.reason == (
        "Financial Reconciliation Failed: seller components total 50.00, "
        "but Order Income is 50.03."
    )


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

            assert extracted.invoice_financial_layout == "UNKNOWN_OR_MIXED"
            assert extracted.refund_amount is None
            assert extracted.post_order_adjustment_observed is True
            assert extracted.post_order_adjustment_type == ORDER_ADJUSTMENT
            assert extracted.post_order_adjustment_final_amount_consistent is True
            assert orders == []
            assert len(reviews) == 1
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
def test_real_adjustment_upload_keeps_transaction_layout_fail_closed_when_only_marker_exists(
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
    assert orders == []
    assert products == []
    assert {review["order_id"] for review in reviews} == set(expected)
    assert all("financial layout is unresolved" in review["reason"].casefold() for review in reviews)
