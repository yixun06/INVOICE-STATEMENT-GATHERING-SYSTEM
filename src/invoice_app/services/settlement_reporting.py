"""Reporting-only Shopee invoice-to-settlement projection.

The temporary Settlement Test Lab consumes this module, but its result is not
test-page state: future reporting or persistence work can reuse the same
non-mutating merge.  It intentionally does not decide underpayment or alter
any invoice source fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..parsers.shopee_weekly_statement_parser import ParsedShopeeWeeklyStatement
from .batch_service import MISSING_VALUE_PLACEHOLDER, canonical_order_identity
from .shopee_weekly_statement_service import MONEY_TOLERANCE


@dataclass(frozen=True)
class ShopeeSettlementReportingRow:
    """One read-only reporting projection for a unique Shopee Order ID."""

    order_id: str
    order_created_date: Any
    income_type: str
    order_income: Decimal | None
    invoice_payment_signal: Any
    statement_match: bool
    effective_payment_status: Any
    payment_evidence_source: str
    settlement_status: str
    payout_completed_date: Any
    released_amount: Decimal | None
    difference: Decimal | None

    @property
    def payment_transition(self) -> str | None:
        if not self.statement_match:
            return None
        signal = _normalized_status(self.invoice_payment_signal)
        if signal == "pending":
            return "Pending → Released"
        if signal == "released":
            return "Already Released → Released"
        return None


@dataclass(frozen=True)
class ShopeeSettlementReportingSummary:
    total_shopee_orders: int
    statement_matched: int
    no_settlement_evidence: int
    pending_to_released: int
    already_released_to_released: int
    different_amount: int
    unmatched_statement_orders: int


@dataclass(frozen=True)
class ShopeeSettlementReportingResult:
    rows: tuple[ShopeeSettlementReportingRow, ...]
    summary: ShopeeSettlementReportingSummary


def build_shopee_settlement_reporting(
    orders: Iterable[Mapping[str, Any]],
    statement: ParsedShopeeWeeklyStatement | None,
) -> ShopeeSettlementReportingResult:
    """Merge current-session Shopee source facts with authoritative Order View.

    The first valid occurrence for each `(Shopee, Order ID)` is selected on
    both sides.  The active batch already guards this identity, and this extra
    reporting-level guard prevents a duplicate session/statement row from
    inflating test counts.  Input mappings and parser dataclasses are never
    modified.
    """

    orders_by_id = _unique_shopee_orders(orders)
    statement_by_id = _unique_statement_order_rows(statement)
    rows: list[ShopeeSettlementReportingRow] = []

    for order_id, order in orders_by_id.items():
        statement_row = statement_by_id.get(order_id)
        order_income = _to_decimal(order.get("order_income"))
        income_type = _display_value(order.get("income_type"))
        invoice_payment_signal = _display_value(order.get("payment_status"))
        if statement_row is None:
            rows.append(
                ShopeeSettlementReportingRow(
                    order_id=order_id,
                    order_created_date=order.get("order_created_date"),
                    income_type=income_type,
                    order_income=order_income,
                    invoice_payment_signal=invoice_payment_signal,
                    statement_match=False,
                    effective_payment_status=invoice_payment_signal,
                    payment_evidence_source="Invoice payment signal",
                    settlement_status="No Settlement Evidence",
                    payout_completed_date=None,
                    released_amount=None,
                    difference=None,
                )
            )
            continue

        released_amount = statement_row.total_released_amount
        difference = (
            released_amount - order_income
            if _is_final_income(income_type)
            and released_amount is not None
            and order_income is not None
            else None
        )
        rows.append(
            ShopeeSettlementReportingRow(
                order_id=order_id,
                order_created_date=order.get("order_created_date"),
                income_type=income_type,
                order_income=order_income,
                invoice_payment_signal=invoice_payment_signal,
                statement_match=True,
                effective_payment_status="Released",
                payment_evidence_source="Weekly Statement",
                settlement_status="Released",
                payout_completed_date=statement_row.payout_completed_date,
                released_amount=released_amount,
                difference=difference,
            )
        )

    result_rows = tuple(rows)
    matched_rows = tuple(row for row in result_rows if row.statement_match)
    summary = ShopeeSettlementReportingSummary(
        total_shopee_orders=len(result_rows),
        statement_matched=len(matched_rows),
        no_settlement_evidence=len(result_rows) - len(matched_rows),
        pending_to_released=sum(
            row.payment_transition == "Pending → Released" for row in matched_rows
        ),
        already_released_to_released=sum(
            row.payment_transition == "Already Released → Released"
            for row in matched_rows
        ),
        different_amount=sum(
            row.difference is not None and abs(row.difference) > MONEY_TOLERANCE
            for row in matched_rows
        ),
        unmatched_statement_orders=len(set(statement_by_id) - set(orders_by_id)),
    )
    return ShopeeSettlementReportingResult(rows=result_rows, summary=summary)


def _unique_shopee_orders(
    orders: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for order in orders:
        identity = canonical_order_identity(order.get("platform"), order.get("order_id"))
        if identity is not None and identity[0] == "Shopee":
            result.setdefault(identity[1], order)
    return result


def _unique_statement_order_rows(statement: ParsedShopeeWeeklyStatement | None) -> dict[str, Any]:
    if statement is None:
        return {}
    result: dict[str, Any] = {}
    for row in statement.order_rows:
        order_id = str(row.order_id).strip()
        if order_id and order_id.casefold() != MISSING_VALUE_PLACEHOLDER.casefold():
            result.setdefault(order_id, row)
    return result


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, "", MISSING_VALUE_PLACEHOLDER):
        return None
    try:
        return Decimal(str(value).strip().replace("RM", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _display_value(value: Any) -> Any:
    if value is None or str(value).strip() == "":
        return MISSING_VALUE_PLACEHOLDER
    return value


def _is_final_income(income_type: Any) -> bool:
    return str(income_type).strip().casefold() == "final"


def _normalized_status(value: Any) -> str:
    return str(value).strip().casefold()
