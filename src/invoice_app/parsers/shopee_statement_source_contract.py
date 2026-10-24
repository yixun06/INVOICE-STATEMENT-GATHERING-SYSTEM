"""Market-aware native Shopee Statement source contracts.

Contracts own only source-layout differences.  Parsed output stays in the
shared canonical Statement models so validation, provenance, and persistence
remain reusable across markets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SummaryLineSpec:
    native_label: str
    line_type: str
    parent_label: str | None
    label_column_index: int


@dataclass(frozen=True)
class ShopeeStatementSourceContract:
    key: str
    currency: str
    required_sheets: tuple[str, ...]
    optional_sheets: tuple[str, ...]
    total_released_header: str
    income_component_columns: tuple[str, ...]
    summary_line_specs: tuple[SummaryLineSpec, ...]
    shipping_discrepancy_required: bool
    adjustment_payout_date_required: bool
    summary_allows_auxiliary_values: bool = False


_MY_COMPONENTS = (
    "Product Price", "Refund Amount",
    "Shipping Fee Paid by Buyer (excl. SST)",
    "Shipping Fee Charged by Logistic Provider",
    "Seller Paid Shipping Fee SST", "Shipping Rebate From Shopee",
    "Reverse Shipping Fee", "Reverse Shipping Fee SST",
    "Saver Programme Shipping Fee Savings", "Return to Seller Fee",
    "Rebate Provided by Shopee", "Voucher Sponsored by Seller",
    "Cofund Voucher Sponsored by Seller", "Coin Cashback Sponsored by Seller",
    "Cofund Coin Cashback Sponsored by Seller", "Commission Fee (incl. SST)",
    "Service Fee (Incl. SST)", "Transaction Fee (Incl. SST)",
    "AMS Commission Fee", "Saver Programme Fee (Incl. SST)",
    "Ads Escrow Top Up Fee",
)

_MY_SUMMARY = (
    SummaryLineSpec("1. Total Revenue", "TOTAL", None, 0),
    SummaryLineSpec("Merchandise Subtotal", "SUBTOTAL", "1. Total Revenue", 0),
    SummaryLineSpec("Original product price", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Your Seller product promotion", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Refund Amount", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Voucher & Rebates", "SUBTOTAL", "1. Total Revenue", 0),
    SummaryLineSpec("Rebate Provided by Shopee", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Voucher Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Cofund Voucher Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Coin Cashback Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Cofund Coin Cashback Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("2. Total Expenses", "TOTAL", None, 0),
    SummaryLineSpec("Shipping Subtotal", "SUBTOTAL", "2. Total Expenses", 0),
    SummaryLineSpec("Shipping Fee Paid by Buyer (excl. SST)", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Shipping Fee Discount from 3PL", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Shipping Rebate From Shopee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Seller Paid Shipping Fee SST", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Actual Shipping Fee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Reverse Shipping Fee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Reverse Shipping Fee SST", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Saver Programme Shipping Fee Savings", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Return to Seller Shipping Fee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Fees & Charges", "SUBTOTAL", "2. Total Expenses", 0),
    SummaryLineSpec("AMS Commission Fee", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Commission Fee (incl. SST)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Service Fee (Incl. SST)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Saver Programme Fee (Incl. SST)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Transaction Fee (Incl. SST)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Ads Escrow Top Up Fee", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("3. Total Released Amount", "TOTAL", None, 0),
    SummaryLineSpec("Other Reference Values", "SECTION_HEADER", None, 0),
    SummaryLineSpec("Shipping Fee Promotion by Seller", "REFERENCE", "Other Reference Values", 0),
)

_SG_COMPONENTS = (
    "Product Price", "Beverage Container Return Scheme (BCRS) Deposit", "Refund Amount",
    "Shipping Fee Paid by Buyer", "Shipping Fee Charged by Logistic Provider",
    "Shipping Rebate From Shopee", "Reverse Shipping Fee",
    "Shipping Fee Saver Program Savings", "Return to Seller Fee",
    "Rebate Provided by Shopee", "Voucher Sponsored by Seller",
    "Cofund Voucher Sponsored by Seller", "Coin Cashback Sponsored by Seller",
    "Cofund Coin Cashback Sponsored by Seller", "Commission fee (Incl. GST)",
    "Service Fee (incl. GST)", "Transaction Fee (Incl. Gst)",
    "AMS Commission Fee", "Shipping Fee Saver Program Fee", "Ads Escrow Top Up Fee",
)

_SG_SUMMARY = (
    SummaryLineSpec("1. Total Revenue", "TOTAL", None, 0),
    SummaryLineSpec("Merchandise Subtotal", "SUBTOTAL", "1. Total Revenue", 0),
    SummaryLineSpec("Original product price", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Beverage Container Return Scheme (BCRS) Deposit", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Your Seller product promotion", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Refund Amount", "DETAIL", "Merchandise Subtotal", 1),
    SummaryLineSpec("Voucher & Rebates", "SUBTOTAL", "1. Total Revenue", 0),
    SummaryLineSpec("Rebate Provided by Shopee", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Voucher Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Cofund Voucher Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Coin Cashback Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("Cofund Coin Cashback Sponsored by Seller", "DETAIL", "Voucher & Rebates", 1),
    SummaryLineSpec("2. Total Expenses", "TOTAL", None, 0),
    SummaryLineSpec("Shipping Subtotal", "SUBTOTAL", "2. Total Expenses", 0),
    SummaryLineSpec("Shipping Fee Paid by Buyer", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Shipping Fee Discount from 3PL", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Shipping Rebate From Shopee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Actual Shipping Fee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Reverse Shipping Fee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Shipping Fee Saver Program Savings", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Return to Seller Shipping Fee", "DETAIL", "Shipping Subtotal", 1),
    SummaryLineSpec("Fees & Charges", "SUBTOTAL", "2. Total Expenses", 0),
    SummaryLineSpec("AMS Commission Fee", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Commission fee (Incl. GST)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Service Fee (incl. GST)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Shipping Fee Saver Program Fee", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Transaction Fee (Incl. Gst)", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Product Goods & Services Tax", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Shipping Goods & Services Tax", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("Ads Escrow Top Up Fee", "DETAIL", "Fees & Charges", 1),
    SummaryLineSpec("3. Total Released Amount", "TOTAL", None, 0),
    SummaryLineSpec("Other Reference Values", "SECTION_HEADER", None, 0),
    SummaryLineSpec("Amount Paid By Buyer", "REFERENCE", "Other Reference Values", 0),
    SummaryLineSpec("Shipping Fee Promotion by Seller", "REFERENCE", "Other Reference Values", 0),
)

SHOPEE_MY_STATEMENT_SOURCE = ShopeeStatementSourceContract(
    key="shopee_my", currency="RM",
    required_sheets=("Summary", "Income", "Service Fee Details", "Shipping Fee Discrepancy"),
    optional_sheets=("Adjustment",), total_released_header="Total Released Amount (RM)",
    income_component_columns=_MY_COMPONENTS, summary_line_specs=_MY_SUMMARY,
    shipping_discrepancy_required=True, adjustment_payout_date_required=True,
    summary_allows_auxiliary_values=False,
)

SHOPEE_SG_STATEMENT_SOURCE = ShopeeStatementSourceContract(
    key="shopee_sg", currency="S$",
    required_sheets=("Summary", "Income", "Service Fee Details"),
    optional_sheets=("Adjustment",), total_released_header="Total Released Amount (S$)",
    income_component_columns=_SG_COMPONENTS, summary_line_specs=_SG_SUMMARY,
    shipping_discrepancy_required=False, adjustment_payout_date_required=False,
    summary_allows_auxiliary_values=True,
)

_CONTRACTS: Mapping[str, ShopeeStatementSourceContract] = {
    SHOPEE_MY_STATEMENT_SOURCE.key: SHOPEE_MY_STATEMENT_SOURCE,
    SHOPEE_SG_STATEMENT_SOURCE.key: SHOPEE_SG_STATEMENT_SOURCE,
}


def resolve_statement_source_contract(key: str | ShopeeStatementSourceContract | None) -> ShopeeStatementSourceContract:
    if isinstance(key, ShopeeStatementSourceContract):
        return key
    selected = str(key or SHOPEE_MY_STATEMENT_SOURCE.key).strip().casefold()
    try:
        return _CONTRACTS[selected]
    except KeyError as error:
        raise ValueError(f"Unsupported Shopee Statement source contract: {key!r}.") from error
