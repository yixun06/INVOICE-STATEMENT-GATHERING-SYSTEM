"""Source-faithful Invoice Adjustment evidence.

This type is intentionally separate from Statement Adjustment evidence and
from the canonical persisted Invoice schema.  It represents what one Shopee
Invoice source visibly says so validation can reason over zero, one, or many
Adjustment events without inventing a business taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ORDER_ADJUSTMENT = "ORDER_ADJUSTMENT"
ADJUSTMENT_EVIDENCE_COMPLETE = "COMPLETE"
ADJUSTMENT_EVIDENCE_INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class InvoiceAdjustmentEvidence:
    semantic_type: str
    source_label: str
    source_reason: str | None
    adjustment_complete_date: str | None
    signed_amount: Decimal | None
    total_adjustment_amount: Decimal | None
    source_locator: str
    section_index: int
    completeness: str
    source_confidence: str = "STRUCTURED_SECTION"

    @property
    def is_complete(self) -> bool:
        return (
            self.completeness == ADJUSTMENT_EVIDENCE_COMPLETE
            and bool(self.source_label)
            and bool(self.source_reason)
            and bool(self.adjustment_complete_date)
            and self.signed_amount is not None
        )
