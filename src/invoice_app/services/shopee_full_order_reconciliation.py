"""Statement proof and canonical mapping for Full Order current snapshots."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from src.invoice_app.domain.historical_invoice import (
    CanonicalInvoiceItem,
    CanonicalInvoiceOrder,
    InvoiceBundle,
)
from src.invoice_app.domain.statement_reconciliation_v2 import (
    IdentityScope,
    SettlementBasis,
    StatementReconciliationBatch,
)
from src.invoice_app.parsers.shopee_weekly_statement_parser import (
    INCOME_COMPONENT_COLUMNS,
    ParsedShopeeWeeklyStatement,
    SettlementIncomeRow,
)
from src.invoice_app.repositories.historical_invoice_repository import (
    source_fingerprint_v2,
)
from src.invoice_app.repositories.shopee_full_order_current_repository import (
    FullOrderCurrentSnapshot,
)
from src.invoice_app.services.product_price_master import ProductPriceMaster
from src.invoice_app.services.market_context import resolve_market_context
from src.invoice_app.services.shopee_statement_item_matching import (
    product_family_resolver_from_price_master,
)
from src.invoice_app.services.shopee_statement_reconciliation_v2 import (
    evaluate_statement_reconciliation,
)


_SHIPPING_COMPONENTS = (
    "Shipping Fee Paid by Buyer (excl. SST)",
    "Shipping Fee Charged by Logistic Provider",
    "Seller Paid Shipping Fee SST",
    "Shipping Rebate From Shopee",
    "Reverse Shipping Fee",
    "Reverse Shipping Fee SST",
    "Saver Programme Shipping Fee Savings",
    "Return to Seller Fee",
)
_VOUCHER_COMPONENTS = (
    "Rebate Provided by Shopee",
    "Voucher Sponsored by Seller",
    "Cofund Voucher Sponsored by Seller",
    "Coin Cashback Sponsored by Seller",
    "Cofund Coin Cashback Sponsored by Seller",
)
_FEE_COMPONENTS = (
    "Commission Fee (incl. SST)",
    "Service Fee (Incl. SST)",
    "Transaction Fee (Incl. SST)",
    "AMS Commission Fee",
    "Saver Programme Fee (Incl. SST)",
    "Ads Escrow Top Up Fee",
)

# Bump only when the Full Order evidence contract used by Statement review changes.
# Stored Streamlit reviews then cannot be presented as current evidence after a
# proof-rule deployment.
FULL_ORDER_STATEMENT_PROOF_RULE_VERSION = "FULL_ORDER_STATEMENT_PROOF_V3"


@dataclass(frozen=True)
class FullOrderStatementProof:
    bundles: tuple[InvoiceBundle, ...]
    reconciliation: StatementReconciliationBatch | None
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(self.bundles) and self.reconciliation is not None and not self.blockers


def prove_full_order_current_against_statement(
    statement: ParsedShopeeWeeklyStatement,
    snapshot: FullOrderCurrentSnapshot,
    *,
    product_master: ProductPriceMaster,
    imported_at: datetime | None = None,
) -> FullOrderStatementProof:
    """Require exact Statement evidence; Current refund requests stay informational."""

    imported_at = imported_at or datetime.now(timezone.utc)
    order_map = {(order.market, order.order_id): order for order in snapshot.orders}
    item_map = snapshot.items_by_identity()
    provisional: list[InvoiceBundle] = []
    blockers: list[str] = []
    statement_orders = _unique_statement_orders(statement.order_rows)
    for order_id, statement_row in statement_orders.items():
        current = next((order for (market, candidate), order in order_map.items() if candidate == order_id and resolve_market_context(market).persisted_platform == "Shopee"), None)
        current_items = item_map.get((current.market, order_id), ()) if current else ()
        if current is None or not current_items:
            blockers.append(f"{order_id}: Full Order current snapshot is missing.")
            continue
        if current.order_status.casefold() in {"cancelled", "canceled"}:
            blockers.append(f"{order_id}: cancelled order remains on the PDF exception path.")
            continue
        if any(
            statement_row.financial_components.get(name) is None
            for name in INCOME_COMPONENT_COLUMNS
        ):
            blockers.append(f"{order_id}: Statement financial bridge is incomplete.")
            continue
        bundle = _canonical_bundle(current, current_items, statement_row, imported_at)
        provisional.append(
            bundle.with_source_fingerprint(source_fingerprint_v2(bundle))
        )
    if blockers:
        return FullOrderStatementProof(tuple(provisional), None, tuple(blockers))

    orders = tuple(bundle.order for bundle in provisional)
    items = tuple(item for bundle in provisional for item in bundle.items)
    reconciliation = evaluate_statement_reconciliation(
        statement,
        orders,
        items,
        product_families=product_family_resolver_from_price_master(product_master),
        tolerance=Decimal("0.00"),
    )
    blockers.extend(reconciliation.statement_validation_issues)
    if not reconciliation.coverage.valid:
        blockers.append("Statement and Full Order product populations are not fully accounted for.")
    for result in reconciliation.orders:
        order_id = result.evidence.order_id
        current = next(
            (
                order
                for (market, candidate), order in order_map.items()
                if candidate == order_id
                and resolve_market_context(market).persisted_platform == "Shopee"
            ),
            None,
        )
        current_items = item_map.get((current.market, order_id), ()) if current else ()
        if result.summary.identity_scope is IdentityScope.UNRESOLVED:
            blockers.append(f"{order_id}: Product Master identity is unresolved.")
        if not result.summary.merchandise_reconciled:
            blockers.append(f"{order_id}: exact Full Order/Statement Product Price proof failed.")
        if result.summary.settlement_basis is SettlementBasis.NONE:
            blockers.append(f"{order_id}: Statement internal financial bridge failed.")
        if any(
            evidence.difference != Decimal("0.00")
            for evidence in result.evidence.merchandise
            if evidence.difference is not None
        ):
            blockers.append(f"{order_id}: non-zero ITEM, GROUP, or ORDER difference remains.")
    return FullOrderStatementProof(
        tuple(provisional),
        reconciliation,
        tuple(dict.fromkeys(blockers)),
    )


def _canonical_bundle(current, current_items, statement_row, imported_at) -> InvoiceBundle:
    components = statement_row.financial_components
    statement_total = sum((components[name] for name in INCOME_COMPONENT_COLUMNS), Decimal("0"))
    product_price = components["Product Price"]
    assert product_price is not None
    order = CanonicalInvoiceOrder(
        platform=resolve_market_context(current.market).persisted_platform,
        order_id=current.order_id,
        order_status=current.order_status,
        order_created_date=current.order_created_date,
        invoice_financial_layout="FULL_ORDER_STATEMENT_PROVED",
        merchandise_subtotal=product_price,
        product_price=sum((item.product_subtotal for item in current_items), Decimal("0")),
        shipping_subtotal=_component_sum(statement_row, _SHIPPING_COMPONENTS),
        vouchers_rebates_total=_component_sum(statement_row, _VOUCHER_COMPONENTS),
        fees_charges_total=_component_sum(statement_row, _FEE_COMPONENTS),
        refund_amount=components["Refund Amount"],
        order_income=statement_total,
        income_type="Final",
        final_amount=statement_total,
        payment_status="RELEASED",
        payout_completed_date=statement_row.payout_completed_date,
        difference=Decimal("0.00"),
        source_pdf=current.source_file,
        source_hash=current.snapshot_hash,
        first_imported_at=imported_at,
    )
    items = tuple(
        CanonicalInvoiceItem(
            platform=resolve_market_context(current.market).persisted_platform,
            order_id=current.order_id,
            item_index=item.item_index,
            seller_sku=(
                item.source_seller_sku
                or item.source_parent_sku
                or item.resolved_seller_sku
            ),
            sku_missing_in_source=not bool(
                item.source_seller_sku or item.source_parent_sku
            ),
            resolved_seller_sku=item.resolved_seller_sku,
            nav=item.nav,
            product_name=item.product_name,
            variation=item.variation or None,
            quantity=item.quantity,
            unit_price=item.unit_price,
            actual_selling_unit_price=None,
            line_subtotal=item.product_subtotal,
            source_pdf=current.source_file,
            source_hash=current.snapshot_hash,
        )
        for item in current_items
    )
    return InvoiceBundle(order, items)


def _unique_statement_orders(
    rows: Iterable[SettlementIncomeRow],
) -> dict[str, SettlementIncomeRow]:
    result: dict[str, SettlementIncomeRow] = {}
    for row in rows:
        existing = result.get(row.order_id)
        if existing is not None and existing != row:
            raise ValueError(f"Statement has conflicting Order rows for {row.order_id}.")
        result[row.order_id] = row
    return result


def _component_sum(row: SettlementIncomeRow, names: tuple[str, ...]) -> Decimal:
    return sum(
        (row.financial_components[name] for name in names),
        Decimal("0"),
    )
