"""Deterministic ERP Staging Data projection from final Product Summary rows."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, fields, replace
from datetime import date
from decimal import Decimal
from typing import Sequence

from src.invoice_app.domain.weekly_billing import (
    StagingDataRow,
    WeeklyBillingSummary,
)
from src.invoice_app.services.product_price_master import ProductPriceMasterRecord


STAGING_DATA_HEADERS = (
    "Your Reference",
    "Posting Date",
    "Sell-To Customer No.",
    "Currency Code",
    "Type",
    "No.",
    "Location Code",
    "Quantity",
    "Unit of Measure Code",
    "Unit Price [RSP] Excl.GST",
    "Order Date",
    "Shipment Date",
    "External Doc No.",
    "Customer Outlet Code",
    "Business Unit Code ERP",
    "Project Code ERP",
    "Transfer-to Code",
    "Customer ",
    "Customer",
    "USOFT - USOFT CODE",
    "USOFT product description",
    "Plan Date",
    "AM/PM",
    "TYPE",
    "Qty. per Unit of Measure",
    "Line Discount %",
)

SELL_TO_CUSTOMER_NO = "HC001543"
CURRENCY_CODE = "MYR"
ITEM_TYPE = "Item"
LOCATION_CODE = "JH02"
UNIT_OF_MEASURE_CODE = "EA"
BUSINESS_UNIT_CODE_ERP = "RETAIL"
PROJECT_CODE_ERP = "JH02"
EXTERNAL_DOC_NO = "Shopee"
CUSTOMER = "RETAIL"
MISSING_USOFT_DESCRIPTION = "N/A"
PLACEHOLDER_NAV = "5000000"
APPROVED_USOFT_DESCRIPTIONS = {
    "4001971": "SN Org Pumpkin Seed Cube BTL 150g",
}


class StagingDataError(RuntimeError):
    """Staging Data cannot be proven from the finalized Product Summary."""


@dataclass(frozen=True)
class _StagingCandidate:
    """Internal SKU-bearing wrapper; SKU is not part of the ERP export schema."""

    sku_code: str
    row: StagingDataRow


def build_staging_data_rows(
    summary: WeeklyBillingSummary,
    *,
    generation_date: date,
    product_master_records: Sequence[ProductPriceMasterRecord] = (),
) -> tuple[StagingDataRow, ...]:
    """Project finalized Product Summary rows into NAV + SKU + price buckets."""

    reference = _your_reference(summary)
    descriptions_by_nav = _usoft_descriptions_by_nav(product_master_records)
    source_candidates = tuple(
        _StagingCandidate(
            sku_code=product.sku_code,
            row=_staging_row_for_product(
                product,
                reference=reference,
                generation_date=generation_date,
                descriptions_by_nav=descriptions_by_nav,
            ),
        )
        for product in summary.product_rows
    )
    candidates = _aggregate_staging_rows(source_candidates)
    _validate_staging_rows(summary, candidates)
    return tuple(candidate.row for candidate in candidates)


def _staging_row_for_product(
    product,
    *,
    reference: str,
    generation_date: date,
    descriptions_by_nav: dict[str, str],
) -> StagingDataRow:
    nav = _normalized_nav(product.nav)
    return StagingDataRow(
            your_reference=reference,
            posting_date=generation_date,
            sell_to_customer_no=SELL_TO_CUSTOMER_NO,
            currency_code=CURRENCY_CODE,
            item_type=ITEM_TYPE,
            nav=nav,
            location_code=LOCATION_CODE,
            quantity=product.quantity,
            unit_of_measure_code=UNIT_OF_MEASURE_CODE,
            unit_price_rsp_excl_gst=product.unit_price,
            order_date=generation_date,
            shipment_date=generation_date,
            external_doc_no=EXTERNAL_DOC_NO,
            customer_outlet_code=None,
            business_unit_code_erp=BUSINESS_UNIT_CODE_ERP,
            project_code_erp=PROJECT_CODE_ERP,
            transfer_to_code=None,
            customer_remark=None,
            customer=CUSTOMER,
            usoft_code=nav,
            usoft_product_description=_usoft_description(nav, descriptions_by_nav),
            plan_date=generation_date,
            am_pm=None,
            secondary_type=None,
            quantity_per_unit_of_measure=None,
            line_discount_percent=None,
        )


def _aggregate_staging_rows(
    source_rows: Sequence[_StagingCandidate],
) -> tuple[_StagingCandidate, ...]:
    """Aggregate exact NAV/SKU/Decimal-price buckets in first-occurrence order."""

    buckets: OrderedDict[tuple[str, str, Decimal], _StagingCandidate] = OrderedDict()
    for candidate in source_rows:
        row = candidate.row
        nav = row.nav.strip()
        price = row.unit_price_rsp_excl_gst
        key = (nav, candidate.sku_code, price)
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = candidate
            continue
        conflicting_field = _conflicting_non_quantity_field(existing.row, row)
        if conflicting_field is not None:
            raise StagingDataError(
                "Staging Data aggregation conflict for "
                f"NAV {nav} at unit price {price}: {conflicting_field}."
            )
        buckets[key] = replace(
            existing,
            row=replace(existing.row, quantity=existing.row.quantity + row.quantity),
        )
    return tuple(buckets.values())


def _conflicting_non_quantity_field(
    first: StagingDataRow,
    candidate: StagingDataRow,
) -> str | None:
    for field in fields(StagingDataRow):
        if field.name == "quantity":
            continue
        if getattr(first, field.name) != getattr(candidate, field.name):
            return field.name
    return None


def _your_reference(summary: WeeklyBillingSummary) -> str:
    period = summary.period
    if period.statement_period_from > period.statement_period_to:
        raise StagingDataError("Staging Data Statement period is invalid.")
    return f"SP{period.statement_period_from:%Y%m%d}{period.statement_period_to:%m%d}"


def _usoft_descriptions_by_nav(
    records: Sequence[ProductPriceMasterRecord],
) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for record in records:
        nav = str(record.nav_code or "").strip()
        description = str(record.usoft_product_description or "").strip()
        if not nav or nav == PLACEHOLDER_NAV or not description:
            continue
        candidates.setdefault(nav, set()).add(description)

    for nav, approved_description in APPROVED_USOFT_DESCRIPTIONS.items():
        descriptions = candidates.get(nav)
        if descriptions is None:
            continue
        if approved_description not in descriptions:
            raise StagingDataError(
                "Product Master does not contain the approved USOFT product "
                f"description for NAV {nav}."
            )
        candidates[nav] = {approved_description}

    conflicts = {
        nav: descriptions
        for nav, descriptions in candidates.items()
        if len(descriptions) > 1
    }
    if conflicts:
        nav = sorted(conflicts)[0]
        raise StagingDataError(
            "Product Master contains conflicting USOFT product descriptions "
            f"for NAV {nav}."
        )
    return {nav: next(iter(descriptions)) for nav, descriptions in candidates.items()}


def _usoft_description(nav: str, descriptions_by_nav: dict[str, str]) -> str:
    clean_nav = nav.strip()
    if not clean_nav or clean_nav == PLACEHOLDER_NAV:
        return MISSING_USOFT_DESCRIPTION
    return descriptions_by_nav.get(clean_nav, MISSING_USOFT_DESCRIPTION)


def _validate_staging_rows(
    summary: WeeklyBillingSummary,
    candidates: tuple[_StagingCandidate, ...],
) -> None:
    expected = _expected_staging_buckets(summary)
    if len(candidates) != len(expected):
        raise StagingDataError("Staging Data aggregation row-count control failed.")
    for (key, expected_quantity), candidate in zip(expected, candidates, strict=True):
        nav, sku_code, price = key
        staging = candidate.row
        if staging.nav != nav:
            raise StagingDataError("Staging Data No. aggregation key control failed.")
        if candidate.sku_code != sku_code:
            raise StagingDataError("Staging Data SKU aggregation key control failed.")
        if staging.quantity != expected_quantity:
            raise StagingDataError("Staging Data Quantity aggregation control failed.")
        if staging.unit_price_rsp_excl_gst != price:
            raise StagingDataError("Staging Data Unit Price row control failed.")
    source_total_quantity = sum(product.quantity for product in summary.product_rows)
    if source_total_quantity != summary.total_quantity:
        raise StagingDataError("Product Summary Quantity total control failed.")
    if sum(candidate.row.quantity for candidate in candidates) != source_total_quantity:
        raise StagingDataError("Staging Data Quantity total control failed.")
    keys = [
        (candidate.row.nav, candidate.sku_code, candidate.row.unit_price_rsp_excl_gst)
        for candidate in candidates
    ]
    if len(keys) != len(set(keys)):
        raise StagingDataError(
            "Staging Data duplicate NAV, SKU Code, and Unit Price control failed."
        )


def _expected_staging_buckets(
    summary: WeeklyBillingSummary,
) -> tuple[tuple[tuple[str, str, Decimal], int], ...]:
    buckets: OrderedDict[tuple[str, str, Decimal], int] = OrderedDict()
    for product in summary.product_rows:
        nav = _normalized_nav(product.nav)
        price = product.unit_price
        key = (nav, product.sku_code, price)
        buckets[key] = buckets.get(key, 0) + product.quantity
    return tuple(buckets.items())


def _normalized_nav(value: object) -> str:
    return str(value or "").strip()
