"""Exact UAT2 Google Sheets schemas shared by persistence and migration code."""

from __future__ import annotations


INVOICE_ORDERS_TAB = "Invoice_Orders"
INVOICE_ITEMS_TAB = "Invoice_Items"
STATEMENT_DATA_TAB = "Statement_Data"

# This is retained only for the one-time, explicitly approved schema migration.
LEGACY_INVOICE_ORDERS_HEADERS = (
    "platform", "order_id", "order_status", "order_created_date", "delivered_date", "completed_date", "fund_transfer_date", "merchandise_subtotal", "product_price", "shipping_subtotal", "shipping_fee_paid_by_buyer", "shipping_fee_charged_by_logistic_provider", "shipping_fee_rebate_from_shopee", "seller_paid_shipping_fee_sst", "vouchers_rebates_total", "voucher_type", "voucher_code", "voucher_funded_by", "voucher_amount", "commission_fee", "service_fee", "transaction_fee", "ads_escrow_top_up_fee", "fees_charges_total", "order_income", "income_type", "final_amount", "refund_amount", "buyer_merchandise_subtotal", "buyer_shipping_fee", "shopee_voucher", "seller_voucher", "total_buyer_payment", "payment_status", "payout_completed_date", "source_pdf", "source_hash", "source_fingerprint", "first_imported_at",
)

INVOICE_ORDERS_DIFFERENCE_INDEX = 35  # AJ, zero-based.
INVOICE_ORDERS_HEADERS = (
    *LEGACY_INVOICE_ORDERS_HEADERS[:INVOICE_ORDERS_DIFFERENCE_INDEX],
    "difference",
    *LEGACY_INVOICE_ORDERS_HEADERS[INVOICE_ORDERS_DIFFERENCE_INDEX:],
)

INVOICE_ITEMS_HEADERS = (
    "platform", "order_id", "item_index", "seller_sku", "nav", "product_name", "variation", "quantity", "unit_price", "actual_selling_unit_price", "line_subtotal", "promotion_group_id", "promotion_label", "source_group_total", "statement_product_price", "statement_refund_amount", "statement_net_selling_amount", "source_pdf", "source_hash",
)

STATEMENT_DATA_HEADERS = (
    "statement_batch_id", "record_type", "sequence_no", "platform",
    "statement_source_filename", "statement_file_hash", "statement_period_from",
    "statement_period_to", "statement_uploaded_at", "statement_uploaded_by",
    "statement_order_count", "statement_sku_count", "statement_summary_total_released",
    "statement_adjustment_control_total", "validation_status", "commit_status",
    "statement_source_row_number", "committed_at", "order_id", "linked_order_id",
    "order_creation_date", "payout_completed_date", "release_channel", "order_type",
    "total_released_amount", "statement_product_id", "statement_product_name",
    "statement_product_price", "statement_refund_amount", "statement_net_selling_amount",
    "adjustment_complete_date", "adjustment_type", "adjustment_reason", "adjustment_amount",
    "comparison_source", "comparison_amount", "difference", "reconciliation_status",
    "matched_item_index", "match_method",
)


assert len(LEGACY_INVOICE_ORDERS_HEADERS) == 39
assert len(INVOICE_ORDERS_HEADERS) == 40
assert INVOICE_ORDERS_HEADERS[INVOICE_ORDERS_DIFFERENCE_INDEX] == "difference"
assert len(INVOICE_ITEMS_HEADERS) == 19
assert len(STATEMENT_DATA_HEADERS) == 40
