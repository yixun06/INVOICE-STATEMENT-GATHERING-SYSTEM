from __future__ import annotations

from typing import Any

from ..pdf_document import PdfDocument
from .base_parser import BaseParser
from .shopee_extractor import SHOPEE_ORDER_STATUSES, extract_shopee_data
from .shopee_mapper import map_shopee_records, map_shopee_review_payloads
from .shopee_product_parser import parse_positioned_products
from .shopee_review_policy import find_shopee_review_issue


class ShopeeParser(BaseParser):
    platform = "Shopee"
    ORDER_STATUSES = SHOPEE_ORDER_STATUSES

    def parse(
        self,
        text: str,
        source_pdf: str,
        batch_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        return self._parse(text, source_pdf, batch_id, positioned_items=[])

    def parse_document(
        self,
        document: PdfDocument,
        source_pdf: str,
        batch_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        positioned_items = parse_positioned_products(document)
        return self._parse(document.text, source_pdf, batch_id, positioned_items)

    def _parse(
        self,
        text: str,
        source_pdf: str,
        batch_id: str,
        positioned_items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        extracted = extract_shopee_data(text, source_pdf, positioned_items)
        review_issue = find_shopee_review_issue(extracted)
        if review_issue:
            order_payload, product_payloads = map_shopee_review_payloads(extracted, batch_id)
            review = self.create_review(
                batch_id,
                source_pdf,
                self.platform,
                review_issue.order_id,
                "Manual Review",
                review_issue.reason,
                reason_code=review_issue.reason_code,
                order_payload=order_payload,
                product_payloads=product_payloads,
            )
            return [], [], [review]

        order, products = map_shopee_records(extracted, batch_id)
        return [order], products, []
