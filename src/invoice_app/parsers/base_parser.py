from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any


class BaseParser(ABC):
    platform: str = ""

    @abstractmethod
    def parse(
        self,
        text: str,
        source_pdf: str,
        batch_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        raise NotImplementedError

    @staticmethod
    def create_review(
        batch_id: str,
        source_pdf: str,
        platform: str,
        order_id: str,
        status: str,
        reason: str,
        *,
        order_payload: dict[str, Any] | None = None,
        product_payloads: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        review = {
            "batch_id": batch_id,
            "source_pdf": source_pdf,
            "platform": platform,
            "order_id": order_id or "N/A",
            "status": status,
            "reason": reason,
            "timestamp": "",
        }
        if order_payload is not None:
            review["order_payload"] = order_payload
        if product_payloads:
            review["product_payloads"] = product_payloads
        return review

    @staticmethod
    def money(value: str | Decimal | None) -> str:
        if value in (None, ""):
            return ""
        if isinstance(value, Decimal):
            return f"{value.quantize(Decimal('0.01'))}"
        return str(value).strip()
