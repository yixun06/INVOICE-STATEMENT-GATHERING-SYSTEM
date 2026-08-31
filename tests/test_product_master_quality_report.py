from __future__ import annotations

from src.invoice_app.services.product_master_quality_report import (
    LOOKUP_STATUS_CONFLICT,
    LOOKUP_STATUS_MATCHED,
    LOOKUP_STATUS_NOT_FOUND,
    build_product_master_quality_report,
    summarize_product_master_quality_report,
    write_product_master_quality_report_csv,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster


def _master(rows: list[dict[str, object]]) -> ProductPriceMaster:
    return ProductPriceMaster.from_rows(rows)


def test_report_combines_exact_sku_and_parent_candidates_and_preserves_leading_zero() -> None:
    report = build_product_master_quality_report(
        [
            {
                "platform": "Shopee",
                "order_id": "ORDER-1",
                "seller_sku": "00123",
                "product_name": "Apple",
                "variation_name": "Red",
            }
        ],
        _master(
            [
                {
                    "seller_sku": "00123",
                    "parent_sku": "P-ONE",
                    "product_name": "Apple",
                    "variation_name": "Red",
                    "unit_selling_price": "5.50",
                },
                {
                    "seller_sku": "CHILD-2",
                    "parent_sku": "00123",
                    "product_name": "Apple",
                    "variation_name": "Green",
                    "unit_selling_price": "5.50",
                },
            ]
        ),
    )

    row = report[0]
    assert row.invoice_seller_sku == "00123"
    assert row.matched_via == "Both"
    assert row.candidate_count == 2
    assert row.unique_candidate_prices == "5.50"
    assert row.lookup_status == LOOKUP_STATUS_MATCHED
    assert row.master_source_rows == "1; 2"



def test_report_labels_parent_sku_only_candidates() -> None:
    master = _master(
        [
            {
                "seller_sku": "CHILD-ONLY",
                "parent_sku": "PARENT-ONLY",
                "product_name": "Apple",
                "variation_name": "Red",
                "unit_selling_price": "5.50",
            }
        ]
    )
    report = build_product_master_quality_report(
        [
            {
                "platform": "Shopee",
                "order_id": "ORDER-PARENT",
                "seller_sku": "PARENT-ONLY",
            }
        ],
        master,
    )

    row = report[0]
    assert row.matched_via == "Parent SKU"
    assert row.candidate_count == 1
    assert row.lookup_status == LOOKUP_STATUS_MATCHED
    assert row.master_parent_sku == "PARENT-ONLY"

def test_report_resolves_different_prices_only_with_exact_normalized_name_variation() -> None:
    master = _master(
        [
            {
                "seller_sku": "SKU-1",
                "product_name": "Tea  Box",
                "variation_name": "Large",
                "unit_selling_price": "10.00",
            },
            {
                "seller_sku": "SKU-1",
                "product_name": "Tea Box",
                "variation_name": "Small",
                "unit_selling_price": "8.00",
            },
        ]
    )
    report = build_product_master_quality_report(
        [
            {
                "platform": "Shopee",
                "order_id": "ORDER-2",
                "seller_sku": "SKU-1",
                "product_name": " tea box ",
                "variation": "large",
            }
        ],
        master,
    )

    row = report[0]
    assert row.lookup_status == LOOKUP_STATUS_MATCHED
    assert row.candidate_count == 2
    assert row.unique_candidate_prices == "8.00; 10.00"
    assert "uniquely resolved" in row.reason


def test_report_distinguishes_conflict_not_found_and_name_variation_mismatch() -> None:
    master = _master(
        [
            {
                "seller_sku": "CONFLICT",
                "product_name": "Same Name",
                "variation_name": "One",
                "unit_selling_price": "4.00",
            },
            {
                "seller_sku": "CONFLICT",
                "product_name": "Same Name",
                "variation_name": "Two",
                "unit_selling_price": "6.00",
            },
            {
                "seller_sku": "MISMATCH",
                "product_name": "Master Name",
                "variation_name": "Master Variation",
                "unit_selling_price": "7.00",
            },
        ]
    )
    report = build_product_master_quality_report(
        [
            {
                "platform": "Shopee",
                "order_id": "ORDER-3",
                "seller_sku": "CONFLICT",
                "product_name": "Same Name",
            },
            {
                "platform": "Shopee",
                "order_id": "ORDER-4",
                "seller_sku": "MISSING",
                "product_name": "No Master",
            },
            {
                "platform": "Shopee",
                "order_id": "ORDER-5",
                "seller_sku": "MISMATCH",
                "product_name": "Invoice Name",
                "variation_name": "Invoice Variation",
            },
        ],
        master,
    )

    assert [row.lookup_status for row in report] == [
        LOOKUP_STATUS_CONFLICT,
        LOOKUP_STATUS_NOT_FOUND,
        LOOKUP_STATUS_MATCHED,
    ]
    assert "Invoice Product Name does not exactly match" in report[2].reason
    assert "Invoice Variation does not exactly match" in report[2].reason

    summary = summarize_product_master_quality_report(report)
    assert summary.total_product_rows_checked == 3
    assert summary.matched_count == 1
    assert summary.price_not_found_count == 1
    assert summary.pricing_conflict_count == 1
    assert summary.top_conflict_skus == (("CONFLICT", 1),)
    assert summary.top_not_found_skus == (("MISSING", 1),)
    assert summary.name_or_variation_mismatch_rows == (report[2],)


def test_report_writer_uses_requested_csv_destination(tmp_path) -> None:
    report = build_product_master_quality_report(
        [{"platform": "Shopee", "order_id": "ORDER-6", "seller_sku": "NONE"}],
        _master([]),
    )
    destination = write_product_master_quality_report_csv(report, tmp_path / "quality.csv")

    contents = destination.read_text(encoding="utf-8-sig")
    assert "Invoice Seller SKU" in contents
    assert "PRICE_NOT_FOUND" in contents
