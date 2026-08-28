from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any


@dataclass
class OrderRecord:
    batch_id: str
    platform: str
    order_id: str
    invoice_number: str = ""
    invoice_date: str = ""
    gross_sales: Decimal | str = ""
    delivery_fee: Decimal | str = ""
    commission_fee: Decimal | str = ""
    service_fee: Decimal | str = ""
    transaction_fee: Decimal | str = ""
    voucher: Decimal | str = ""
    platform_fees: Decimal | str = ""
    ads_fee: Decimal | str = ""
    estimated_order_income: Decimal | str = ""
    net_income: Decimal | str = ""
    net_amount: Decimal | str = ""
    source_pdf: str = ""
    status: str = "Accepted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductRecord:
    batch_id: str
    platform: str
    order_id: str
    invoice_number: str = ""
    invoice_date: str = ""
    product_name: str = ""
    seller_sku: str = ""
    quantity: int = 0
    unit_price: Decimal | str = ""
    line_total: Decimal | str = ""
    source_pdf: str = ""
    status: str = "Accepted"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["quantity"] = int(self.quantity or 0)
        return data


@dataclass
class ReviewRecord:
    batch_id: str
    source_pdf: str
    platform: str
    order_id: str
    status: str
    reason: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
