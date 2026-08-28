from __future__ import annotations

from decimal import Decimal
from typing import Any

from .shopee_extractor import ShopeeExtractedData
from .shopee_financial_parser import (
    MISSING_FINANCIAL_VALUE,
    calculate_platform_fees,
    is_missing_financial_value,
)


SHOPEE_PLATFORM = "Shopee"


def resolve_shopee_payment_status(
    fund_transfer_date: str | None,
    income_type: str | None,
) -> str:
    """Derive Shopee's transfer state without implying bank receipt."""
    if _has_source_value(fund_transfer_date):
        return "Released"
    if str(income_type or "").strip().casefold() == "estimated":
        return "Pending"
    return MISSING_FINANCIAL_VALUE


def map_shopee_records(
    data: ShopeeExtractedData,
    batch_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return map_shopee_order(data, batch_id), map_shopee_products(data, batch_id)


def map_shopee_review_payloads(
    data: ShopeeExtractedData,
    batch_id: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Package already-extracted source values without deriving missing amounts."""
    delivery_fee = _financial_value(data.income, "shipping_fee_paid_by_buyer")
    income_type = _financial_value(data.income, "income_type")
    order_payload = None
    if data.order_id:
        order_payload = {
            "batch_id": batch_id,
            "platform": SHOPEE_PLATFORM,
            "order_id": data.order_id,
            "delivery_fee": delivery_fee,
            "shipping_fee_paid_by_buyer": delivery_fee,
            "fund_transfer_date": data.fund_transfer_date,
            "income_type": income_type,
            "payment_status": resolve_shopee_payment_status(data.fund_transfer_date, income_type),
            "source_pdf": data.source_pdf,
            "status": "Manual Review",
        }
    return order_payload, map_shopee_products(
        data,
        batch_id,
        derive_missing_amounts=False,
        status="Manual Review",
    )


def map_shopee_order(data: ShopeeExtractedData, batch_id: str) -> dict[str, Any]:
    income = data.income
    buyer_payment = data.buyer_payment
    voucher = data.voucher
    order_income = _financial_value(income, "order_income")
    income_type = _financial_value(income, "income_type")
    estimated_income = _financial_value(income, "estimated_order_income")
    final_amount = _financial_value(income, "final_amount")
    current_income = _first_present_financial_value(final_amount, order_income, estimated_income)

    return {
        "batch_id": batch_id,
        "platform": SHOPEE_PLATFORM,
        "order_id": data.order_id,
        "order_status": data.order_status,
        "invoice_number": "",
        "order_created_date": data.order_created_date,
        "invoice_date": data.order_created_date,
        "delivered_date": data.delivered_date,
        "completed_date": data.completed_date,
        "fund_transfer_date": data.fund_transfer_date,
        "source_pdf": data.source_pdf,
        "gross_sales": _financial_value(income, "merchandise_subtotal"),
        "delivery_fee": _financial_value(income, "shipping_fee_paid_by_buyer"),
        "commission_fee": _financial_value(income, "commission_fee"),
        "service_fee": _financial_value(income, "service_fee"),
        "transaction_fee": _financial_value(income, "transaction_fee"),
        "voucher": _financial_value(income, "vouchers_rebates_total"),
        "platform_fees": calculate_platform_fees(income),
        "ads_fee": _financial_value(income, "ads_escrow_top_up_fee"),
        "estimated_order_income": order_income,
        "net_income": current_income,
        "net_amount": current_income,
        "merchandise_subtotal": _financial_value(income, "merchandise_subtotal"),
        "product_price": _financial_value(income, "product_price"),
        "shipping_subtotal": _financial_value(income, "shipping_subtotal"),
        "shipping_fee_paid_by_buyer": _financial_value(income, "shipping_fee_paid_by_buyer"),
        "shipping_fee_charged_by_logistic_provider": _financial_value(
            income, "shipping_fee_charged_by_logistic_provider"
        ),
        "shipping_fee_rebate_from_shopee": _financial_value(
            income, "shipping_fee_rebate_from_shopee"
        ),
        "seller_paid_shipping_fee_sst": _financial_value(income, "seller_paid_shipping_fee_sst"),
        "vouchers_rebates_total": _financial_value(income, "vouchers_rebates_total"),
        "voucher_type": _financial_value(voucher, "voucher_type"),
        "voucher_code": _financial_value(voucher, "voucher_code"),
        "voucher_funded_by": _financial_value(voucher, "voucher_funded_by"),
        "voucher_amount": _financial_value(voucher, "voucher_amount"),
        "fees_charges_total": _financial_value(income, "fees_charges_total"),
        "ads_escrow_top_up_fee": _financial_value(income, "ads_escrow_top_up_fee"),
        "order_income": order_income,
        "income_type": income_type,
        "payment_status": resolve_shopee_payment_status(data.fund_transfer_date, income_type),
        "final_amount": final_amount,
        "buyer_merchandise_subtotal": _financial_value(
            buyer_payment, "buyer_merchandise_subtotal"
        ),
        "buyer_shipping_fee": _financial_value(buyer_payment, "buyer_shipping_fee"),
        "shopee_voucher": _financial_value(buyer_payment, "shopee_voucher"),
        "seller_voucher": _financial_value(buyer_payment, "seller_voucher"),
        "total_buyer_payment": _financial_value(buyer_payment, "total_buyer_payment"),
        "status": "Accepted",
    }


def _financial_value(values: dict[str, str], field: str) -> str:
    value = values.get(field)
    return MISSING_FINANCIAL_VALUE if is_missing_financial_value(value) else str(value).strip()

def _has_source_value(value: str | None) -> bool:
    return value is not None and str(value).strip() not in {"", MISSING_FINANCIAL_VALUE}



def _first_present_financial_value(*values: str) -> str:
    for value in values:
        if not is_missing_financial_value(value):
            return value
    return MISSING_FINANCIAL_VALUE


def map_shopee_products(
    data: ShopeeExtractedData,
    batch_id: str,
    *,
    derive_missing_amounts: bool = True,
    status: str = "Accepted",
) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for item in data.product_items:
        quantity = item.get("quantity", "")
        unit_price_value = item.get("unit_price")
        line_total_value = item.get("line_total")
        if derive_missing_amounts:
            quantity = int(quantity)
            unit_price = unit_price_value or Decimal("0")
            line_total = line_total_value or Decimal("0")
            if unit_price == 0 and line_total != 0:
                unit_price = line_total / Decimal(quantity)
            elif line_total == 0 and unit_price != 0:
                line_total = unit_price * Decimal(quantity)
            unit_price_text = str(unit_price.quantize(Decimal("0.01")))
            line_total_text = str(line_total.quantize(Decimal("0.01")))
        else:
            unit_price_text = _source_product_money(unit_price_value)
            line_total_text = _source_product_money(line_total_value)

        product = {
            "batch_id": batch_id,
            "platform": SHOPEE_PLATFORM,
            "order_id": data.order_id,
            "invoice_date": data.order_created_date,
            "order_created_date": data.order_created_date,
            "product_name": item["product_name"],
            "seller_sku": item["seller_sku"],
            "quantity": quantity,
            "unit_price": unit_price_text,
            "line_total": line_total_text,
            "line_subtotal": line_total_text,
            "sku_missing_in_source": bool(item.get("sku_missing_in_source")),
            "source_pdf": data.source_pdf,
            "status": status,
        }
        product.update(_promotion_source_metadata(item))
        products.append(product)
    return products


def _source_product_money(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return MISSING_FINANCIAL_VALUE
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    return str(value).strip()


def _promotion_source_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Expose parser source evidence without changing legacy product amounts."""
    metadata: dict[str, Any] = {}
    if "source_line_subtotal" in item:
        metadata["source_line_subtotal"] = _source_product_money(item.get("source_line_subtotal"))

    for field in ("promotion_group_id", "promotion_label", "promotion_metadata_status"):
        if str(item.get(field, "")).strip():
            metadata[field] = str(item[field]).strip()
    if item.get("promotion_group_total") is not None:
        metadata["promotion_group_total"] = _source_product_money(item["promotion_group_total"])
    for field in ("promotion_target_qty", "promotion_member_qty"):
        if field in item:
            metadata[field] = item[field]
    if str(item.get("promotion_incomplete_reason", "")).strip():
        metadata["promotion_incomplete_reason"] = str(item["promotion_incomplete_reason"]).strip()
    return metadata
