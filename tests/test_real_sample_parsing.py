from decimal import Decimal
from pathlib import Path

import pytest
from src.invoice_app.pdf_document import read_pdf_document
from src.invoice_app.parsers.shopee_extractor import extract_shopee_data
from src.invoice_app.parsers.shopee_parser import ShopeeParser
from src.invoice_app.parsers.shopee_product_parser import parse_positioned_products
from src.invoice_app.parsers.shopee_review_policy import find_shopee_review_issue
from src.invoice_app.parsers.validation import validate_shopee_product_amounts
from src.invoice_app.services.batch_service import (
    apply_batch_rules,
    extract_pdf_text,
    process_pdf_file,
    process_pdf_text,
)


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "INVOICE TEST - Copy"
SHOPEE_SAMPLES = SAMPLES / "SHOPEE" / "Shopee"

SHOPEE_COMPLETENESS_REGRESSION_SAMPLES = (
    "260825A7T243W0.pdf",
    "260825AQXN44JQ.pdf",
    "260826AUAQ47G2.pdf",
    "260826BNADCPFV.pdf",
    "260826BX6A2ERP.pdf",
    "260826BXBA8HWE.pdf",
    "260826C06AHD34.pdf",
    "260826C081VHPF.pdf",
    "260826C1G2MMG9.pdf",
    "260826C22PN4TG.pdf",
    "260826C2QVQVAA.pdf",
    "260826C35718XB.pdf",
)
SHOPEE_COMPLETENESS_REGRESSION_ROOT = (
    ROOT / "tests" / "fixtures" / "shopee_completeness_20260826"
)


def extract_real_shopee_sample(folder: str, filename: str):
    pdf_path = SHOPEE_SAMPLES / folder / filename
    document = read_pdf_document(pdf_path)
    extracted = extract_shopee_data(
        document.text,
        pdf_path.name,
        parse_positioned_products(document),
    )
    return pdf_path, document, extracted


@pytest.mark.parametrize("filename", SHOPEE_COMPLETENESS_REGRESSION_SAMPLES)
def test_parse_real_shopee_to_ship_income_anchor_across_pages_regression(filename):
    pdf_path = SHOPEE_COMPLETENESS_REGRESSION_ROOT / filename
    document = read_pdf_document(pdf_path)
    extracted = extract_shopee_data(
        document.text,
        pdf_path.name,
        parse_positioned_products(document),
    )
    orders, products, reviews = ShopeeParser().parse_document(
        document,
        pdf_path.name,
        "batch-20260826-completeness",
    )

    assert len(document.pages) == 2
    assert any("Estimated Order Income" in page.text for page in document.pages)
    assert extracted.order_status == "To Ship"
    source_has_ads_escrow_fee = any("Ads Escrow Top Up Fee" in page.text for page in document.pages)
    assert (extracted.income["ads_escrow_top_up_fee"] != "N/A") is source_has_ads_escrow_fee

    assert find_shopee_review_issue(extracted) is None
    assert len(orders) == 1
    assert products
    assert reviews == []


def test_parse_real_lazada_sample():
    pdf_path = SAMPLES / "LAZADA" / "Lazada" / "LAZ 020326.pdf"
    text = extract_pdf_text(pdf_path)
    orders, products, reviews = process_pdf_text(pdf_path.name, text, "batch-real")

    assert len(orders) > 0
    assert len(products) > 0
    assert any(order["order_id"] == "501548767521442" for order in orders)
    assert any(
        product["order_id"] == "501548767521442"
        and product["seller_sku"] == "9555208106944-6"
        and product["line_total"] == "28.97"
        for product in products
    )
    assert len(reviews) < 6


def test_parse_real_shopee_incomplete_delivered_routes_to_manual_review():
    pdf_path, document, extracted = extract_real_shopee_sample(
        "08082026",
        "260807PYHX4FHP.pdf",
    )
    issue = find_shopee_review_issue(extracted)
    orders, products, reviews = ShopeeParser().parse_document(
        document,
        pdf_path.name,
        "batch-real",
    )

    assert extracted.order_status == "Delivered"
    assert extracted.order_id == "260807PYHX4FHP"
    assert extracted.income["merchandise_subtotal"] == "71.25"
    assert extracted.income["order_income"] == "N/A"
    assert extracted.income["income_type"] == "N/A"
    assert len(extracted.product_items) == 3
    assert {item["seller_sku"] for item in extracted.product_items} == {
        "9555208107934",
        "9555208107941",
        "9551031010663",
    }
    assert issue is not None
    assert "Estimated Order Income or Order Income" in issue.reason
    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert reviews[0]["status"] == "Manual Review"


def test_parse_real_shopee_complete_delivered_regression():
    pdf_path = SHOPEE_SAMPLES / "08082026" / "260807PYXCRSA7.pdf"
    orders, products, reviews = process_pdf_file(pdf_path.name, pdf_path, "batch-real")

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 1
    order = orders[0]
    assert order["platform"] == "Shopee"
    assert order["order_id"] == "260807PYXCRSA7"
    assert order["order_status"] == "Delivered"
    assert order["order_created_date"] == "07/08/2026 16:23"
    assert order["delivered_date"] == "10/08/2026 11:03"
    assert order["merchandise_subtotal"] == "6.97"
    assert order["product_price"] == "6.97"
    assert order["shipping_subtotal"] == "0.00"
    assert order["shipping_fee_paid_by_buyer"] == "0.00"
    assert order["shipping_fee_charged_by_logistic_provider"] == "-4.90"
    assert order["shipping_fee_rebate_from_shopee"] == "4.90"
    assert order["seller_paid_shipping_fee_sst"] == "0.00"
    assert order["fees_charges_total"] == "-2.57"
    assert order["commission_fee"] == "-0.83"
    assert order["service_fee"] == "-1.10"
    assert order["transaction_fee"] == "-0.26"
    assert order["ads_escrow_top_up_fee"] == "-0.38"
    assert order["order_income"] == "4.40"
    assert order["income_type"] == "Estimated"
    assert order["final_amount"] == "N/A"
    assert products[0]["product_name"] == "Simply Natural Organic Pearl Barley China (500g)"
    assert products[0]["seller_sku"] == "9555208106265"
    assert products[0]["quantity"] == 1
    assert products[0]["unit_price"] == "6.97"
    assert products[0]["line_subtotal"] == "6.97"


def test_parse_real_shopee_complete_shipped_regression():
    pdf_path = SHOPEE_SAMPLES / "08082026" / "260807PHEMKPJ3.pdf"
    orders, products, reviews = process_pdf_file(pdf_path.name, pdf_path, "batch-real")

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 1
    order = orders[0]
    assert order["platform"] == "Shopee"
    assert order["order_id"] == "260807PHEMKPJ3"
    assert order["order_status"] == "Shipped"
    assert order["order_created_date"] == "07/08/2026 12:04"
    assert order["merchandise_subtotal"] == "54.00"
    assert order["product_price"] == "54.00"
    assert order["shipping_subtotal"] == "0.00"
    assert order["shipping_fee_paid_by_buyer"] == "0.00"
    assert order["shipping_fee_charged_by_logistic_provider"] == "0.00"
    assert order["seller_paid_shipping_fee_sst"] == "0.00"
    assert order["vouchers_rebates_total"] == "-10.79"
    assert order["fees_charges_total"] == "-13.60"
    assert order["commission_fee"] == "-5.60"
    assert order["service_fee"] == "-4.04"
    assert order["transaction_fee"] == "-1.63"
    assert order["ads_escrow_top_up_fee"] == "-2.33"
    assert order["order_income"] == "29.61"
    assert order["income_type"] == "Estimated"
    assert order["final_amount"] == "N/A"
    assert products[0]["product_name"] == "Dr Ros Multi Drop 15ml 酵素万⽤滴"
    assert products[0]["seller_sku"] == "9555730900270"
    assert products[0]["quantity"] == 2
    assert products[0]["unit_price"] == "27.00"
    assert products[0]["line_subtotal"] == "54.00"


def test_parse_real_shopee_order_received_financials():
    pdf_path = SHOPEE_SAMPLES / "08082026" / "260807PYRY6NTE.pdf"
    orders, products, reviews = process_pdf_file(pdf_path.name, pdf_path, "batch-real")

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 5
    order = orders[0]
    assert order["platform"] == "Shopee"
    assert order["order_id"] == "260807PYRY6NTE"
    assert order["order_status"] == "Order Received"
    assert order["order_created_date"] == "07/08/2026 16:20"
    assert order["delivered_date"] == "09/08/2026 15:19"
    assert order["completed_date"] == "10/08/2026 10:05"
    assert order["fund_transfer_date"] == "10/08/2026 10:06"
    assert order["merchandise_subtotal"] == "70.87"
    assert order["product_price"] == "70.87"
    assert order["shipping_subtotal"] == "-6.68"
    assert order["shipping_fee_paid_by_buyer"] == "0.00"
    assert order["shipping_fee_charged_by_logistic_provider"] == "-6.30"
    assert order["seller_paid_shipping_fee_sst"] == "-0.38"
    assert order["vouchers_rebates_total"] == "-11.58"
    assert order["fees_charges_total"] == "-17.20"
    assert order["commission_fee"] == "-6.42"
    assert order["service_fee"] == "-5.34"
    assert order["transaction_fee"] == "-2.24"
    assert order["ads_escrow_top_up_fee"] == "-3.20"
    assert order["order_income"] == "35.41"
    assert order["income_type"] == "Final"
    assert order["final_amount"] == "35.41"
    assert order["buyer_merchandise_subtotal"] == "70.87"
    assert order["buyer_shipping_fee"] == "0.00"
    assert order["shopee_voucher"] == "-2.98"
    assert order["seller_voucher"] == "-11.58"
    assert order["total_buyer_payment"] == "56.31"
    assert products[0]["product_name"] == (
        "Simply Natural Organic Rolled Oats (Value Pack) 1000g Finland | 燕⻨⽚ | 有机燕⻨⽚"
    )
    assert products[0]["seller_sku"] == "9555208105022"
    assert products[0]["quantity"] == 1
    assert products[0]["unit_price"] == "18.00"
    assert products[0]["line_subtotal"] == "18.00"
    assert products[2]["product_name"] == "Better Gourmet Multigrain Ring 40g (HALAL)"
    assert products[2]["variation_name"] == "Onion"
    assert products[2]["seller_sku"] == "9555208018803"
    assert products[2]["quantity"] == 2
    assert products[2]["unit_price"] == "3.51"
    assert products[2]["line_subtotal"] == "7.02"


def test_parse_real_shopee_to_ship_without_final_amount():
    pdf_path = SHOPEE_SAMPLES / "13082026" / "2608126EM35NKX.pdf"
    orders, products, reviews = process_pdf_file(pdf_path.name, pdf_path, "batch-real")

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 1
    order = orders[0]
    assert order["platform"] == "Shopee"
    assert order["order_id"] == "2608126EM35NKX"
    assert order["order_status"] == "To Ship"
    assert order["order_created_date"] == "12/08/2026 16:17"
    assert order["merchandise_subtotal"] == "95.60"
    assert order["product_price"] == "95.60"
    assert order["shipping_subtotal"] == "-5.19"
    assert order["shipping_fee_paid_by_buyer"] == "0.00"
    assert order["shipping_fee_charged_by_logistic_provider"] == "-4.90"
    assert order["seller_paid_shipping_fee_sst"] == "-0.29"
    assert order["vouchers_rebates_total"] == "-6.00"
    assert order["fees_charges_total"] == "-22.80"
    assert order["commission_fee"] == "-8.71"
    assert order["service_fee"] == "-5.86"
    assert order["transaction_fee"] == "-3.39"
    assert order["ads_escrow_top_up_fee"] == "-4.84"
    assert order["order_income"] == "61.61"
    assert order["income_type"] == "Estimated"
    assert order["final_amount"] == "N/A"
    assert products[0]["product_name"] == (
        "Simply Natural G Seasoning Powder 170g 鲜味素G粉 | 零味精（MSG）| 低钠 | "
        "Vegan-friendly | less sodium"
    )
    assert products[0]["seller_sku"] == "9555208107736"
    assert products[0]["quantity"] == 4
    assert products[0]["unit_price"] == "23.90"
    assert products[0]["line_subtotal"] == "95.60"


def test_parse_real_shopee_promotional_bundle_allocation():
    pdf_path, document, extracted = extract_real_shopee_sample(
        "08082026",
        "260807QQJS16J4.pdf",
    )
    issue = find_shopee_review_issue(extracted)
    orders, products, reviews = ShopeeParser().parse_document(
        document,
        pdf_path.name,
        "batch-real",
    )

    assert extracted.order_status == "Order Received"
    assert extracted.income["merchandise_subtotal"] == "178.00"
    assert extracted.income["order_income"] == "N/A"
    assert extracted.income["income_type"] == "N/A"
    assert len(extracted.product_items) == 2
    assert {item["quantity"] for item in extracted.product_items} == {2}
    assert {item["unit_price"] for item in extracted.product_items} == {Decimal("58.80")}
    assert {item["source_group_total"] for item in extracted.product_items} == {Decimal("178.00")}
    assert {item["source_line_subtotal"] for item in extracted.product_items} == {None}
    assert {item["seller_sku"] for item in extracted.product_items} == {
        "9555208013969-New",
        "9555208013938",
    }
    assert {item["variation"] for item in extracted.product_items} == {"500ml"}
    assert any("蓝靛果汁" in item["product_name"] for item in extracted.product_items)
    assert any("沙棘果汁" in item["product_name"] for item in extracted.product_items)
    assert validate_shopee_product_amounts(
        list(extracted.product_items),
        extracted.income["merchandise_subtotal"],
    ) is None
    assert issue is not None
    assert "Estimated Order Income or Order Income" in issue.reason
    assert orders == []
    assert products == []
    assert len(reviews) == 1
    assert reviews[0]["status"] == "Manual Review"


def test_parse_real_shopee_without_source_sku_remains_accepted():
    pdf_path = SHOPEE_SAMPLES / "08082026" / "260808QURJ0K5F.pdf"
    orders, products, reviews = process_pdf_file(pdf_path.name, pdf_path, "batch-real")
    orders, products, reviews = apply_batch_rules(orders, products, reviews)

    assert reviews == []
    assert len(orders) == 1
    assert len(products) == 1
    assert products[0]["seller_sku"] == "N/A"
    assert products[0]["quantity"] == 4
    assert products[0]["line_total"] == "83.60"
    assert "remarks" not in products[0]


def test_parse_real_zenxin_multi_page_sample():
    pdf_path = SAMPLES / "ZENXIN" / "zenxin.com.my" / "_Invoice_161821.pdf"
    text = extract_pdf_text(pdf_path)
    orders, products, reviews = process_pdf_text(pdf_path.name, text, "batch-real")

    assert len(orders) == 1
    assert reviews == []
    assert len(products) == 11
    assert all(product["order_id"] == "10123" for product in products)
    assert any(
        product["seller_sku"] == "3000309" and product["quantity"] == 3 and product["line_total"] == "35.70"
        for product in products
    )
