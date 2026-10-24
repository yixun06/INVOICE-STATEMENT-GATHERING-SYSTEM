"""SQLAlchemy 2.0 declarative ORM models for InvoiceGather PostgreSQL backend.

These models mirror the exact column order defined in uat2_persistence_schema.py.
All financial amounts use Numeric(12, 2) — never Float — to preserve Decimal semantics.
Primary keys match the existing Google Sheets identity contracts.

Do NOT import these from any existing business-logic file.  They are used only
by PostgreSQL repository adapters and migration/sync scripts.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Invoice_Orders  (44 columns, PK: platform + order_id)
# ---------------------------------------------------------------------------

class InvoiceOrder(Base):
    __tablename__ = "invoice_orders"

    platform: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_status: Mapped[str | None] = mapped_column(String(256))
    order_created_date: Mapped[str | None] = mapped_column(String(32))
    delivered_date: Mapped[str | None] = mapped_column(String(32))
    completed_date: Mapped[str | None] = mapped_column(String(32))
    fund_transfer_date: Mapped[str | None] = mapped_column(String(32))
    invoice_financial_layout: Mapped[str | None] = mapped_column(String(128))
    merchandise_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    product_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    shipping_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    shipping_fee_paid_by_buyer: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    shipping_fee_charged_by_logistic_provider: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    shipping_fee_rebate_from_shopee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    seller_paid_shipping_fee_sst: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    reverse_shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    reverse_shipping_fee_sst: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    vouchers_rebates_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    voucher_type: Mapped[str | None] = mapped_column(String(128))
    voucher_code: Mapped[str | None] = mapped_column(String(128))
    voucher_funded_by: Mapped[str | None] = mapped_column(String(128))
    voucher_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    commission_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    service_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    transaction_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    ams_commission_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    ads_escrow_top_up_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fees_charges_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    order_income: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    income_type: Mapped[str | None] = mapped_column(String(128))
    final_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    buyer_merchandise_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    buyer_shipping_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    shopee_voucher: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    seller_voucher: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_buyer_payment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payment_status: Mapped[str | None] = mapped_column(String(128))
    payout_completed_date: Mapped[str | None] = mapped_column(String(32))
    difference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    source_pdf: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(128))
    source_fingerprint: Mapped[str | None] = mapped_column(String(128))
    first_imported_at: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_invoice_orders_platform_status", "platform", "order_status"),
        Index("ix_invoice_orders_order_created_date", "order_created_date"),
        Index("ix_invoice_orders_payout_date", "payout_completed_date"),
    )


# ---------------------------------------------------------------------------
# Invoice_Items  (23 columns, PK: platform + order_id + item_index)
# ---------------------------------------------------------------------------

class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    platform: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    item_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_sku: Mapped[str | None] = mapped_column(String(256))
    sku_missing_in_source: Mapped[str | None] = mapped_column(String(8))
    resolved_seller_sku: Mapped[str | None] = mapped_column(String(256))
    nav: Mapped[str | None] = mapped_column(String(128))
    product_name: Mapped[str | None] = mapped_column(Text)
    variation: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    actual_selling_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    line_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    promotion_group_id: Mapped[str | None] = mapped_column(String(128))
    promotion_label: Mapped[str | None] = mapped_column(String(256))
    promotion_advertised_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    promotion_discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    source_group_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_product_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_net_selling_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    source_pdf: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        Index("ix_invoice_items_platform_order", "platform", "order_id"),
        Index("ix_invoice_items_nav", "nav"),
        Index("ix_invoice_items_resolved_sku", "resolved_seller_sku"),
    )


# ---------------------------------------------------------------------------
# Statement_Data  (40 columns, PK: surrogate BigInteger autoincrement)
# ---------------------------------------------------------------------------

class StatementData(Base):
    __tablename__ = "statement_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    statement_batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    record_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_no: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(64))
    statement_source_filename: Mapped[str | None] = mapped_column(Text)
    statement_file_hash: Mapped[str | None] = mapped_column(String(128))
    statement_period_from: Mapped[str | None] = mapped_column(String(32))
    statement_period_to: Mapped[str | None] = mapped_column(String(32))
    statement_uploaded_at: Mapped[str | None] = mapped_column(String(64))
    statement_uploaded_by: Mapped[str | None] = mapped_column(String(128))
    statement_order_count: Mapped[int | None] = mapped_column(Integer)
    statement_sku_count: Mapped[int | None] = mapped_column(Integer)
    statement_summary_total_released: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_adjustment_control_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    validation_status: Mapped[str | None] = mapped_column(String(128))
    commit_status: Mapped[str | None] = mapped_column(String(128))
    statement_source_row_number: Mapped[int | None] = mapped_column(Integer)
    committed_at: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(128))
    linked_order_id: Mapped[str | None] = mapped_column(String(128))
    order_creation_date: Mapped[str | None] = mapped_column(String(32))
    payout_completed_date: Mapped[str | None] = mapped_column(String(32))
    release_channel: Mapped[str | None] = mapped_column(String(256))
    order_type: Mapped[str | None] = mapped_column(String(128))
    total_released_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_product_id: Mapped[str | None] = mapped_column(String(256))
    statement_product_name: Mapped[str | None] = mapped_column(Text)
    statement_product_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_refund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    statement_net_selling_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    adjustment_complete_date: Mapped[str | None] = mapped_column(String(32))
    adjustment_type: Mapped[str | None] = mapped_column(String(128))
    adjustment_reason: Mapped[str | None] = mapped_column(Text)
    adjustment_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    comparison_source: Mapped[str | None] = mapped_column(String(128))
    comparison_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    difference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    reconciliation_status: Mapped[str | None] = mapped_column(String(128))
    matched_item_index: Mapped[str | None] = mapped_column(String(32))
    match_method: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        UniqueConstraint(
            "statement_batch_id", "record_type", "sequence_no",
            name="uq_statement_data_source_row",
        ),
        Index("ix_statement_data_batch", "statement_batch_id"),
        Index("ix_statement_data_order_id", "order_id"),
        Index("ix_statement_data_platform_period", "platform", "statement_period_from", "statement_period_to"),
        Index("ix_statement_data_commit_status", "commit_status"),
        Index("ix_statement_data_payout_date", "payout_completed_date"),
    )


# ---------------------------------------------------------------------------
# Statement_Financial_Components  (17 columns, PK: surrogate BigInteger)
# This is the largest table (~397K rows).
# ---------------------------------------------------------------------------

class StatementFinancialComponent(Base):
    __tablename__ = "statement_financial_components"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    statement_batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    statement_file_hash: Mapped[str | None] = mapped_column(String(128))
    record_type: Mapped[str | None] = mapped_column(String(128))
    statement_source_sheet: Mapped[str] = mapped_column(String(128), nullable=False)
    statement_source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_no: Mapped[str | None] = mapped_column(String(64))
    platform: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(128))
    statement_product_id: Mapped[str | None] = mapped_column(String(256))
    component_name: Mapped[str] = mapped_column(String(256), nullable=False)
    component_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    component_note: Mapped[str | None] = mapped_column(Text)
    statement_period_from: Mapped[str | None] = mapped_column(String(32))
    statement_period_to: Mapped[str | None] = mapped_column(String(32))
    payout_completed_date: Mapped[str | None] = mapped_column(String(32))
    committed_at: Mapped[str | None] = mapped_column(String(64))
    commit_status: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        UniqueConstraint(
            "statement_batch_id", "statement_source_sheet",
            "statement_source_row_number", "component_name",
            name="uq_sfc_source_component",
        ),
        Index("ix_sfc_batch", "statement_batch_id"),
        Index("ix_sfc_order_id", "order_id"),
        Index("ix_sfc_component_name", "component_name"),
        Index("ix_sfc_period", "statement_period_from", "statement_period_to"),
        Index("ix_sfc_commit_status", "commit_status"),
    )


# ---------------------------------------------------------------------------
# Statement_Summary  (14 columns, PK: surrogate BigInteger)
# ---------------------------------------------------------------------------

class StatementSummary(Base):
    __tablename__ = "statement_summary"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    statement_batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    statement_file_hash: Mapped[str | None] = mapped_column(String(128))
    platform: Mapped[str | None] = mapped_column(String(64))
    statement_period_from: Mapped[str | None] = mapped_column(String(32))
    statement_period_to: Mapped[str | None] = mapped_column(String(32))
    statement_source_sheet: Mapped[str] = mapped_column(String(128), nullable=False)
    statement_source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    native_label: Mapped[str] = mapped_column(Text, nullable=False)
    line_type: Mapped[str | None] = mapped_column(String(128))
    parent_source_row_number: Mapped[str | None] = mapped_column(String(32))
    component_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(16))
    committed_at: Mapped[str | None] = mapped_column(String(64))
    commit_status: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        UniqueConstraint(
            "statement_batch_id", "statement_source_sheet",
            "statement_source_row_number", "native_label",
            name="uq_statement_summary_source_row",
        ),
        Index("ix_statement_summary_batch", "statement_batch_id"),
        Index("ix_statement_summary_platform_period", "platform", "statement_period_from"),
    )


# ---------------------------------------------------------------------------
# Order_Adjustments  (21 columns, PK: adjustment_event_fingerprint)
# ---------------------------------------------------------------------------

class OrderAdjustment(Base):
    __tablename__ = "order_adjustments"

    adjustment_event_fingerprint: Mapped[str] = mapped_column(String(128), primary_key=True)
    platform: Mapped[str | None] = mapped_column(String(64))
    linked_order_id: Mapped[str | None] = mapped_column(String(128))
    adjustment_type: Mapped[str | None] = mapped_column(String(128))
    adjustment_description: Mapped[str | None] = mapped_column(Text)
    adjustment_reason: Mapped[str | None] = mapped_column(Text)
    adjustment_complete_date: Mapped[str | None] = mapped_column(String(32))
    adjustment_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payout_completed_date: Mapped[str | None] = mapped_column(String(32))
    statement_batch_id: Mapped[str | None] = mapped_column(String(128))
    statement_sequence_no: Mapped[str | None] = mapped_column(String(64))
    statement_source_filename: Mapped[str | None] = mapped_column(Text)
    statement_file_hash: Mapped[str | None] = mapped_column(String(128))
    evidence_status: Mapped[str | None] = mapped_column(String(128))
    invoice_evidence_date: Mapped[str | None] = mapped_column(String(32))
    invoice_evidence_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    invoice_evidence_final_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    invoice_evidence_reason: Mapped[str | None] = mapped_column(Text)
    invoice_source_pdf: Mapped[str | None] = mapped_column(Text)
    invoice_source_hash: Mapped[str | None] = mapped_column(String(128))
    first_observed_at: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_order_adj_platform", "platform"),
        Index("ix_order_adj_linked_order_id", "linked_order_id"),
    )


# ---------------------------------------------------------------------------
# Shopee_FullOrder_Current_Orders  (7 columns, PK: market + order_id)
# ---------------------------------------------------------------------------

class FullOrderCurrentOrder(Base):
    __tablename__ = "full_order_current_orders"

    market: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_status: Mapped[str | None] = mapped_column(String(256))
    order_created_date: Mapped[str | None] = mapped_column(String(32))
    source_file: Mapped[str | None] = mapped_column(Text)
    snapshot_hash: Mapped[str | None] = mapped_column(String(128))
    last_synced_at: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_foc_orders_status", "market", "order_status"),
        Index("ix_foc_orders_created_date", "order_created_date"),
    )


# ---------------------------------------------------------------------------
# Shopee_FullOrder_Current_Items  (14 columns, PK: market + order_id + item_index)
# ---------------------------------------------------------------------------

class FullOrderCurrentItem(Base):
    __tablename__ = "full_order_current_items"

    market: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    item_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_parent_sku: Mapped[str | None] = mapped_column(String(256))
    source_seller_sku: Mapped[str | None] = mapped_column(String(256))
    resolved_seller_sku: Mapped[str | None] = mapped_column(String(256))
    nav: Mapped[str | None] = mapped_column(String(128))
    product_name: Mapped[str | None] = mapped_column(Text)
    variation: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    product_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    return_refund_status: Mapped[str | None] = mapped_column(String(128))
    resolve_sku: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_foc_items_market_order", "market", "order_id"),
        Index("ix_foc_items_nav", "nav"),
        Index("ix_foc_items_resolved_sku", "resolved_seller_sku"),
    )
