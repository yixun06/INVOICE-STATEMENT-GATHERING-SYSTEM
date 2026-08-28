import ast
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from src.invoice_app.services import batch_service
from src.invoice_app.parsers.lazada_parser import LazadaParser
from src.invoice_app.services.all_products import build_all_product_rows
from src.invoice_app.services.auth_service import authenticate
from src.invoice_app.services.batch_service import (
    FIELD_LABELS,
    PLATFORM_ORDER_FIELDS,
    PLATFORM_PRODUCT_FIELDS,
    append_batch_results,
    append_batch_results_with_metadata,
    apply_batch_rules,
    canonical_order_identity,
    is_manual_review_record,
    prepare_uploaded_invoice_files,
    process_pdf_text,
    process_pdf_text_with_outcome,
)


class FakeUploadedFile:
    def __init__(self, name: str, file_bytes: bytes):
        self.name = name
        self._file_bytes = file_bytes

    def getvalue(self) -> bytes:
        return self._file_bytes


def build_zip(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zip_file:
        for name, file_bytes in entries.items():
            zip_file.writestr(name, file_bytes)
    return buffer.getvalue()


def test_default_admin_login():
    assert authenticate("admin", "admin123") is True
    assert authenticate("admin", "wrong-pass") is False


CANONICAL_SHOPEE_FINANCIAL_ORDER_FIELDS = (
    "order_income",
    "income_type",
    "merchandise_subtotal",
    "product_price",
    "shipping_subtotal",
    "shipping_fee_paid_by_buyer",
    "shipping_fee_charged_by_logistic_provider",
    "shipping_fee_rebate_from_shopee",
    "seller_paid_shipping_fee_sst",
    "vouchers_rebates_total",
    "voucher_type",
    "voucher_code",
    "voucher_funded_by",
    "voucher_amount",
    "fees_charges_total",
    "commission_fee",
    "service_fee",
    "transaction_fee",
    "ads_escrow_top_up_fee",
    "final_amount",
    "buyer_merchandise_subtotal",
    "buyer_shipping_fee",
    "shopee_voucher",
    "seller_voucher",
    "total_buyer_payment",
)


def test_shopee_canonical_financial_order_fields_are_available_and_labeled():
    for field in CANONICAL_SHOPEE_FINANCIAL_ORDER_FIELDS:
        assert field in PLATFORM_ORDER_FIELDS["Shopee"]
        assert FIELD_LABELS[field]

    assert FIELD_LABELS["order_income"] == "Order Income"
    assert FIELD_LABELS["income_type"] == "Income Type"
    assert FIELD_LABELS["shipping_fee_rebate_from_shopee"] == "Shipping Fee Rebate from Shopee"
    assert "adjustment_complete_date" not in PLATFORM_ORDER_FIELDS["Shopee"]
    assert "adjustment_reason" not in PLATFORM_ORDER_FIELDS["Shopee"]
    assert "released_amount" not in PLATFORM_ORDER_FIELDS["Shopee"]


def test_review_payload_keys_stay_internal_to_review_records():
    for internal_field in ("order_payload", "product_payloads"):
        assert internal_field not in FIELD_LABELS
        assert all(internal_field not in fields for fields in PLATFORM_ORDER_FIELDS.values())
        assert all(internal_field not in fields for fields in PLATFORM_PRODUCT_FIELDS.values())


def test_shopee_order_normalization_removes_retired_adjustment_and_remarks_fields():
    normalized_orders, normalized_products, _ = apply_batch_rules(
        [
            {
                "batch_id": "batch-adjustment",
                "platform": "Shopee",
                "order_id": "SHP-ADJ-1",
                "source_pdf": "adjustment.pdf",
                "income_type": "Estimated",
                "shipping_fee_rebate_from_shopee": "RM 1.25",
                "released_amount": "2.50",
                "adjustment_complete_date": "2026-08-19 11:30:00",
                "adjustment_reason": "Refund adjustment",
            }
        ],
        [
            {
                "batch_id": "batch-adjustment",
                "platform": "Shopee",
                "order_id": "SHP-ADJ-1",
                "product_name": "Test Product",
                "seller_sku": "SKU-1",
                "quantity": 1,
                "unit_price": "",
                "line_total": "10.00",
                "line_subtotal": "10.00",
                "source_pdf": "adjustment.pdf",
                "status": "Accepted",
                "remarks": "",
            }
        ],
        [],
    )
    assert normalized_orders[0]["income_type"] == "Estimated"
    assert normalized_orders[0]["shipping_fee_rebate_from_shopee"] == "1.25"
    assert "released_amount" not in normalized_orders[0]
    assert "adjustment_complete_date" not in normalized_orders[0]
    assert "adjustment_reason" not in normalized_orders[0]
    assert normalized_orders[0]["final_amount"] == "N/A"
    assert normalized_orders[0]["completed_date"] == "N/A"
    assert "remarks" not in normalized_orders[0]
    assert normalized_products[0]["unit_price"] == "N/A"
    assert "remarks" not in normalized_products[0]


def test_preview_money_column_contract_covers_canonical_money_fields_only():
    app_module = ast.parse(Path("app.py").read_text())
    money_columns: set[str] = set()
    for node in app_module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MONEY_COLUMNS"
            for target in node.targets
        ):
            money_columns = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            break

    assert "shipping_fee_rebate_from_shopee" in money_columns
    assert "released_amount" not in money_columns
    assert "income_type" not in money_columns
    assert "adjustment_complete_date" not in money_columns
    assert "adjustment_reason" not in money_columns


def test_shopee_truncated_income_details_goes_to_manual_review():
    text = """
    Order ID: SHP123
    SHP123 07/08/2026
    New Order
    Hide Income Details
    No. Product(s) Unit Price Quantity Subtotal
    Test Product 1
    1 Variation: Original 12.50 2 25.00
    SKU: ABC-001
    Merchandise Subtotal RM25.00
    """

    orders, products, reviews = process_pdf_text("truncated-shopee.pdf", text, "batch-001")

    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert reviews[0]["order_id"] == "SHP123"
    assert reviews[0]["reason"].startswith("Income Completion Anchor Missing:")
    assert "Estimated Order Income or Order Income" in reviews[0]["reason"]
    assert "re-upload" in reviews[0]["reason"]
    assert reviews[0]["order_payload"]["delivery_fee"] == "N/A"
    assert reviews[0]["order_payload"]["status"] == "Manual Review"
    assert len(reviews[0]["product_payloads"]) == 1
    assert reviews[0]["product_payloads"][0]["seller_sku"] == "ABC-001"
    assert reviews[0]["product_payloads"][0]["quantity"] == 2
    assert reviews[0]["product_payloads"][0]["unit_price"] == "12.50"
    assert reviews[0]["product_payloads"][0]["status"] == "Manual Review"


def test_shopee_missing_income_anchor_still_goes_to_manual_review_when_ads_fee_is_absent():
    text = """
    Order ID: SHP123
    SHP123 07/08/2026
    New Order
    Hide Income Details
    No. Product(s) Unit Price Quantity Subtotal
    Test Product 1
    1 Variation: Original 12.50 2 25.00
    SKU: ABC-001
    Merchandise Subtotal RM25.00
    Shipping Fee Paid by Buyer (excl. SST) RM0.00
    Product Price RM25.00
    Shipping Subtotal RM0.00
    Shipping Fee Charged by Logistic Provider RM0.00
    Seller Paid Shipping Fee SST RM0.00
    Fees & Charges -RM3.00
    Commission Fee (Incl.SST) -RM1.00
    Service Fee -RM1.00
    Transaction Fee (Incl. SST) -RM1.00
    """

    orders, products, reviews = process_pdf_text("missing-ads-shopee.pdf", text, "batch-001")

    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert "Estimated Order Income or Order Income" in reviews[0]["reason"]
    assert reviews[0]["order_payload"]["delivery_fee"] == "0.00"
    assert len(reviews[0]["product_payloads"]) == 1


def test_unknown_platform_is_unsupported_without_review_or_payload():
    result = process_pdf_text_with_outcome(
        "unknown.pdf",
        "This document has no known platform or invoice anchors.",
        "batch-001",
    )

    assert result.orders == []
    assert result.products == []
    assert result.reviews == []
    assert result.processing_errors == []
    assert result.unsupported_files[0]["status"] == "Unsupported"
    assert result.unsupported_files[0]["filename"] == "unknown.pdf"


def test_process_pdf_text_for_declarative_shopee_input():
    source_pdf = "sample-shopee.pdf"
    text = """
    Order ID: SHP123
    SHP123 07/08/2026
    New Order
    Hide Income Details
    No. Product(s) Unit Price Quantity Subtotal
    Test Product 1
    1 Variation: Original 12.50 2 25.00
    SKU: ABC-001
    Merchandise Subtotal RM25.00
    Shipping Fee Paid by Buyer (excl. SST) RM0.00
    Product Price RM25.00
    Shipping Subtotal RM0.00
    Shipping Fee Charged by Logistic Provider RM0.00
    Seller Paid Shipping Fee SST RM0.00
    Fees & Charges -RM3.00
    Commission Fee (Incl.SST) -RM1.00
    Service Fee -RM1.00
    Transaction Fee (Incl. SST) -RM1.00
    Ads Escrow Top Up Fee RM0.00
    Estimated Order Income RM22.00
    """
    orders, products, reviews = process_pdf_text(source_pdf, text, "batch-001")
    assert orders[0]["order_id"] == "SHP123"
    assert orders[0]["delivery_fee"] == "0.00"
    assert orders[0]["commission_fee"] == "-1.00"
    assert products[0]["seller_sku"] == "ABC-001"
    assert products[0]["quantity"] == 2
    assert reviews == []


def test_prepare_uploaded_invoice_files_archives_direct_and_zipped_pdfs(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", tmp_path / "archive")
    uploaded_files = [
        FakeUploadedFile("direct.pdf", b"direct pdf bytes"),
        FakeUploadedFile(
            "invoice-bundle.zip",
            build_zip(
                {
                    "nested/lazada.pdf": b"lazada pdf bytes",
                    "notes.txt": b"ignore me",
                    "shopee.PDF": b"shopee pdf bytes",
                }
            ),
        ),
    ]

    archived_pdfs, reviews = prepare_uploaded_invoice_files(uploaded_files, "batch-zip")

    assert reviews == []
    assert [archived.source_pdf for archived in archived_pdfs] == [
        "direct.pdf",
        "invoice-bundle.zip::nested/lazada.pdf",
        "invoice-bundle.zip::shopee.PDF",
    ]
    assert [archived.archive_path.read_bytes() for archived in archived_pdfs] == [
        b"direct pdf bytes",
        b"lazada pdf bytes",
        b"shopee pdf bytes",
    ]


def test_prepare_uploaded_invoice_files_reviews_zip_without_pdfs(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", tmp_path / "archive")
    uploaded_files = [FakeUploadedFile("empty.zip", build_zip({"notes.txt": b"no invoices"}))]

    archived_pdfs, reviews = prepare_uploaded_invoice_files(uploaded_files, "batch-empty")

    assert archived_pdfs == []
    assert len(reviews) == 1
    assert reviews[0]["source_pdf"] == "empty.zip"
    assert reviews[0]["status"] == "Unsupported"
    assert reviews[0]["message"] == "ZIP file does not contain any PDF invoices."


def test_prepare_uploaded_invoice_files_reviews_invalid_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", tmp_path / "archive")
    uploaded_files = [FakeUploadedFile("broken.zip", b"not actually a zip")]

    archived_pdfs, reviews = prepare_uploaded_invoice_files(uploaded_files, "batch-broken")

    assert archived_pdfs == []
    assert len(reviews) == 1
    assert reviews[0]["source_pdf"] == "broken.zip"
    assert reviews[0]["status"] == "Processing Error"
    assert "could not be opened" in reviews[0]["message"]


def test_lazada_sku_anchor_fallback_recovers_wrapped_product_row():
    source_pdf = "wrapped-lazada.pdf"
    text = """
    Invoice Number: LZD-INV-1
    Order Number: 501548767521442
    Order Date: 02 Mar 2026
    Invoice Date: 02 Mar 2026
    Payment Method: Online Banking
    Product name Seller SKU Shop SKU Price Paid Price
    1 Organic Soy Drink Bundle
    9555208106944-6 SHOP-9555208106944 28.97 28.97
    Subtotal: RM 28.97
    Shipping: RM 0.00
    Net paid: RM 28.97
    """

    orders, products, reviews = process_pdf_text(source_pdf, text, "batch-001")

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 1
    assert products[0]["product_name"] == "Organic Soy Drink Bundle"
    assert products[0]["seller_sku"] == "9555208106944-6"
    assert products[0]["quantity"] == 1
    assert products[0]["line_total"] == "28.97"


def _seller_center_order(
    order_id: str,
    *,
    product_rows: str,
    subtotal: str,
    voucher: str,
    total: str,
    shipping: str,
    net_paid: str,
    your_items_first: bool,
) -> str:
    table_and_financials = "\n".join(
        (
            "# Product name Seller SKU Shop SKU Price Paid Price",
            product_rows.strip(),
            f"Subtotal: RM {subtotal}",
            f"Less: Voucher applied: RM {voucher}",
            f"Total: RM {total}",
            f"Shipping: +RM {shipping}",
            f"Net paid: RM {net_paid}",
        )
    )
    summary = "\n".join(
        (
            "Order Summary",
            f"Order Number: {order_id}",
            "Order Date: 22 08 2026",
            "Payment Method: GN_TNG_EBANK",
        )
    )
    if your_items_first:
        return "\n".join((f"Your ordered items for {order_id}", table_and_financials, summary))
    return "\n".join((summary, f"Your ordered items for {order_id}", table_and_financials))


def test_lazada_invoice_layout_multi_order_keeps_all_orders():
    text = """
    Invoice Number: LZD-INV-1
    Order Number: LZD-OLD-1
    Order Date: 01 03 2026
    Invoice Date: 01 03 2026
    Payment Method: Card
    Product name Seller SKU Shop SKU Price Paid Price
    1 Legacy One SKU-1 SHOP-1 10.00 9.00
    Subtotal: RM 10.00
    Less: Voucher applied: RM -1.00
    Total: RM 9.00
    Shipping: +RM 0.00
    Net paid: RM 9.00

    Invoice Number: LZD-INV-2
    Order Number: LZD-OLD-2
    Order Date: 02 03 2026
    Invoice Date: 02 03 2026
    Payment Method: Card
    Product name Seller SKU Shop SKU Price Paid Price
    1 Legacy Two SKU-2 SHOP-2 20.00 18.00
    Subtotal: RM 20.00
    Less: Voucher applied: RM -2.00
    Total: RM 18.00
    Shipping: +RM 0.00
    Net paid: RM 18.00
    """

    orders, products, reviews = process_pdf_text("legacy-multi.pdf", text, "batch-001")

    assert reviews == []
    assert len(orders) == 2
    assert len(products) == 2
    assert {order["order_id"] for order in orders} == {"LZD-OLD-1", "LZD-OLD-2"}
    assert {order["invoice_number"] for order in orders} == {"LZD-INV-1", "LZD-INV-2"}


def test_lazada_seller_center_your_items_anchor_keeps_orders_and_financials():
    text = "\n".join(
        [
            _seller_center_order(
                "LZD-NEW-1",
                product_rows="1 First Order SKU-SHARED SHOP-1 10.00 8.50",
                subtotal="10.00",
                voucher="-1.50",
                total="8.50",
                shipping="0.00",
                net_paid="8.50",
                your_items_first=True,
            ),
            _seller_center_order(
                "LZD-NEW-2",
                product_rows="1 Second Order SKU-SHARED SHOP-2 20.00 18.75",
                subtotal="20.00",
                voucher="-1.25",
                total="18.75",
                shipping="4.90",
                net_paid="23.65",
                your_items_first=True,
            ),
        ]
    )

    orders, products, reviews = process_pdf_text("seller-center-your-first.pdf", text, "batch-001")

    assert reviews == []
    assert len(orders) == 2
    assert len(products) == 2
    by_order = {order["order_id"]: order for order in orders}
    assert {field: by_order["LZD-NEW-1"][field] for field in ("subtotal", "voucher", "total", "shipping_fee", "net_paid")} == {
        "subtotal": "10.00",
        "voucher": "-1.50",
        "total": "8.50",
        "shipping_fee": "0.00",
        "net_paid": "8.50",
    }
    assert {field: by_order["LZD-NEW-2"][field] for field in ("subtotal", "voucher", "total", "shipping_fee", "net_paid")} == {
        "subtotal": "20.00",
        "voucher": "-1.25",
        "total": "18.75",
        "shipping_fee": "4.90",
        "net_paid": "23.65",
    }
    assert all(order["invoice_number"] == "" and order["invoice_date"] == "" for order in orders)
    assert {product["order_id"] for product in products} == {"LZD-NEW-1", "LZD-NEW-2"}


def test_lazada_seller_center_order_number_anchor_merges_within_order_only():
    text = "\n".join(
        [
            _seller_center_order(
                "LZD-ORDER-FIRST-1",
                product_rows="""
                1 Repeated Product SKU-REPEAT SHOP-1 18.00 12.09
                2 Repeated Product SKU-REPEAT SHOP-1 18.00 12.10
                """,
                subtotal="32.40",
                voucher="-8.21",
                total="24.19",
                shipping="0.00",
                net_paid="24.19",
                your_items_first=False,
            ),
            _seller_center_order(
                "LZD-ORDER-FIRST-2",
                product_rows="1 Separate Order SKU-REPEAT SHOP-2 18.00 17.00",
                subtotal="18.00",
                voucher="-1.00",
                total="17.00",
                shipping="0.00",
                net_paid="17.00",
                your_items_first=False,
            ),
        ]
    )

    orders, products, reviews = process_pdf_text("seller-center-order-first.pdf", text, "batch-001")

    assert reviews == []
    assert len(orders) == 2
    assert len(products) == 2
    products_by_order = {product["order_id"]: product for product in products}
    assert products_by_order["LZD-ORDER-FIRST-1"]["quantity"] == 2
    assert products_by_order["LZD-ORDER-FIRST-1"]["line_total"] == "24.19"
    assert products_by_order["LZD-ORDER-FIRST-2"]["quantity"] == 1
    assert products_by_order["LZD-ORDER-FIRST-2"]["line_total"] == "17.00"


def test_lazada_seller_center_invalid_order_does_not_block_valid_sibling():
    text = "\n".join(
        [
            _seller_center_order(
                "LZD-VALID",
                product_rows="1 Valid Product SKU-VALID SHOP-VALID 10.00 9.00",
                subtotal="10.00",
                voucher="-1.00",
                total="9.00",
                shipping="0.00",
                net_paid="9.00",
                your_items_first=True,
            ),
            _seller_center_order(
                "LZD-BAD",
                product_rows="",
                subtotal="5.00",
                voucher="0.00",
                total="5.00",
                shipping="0.00",
                net_paid="5.00",
                your_items_first=True,
            ),
        ]
    )

    orders, products, reviews = process_pdf_text("seller-center-invalid.pdf", text, "batch-001")

    assert len(orders) == 1
    assert len(products) == 1
    assert orders[0]["order_id"] == "LZD-VALID"
    assert len(reviews) == 1
    assert reviews[0]["order_id"] == "LZD-BAD"
    assert reviews[0]["status"] == "Manual Review"


def test_lazada_seller_center_conflicting_anchor_ids_do_not_bind_wrong_order_data():
    text = """
    Your ordered items for LZD-ANCHOR-A
    # Product name Seller SKU Shop SKU Price Paid Price
    1 Untrusted Product SKU-A SHOP-A 10.00 9.00
    Subtotal: RM 10.00
    Less: Voucher applied: RM -1.00
    Total: RM 9.00
    Shipping: +RM 0.00
    Net paid: RM 9.00
    Order Summary
    Order Number: LZD-ANCHOR-B

    Your ordered items for LZD-ANCHOR-B
    # Product name Seller SKU Shop SKU Price Paid Price
    1 Valid Product SKU-B SHOP-B 20.00 18.00
    Subtotal: RM 20.00
    Less: Voucher applied: RM -2.00
    Total: RM 18.00
    Shipping: +RM 0.00
    Net paid: RM 18.00
    Order Summary
    Order Number: LZD-ANCHOR-B
    """

    orders, products, reviews = process_pdf_text("seller-center-conflict.pdf", text, "batch-001")

    assert len(orders) == 1
    assert len(products) == 1
    assert orders[0]["order_id"] == "LZD-ANCHOR-B"
    assert products[0]["seller_sku"] == "SKU-B"
    assert len(reviews) == 1
    assert reviews[0]["status"] == "Manual Review"
    assert "missing Order Number" in reviews[0]["reason"]

def test_lazada_review_branch_packages_already_extracted_products_without_accepting_them(monkeypatch):
    parser = LazadaParser()
    monkeypatch.setattr(
        parser,
        "_parse_items",
        lambda _block: [
            {
                "product_name": "Valid extracted product",
                "seller_sku": "SKU-1",
                "shop_sku": "SHOP-1",
                "quantity": 1,
                "unit_price": Decimal("12.00"),
                "line_total": Decimal("10.00"),
            },
            {
                "product_name": "Needs review",
                "seller_sku": "",
                "shop_sku": "SHOP-2",
                "quantity": 1,
                "unit_price": Decimal("8.00"),
                "line_total": Decimal("8.00"),
            },
        ],
    )
    text = (
        "Invoice Number: LZD-INV-R\n"
        "Order Number: LZD-R\n"
        "Product name Seller SKU Shop SKU Price Paid Price\n"
        "Shipping: RM 4.90\n"
    )

    orders, products, reviews = parser.parse(text, "lazada-review.pdf", "batch-001")

    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert reviews[0]["order_payload"]["delivery_fee"] == "4.90"
    assert reviews[0]["order_payload"]["status"] == "Manual Review"
    assert [payload["product_name"] for payload in reviews[0]["product_payloads"]] == [
        "Valid extracted product",
        "Needs review",
    ]
    assert all(payload["status"] == "Manual Review" for payload in reviews[0]["product_payloads"])


def test_zenxin_sku_anchor_fallback_recovers_inline_product_metrics():
    source_pdf = "inline-zenxin.pdf"
    text = """
    Invoice No. INV-161821
    Order No. 10123
    Date: 18/08/2026
    Amount: RM 35.70
    Product Qty Price Total
    Organic Broccoli 3 RM11.90 RM35.70
    SKU: 3000309
    Standard Delivery RM0.00
    Total RM35.70
    """

    orders, products, reviews = process_pdf_text(source_pdf, text, "batch-001")

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 1
    assert products[0]["product_name"] == "Organic Broccoli"
    assert products[0]["seller_sku"] == "3000309"
    assert products[0]["quantity"] == 3
    assert products[0]["line_total"] == "35.70"


def test_zenxin_validation_sends_missing_sku_to_manual_review():
    source_pdf = "missing-sku-zenxin.pdf"
    text = """
    Invoice No. INV-161821
    Order No. 10123
    Date: 18/08/2026
    Amount: RM 35.70
    Product Qty Price Total
    Organic Broccoli
    3 RM11.90 RM35.70
    Standard Delivery RM0.00
    Total RM35.70
    """

    orders, products, reviews = process_pdf_text(source_pdf, text, "batch-001")

    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert reviews[0]["status"] == "Manual Review"
    assert "Deterministic validation failed" in reviews[0]["reason"]
    assert "product_payloads" not in reviews[0]


def test_zenxin_review_payload_keeps_valid_sibling_product_for_all_display():
    text = """
    Invoice No. INV-REVIEW
    Order No. ZNX-REVIEW
    Date: 18/08/2026
    Amount: RM 15.00
    Product Qty Price Total
    Valid Product
    1 RM10.00 RM10.00
    SKU: Z-VALID
    1 RM5.00 RM5.00
    SKU: Z-INVALID
    Standard Delivery RM4.90
    Total RM19.90
    """

    orders, products, reviews = process_pdf_text("zenxin-review.pdf", text, "batch-001")

    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert reviews[0]["order_payload"]["delivery_fee"] == "4.90"
    assert len(reviews[0]["product_payloads"]) == 2
    all_rows = build_all_product_rows([], [], reviews)
    assert len(all_rows) == 1
    assert all_rows[0]["product_name"] == "Valid Product"
    assert "data_status" not in all_rows[0]


def test_lazada_missing_fields_use_na_without_changing_real_values():
    orders = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "LZD-1",
            "source_pdf": "lazada.pdf",
            "subtotal": "20.00",
            "shipping_fee": "0.00",
        }
    ]
    products = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "LZD-1",
            "source_pdf": "lazada.pdf",
            "product_name": "Product A",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_total": "20.00",
        }
    ]

    accepted_orders, accepted_products, reviews = apply_batch_rules(orders, products, [])

    assert reviews == []
    assert accepted_orders[0]["subtotal"] == "20.00"
    assert accepted_orders[0]["shipping_fee"] == "0.00"
    assert accepted_orders[0]["invoice_number"] == "N/A"
    assert accepted_orders[0]["payment_method"] == "N/A"
    assert accepted_orders[0]["voucher_applied"] == "N/A"
    assert accepted_orders[0]["total"] == "N/A"
    assert accepted_orders[0]["net_paid"] == "N/A"
    assert "remarks" not in accepted_orders[0]
    assert accepted_products[0]["paid_price"] == "20.00"
    assert accepted_products[0]["price"] == "N/A"
    assert accepted_products[0]["shop_sku"] == "N/A"
    assert "remarks" not in accepted_products[0]


def test_zenxin_missing_fields_use_na_without_inventing_invoice_amount():
    orders = [
        {
            "batch_id": "batch-1",
            "platform": "ZENXIN",
            "order_id": "ZNX-1",
            "source_pdf": "zenxin.pdf",
            "total": "35.70",
        }
    ]
    products = [
        {
            "batch_id": "batch-1",
            "platform": "ZENXIN",
            "order_id": "ZNX-1",
            "source_pdf": "zenxin.pdf",
            "product_name": "Product A",
            "seller_sku": "3000309",
            "quantity": 3,
            "line_total": "35.70",
        }
    ]

    accepted_orders, accepted_products, reviews = apply_batch_rules(orders, products, [])

    assert reviews == []
    assert accepted_orders[0]["total"] == "35.70"
    assert accepted_orders[0]["invoice_amount"] == "N/A"
    assert accepted_orders[0]["invoice_number"] == "N/A"
    assert accepted_orders[0]["payment_method"] == "N/A"
    assert accepted_orders[0]["shipping_fee"] == "N/A"
    assert accepted_orders[0]["discount"] == "N/A"
    assert "remarks" not in accepted_orders[0]
    assert accepted_products[0]["line_total_inc_tax"] == "35.70"
    assert accepted_products[0]["unit_price"] == "N/A"
    assert accepted_products[0]["invoice_date"] == "N/A"
    assert "remarks" not in accepted_products[0]


def test_apply_batch_rules_handles_duplicate_order_ids_and_sku_grouping():
    orders = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "source_pdf": "a.pdf",
            "gross_sales": "20.00",
            "delivery_fee": "1.00",
            "net_amount": "21.00",
        },
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "source_pdf": "b.pdf",
            "gross_sales": "20.00",
            "delivery_fee": "1.00",
            "net_amount": "21.00",
        },
    ]
    products = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "product_name": "Product A",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_total": "10.00",
            "unit_price": "10.00",
            "source_pdf": "a.pdf",
        },
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "product_name": "Product A",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_total": "10.00",
            "unit_price": "10.00",
            "source_pdf": "a.pdf",
        },
    ]

    accepted_orders, accepted_products, reviews = apply_batch_rules(orders, products, [])
    assert len(accepted_orders) == 1
    assert len(accepted_products) == 1
    assert accepted_products[0]["quantity"] == 2
    assert accepted_products[0]["line_total"] == "20.00"
    assert len(reviews) == 1
    assert reviews[0]["status"] == "Duplicate Skipped"
    assert reviews[0]["reason"] == "Duplicate Order"


def test_append_batch_results_keeps_existing_rows_and_flags_duplicate_new_orders():
    existing_orders = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "source_pdf": "first.pdf",
        }
    ]
    existing_products = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "source_pdf": "first.pdf",
            "product_name": "First product",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_total": "10.00",
        }
    ]
    new_orders = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-2",
            "source_pdf": "second.pdf",
        },
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "source_pdf": "duplicate.pdf",
        },
    ]
    new_products = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-2",
            "source_pdf": "second.pdf",
            "product_name": "Second product",
            "seller_sku": "SKU-2",
            "quantity": 2,
            "line_total": "20.00",
        },
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-1",
            "source_pdf": "duplicate.pdf",
            "product_name": "Duplicate product",
            "seller_sku": "SKU-3",
            "quantity": 1,
            "line_total": "99.00",
        },
    ]

    orders, products, reviews = append_batch_results(
        existing_orders,
        existing_products,
        [],
        new_orders,
        new_products,
        [],
    )

    assert [order["order_id"] for order in orders] == ["ORD-1", "ORD-2"]
    assert {product["seller_sku"] for product in products} == {"SKU-1", "SKU-2"}
    assert len(reviews) == 1
    assert reviews[0]["order_id"] == "ORD-1"
    assert reviews[0]["status"] == "Duplicate Skipped"
    assert reviews[0]["reason"] == "Duplicate Order"


def test_incremental_batches_keep_existing_rows_and_add_only_new_orders():
    orders, products, reviews = append_batch_results(
        [],
        [],
        [],
        [{"batch_id": "batch-1", "platform": "Shopee", "order_id": "SHP-1", "source_pdf": "a.pdf"}],
        [
            {
                "batch_id": "batch-1",
                "platform": "Shopee",
                "order_id": "SHP-1",
                "source_pdf": "a.pdf",
                "product_name": "First product",
                "seller_sku": "SKU-1",
                "quantity": 1,
                "unit_price": "10.00",
                "line_total": "10.00",
            }
        ],
        [],
    )

    orders, products, reviews = append_batch_results(
        orders,
        products,
        reviews,
        [{"batch_id": "batch-1", "platform": "Lazada", "order_id": "LZD-2", "source_pdf": "b.pdf"}],
        [
            {
                "batch_id": "batch-1",
                "platform": "Lazada",
                "order_id": "LZD-2",
                "source_pdf": "b.pdf",
                "product_name": "Second product",
                "seller_sku": "SKU-2",
                "quantity": 2,
                "unit_price": "20.00",
                "line_total": "40.00",
            }
        ],
        [],
    )

    assert {(order["platform"], order["order_id"]) for order in orders} == {
        ("Shopee", "SHP-1"),
        ("Lazada", "LZD-2"),
    }
    assert {(product["seller_sku"], product["quantity"]) for product in products} == {
        ("SKU-1", 1),
        ("SKU-2", 2),
    }
    assert reviews == []


def test_repeated_same_pdf_duplicate_never_changes_accepted_products():
    order = {"batch_id": "batch-1", "platform": "Lazada", "order_id": "ORD-1", "source_pdf": "same.pdf"}
    product = {
        "batch_id": "batch-1",
        "platform": "Lazada",
        "order_id": "ORD-1",
        "source_pdf": "same.pdf",
        "product_name": "Product A",
        "seller_sku": "SKU-1",
        "quantity": 1,
        "unit_price": "10.00",
        "line_total": "10.00",
    }
    orders, products, reviews = append_batch_results([], [], [], [order], [product], [])

    for _ in range(2):
        orders, products, reviews = append_batch_results(
            orders,
            products,
            reviews,
            [{**order, "platform": "lazada"}],
            [{**product, "platform": "LAZADA"}],
            [],
        )

    assert len(orders) == 1
    assert len(products) == 1
    assert products[0]["quantity"] == 1
    assert products[0]["line_total"] == "10.00"
    assert len(build_all_product_rows(orders, products)) == 1
    assert len(reviews) == 2
    assert all(review["status"] == "Duplicate Skipped" and review["reason"] == "Duplicate Order" for review in reviews)


def test_repeated_multi_product_duplicate_with_same_or_different_filename_never_pollutes_accepted():
    order = {"batch_id": "batch-1", "platform": "Lazada", "order_id": "ORD-M", "source_pdf": "first.pdf"}
    base_products = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-M",
            "source_pdf": "first.pdf",
            "product_name": "Product A",
            "seller_sku": "SKU-A",
            "quantity": 1,
            "unit_price": "10.00",
            "line_total": "10.00",
        },
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": "ORD-M",
            "source_pdf": "first.pdf",
            "product_name": "Product B",
            "seller_sku": "SKU-B",
            "quantity": 2,
            "unit_price": "5.00",
            "line_total": "10.00",
        },
    ]
    orders, products, reviews = append_batch_results([], [], [], [order], base_products, [])

    for platform, source_pdf in (
        ("lazada", "first.pdf"),
        (" LAZADA ", "renamed-copy.pdf"),
        ("Lazada", "another-copy.pdf"),
    ):
        duplicate_order = {**order, "platform": platform, "source_pdf": source_pdf}
        duplicate_products = [
            {**product, "platform": platform, "source_pdf": source_pdf}
            for product in base_products
        ]
        orders, products, reviews = append_batch_results(
            orders,
            products,
            reviews,
            [duplicate_order],
            duplicate_products,
            [],
        )

    assert len(orders) == 1
    assert len(products) == 2
    assert {product["seller_sku"]: product["quantity"] for product in products} == {
        "SKU-A": 1,
        "SKU-B": 2,
    }
    assert {product["seller_sku"]: product["line_total"] for product in products} == {
        "SKU-A": "10.00",
        "SKU-B": "10.00",
    }
    assert len(reviews) == 3
    assert len(build_all_product_rows(orders, products, reviews)) == 2


def test_canonical_identity_normalizes_platform_and_rejects_missing_order_ids():
    assert canonical_order_identity(" lazada ", " ORD-1 ") == ("Lazada", "ORD-1")
    assert canonical_order_identity("SHOPEE", "SHP-1") == ("Shopee", "SHP-1")
    assert canonical_order_identity(" zenxin ", " ZNX-1 ") == ("ZENXIN", "ZNX-1")
    assert canonical_order_identity(None, "ORD-1") is None
    assert canonical_order_identity("ZENXIN", None) is None
    assert canonical_order_identity("ZENXIN", "") is None
    assert canonical_order_identity("ZENXIN", "  ") is None
    assert canonical_order_identity("ZENXIN", "n/a") is None

    accepted_orders, accepted_products, reviews = apply_batch_rules(
        [
            {"platform": "Lazada", "order_id": None, "source_pdf": "none.pdf"},
            {"platform": "Lazada", "order_id": " ", "source_pdf": "blank.pdf"},
            {"platform": "Lazada", "order_id": "N/A", "source_pdf": "na.pdf"},
        ],
        [],
        [],
    )

    assert accepted_orders == []
    assert accepted_products == []
    assert len(reviews) == 3
    assert all(review["reason"] == "Missing Platform or Order ID in parsed order-level data." for review in reviews)


def test_apply_batch_rules_merges_same_sku_only_inside_same_order():
    orders = [
        {
            "batch_id": "batch-1",
            "platform": "Shopee",
            "order_id": "SHP-1",
            "source_pdf": "one.pdf",
            "gross_sales": "30.00",
            "net_amount": "30.00",
        },
        {
            "batch_id": "batch-1",
            "platform": "Shopee",
            "order_id": "SHP-2",
            "source_pdf": "two.pdf",
            "gross_sales": "15.00",
            "net_amount": "15.00",
        },
    ]
    products = [
        {
            "batch_id": "batch-1",
            "platform": "Shopee",
            "order_id": "SHP-1",
            "product_name": "Product A",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_total": "10.00",
            "unit_price": "10.00",
            "source_pdf": "one.pdf",
        },
        {
            "batch_id": "batch-1",
            "platform": "Shopee",
            "order_id": "SHP-1",
            "product_name": "Product A renamed",
            "seller_sku": "SKU-1",
            "quantity": 2,
            "line_total": "20.00",
            "unit_price": "10.00",
            "source_pdf": "one.pdf",
        },
        {
            "batch_id": "batch-1",
            "platform": "Shopee",
            "order_id": "SHP-2",
            "product_name": "Product A",
            "seller_sku": "SKU-1",
            "quantity": 1,
            "line_total": "15.00",
            "unit_price": "15.00",
            "source_pdf": "two.pdf",
        },
    ]

    accepted_orders, accepted_products, reviews = apply_batch_rules(orders, products, [])

    assert len(accepted_orders) == 2
    assert len(accepted_products) == 2
    by_order = {product["order_id"]: product for product in accepted_products}
    assert by_order["SHP-1"]["quantity"] == 3
    assert by_order["SHP-1"]["line_total"] == "30.00"
    assert by_order["SHP-2"]["quantity"] == 1
    assert reviews == []


def test_normal_order_reports_imported_without_skipped_metadata():
    order = {"batch_id": "batch-1", "platform": "Shopee", "order_id": "ORD-N", "source_pdf": "normal.pdf"}
    product = {
        "batch_id": "batch-1",
        "platform": "Shopee",
        "order_id": "ORD-N",
        "source_pdf": "normal.pdf",
        "product_name": "Normal product",
        "seller_sku": "SKU-N",
        "quantity": 1,
        "unit_price": "10.00",
        "line_total": "10.00",
    }

    result = append_batch_results_with_metadata([], [], [], [order], [product], [])

    assert result.imported_order_count == 1
    assert result.manual_review_count == 0
    assert result.duplicate_orders == []
    assert [row["order_id"] for row in result.orders] == ["ORD-N"]
    assert [row["seller_sku"] for row in result.products] == ["SKU-N"]


def test_manual_review_identity_blocks_repeat_without_duplicate_payload_pollution():
    review = {
        "batch_id": "batch-1",
        "platform": "Shopee",
        "order_id": "ORD-R",
        "source_pdf": "first-review.pdf",
        "status": "Manual Review",
        "reason": "Financial Reconciliation Failed",
        "order_payload": {"platform": "Shopee", "order_id": "ORD-R", "delivery_fee": "1.00"},
        "product_payloads": [
            {
                "platform": "Shopee",
                "order_id": "ORD-R",
                "product_name": "Review product",
                "seller_sku": "SKU-R",
                "quantity": 1,
                "unit_price": "12.00",
            }
        ],
    }
    first = append_batch_results_with_metadata([], [], [], [], [], [review])

    repeated_review = {**review, "source_pdf": "second-review.pdf"}
    second = append_batch_results_with_metadata(
        first.orders,
        first.products,
        first.reviews,
        [],
        [],
        [repeated_review],
    )

    assert first.manual_review_count == 1
    assert second.manual_review_count == 0
    assert len(second.duplicate_orders) == 1
    assert second.duplicate_orders[0]["status"] == "Duplicate Skipped"
    assert "order_payload" not in second.duplicate_orders[0]
    assert "product_payloads" not in second.duplicate_orders[0]
    assert sum(is_manual_review_record(item) for item in second.reviews) == 1
    assert len(build_all_product_rows(second.orders, second.products, second.reviews)) == 1


def test_missing_order_ids_never_form_duplicate_identity_even_when_repeated():
    reviews = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": order_id,
            "source_pdf": f"missing-{index}.pdf",
            "status": "Manual Review",
            "reason": "Missing required data",
        }
        for index, order_id in enumerate((None, "", "   ", "N/A"), start=1)
    ]

    first = append_batch_results_with_metadata([], [], [], [], [], reviews)
    second = append_batch_results_with_metadata(
        first.orders,
        first.products,
        first.reviews,
        [],
        [],
        reviews,
    )

    assert first.duplicate_orders == []
    assert second.duplicate_orders == []
    assert first.manual_review_count == 4
    assert second.manual_review_count == 4
    assert sum(is_manual_review_record(item) for item in second.reviews) == 8


def test_multi_order_append_skips_only_duplicate_order_and_imports_siblings():
    existing_order = {"batch_id": "batch-1", "platform": "Lazada", "order_id": "ORDER-B", "source_pdf": "old.pdf"}
    existing_product = {
        "batch_id": "batch-1",
        "platform": "Lazada",
        "order_id": "ORDER-B",
        "source_pdf": "old.pdf",
        "product_name": "Existing B",
        "seller_sku": "SKU-B",
        "quantity": 1,
        "unit_price": "5.00",
        "line_total": "5.00",
    }
    new_orders = [
        {"batch_id": "batch-1", "platform": "Lazada", "order_id": order_id, "source_pdf": "multi.pdf"}
        for order_id in ("ORDER-A", "ORDER-B", "ORDER-C")
    ]
    new_products = [
        {
            "batch_id": "batch-1",
            "platform": "Lazada",
            "order_id": order_id,
            "source_pdf": "multi.pdf",
            "product_name": f"Product {order_id}",
            "seller_sku": f"SKU-{order_id}",
            "quantity": 1,
            "unit_price": "10.00",
            "line_total": "10.00",
        }
        for order_id in ("ORDER-A", "ORDER-B", "ORDER-C")
    ]

    result = append_batch_results_with_metadata(
        [existing_order],
        [existing_product],
        [],
        new_orders,
        new_products,
        [],
    )

    assert result.imported_order_count == 2
    assert [item["order_id"] for item in result.duplicate_orders] == ["ORDER-B"]
    assert {order["order_id"] for order in result.orders} == {"ORDER-A", "ORDER-B", "ORDER-C"}
    assert {product["order_id"] for product in result.products} == {"ORDER-A", "ORDER-B", "ORDER-C"}
    assert next(product for product in result.products if product["order_id"] == "ORDER-B")["source_pdf"] == "old.pdf"
