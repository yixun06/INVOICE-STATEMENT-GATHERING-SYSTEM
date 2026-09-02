from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import re
import uuid
from typing import Any
from zipfile import BadZipFile, ZipFile

from ..config import ARCHIVE_DIR
from ..detector import detect_platform
from ..pdf_document import PdfDocument, read_pdf_document, read_pdf_document_selective_words
from ..parsers.lazada_parser import LazadaParser
from ..parsers.shopee_parser import ShopeeParser
from ..parsers.zenxin_parser import ZenxinParser
from ..utils.normalize import parse_decimal, parse_quantity

PARSER_MAP = {
    "Shopee": ShopeeParser(),
    "Lazada": LazadaParser(),
    "ZENXIN": ZenxinParser(),
}

PLATFORMS = ("Shopee", "Lazada", "ZENXIN")
MISSING_VALUE_PLACEHOLDER = "N/A"
_CANONICAL_PLATFORM_BY_NAME = {platform.casefold(): platform for platform in PLATFORMS}


@dataclass(frozen=True)
class ArchivedPdf:
    source_pdf: str
    archive_path: Path


@dataclass(frozen=True)
class PdfProcessingResult:
    orders: list[dict[str, Any]]
    products: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    unsupported_files: list[dict[str, Any]]
    processing_errors: list[dict[str, Any]]


@dataclass(frozen=True)
class BatchAppendResult:
    orders: list[dict[str, Any]]
    products: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    duplicate_orders: list[dict[str, Any]]
    imported_order_count: int
    manual_review_count: int


ORDER_LEVEL_FIELDS = [
    "batch_id",
    "platform",
    "order_id",
    "invoice_number",
    "invoice_date",
    "order_date",
    "payment_method",
    "gross_sales",
    "delivery_fee",
    "commission_fee",
    "service_fee",
    "transaction_fee",
    "voucher",
    "platform_fees",
    "ads_fee",
    "estimated_order_income",
    "net_income",
    "net_amount",
    "order_status",
    "payment_status",
    "order_created_date",
    "delivered_date",
    "completed_date",
    "fund_transfer_date",
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
    "ads_escrow_top_up_fee",
    "order_income",
    "income_type",
    "final_amount",
    "buyer_merchandise_subtotal",
    "buyer_shipping_fee",
    "shopee_voucher",
    "seller_voucher",
    "total_buyer_payment",
    "subtotal",
    "voucher_applied",
    "total",
    "shipping_fee",
    "net_paid",
    "invoice_amount",
    "discount",
    "source_pdf",
    "status",
]

PLATFORM_ORDER_FIELDS = {
    "Shopee": [
        "platform",
        "order_id",
        "order_status",
        "payment_status",
        "order_created_date",
        "delivered_date",
        "completed_date",
        "fund_transfer_date",
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
        "order_income",
        "income_type",
        "final_amount",
        "buyer_merchandise_subtotal",
        "buyer_shipping_fee",
        "shopee_voucher",
        "seller_voucher",
        "total_buyer_payment",
        "source_pdf",
        "status",
    ],
    "Lazada": [
        "platform",
        "invoice_number",
        "order_id",
        "order_date",
        "invoice_date",
        "payment_method",
        "subtotal",
        "voucher_applied",
        "total",
        "shipping_fee",
        "net_paid",
        "source_pdf",
        "status",
    ],
    "ZENXIN": [
        "platform",
        "invoice_number",
        "order_id",
        "invoice_date",
        "invoice_amount",
        "payment_method",
        "shipping_fee",
        "subtotal",
        "discount",
        "total",
        "source_pdf",
        "status",
    ],
}

PRODUCT_LEVEL_FIELDS = [
    "batch_id",
    "platform",
    "order_id",
    "invoice_number",
    "invoice_date",
    "order_date",
    "payment_method",
    "product_name",
    "seller_sku",
    "shop_sku",
    "quantity",
    "unit_price",
    "line_total",
    "line_subtotal",
    "price",
    "paid_price",
    "line_total_inc_tax",
    "source_pdf",
    "status",
]

PLATFORM_PRODUCT_FIELDS = {
    "Shopee": [
        "platform",
        "order_id",
        "product_name",
        "seller_sku",
        "quantity",
        "unit_price",
        "line_subtotal",
        "source_pdf",
        "status",
    ],
    "Lazada": [
        "platform",
        "invoice_number",
        "order_id",
        "order_date",
        "invoice_date",
        "payment_method",
        "product_name",
        "seller_sku",
        "shop_sku",
        "quantity",
        "price",
        "paid_price",
        "source_pdf",
        "status",
    ],
    "ZENXIN": [
        "platform",
        "invoice_number",
        "order_id",
        "invoice_date",
        "payment_method",
        "product_name",
        "seller_sku",
        "quantity",
        "unit_price",
        "line_total_inc_tax",
        "source_pdf",
        "status",
    ],
}

PLATFORM_ORDER_PLACEHOLDER_FIELDS = {
    "Shopee": tuple(PLATFORM_ORDER_FIELDS["Shopee"]),
    "Lazada": (
        *PLATFORM_ORDER_FIELDS["Lazada"],
        "gross_sales",
        "delivery_fee",
        "voucher",
        "net_amount",
        "net_income",
        "total_amount",
    ),
    "ZENXIN": (
        *PLATFORM_ORDER_FIELDS["ZENXIN"],
        "gross_sales",
        "delivery_fee",
        "voucher",
        "net_amount",
        "net_income",
    ),
}

PLATFORM_PRODUCT_PLACEHOLDER_FIELDS = {
    "Shopee": tuple(PLATFORM_PRODUCT_FIELDS["Shopee"]),
    "Lazada": (*PLATFORM_PRODUCT_FIELDS["Lazada"], "unit_price", "line_total"),
    "ZENXIN": (*PLATFORM_PRODUCT_FIELDS["ZENXIN"], "line_total"),
}

FIELD_LABELS = {
    "platform": "Platform",
    "order_id": "Order ID",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "order_date": "Order Date",
    "payment_method": "Payment Method",
    "product_name": "Product Name",
    "seller_sku": "Seller SKU",
    "shop_sku": "Shop SKU",
    "quantity": "Quantity",
    "unit_price": "Unit Price",
    "line_total": "Line Total",
    "line_subtotal": "Line Subtotal",
    "price": "Price",
    "paid_price": "Paid Price",
    "line_total_inc_tax": "Line Total (Inc. Tax)",
    "order_status": "Order Status",
    "payment_status": "Payment Status",
    "order_created_date": "Order Created Date",
    "delivered_date": "Delivered Date",
    "completed_date": "Completed Date",
    "fund_transfer_date": "Fund Transfer Date",
    "merchandise_subtotal": "Merchandise Subtotal",
    "product_price": "Product Price",
    "shipping_subtotal": "Shipping Subtotal",
    "shipping_fee_paid_by_buyer": "Shipping Fee Paid by Buyer",
    "shipping_fee_charged_by_logistic_provider": "Shipping Fee Charged by Logistic Provider",
    "shipping_fee_rebate_from_shopee": "Shipping Fee Rebate from Shopee",
    "seller_paid_shipping_fee_sst": "Seller Paid Shipping Fee SST",
    "vouchers_rebates_total": "Vouchers & Rebates Total",
    "voucher_type": "Voucher Type",
    "voucher_code": "Voucher Code",
    "voucher_funded_by": "Voucher Funded By",
    "voucher_amount": "Voucher Amount",
    "fees_charges_total": "Fees & Charges Total",
    "commission_fee": "Commission Fee",
    "service_fee": "Service Fee",
    "transaction_fee": "Transaction Fee",
    "ads_escrow_top_up_fee": "Ads Escrow Top Up Fee",
    "order_income": "Order Income",
    "income_type": "Income Type",
    "final_amount": "Final Amount",
    "buyer_merchandise_subtotal": "Buyer Merchandise Subtotal",
    "buyer_shipping_fee": "Buyer Shipping Fee",
    "shopee_voucher": "Shopee Voucher",
    "seller_voucher": "Seller Voucher",
    "total_buyer_payment": "Total Buyer Payment",
    "subtotal": "Subtotal",
    "voucher_applied": "Voucher Applied",
    "total": "Total",
    "shipping_fee": "Shipping Fee",
    "net_paid": "Net Paid",
    "invoice_amount": "Invoice Amount",
    "discount": "Discount",
    "source_pdf": "Source PDF",
    "status": "Status",
    "reason": "Reason",
}


def create_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]


def canonical_platform_label(platform: Any) -> str:
    """Return the project's display label for a known platform."""
    platform_text = "" if platform is None else str(platform).strip()
    if not platform_text:
        return ""
    return _CANONICAL_PLATFORM_BY_NAME.get(platform_text.casefold(), platform_text)


def canonical_order_identity(platform: Any, order_id: Any) -> tuple[str, str] | None:
    """Return the stable duplicate/join identity for a valid parsed order."""
    platform_text = canonical_platform_label(platform)
    order_id_text = "" if order_id is None else str(order_id).strip()
    if not platform_text or not order_id_text or order_id_text.casefold() == MISSING_VALUE_PLACEHOLDER.casefold():
        return None

    canonical_platform = platform_text if platform_text in PLATFORMS else platform_text.casefold()
    return canonical_platform, order_id_text


def archive_uploaded_file(uploaded_file: Any, batch_id: str) -> Path:
    return archive_pdf_bytes(uploaded_file.name, uploaded_file.getvalue(), batch_id)


def prepare_uploaded_invoice_files(
    uploaded_files: list[Any],
    batch_id: str,
) -> tuple[list[ArchivedPdf], list[dict[str, Any]]]:
    archived_pdfs: list[ArchivedPdf] = []
    reviews: list[dict[str, Any]] = []

    for uploaded_file in uploaded_files:
        filename = str(uploaded_file.name)
        lowered_filename = filename.lower()
        if lowered_filename.endswith(".pdf"):
            archived_pdfs.append(
                ArchivedPdf(
                    source_pdf=filename,
                    archive_path=archive_uploaded_file(uploaded_file, batch_id),
                )
            )
            continue

        if lowered_filename.endswith(".zip"):
            zip_pdfs, zip_reviews = archive_pdf_members_from_zip(uploaded_file, batch_id)
            archived_pdfs.extend(zip_pdfs)
            reviews.extend(zip_reviews)
            continue

        reviews.append(
            create_file_outcome_record(
                source_pdf=filename,
                status="Unsupported",
                message="Unsupported upload type. Upload PDF files or ZIP files containing PDFs.",
            )
        )

    return archived_pdfs, reviews


def archive_pdf_members_from_zip(uploaded_file: Any, batch_id: str) -> tuple[list[ArchivedPdf], list[dict[str, Any]]]:
    source_zip = str(uploaded_file.name)
    archived_pdfs: list[ArchivedPdf] = []
    reviews: list[dict[str, Any]] = []
    try:
        with ZipFile(BytesIO(uploaded_file.getvalue())) as zip_file:
            pdf_members = [
                member
                for member in zip_file.infolist()
                if not member.is_dir() and member.filename.lower().endswith(".pdf")
            ]
            if not pdf_members:
                return [], [
                    create_file_outcome_record(
                        source_pdf=source_zip,
                        status="Unsupported",
                        message="ZIP file does not contain any PDF invoices.",
                    )
                ]

            for member in pdf_members:
                source_pdf = f"{source_zip}::{member.filename}"
                try:
                    pdf_bytes = zip_file.read(member)
                except (BadZipFile, RuntimeError) as exc:
                    reviews.append(
                        create_file_outcome_record(
                            source_pdf=source_pdf,
                            status="Processing Error",
                            message=f"PDF file inside ZIP could not be read: {exc}",
                        )
                    )
                    continue
                archived_pdfs.append(
                    ArchivedPdf(
                        source_pdf=source_pdf,
                        archive_path=archive_pdf_bytes(source_pdf, pdf_bytes, batch_id),
                    )
                )
    except BadZipFile:
        return [], [
            create_file_outcome_record(
                source_pdf=source_zip,
                status="Processing Error",
                message="ZIP file could not be opened. It may be corrupted or not a valid ZIP archive.",
            )
        ]

    return archived_pdfs, reviews


def archive_pdf_bytes(source_name: str, file_bytes: bytes, batch_id: str) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    batch_dir = ARCHIVE_DIR / batch_id
    batch_dir.mkdir(exist_ok=True)
    target_path = _unique_archive_path(batch_dir, _safe_archive_name(source_name))
    target_path.write_bytes(file_bytes)
    return target_path


def resolve_archived_pdf_path(batch_id: str | None, source_pdf: str | None) -> Path | None:
    """Return the existing archived source path for a current-batch PDF, if present."""
    if not batch_id or not source_pdf:
        return None
    candidate = ARCHIVE_DIR / str(batch_id).strip() / _safe_archive_name(str(source_pdf))
    return candidate if candidate.is_file() else None


def create_manual_review_record(
    batch_id: str,
    source_pdf: str,
    platform: str,
    order_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "source_pdf": source_pdf,
        "platform": platform,
        "order_id": order_id,
        "status": "Manual Review",
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def create_file_outcome_record(
    source_pdf: str,
    status: str,
    message: str,
) -> dict[str, Any]:
    return {
        "filename": source_pdf,
        "source_pdf": source_pdf,
        "status": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def create_duplicate_order_record(order: dict[str, Any]) -> dict[str, Any]:
    identity = canonical_order_identity(order.get("platform"), order.get("order_id"))
    order_payload = order.get("order_payload")
    if identity is None and isinstance(order_payload, dict):
        identity = canonical_order_identity(order_payload.get("platform"), order_payload.get("order_id"))
    platform, order_id = identity or (
        canonical_platform_label(order.get("platform")) or "Unknown",
        str(order.get("order_id") or "").strip() or MISSING_VALUE_PLACEHOLDER,
    )
    return {
        "batch_id": order.get("batch_id", ""),
        "source_pdf": order.get("source_pdf", ""),
        "platform": platform,
        "order_id": order_id,
        "status": "Duplicate Skipped",
        "reason": "Duplicate Order",
        "message": "Already exists in current batch.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def is_duplicate_review(review: dict[str, Any]) -> bool:
    status = str(review.get("status", review.get("Status", ""))).strip().casefold()
    reason = str(review.get("reason", review.get("Reason", ""))).strip().casefold()
    return status == "duplicate skipped" or reason in {
        "duplicate order",
        "duplicate order id in current batch for the same platform.",
    }


def is_manual_review_record(review: dict[str, Any]) -> bool:
    status = str(review.get("status", review.get("Status", ""))).strip().casefold()
    return status in {"", "manual review"} and not is_duplicate_review(review)


def _review_identity(review: dict[str, Any]) -> tuple[str, str] | None:
    identity = canonical_order_identity(review.get("platform"), review.get("order_id"))
    if identity is not None:
        return identity
    order_payload = review.get("order_payload")
    if isinstance(order_payload, dict):
        return canonical_order_identity(order_payload.get("platform"), order_payload.get("order_id"))
    return None


def extract_pdf_text(pdf_path: str | Path) -> str:
    return read_pdf_document(pdf_path, include_words=False).text


def process_pdf_file(
    source_pdf: str,
    pdf_path: str | Path,
    batch_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result = process_pdf_file_with_outcome(source_pdf, pdf_path, batch_id)
    return result.orders, result.products, result.reviews


def process_pdf_file_with_outcome(
    source_pdf: str,
    pdf_path: str | Path,
    batch_id: str,
) -> PdfProcessingResult:
    document = read_pdf_document_selective_words(
        pdf_path,
        should_include_words=lambda text: detect_platform(text) == "Shopee",
    )
    return _process_pdf_content_with_outcome(source_pdf, document.text, batch_id, document=document)


def process_pdf_text(
    source_pdf: str,
    text: str,
    batch_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result = process_pdf_text_with_outcome(source_pdf, text, batch_id)
    return result.orders, result.products, result.reviews


def process_pdf_text_with_outcome(
    source_pdf: str,
    text: str,
    batch_id: str,
) -> PdfProcessingResult:
    return _process_pdf_content_with_outcome(source_pdf, text, batch_id, document=None)


def _process_pdf_content_with_outcome(
    source_pdf: str,
    text: str,
    batch_id: str,
    document: PdfDocument | None,
) -> PdfProcessingResult:
    platform = detect_platform(text)
    if platform is None:
        return PdfProcessingResult(
            orders=[],
            products=[],
            reviews=[],
            unsupported_files=[
                create_file_outcome_record(
                    source_pdf=source_pdf,
                    status="Unsupported",
                    message="Not recognized as a supported invoice.",
                )
            ],
            processing_errors=[],
        )

    parser = PARSER_MAP.get(platform)
    if parser is None:
        return PdfProcessingResult(
            orders=[],
            products=[],
            reviews=[],
            unsupported_files=[],
            processing_errors=[
                create_file_outcome_record(
                    source_pdf=source_pdf,
                    status="Processing Error",
                    message=f"No dedicated parser registered for detected platform {platform}.",
                )
            ],
        )

    if platform == "Shopee" and document is not None and isinstance(parser, ShopeeParser):
        orders, products, reviews = parser.parse_document(document, source_pdf, batch_id)
    else:
        orders, products, reviews = parser.parse(text, source_pdf, batch_id)
    for review in reviews:
        review["timestamp"] = datetime.now(timezone.utc).isoformat()
        _normalize_review_payload(review)
    return PdfProcessingResult(
        orders=orders,
        products=products,
        reviews=reviews,
        unsupported_files=[],
        processing_errors=[],
    )


def apply_batch_rules(
    orders: list[dict[str, Any]],
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted_orders: list[dict[str, Any]] = []
    seen_orders: set[tuple[str, str]] = set()
    valid_keys: set[tuple[str, str]] = set()
    valid_order_sources: set[tuple[str, str, str]] = set()

    for order in orders:
        key = canonical_order_identity(order.get("platform"), order.get("order_id"))
        if key is None:
            platform = "" if order.get("platform") is None else str(order.get("platform")).strip()
            order_id = "" if order.get("order_id") is None else str(order.get("order_id")).strip()
            reviews.append(
                {
                    "batch_id": order.get("batch_id", ""),
                    "source_pdf": order.get("source_pdf", ""),
                    "platform": platform or "Unknown",
                    "order_id": order_id or "N/A",
                    "status": "Manual Review",
                    "reason": "Missing Platform or Order ID in parsed order-level data.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            continue
        platform, order_id = key
        if key in seen_orders:
            reviews.append(create_duplicate_order_record(order))
            continue
        seen_orders.add(key)
        valid_keys.add(key)
        source_pdf = str(order.get("source_pdf", "")).strip()
        if source_pdf:
            valid_order_sources.add((platform, order_id, source_pdf))
        accepted_orders.append(_normalize_order_record({**order, "platform": platform, "order_id": order_id}))

    merged_products: dict[tuple[str, str, str], dict[str, Any]] = {}
    for product in products:
        key = canonical_order_identity(product.get("platform"), product.get("order_id"))
        if key is None:
            continue
        platform, order_id = key
        source_pdf = str(product.get("source_pdf", "")).strip()
        if key not in valid_keys:
            continue
        if source_pdf and (platform, order_id, source_pdf) not in valid_order_sources:
            continue

        sku = str(product.get("seller_sku", "")).strip()
        product_name = str(product.get("product_name", "")).strip()
        quantity = parse_quantity(product.get("quantity"))
        sku_missing_in_source = platform == "Shopee" and bool(product.get("sku_missing_in_source"))
        if (not sku and not sku_missing_in_source) or not product_name or quantity <= 0:
            reviews.append(
                {
                    "batch_id": product.get("batch_id", ""),
                    "source_pdf": source_pdf,
                    "platform": platform or "Unknown",
                    "order_id": order_id or "N/A",
                    "status": "Manual Review",
                    "reason": "Missing or invalid product name, SKU, or quantity in product-level data.",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            continue

        merge_identity = sku or f"SOURCE-NO-SKU:{product_name}"
        merge_key = (platform, order_id, merge_identity)
        normalized_product = {**product, "platform": platform, "order_id": order_id}

        if merge_key not in merged_products:
            merged_products[merge_key] = _normalize_product_record(normalized_product)
            continue

        current = merged_products[merge_key]
        incoming = _normalize_product_record(normalized_product)
        current["quantity"] = parse_quantity(current.get("quantity")) + quantity
        current_total = parse_decimal(current.get("line_total")) + parse_decimal(incoming.get("line_total"))
        current["line_total"] = _money(current_total)
        current_unit = str(current.get("unit_price", "")).strip()
        incoming_unit = str(incoming.get("unit_price", "")).strip()
        if current_unit and incoming_unit and parse_decimal(current_unit) != parse_decimal(incoming_unit):
            current["unit_price"] = ""
        _sync_product_aliases(current)
        _apply_platform_placeholders(current, PLATFORM_PRODUCT_PLACEHOLDER_FIELDS)

    accepted_products = sorted(
        merged_products.values(),
        key=lambda row: (str(row.get("platform", "")), str(row.get("order_id", "")), str(row.get("product_name", ""))),
    )
    accepted_orders = sorted(
        accepted_orders,
        key=lambda row: (str(row.get("platform", "")), str(row.get("order_id", ""))),
    )

    return accepted_orders, accepted_products, reviews


def append_batch_results(
    existing_orders: list[dict[str, Any]],
    existing_products: list[dict[str, Any]],
    existing_reviews: list[dict[str, Any]],
    new_orders: list[dict[str, Any]],
    new_products: list[dict[str, Any]],
    new_reviews: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Combine an additional upload with the active batch and reapply its rules."""
    result = append_batch_results_with_metadata(
        existing_orders,
        existing_products,
        existing_reviews,
        new_orders,
        new_products,
        new_reviews,
    )
    return (
        result.orders,
        result.products,
        result.reviews,
    )


def append_batch_results_with_metadata(
    existing_orders: list[dict[str, Any]],
    existing_products: list[dict[str, Any]],
    existing_reviews: list[dict[str, Any]],
    new_orders: list[dict[str, Any]],
    new_products: list[dict[str, Any]],
    new_reviews: list[dict[str, Any]],
) -> BatchAppendResult:
    """Append one parser result and report order-level changes for this action."""
    # Intentional V1 limit: identity is only (canonical Platform, trimmed Order ID).
    # Newer status documents with the same identity are skipped; missing IDs have no identity.
    existing_identities = {
        identity
        for order in existing_orders
        if (identity := canonical_order_identity(order.get("platform"), order.get("order_id"))) is not None
    }
    existing_identities.update(
        identity
        for review in existing_reviews
        if is_manual_review_record(review) and (identity := _review_identity(review)) is not None
    )
    seen_identities = set(existing_identities)
    accepted_new_orders: list[dict[str, Any]] = []
    accepted_new_identities: set[tuple[str, str]] = set()
    duplicate_orders: list[dict[str, Any]] = []

    for order in new_orders:
        identity = canonical_order_identity(order.get("platform"), order.get("order_id"))
        if identity is not None and identity in seen_identities:
            duplicate_orders.append(create_duplicate_order_record(order))
            continue
        accepted_new_orders.append(order)
        if identity is not None:
            seen_identities.add(identity)
            accepted_new_identities.add(identity)

    accepted_new_products = [
        product
        for product in new_products
        if canonical_order_identity(product.get("platform"), product.get("order_id"))
        in accepted_new_identities
    ]

    accepted_new_reviews: list[dict[str, Any]] = []
    for review in new_reviews:
        if not is_manual_review_record(review):
            accepted_new_reviews.append(review)
            continue
        identity = _review_identity(review)
        if identity is not None and identity in seen_identities:
            duplicate_orders.append(create_duplicate_order_record(review))
            continue
        accepted_new_reviews.append(review)
        if identity is not None:
            seen_identities.add(identity)

    manual_review_count_before = sum(is_manual_review_record(review) for review in existing_reviews)
    combined_orders, combined_products, combined_reviews = apply_batch_rules(
        [*existing_orders, *accepted_new_orders],
        [*existing_products, *accepted_new_products],
        [*existing_reviews, *accepted_new_reviews],
    )
    combined_reviews.extend(duplicate_orders)
    manual_review_count_after = sum(is_manual_review_record(review) for review in combined_reviews)

    return BatchAppendResult(
        orders=combined_orders,
        products=combined_products,
        reviews=combined_reviews,
        duplicate_orders=duplicate_orders,
        imported_order_count=max(0, len(combined_orders) - len(existing_orders)),
        manual_review_count=max(0, manual_review_count_after - manual_review_count_before),
    )


def split_by_platform(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {platform: [] for platform in PLATFORMS}
    for row in rows:
        platform = canonical_platform_label(row.get("platform"))
        if platform in grouped:
            grouped[platform].append(row)
    return grouped


def _normalize_order_record(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized.pop("remarks", None)
    normalized.pop("adjustment_complete_date", None)
    normalized.pop("adjustment_reason", None)
    normalized.pop("released_amount", None)
    for field in _all_order_fields():
        normalized.setdefault(field, "")
    _sync_order_aliases(normalized)
    for field in (
        "gross_sales",
        "delivery_fee",
        "commission_fee",
        "service_fee",
        "transaction_fee",
        "voucher",
        "platform_fees",
        "ads_fee",
        "estimated_order_income",
        "net_income",
        "net_amount",
        "merchandise_subtotal",
        "product_price",
        "shipping_subtotal",
        "shipping_fee_paid_by_buyer",
        "shipping_fee_charged_by_logistic_provider",
        "shipping_fee_rebate_from_shopee",
        "seller_paid_shipping_fee_sst",
        "vouchers_rebates_total",
        "voucher_amount",
        "fees_charges_total",
        "ads_escrow_top_up_fee",
        "order_income",
        "final_amount",
        "buyer_merchandise_subtotal",
        "buyer_shipping_fee",
        "shopee_voucher",
        "seller_voucher",
        "total_buyer_payment",
        "subtotal",
        "voucher_applied",
        "total",
        "shipping_fee",
        "net_paid",
        "invoice_amount",
        "discount",
    ):
        normalized[field] = _normalize_money_value(normalized.get(field, ""))
    _sync_order_aliases(normalized)
    _apply_platform_placeholders(normalized, PLATFORM_ORDER_PLACEHOLDER_FIELDS)
    return normalized


def _normalize_product_record(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized.pop("remarks", None)
    for field in PRODUCT_LEVEL_FIELDS:
        normalized.setdefault(field, "")
    _sync_product_aliases(normalized)
    normalized["quantity"] = parse_quantity(normalized.get("quantity"))
    for field in ("unit_price", "line_total", "line_subtotal", "price", "paid_price", "line_total_inc_tax"):
        normalized[field] = _normalize_money_value(normalized.get(field, ""))
    _sync_product_aliases(normalized)
    _apply_platform_placeholders(normalized, PLATFORM_PRODUCT_PLACEHOLDER_FIELDS)
    return normalized


def _normalize_review_payload(review: dict[str, Any]) -> None:
    order_payload = review.get("order_payload")
    if isinstance(order_payload, dict):
        normalized_order = _normalize_order_record(order_payload)
        order_identity = canonical_order_identity(
            normalized_order.get("platform"),
            normalized_order.get("order_id"),
        )
        if order_identity is not None:
            normalized_order["platform"], normalized_order["order_id"] = order_identity
        normalized_order["status"] = "Manual Review"
        review["order_payload"] = normalized_order

    product_payloads = review.get("product_payloads")
    if not isinstance(product_payloads, list):
        return

    normalized_products: list[dict[str, Any]] = []
    for product_payload in product_payloads:
        if not isinstance(product_payload, dict):
            continue
        original_quantity = product_payload.get("quantity")
        normalized_product = _normalize_product_record(product_payload)
        product_identity = canonical_order_identity(
            normalized_product.get("platform"),
            normalized_product.get("order_id"),
        )
        if product_identity is not None:
            normalized_product["platform"], normalized_product["order_id"] = product_identity
        if original_quantity is None or str(original_quantity).strip() == "":
            normalized_product["quantity"] = MISSING_VALUE_PLACEHOLDER
        elif parse_quantity(original_quantity) <= 0:
            normalized_product["quantity"] = original_quantity
        normalized_product["status"] = "Manual Review"
        normalized_products.append(normalized_product)
    review["product_payloads"] = normalized_products


def _sync_order_aliases(row: dict[str, Any]) -> None:
    platform = str(row.get("platform", "")).strip()
    if platform == "Shopee":
        _copy_if_blank(row, "order_created_date", "invoice_date")
        _copy_if_blank(row, "invoice_date", "order_created_date")
        _copy_if_blank(row, "merchandise_subtotal", "gross_sales")
        _copy_if_blank(row, "gross_sales", "merchandise_subtotal")
        _copy_if_blank(row, "shipping_fee_paid_by_buyer", "delivery_fee")
        _copy_if_blank(row, "delivery_fee", "shipping_fee_paid_by_buyer")
        _copy_if_blank(row, "vouchers_rebates_total", "voucher")
        _copy_if_blank(row, "voucher", "vouchers_rebates_total")
        _copy_if_blank(row, "fees_charges_total", "platform_fees")
        _copy_if_blank(row, "platform_fees", "fees_charges_total")
        _copy_if_blank(row, "ads_escrow_top_up_fee", "ads_fee")
        _copy_if_blank(row, "ads_fee", "ads_escrow_top_up_fee")
        _copy_if_blank(row, "order_income", "estimated_order_income")
        _copy_if_blank(row, "estimated_order_income", "order_income")
        _copy_if_blank(row, "net_income", "order_income")
        _copy_if_blank(row, "net_amount", "final_amount")
        _copy_if_blank(row, "net_amount", "order_income")
        _copy_if_blank(row, "seller_voucher", "voucher_amount")
    elif platform == "Lazada":
        _copy_if_blank(row, "gross_sales", "subtotal")
        _copy_if_blank(row, "subtotal", "gross_sales")
        _copy_if_blank(row, "voucher", "voucher_applied")
        _copy_if_blank(row, "voucher_applied", "voucher")
        _copy_if_blank(row, "delivery_fee", "shipping_fee")
        _copy_if_blank(row, "shipping_fee", "delivery_fee")
        _copy_if_blank(row, "net_amount", "net_paid")
        _copy_if_blank(row, "net_paid", "net_amount")
        _copy_if_blank(row, "net_income", "net_paid")
        _copy_if_blank(row, "total", "total_amount")
        _copy_if_blank(row, "total_amount", "total")
    elif platform == "ZENXIN":
        _copy_if_blank(row, "gross_sales", "subtotal")
        _copy_if_blank(row, "subtotal", "gross_sales")
        _copy_if_blank(row, "voucher", "discount")
        _copy_if_blank(row, "discount", "voucher")
        _copy_if_blank(row, "delivery_fee", "shipping_fee")
        _copy_if_blank(row, "shipping_fee", "delivery_fee")
        _copy_if_blank(row, "net_amount", "total")
        _copy_if_blank(row, "total", "net_amount")
        _copy_if_blank(row, "net_income", "total")


def _sync_product_aliases(row: dict[str, Any]) -> None:
    platform = str(row.get("platform", "")).strip()
    if platform == "Shopee":
        if str(row.get("line_total", "")).strip() != "":
            row["line_subtotal"] = row["line_total"]
        _copy_if_blank(row, "line_subtotal", "line_total")
        _copy_if_blank(row, "line_total", "line_subtotal")
    elif platform == "Lazada":
        if str(row.get("unit_price", "")).strip() != "":
            row["price"] = row["unit_price"]
        if str(row.get("line_total", "")).strip() != "":
            row["paid_price"] = row["line_total"]
        _copy_if_blank(row, "price", "unit_price")
        _copy_if_blank(row, "unit_price", "price")
        _copy_if_blank(row, "paid_price", "line_total")
        _copy_if_blank(row, "line_total", "paid_price")
    elif platform == "ZENXIN":
        if str(row.get("line_total", "")).strip() != "":
            row["line_total_inc_tax"] = row["line_total"]
        _copy_if_blank(row, "line_total_inc_tax", "line_total")
        _copy_if_blank(row, "line_total", "line_total_inc_tax")


def _copy_if_blank(row: dict[str, Any], target: str, source: str) -> None:
    if str(row.get(target, "")).strip() == "" and str(row.get(source, "")).strip() != "":
        row[target] = row[source]


def _apply_platform_placeholders(row: dict[str, Any], platform_fields: dict[str, tuple[str, ...]]) -> None:
    platform = str(row.get("platform", "")).strip()
    for field in platform_fields.get(platform, ()):
        if str(row.get(field, "")).strip() == "":
            row[field] = MISSING_VALUE_PLACEHOLDER


def _normalize_money_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    if text == MISSING_VALUE_PLACEHOLDER:
        return MISSING_VALUE_PLACEHOLDER
    return _money(parse_decimal(text))


def _sum_money(rows: list[dict[str, Any]], field: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        value = str(row.get(field, "")).strip()
        if value == "":
            continue
        total += parse_decimal(value)
    return total


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _all_order_fields() -> list[str]:
    fields = list(ORDER_LEVEL_FIELDS)
    for platform_fields in PLATFORM_ORDER_FIELDS.values():
        for field in platform_fields:
            if field not in fields:
                fields.append(field)
    return fields


def _safe_archive_name(source_name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", source_name).strip(" ._")
    if not sanitized:
        sanitized = "invoice.pdf"
    if not sanitized.lower().endswith(".pdf"):
        sanitized = f"{sanitized}.pdf"
    if len(sanitized) <= 180:
        return sanitized

    suffix = Path(sanitized).suffix or ".pdf"
    return f"{sanitized[: 180 - len(suffix)]}{suffix}"


def _unique_archive_path(batch_dir: Path, filename: str) -> Path:
    target_path = batch_dir / filename
    if not target_path.exists():
        return target_path

    stem = target_path.stem
    suffix = target_path.suffix
    counter = 2
    while True:
        candidate = batch_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
