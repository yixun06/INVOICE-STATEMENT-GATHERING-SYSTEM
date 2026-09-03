from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.invoice_app.review_reason_codes import (
    INCOMPLETE_PROMOTION_EVIDENCE,
    INCOME_COMPLETION_ANCHOR_MISSING,
    NO_VALID_PRODUCTS,
    PRODUCT_AMOUNT_RECONCILIATION_FAILED,
    PRODUCT_COUNT_MISMATCH,
)
from src.invoice_app.services.all_products import (
    partition_cross_platform_product_summary_rows,
    summarize_cross_platform_products,
)


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _reporting_row(order_id: str, seller_sku: str, amount: str) -> dict:
    return {
        "platform": "Shopee",
        "order_id": order_id,
        "product_name": f"Product {seller_sku}",
        "seller_sku": seller_sku,
        "quantity": 1,
        "reporting_unit_selling_price": Decimal(amount),
        "reporting_actual_selling_value": Decimal(amount),
        "reporting_discount_given": Decimal("0.00"),
        "reporting_order_created_date": date(2026, 8, 20),
    }


def _review(order_id: str, reason_code: str, reason: str | None = None) -> dict:
    return {
        "platform": "Shopee",
        "order_id": order_id,
        "status": "Manual Review",
        "reason_code": reason_code,
        "reason": reason or reason_code,
        "source_pdf": f"{order_id}.pdf",
        "order_payload": {"order_created_date": "20/08/2026"},
    }


def test_product_summary_exclusion_codes_remove_entire_orders_without_zero_filling():
    rows = [
        _reporting_row("COUNT", "SKU-COUNT", "10.00"),
        _reporting_row("AMOUNT", "SKU-AMOUNT", "20.00"),
        _reporting_row("PROMO", "SKU-PROMO", "30.00"),
        _reporting_row("NO-PRODUCT", "SKU-NO-PRODUCT", "40.00"),
        _reporting_row("INCOME", "SKU-INCOME", "50.00"),
    ]
    reviews = [
        _review("COUNT", PRODUCT_COUNT_MISMATCH),
        _review("AMOUNT", PRODUCT_AMOUNT_RECONCILIATION_FAILED),
        _review("PROMO", INCOMPLETE_PROMOTION_EVIDENCE),
        _review("NO-PRODUCT", NO_VALID_PRODUCTS),
        _review("INCOME", INCOME_COMPLETION_ANCHOR_MISSING),
    ]

    summary_rows, exclusions = partition_cross_platform_product_summary_rows(rows, reviews)
    summary = summarize_cross_platform_products(summary_rows)

    assert [row["order_id"] for row in summary_rows] == ["INCOME"]
    assert summary == [
        {
            "seller_sku": "SKU-INCOME",
            "product_name": "Product SKU-INCOME",
            "unit_selling_price": Decimal("50.00"),
            "total_quantity": 1,
            "total_selling_price": Decimal("50.00"),
            "total_discount_given": Decimal("0.00"),
        }
    ]
    assert [row["order_id"] for row in exclusions] == ["AMOUNT", "COUNT", "NO-PRODUCT", "PROMO"]
    assert all(row["source_pdf"].endswith(".pdf") for row in exclusions)


def test_product_summary_exclusion_listing_deduplicates_multiple_reason_codes_per_order():
    rows = [_reporting_row("MULTI", "SKU-MULTI", "15.00")]
    reviews = [
        _review("MULTI", PRODUCT_COUNT_MISMATCH, "Product count is incomplete."),
        _review("MULTI", INCOMPLETE_PROMOTION_EVIDENCE, "Promotion evidence is incomplete."),
    ]

    summary_rows, exclusions = partition_cross_platform_product_summary_rows(rows, reviews)

    assert summary_rows == []
    assert len(exclusions) == 1
    assert exclusions[0]["order_id"] == "MULTI"
    assert exclusions[0]["reason_code"] == (
        f"{PRODUCT_COUNT_MISMATCH}, {INCOMPLETE_PROMOTION_EVIDENCE}"
    )
    assert exclusions[0]["reason"] == "Product count is incomplete.\nPromotion evidence is incomplete."


def test_cross_platform_summary_renders_one_excluded_order_with_pdf_action(monkeypatch, tmp_path):
    from src.invoice_app.services import batch_service

    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", archive_dir)
    batch_id = "summary-exclusion-batch"
    source_pdf = "excluded.pdf"
    source_path = archive_dir / batch_id / source_pdf
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF-test")

    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {"platform": "Shopee", "order_id": "OK", "order_created_date": "20/08/2026"}
    ]
    app.session_state["products"] = [
        {
            "platform": "Shopee",
            "order_id": "OK",
            "product_name": "Included product",
            "seller_sku": "SKU-OK",
            "quantity": 1,
            "line_subtotal": "10.00",
        }
    ]
    app.session_state["reviews"] = [
        {
            **_review("EXCLUDED", PRODUCT_COUNT_MISMATCH, "Product count mismatch."),
            "batch_id": batch_id,
            "source_pdf": source_pdf,
            "product_payloads": [
                {
                    "platform": "Shopee",
                    "order_id": "EXCLUDED",
                    "product_name": "Excluded product",
                    "seller_sku": "SKU-EXCLUDED",
                    "quantity": 1,
                    "line_subtotal": "10.00",
                }
            ],
        }
    ]
    app.session_state["batch_id"] = batch_id
    app.session_state["pdf_count"] = 2
    app.session_state["navigation"] = "Cross Platform Summary"

    app.run(timeout=20)

    assert app.exception == []
    assert "Excluded from Product Summary" in {element.value for element in app.subheader}
    assert any("EXCLUDED" in element.value for element in app.markdown)
    assert any("Product count mismatch." in element.value for element in app.caption)
    assert "View PDF" in {button.label for button in app.get("download_button")}


def test_cross_platform_summary_shows_missing_sku_rows_with_actual_sales_and_pdf(monkeypatch, tmp_path):
    from src.invoice_app.services import batch_service

    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(batch_service, "ARCHIVE_DIR", archive_dir)
    batch_id = "missing-sku-batch"
    source_pdf = "missing-sku.pdf"
    source_path = archive_dir / batch_id / source_pdf
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF-test")

    app = AppTest.from_file(str(APP_PATH))
    app.session_state["authenticated"] = True
    app.session_state["orders"] = [
        {"platform": "Lazada", "order_id": "MISSING-SKU", "order_date": "20 08 2026"},
        {"platform": "Lazada", "order_id": "INCLUDED", "order_date": "20 08 2026"},
    ]
    app.session_state["products"] = [
        {
            "platform": "Lazada",
            "order_id": "MISSING-SKU",
            "product_name": "Needs seller SKU",
            "variation": "Size M",
            "seller_sku": "",
            "quantity": 2,
            "unit_price": "8.00",
            "paid_price": "12.50",
            "source_pdf": source_pdf,
        },
        {
            "platform": "Lazada",
            "order_id": "INCLUDED",
            "product_name": "Included product",
            "seller_sku": "SKU-INCLUDED",
            "quantity": 1,
            "unit_price": "10.00",
            "paid_price": "10.00",
            "source_pdf": "included.pdf",
        },
    ]
    app.session_state["reviews"] = []
    app.session_state["batch_id"] = batch_id
    app.session_state["pdf_count"] = 2
    app.session_state["navigation"] = "Cross Platform Summary"

    app.run(timeout=20)

    assert app.exception == []
    assert "Missing SKU / Not included in Product Summary" in {
        element.value for element in app.subheader
    }
    frames_by_columns = {tuple(frame.value.columns): frame.value for frame in app.dataframe}
    summary = frames_by_columns[
        ("Seller SKU", "Product Name", "Unit Selling Price", "Total Quantity", "Total Selling Price", "Total Discount Given")
    ]
    assert summary["Seller SKU"].tolist() == ["SKU-INCLUDED"]
    assert summary["Total Quantity"].tolist() == [1]
    assert summary["Total Selling Price"].tolist() == [10.0]
    missing_sku = frames_by_columns[
        ("Order ID", "Order Created Date", "Product Name", "Variation", "Quantity", "Actual Selling Value", "Reason")
    ]
    assert missing_sku["Order ID"].tolist() == ["MISSING-SKU"]
    assert missing_sku["Product Name"].tolist() == ["Needs seller SKU"]
    assert missing_sku["Variation"].tolist() == ["Size M"]
    assert missing_sku["Quantity"].tolist() == [2]
    assert missing_sku["Actual Selling Value"].tolist() == [12.5]
    assert missing_sku["Reason"].tolist() == ["Missing Seller SKU"]
    assert "INCLUDED" not in missing_sku["Order ID"].tolist()
    assert "View PDF" in {button.label for button in app.get("download_button")}
