"""Read-only Shopee invoice-to-Weekly-Statement settlement projection.

This module decides settlement evidence per Order ID. It deliberately keeps
whole-statement source-quality validation in the staging service: a malformed
unrelated source row must not erase valid evidence for another order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..parsers.shopee_weekly_statement_parser import (
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from .batch_service import MISSING_VALUE_PLACEHOLDER, canonical_order_identity
from .shopee_weekly_statement_service import MONEY_TOLERANCE


@dataclass(frozen=True)
class ShopeeSettlementReportingRow:
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
    invoice_refund_amount: Decimal | None
    statement_refund_amount: Decimal | None
    refund_validation: str
    ready_to_invoice: str
    difference: Decimal | None

    @property
    def payment_transition(self) -> str | None:
        if self.settlement_status != "Settled":
            return None
        signal = _normalized_status(self.invoice_payment_signal)
        if signal == "pending":
            return "Pending → Settled"
        if signal == "released":
            return "Already Released → Settled"
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


@dataclass(frozen=True)
class _EvidenceDecision:
    status: str
    row: SettlementIncomeRow | None = None


def build_shopee_settlement_reporting(
    orders: Iterable[Mapping[str, Any]],
    statement: ParsedShopeeWeeklyStatement | None,
) -> ShopeeSettlementReportingResult:
    """Build a non-mutating, order-level settlement decision.

    An Order View row proves settlement only when its target order ID, payout
    date, and Total Released Amount are all valid. Components, creation date,
    SKU rows, and adjustment data do not participate in this authority test.
    """
    orders_by_id = _unique_shopee_orders(orders)
    rows: list[ShopeeSettlementReportingRow] = []

    for order_id, order in orders_by_id.items():
        decision = _settlement_evidence_for_order(statement, order_id)
        order_income = _to_decimal(order.get("order_income"))
        income_type = _display_value(order.get("income_type"))
        invoice_payment_signal = _display_value(order.get("payment_status"))
        invoice_refund = _to_decimal(order.get("refund_amount"))
        statement_row = decision.row
        statement_refund = _statement_refund_amount(statement_row)
        difference = (
            statement_row.total_released_amount - order_income
            if statement_row is not None
            and _is_final_income(income_type)
            and statement_row.total_released_amount is not None
            and order_income is not None
            else None
        )
        refund_validation = _refund_validation(
            decision.status, invoice_refund, statement_refund, statement_row
        )
        ready_to_invoice = _ready_to_invoice(decision.status, refund_validation)
        settled = decision.status == "Settled"
        rows.append(
            ShopeeSettlementReportingRow(
                order_id=order_id,
                order_created_date=order.get("order_created_date"),
                income_type=income_type,
                order_income=order_income,
                invoice_payment_signal=invoice_payment_signal,
                statement_match=settled,
                effective_payment_status="Settled" if settled else invoice_payment_signal,
                payment_evidence_source=(
                    "Weekly Statement" if settled else "Invoice payment signal"
                ),
                settlement_status=decision.status,
                payout_completed_date=(
                    statement_row.payout_completed_date if statement_row else None
                ),
                released_amount=(
                    statement_row.total_released_amount if statement_row else None
                ),
                invoice_refund_amount=invoice_refund,
                statement_refund_amount=statement_refund,
                refund_validation=refund_validation,
                ready_to_invoice=ready_to_invoice,
                difference=difference,
            )
        )

    result_rows = tuple(rows)
    settled_rows = tuple(row for row in result_rows if row.settlement_status == "Settled")
    return ShopeeSettlementReportingResult(
        rows=result_rows,
        summary=ShopeeSettlementReportingSummary(
            total_shopee_orders=len(result_rows),
            statement_matched=len(settled_rows),
            no_settlement_evidence=sum(
                row.settlement_status == "No Settlement Evidence" for row in result_rows
            ),
            pending_to_released=sum(
                row.payment_transition == "Pending → Settled" for row in settled_rows
            ),
            already_released_to_released=sum(
                row.payment_transition == "Already Released → Settled"
                for row in settled_rows
            ),
            different_amount=sum(
                row.difference is not None and abs(row.difference) > MONEY_TOLERANCE
                for row in settled_rows
            ),
            unmatched_statement_orders=len(
                _valid_statement_order_ids(statement) - set(orders_by_id)
            ),
        ),
    )


def _settlement_evidence_for_order(
    statement: ParsedShopeeWeeklyStatement | None, order_id: str
) -> _EvidenceDecision:
    if statement is None:
        return _EvidenceDecision("No Settlement Evidence")
    candidates = [
        row for row in statement.income_rows if _same_order_id(row.order_id, order_id)
    ]
    order_rows = [row for row in candidates if row.view_by == "Order"]
    malformed = [
        row
        for row in candidates
        if row.view_by not in {"Order", "Sku"}
        or (row.view_by == "Order" and not _has_valid_evidence_fields(row))
    ]
    if malformed:
        return _EvidenceDecision("Needs Review")
    valid_rows = [row for row in order_rows if _has_valid_evidence_fields(row)]
    if not valid_rows:
        return _EvidenceDecision("No Settlement Evidence")
    if len({_evidence_fingerprint(row) for row in valid_rows}) != 1:
        return _EvidenceDecision("Needs Review")
    return _EvidenceDecision("Settled", valid_rows[0])


def _has_valid_evidence_fields(row: SettlementIncomeRow) -> bool:
    return (
        bool(_normalized_order_id(row.order_id))
        and row.payout_completed_date is not None
        and row.total_released_amount is not None
    )


def _evidence_fingerprint(row: SettlementIncomeRow) -> tuple[Any, ...]:
    return (
        row.payout_completed_date,
        row.total_released_amount,
        _statement_refund_amount(row),
    )


def _statement_refund_amount(row: SettlementIncomeRow | None) -> Decimal | None:
    if row is None or "Refund Amount" not in row.financial_components:
        return None
    return row.financial_components.get("Refund Amount")


def _refund_validation(
    settlement_status: str,
    invoice_refund: Decimal | None,
    statement_refund: Decimal | None,
    statement_row: SettlementIncomeRow | None,
) -> str:
    if settlement_status != "Settled":
        return "Unavailable"
    if statement_row is None or statement_refund is None:
        return "Needs Review"
    invoice_effective = invoice_refund if invoice_refund is not None else Decimal("0")
    if invoice_effective == Decimal("0") and statement_refund == Decimal("0"):
        return "No Refund"
    if abs(invoice_effective - statement_refund) <= MONEY_TOLERANCE:
        return "Matched"
    return "Mismatch"


def _ready_to_invoice(settlement_status: str, refund_validation: str) -> str:
    if settlement_status == "No Settlement Evidence":
        return "No Settlement Evidence"
    if settlement_status != "Settled":
        return "Needs Review"
    return "Ready to Invoice" if refund_validation in {"Matched", "No Refund"} else "Needs Review"


def _unique_shopee_orders(
    orders: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for order in orders:
        identity = canonical_order_identity(order.get("platform"), order.get("order_id"))
        if identity is not None and identity[0] == "Shopee":
            result.setdefault(identity[1], order)
    return result


def _valid_statement_order_ids(statement: ParsedShopeeWeeklyStatement | None) -> set[str]:
    if statement is None:
        return set()
    return {
        _normalized_order_id(row.order_id)
        for row in statement.order_rows
        if _has_valid_evidence_fields(row)
    }


def _same_order_id(left: Any, right: str) -> bool:
    return _normalized_order_id(left) == _normalized_order_id(right)


def _normalized_order_id(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() == MISSING_VALUE_PLACEHOLDER.casefold() else text


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, "", MISSING_VALUE_PLACEHOLDER):
        return None
    try:
        return Decimal(str(value).strip().replace("RM", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _display_value(value: Any) -> Any:
    return MISSING_VALUE_PLACEHOLDER if value is None or str(value).strip() == "" else value


def _is_final_income(income_type: Any) -> bool:
    return str(income_type).strip().casefold() == "final"


def _normalized_status(value: Any) -> str:
    return str(value).strip().casefold()
