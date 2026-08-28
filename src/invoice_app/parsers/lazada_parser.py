from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..utils.normalize import parse_decimal
from .base_parser import BaseParser
from .validation import format_validation_errors, validate_product_items


class LazadaParser(BaseParser):
    platform = "Lazada"

    def parse(
        self,
        text: str,
        source_pdf: str,
        batch_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        orders: list[dict[str, Any]] = []
        products: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        blocks = self._split_order_blocks(text)
        if not blocks:
            reviews.append(
                self.create_review(
                    batch_id,
                    source_pdf,
                    self.platform,
                    "N/A",
                    "Manual Review",
                    "Order Number not found.",
                )
            )
            return orders, products, reviews

        for block in blocks:
            order_id = self._find_pattern(block, r"Order Number\s*:\s*([A-Za-z0-9-]+)")
            if not order_id:
                reviews.append(
                    self.create_review(
                        batch_id,
                        source_pdf,
                        self.platform,
                        "N/A",
                        "Manual Review",
                        "Lazada order block missing Order Number.",
                    )
                )
                continue

            invoice_number = self._find_pattern(block, r"Invoice Number\s*:\s*([A-Za-z0-9-]+)")
            order_date = self._find_pattern(block, r"Order Date\s*:\s*([0-9 ]+)")
            invoice_date = self._find_pattern(block, r"Invoice Date\s*:\s*([0-9 ]+)")
            payment_method = self._find_pattern(block, r"Payment Method\s*:\s*([^\n]+)")
            gross_sales = self._extract_money(block, r"^Subtotal:\s*RM\s*([-\d,.]+)")
            voucher = self._extract_money(block, r"^Less:\s*Voucher applied:\s*RM\s*([-\d,.]+)")
            total_amount = self._extract_money(block, r"^Total:\s*RM\s*([-\d,.]+)")
            delivery_fee = self._extract_money(block, r"^Shipping:\s*\+?RM\s*([-\d,.]+)")
            net_amount = self._extract_money(block, r"^Net paid:\s*RM\s*([-\d,.]+)")
            parsed_items = self._parse_items(block)
            order_payload = {
                "batch_id": batch_id,
                "platform": self.platform,
                "order_id": order_id,
                "invoice_number": invoice_number,
                "order_date": order_date,
                "invoice_date": invoice_date,
                "payment_method": payment_method,
                "source_pdf": source_pdf,
                "gross_sales": gross_sales,
                "delivery_fee": delivery_fee,
                "commission_fee": "",
                "service_fee": "",
                "transaction_fee": "",
                "voucher": voucher,
                "subtotal": gross_sales,
                "voucher_applied": voucher,
                "total": total_amount,
                "shipping_fee": delivery_fee,
                "net_paid": net_amount,
                "platform_fees": "",
                "ads_fee": "",
                "estimated_order_income": "",
                "net_income": net_amount,
                "net_amount": net_amount,
                "total_amount": total_amount,
                "status": "Accepted",
            }
            product_payloads = [
                {
                    "batch_id": batch_id,
                    "platform": self.platform,
                    "order_id": order_id,
                    "invoice_number": invoice_number,
                    "order_date": order_date,
                    "invoice_date": invoice_date,
                    "payment_method": payment_method,
                    "product_name": item.get("product_name", ""),
                    "seller_sku": item.get("seller_sku", ""),
                    "shop_sku": item.get("shop_sku", ""),
                    "quantity": item.get("quantity", ""),
                    "unit_price": self._payload_money(item.get("unit_price")),
                    "line_total": self._payload_money(item.get("line_total")),
                    "price": self._payload_money(item.get("unit_price")),
                    "paid_price": self._payload_money(item.get("line_total")),
                    "source_pdf": source_pdf,
                    "status": "Accepted",
                }
                for item in parsed_items
            ]

            validation_errors = validate_product_items(parsed_items, require_sku=True)
            if validation_errors:
                reviews.append(
                    self.create_review(
                        batch_id,
                        source_pdf,
                        self.platform,
                        order_id,
                        "Manual Review",
                        format_validation_errors(validation_errors),
                        order_payload={**order_payload, "status": "Manual Review"},
                        product_payloads=[
                            {**product, "status": "Manual Review"}
                            for product in product_payloads
                        ],
                    )
                )
                continue

            orders.append(order_payload)
            products.extend(product_payloads)

        return orders, products, reviews

    @staticmethod
    def _find_pattern(text: str, pattern: str) -> str:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _split_order_blocks(text: str) -> list[str]:
        invoice_anchor = r"(?=Invoice Number:\s*[A-Za-z0-9-]+)"
        if re.search(invoice_anchor, text, flags=re.IGNORECASE):
            chunks = re.split(invoice_anchor, text, flags=re.IGNORECASE)
            return [chunk for chunk in chunks if re.search(r"Order Number\s*:", chunk, flags=re.IGNORECASE)]

        anchors_by_order_id: dict[str, int] = {}
        for pattern in (
            r"Order Number\s*:\s*([A-Za-z0-9-]+)",
            r"Your ordered items for\s+([A-Za-z0-9-]+)",
        ):
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                order_id = match.group(1).strip()
                order_key = order_id.casefold()
                current_start = anchors_by_order_id.get(order_key)
                if current_start is None or match.start() < current_start:
                    anchors_by_order_id[order_key] = match.start()

        starts = sorted(anchors_by_order_id.values())
        return [
            text[start : starts[index + 1] if index + 1 < len(starts) else None]
            for index, start in enumerate(starts)
        ]

    @staticmethod
    def _extract_money(text: str, pattern: str) -> str:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            return ""
        return str(parse_decimal(match.group(1)).quantize(Decimal("0.01")))

    @staticmethod
    def _payload_money(value: Any) -> str:
        if value is None or str(value).strip() == "":
            return ""
        if isinstance(value, Decimal):
            return str(value.quantize(Decimal("0.01")))
        return str(value).strip()

    def _parse_items(self, block: str) -> list[dict[str, Any]]:
        primary_items = self._parse_items_from_table(block)
        if not validate_product_items(primary_items, require_sku=True):
            return primary_items

        fallback_items = self._parse_items_from_sku_anchors(block)
        if fallback_items and not validate_product_items(fallback_items, require_sku=True):
            return fallback_items

        return primary_items or fallback_items

    def _parse_items_from_table(self, block: str) -> list[dict[str, Any]]:
        raw_item_lines = self._extract_item_lines(block)
        if not raw_item_lines:
            return []

        merged_items: dict[tuple[str, str], dict[str, Any]] = {}
        current_item: dict[str, Any] | None = None

        for line in raw_item_lines:
            if re.match(r"^\d+\s+", line):
                if current_item:
                    self._add_merged_item(merged_items, current_item)
                match = re.match(
                    r"^\d+\s+(?P<product>.+?)\s+(?P<seller_sku>\S+)\s+(?P<shop_sku>\S+)\s+"
                    r"(?P<unit_price>-?\d+(?:\.\d+)?)\s+(?P<paid_price>-?\d+(?:\.\d+)?)$",
                    line,
                )
                if not match:
                    current_item = None
                    continue
                current_item = {
                    "product_name": match.group("product").strip(),
                    "seller_sku": match.group("seller_sku").strip(),
                    "shop_sku": match.group("shop_sku").strip(),
                    "quantity": 1,
                    "unit_price": parse_decimal(match.group("unit_price")),
                    "line_total": parse_decimal(match.group("paid_price")),
                }
                continue

            if not current_item:
                continue

            tail_match = re.match(r"^(?P<text>.*?)(?P<suffix>\d{6,})$", line)
            if tail_match and str(current_item["shop_sku"]).endswith("-"):
                current_item["shop_sku"] = f"{current_item['shop_sku']}{tail_match.group('suffix')}"
                continuation_text = tail_match.group("text").strip()
            else:
                continuation_text = line

            if continuation_text:
                current_item["product_name"] = f"{current_item['product_name']} {continuation_text}".strip()

        if current_item:
            self._add_merged_item(merged_items, current_item)

        return list(merged_items.values())

    def _parse_items_from_sku_anchors(self, block: str) -> list[dict[str, Any]]:
        raw_item_lines = self._extract_item_lines(block)
        if not raw_item_lines:
            return []

        candidates: list[str] = []
        buffer: list[str] = []
        for line in raw_item_lines:
            if re.match(r"^\d+\s+", line) and buffer:
                self._flush_lazada_candidate(buffer, candidates)
            buffer.append(line)
            if self._has_two_amount_tail(" ".join(buffer)):
                self._flush_lazada_candidate(buffer, candidates)
        self._flush_lazada_candidate(buffer, candidates)

        merged_items: dict[tuple[str, str], dict[str, Any]] = {}
        amount = r"(?:RM\s*)?-?\d[\d,]*(?:\.\d+)?"
        for candidate in candidates:
            normalized = re.sub(r"\s+", " ", candidate).strip()
            match = re.match(
                rf"^(?:\d+\s+)?(?P<body>.+?)\s+(?P<unit_price>{amount})\s+(?P<paid_price>{amount})$",
                normalized,
                flags=re.IGNORECASE,
            )
            if not match:
                continue

            parts = match.group("body").split()
            if len(parts) < 3:
                continue
            item = {
                "product_name": " ".join(parts[:-2]).strip(),
                "seller_sku": parts[-2].strip(),
                "shop_sku": parts[-1].strip(),
                "quantity": 1,
                "unit_price": parse_decimal(match.group("unit_price")),
                "line_total": parse_decimal(match.group("paid_price")),
            }
            self._add_merged_item(merged_items, item)

        return list(merged_items.values())

    @staticmethod
    def _extract_item_lines(block: str) -> list[str]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        header_index = next(
            (
                index
                for index, line in enumerate(lines)
                if "Product name Seller SKU Shop SKU Price Paid Price" in line
                or "Product name Seller SKU Shop SKU Price Paid" in line
            ),
            -1,
        )
        if header_index < 0:
            return []

        raw_item_lines: list[str] = []
        for line in lines[header_index + 1 :]:
            if re.match(r"^(Subtotal:|Less:|Total:|Shipping:|Net paid:)", line, flags=re.IGNORECASE):
                break
            raw_item_lines.append(line)

        return raw_item_lines

    @staticmethod
    def _flush_lazada_candidate(buffer: list[str], candidates: list[str]) -> None:
        if not buffer:
            return
        candidate = " ".join(buffer).strip()
        if candidate:
            candidates.append(candidate)
        buffer.clear()

    @staticmethod
    def _has_two_amount_tail(value: str) -> bool:
        amount = r"(?:RM\s*)?-?\d[\d,]*(?:\.\d+)?"
        return bool(re.search(rf"\s{amount}\s+{amount}$", value.strip(), flags=re.IGNORECASE))

    @staticmethod
    def _add_merged_item(merged_items: dict[tuple[str, str], dict[str, Any]], item: dict[str, Any]) -> None:
        product_name = item["product_name"]
        seller_sku = item["seller_sku"]
        unit_price = item["unit_price"]
        line_total = item["line_total"]
        key = (product_name, seller_sku)

        if key not in merged_items:
            merged_items[key] = {
                "product_name": product_name,
                "seller_sku": seller_sku,
                "shop_sku": item["shop_sku"],
                "quantity": 0,
                "unit_price": unit_price,
                "line_total": Decimal("0"),
            }

        merged_items[key]["quantity"] += 1
        merged_items[key]["line_total"] += line_total
        if merged_items[key]["unit_price"] == Decimal("0"):
            merged_items[key]["unit_price"] = unit_price
