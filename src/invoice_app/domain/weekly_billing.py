"""Persistence-neutral Weekly Billing result models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class ActualSellingAmountBasis(str, Enum):
    DIRECT = "DIRECT"
    PROMOTION_SAME_PRICE_ALLOCATED = "PROMOTION_SAME_PRICE_ALLOCATED"
    PROMOTION_MIXED_PRICE_WEIGHTED_ALLOCATED = (
        "PROMOTION_MIXED_PRICE_WEIGHTED_ALLOCATED"
    )


@dataclass(frozen=True, order=True)
class BillingPeriod:
    statement_period_from: date
    statement_period_to: date
    statement_batch_id: str
    statement_file_hash: str

    @property
    def label(self) -> str:
        return (
            f"{self.statement_period_from.isoformat()} → "
            f"{self.statement_period_to.isoformat()}"
        )


@dataclass(frozen=True)
class BillingSourceItem:
    order_id: str
    item_index: int
    nav: str
    seller_sku: str | None
    product_name: str
    uom: str | None
    historical_pm_unit_price: Decimal
    quantity: int
    actual_selling_amount: Decimal
    actual_selling_amount_basis: ActualSellingAmountBasis
    promotion_group_id: str | None = None
    source_group_total: Decimal | None = None
    variation: str = ""

    @property
    def standard_amount(self) -> Decimal:
        return self.historical_pm_unit_price * self.quantity

    @property
    def discount_amount(self) -> Decimal:
        return self.standard_amount - self.actual_selling_amount


@dataclass(frozen=True)
class ProductSummaryRow:
    number: int
    nav: str
    product_name: str
    uom: str | None
    unit_price: Decimal
    quantity: int
    discount_percent: Decimal | None
    discount_amount: Decimal
    amount: Decimal
    source_item_count: int


@dataclass(frozen=True)
class StagingDataRow:
    """One ERP-facing row mapped from one finalized Product Summary row."""

    your_reference: str
    posting_date: date
    sell_to_customer_no: str
    currency_code: str
    item_type: str
    nav: str
    location_code: str
    quantity: int
    unit_of_measure_code: str
    order_date: date
    shipment_date: date
    customer_outlet_code: None
    business_unit_code_erp: str
    project_code_erp: str
    external_doc_no: int
    unit_price_excl_gst: Decimal
    ship_to_code: int


@dataclass(frozen=True)
class WeeklyBillingSummary:
    period: BillingPeriod
    order_count: int
    invoice_item_count: int
    source_items: tuple[BillingSourceItem, ...]
    product_rows: tuple[ProductSummaryRow, ...]
    total_quantity: int
    total_standard_amount: Decimal
    total_discount_amount: Decimal
    total_amount: Decimal
    normal_amount_total: Decimal
    promotion_amount_total: Decimal
    promotion_group_count: int
    same_price_promotion_count: int
    mixed_price_promotion_count: int


@dataclass(frozen=True)
class FinancialSummaryRow:
    """One native Shopee Summary line, ready for Billing display/export."""

    statement_source_row_number: int
    native_label: str
    line_type: str
    parent_source_row_number: int | None
    amount: Decimal | None
    currency: str


@dataclass(frozen=True)
class FinancialControl:
    """One independently-derived ORDER-ledger control against native Summary."""

    name: str
    derived_amount: Decimal
    native_amount: Decimal
    passed: bool


@dataclass(frozen=True)
class WeeklyBillingFinancialSummary:
    period: BillingPeriod
    currency: str
    rows: tuple[FinancialSummaryRow, ...]
    controls: tuple[FinancialControl, ...]
    export_ready: bool
    validation_failures: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyBillingReport:
    """The one selected committed Statement batch rendered by Billing."""

    product_summary: WeeklyBillingSummary
    financial_summary: WeeklyBillingFinancialSummary
