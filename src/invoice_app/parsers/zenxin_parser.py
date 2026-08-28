from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..utils.normalize import normalize_whitespace, parse_decimal, parse_quantity
from .base_parser import BaseParser
from .validation import format_validation_errors, validate_product_items


class ZenxinParser(BaseParser):
    platform = "ZENXIN"

    def parse(
        self,
        text: str,
        source_pdf: str,
        batch_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        orders: list[dict[str, Any]] = []
        products: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        order_id = self._extract_order_id(text)
        if not order_id:
            reviews.append(
                self.create_review(
                    batch_id,
                    source_pdf,
                    self.platform,
                    "N/A",
                    "Manual Review",
                    "Order No. could not be detected.",
                )
            )
            return orders, products, reviews

        invoice_number = self._find_pattern(text, r"Invoice No\.\s*([A-Za-z0-9-]+)")
        invoice_date = self._find_pattern(text, r"Date:\s*([0-9/]+)")
        invoice_amount = self._extract_money(text, r"Amount:\s*RM\s*([-\d,.]+)")
        payment_method = self._find_pattern(text, r"Payment method:\s*([^\n]+)")
        gross_sales = self._extract_money(text, r"Subtotal Discount inc\.\s*RM\s*([-\d,.]+)")
        if gross_sales == "":
            gross_sales = invoice_amount
        discount_value = self._extract_money(text, r"Discount\s*-\s*RM\s*([-\d,.]+)")
        net_amount = self._extract_money(text, r"Total\s*RM\s*([-\d,.]+)")
        delivery_fee = self._extract_delivery_fee(text)
        parsed_items = self._parse_items(text)
        order_payload = {
            "batch_id": batch_id,
            "platform": self.platform,
            "order_id": order_id,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "invoice_amount": invoice_amount,
            "payment_method": payment_method,
            "source_pdf": source_pdf,
            "gross_sales": gross_sales,
            "delivery_fee": delivery_fee,
            "commission_fee": "",
            "service_fee": "",
            "transaction_fee": "",
            "voucher": discount_value,
            "shipping_fee": delivery_fee,
            "subtotal": gross_sales,
            "discount": discount_value,
            "total": net_amount,
            "platform_fees": "",
            "ads_fee": "",
            "estimated_order_income": "",
            "net_income": net_amount,
            "net_amount": net_amount,
            "status": "Accepted",
        }
        product_payloads = [
            {
                "batch_id": batch_id,
                "platform": self.platform,
                "order_id": order_id,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "payment_method": payment_method,
                "product_name": item.get("product_name", ""),
                "seller_sku": item.get("seller_sku", ""),
                "quantity": item.get("quantity", ""),
                "unit_price": self._payload_money(item.get("unit_price")),
                "line_total": self._payload_money(item.get("line_total")),
                "line_total_inc_tax": self._payload_money(item.get("line_total")),
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
            return orders, products, reviews

        orders.append(order_payload)
        products.extend(product_payloads)

        return orders, products, reviews

    @staticmethod
    def _extract_order_id(text: str) -> str:
        match = re.search(r"Order No\.?\s*[:\-]?\s*([A-Za-z0-9-]+)", text, flags=re.IGNORECASE)
        return match.group(1) if match else ""

    @staticmethod
    def _find_pattern(text: str, pattern: str) -> str:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return normalize_whitespace(match.group(1)) if match else ""

    def _parse_items(self, text: str) -> list[dict[str, Any]]:
        primary_items = self._parse_items_from_rows(text)
        if not validate_product_items(primary_items, require_sku=True):
            return primary_items

        fallback_items = self._parse_items_from_sku_anchors(text)
        if fallback_items and not validate_product_items(fallback_items, require_sku=True):
            return fallback_items

        return primary_items or fallback_items

    def _parse_items_from_rows(self, text: str) -> list[dict[str, Any]]:
        lines = self._product_section_lines(text)
        items: list[dict[str, Any]] = []
        merged_items: dict[tuple[str, str], dict[str, Any]] = {}
        current_name_parts: list[str] = []
        current_metrics: dict[str, Any] | None = None

        for line in lines:
            if line.startswith("Organic Express by Zenxin"):
                continue
            if line.lower().startswith("sku:"):
                if current_metrics:
                    current_metrics["seller_sku"] = line.split(":", 1)[1].strip()
                    items.append(current_metrics)
                    current_metrics = None
                continue

            metric_match = re.match(r"^(?P<qty>\d+)\s+RM(?P<rest>.+)$", line, flags=re.IGNORECASE)
            if metric_match:
                qty = parse_quantity(metric_match.group("qty"))
                amounts = re.findall(r"RM\s*([-\d,.]+)", line, flags=re.IGNORECASE)
                if not amounts:
                    continue
                unit_price = parse_decimal(amounts[0])
                line_total = parse_decimal(amounts[-1])
                current_metrics = {
                    "product_name": normalize_whitespace(" ".join(current_name_parts)),
                    "seller_sku": "",
                    "quantity": qty,
                    "unit_price": unit_price,
                    "line_total": line_total,
                }
                current_name_parts = []
                continue

            current_name_parts.append(line)

        for item in items:
            key = (item["product_name"], item["seller_sku"])
            if key not in merged_items:
                merged_items[key] = {
                    "product_name": item["product_name"],
                    "seller_sku": item["seller_sku"],
                    "quantity": 0,
                    "unit_price": item["unit_price"],
                    "line_total": Decimal("0"),
                }
            merged_items[key]["quantity"] += item["quantity"]
            merged_items[key]["line_total"] += item["line_total"]

        return list(merged_items.values())

    def _parse_items_from_sku_anchors(self, text: str) -> list[dict[str, Any]]:
        lines = self._product_section_lines(text)
        items: list[dict[str, Any]] = []

        previous_sku_index = -1
        for index, line in enumerate(lines):
            if not line.lower().startswith("sku:"):
                continue

            seller_sku = line.split(":", 1)[1].strip()
            metric_index = self._find_previous_metric_line(lines, index)
            if metric_index < 0:
                previous_sku_index = index
                continue

            metrics = self._parse_zenxin_metric_line(lines[metric_index])
            if not metrics:
                previous_sku_index = index
                continue

            name_parts = [
                candidate
                for candidate in lines[previous_sku_index + 1 : metric_index]
                if not self._is_zenxin_noise_line(candidate)
            ]
            if metrics["name_prefix"]:
                name_parts.append(metrics["name_prefix"])

            items.append(
                {
                    "product_name": normalize_whitespace(" ".join(name_parts)),
                    "seller_sku": seller_sku,
                    "quantity": metrics["quantity"],
                    "unit_price": metrics["unit_price"],
                    "line_total": metrics["line_total"],
                }
            )
            previous_sku_index = index

        return self._merge_items(items)

    @staticmethod
    def _product_section_lines(text: str) -> list[str]:
        lines = [normalize_whitespace(line) for line in text.splitlines() if line.strip()]
        product_lines: list[str] = []
        in_product_section = False

        for line in lines:
            if "Product Qty Price Total" in line:
                in_product_section = True
                continue
            if not in_product_section:
                continue
            if line.startswith(("Free Shipping", "Standard Delivery", "Subtotal", "Discount", "Total", "Notes:")):
                break
            product_lines.append(line)

        return product_lines

    @staticmethod
    def _find_previous_metric_line(lines: list[str], sku_index: int) -> int:
        for index in range(sku_index - 1, -1, -1):
            if ZenxinParser._parse_zenxin_metric_line(lines[index]):
                return index
            if lines[index].lower().startswith("sku:"):
                break
        return -1

    @staticmethod
    def _parse_zenxin_metric_line(line: str) -> dict[str, Any] | None:
        amount = r"RM\s*[-\d,.]+"
        match = re.match(rf"^(?:(?P<name_prefix>.+?)\s+)?(?P<qty>\d+)\s+(?P<amounts>{amount}.*)$", line, flags=re.IGNORECASE)
        if not match:
            return None

        amounts = re.findall(r"RM\s*([-\d,.]+)", line, flags=re.IGNORECASE)
        if not amounts:
            return None

        return {
            "name_prefix": normalize_whitespace(match.group("name_prefix") or ""),
            "quantity": parse_quantity(match.group("qty")),
            "unit_price": parse_decimal(amounts[0]),
            "line_total": parse_decimal(amounts[-1]),
        }

    @staticmethod
    def _is_zenxin_noise_line(line: str) -> bool:
        return line.startswith("Organic Express by Zenxin") or "Product Qty Price Total" in line

    @staticmethod
    def _merge_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged_items: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            key = (item["product_name"], item["seller_sku"])
            if key not in merged_items:
                merged_items[key] = {
                    "product_name": item["product_name"],
                    "seller_sku": item["seller_sku"],
                    "quantity": 0,
                    "unit_price": item["unit_price"],
                    "line_total": Decimal("0"),
                }
            merged_items[key]["quantity"] += item["quantity"]
            merged_items[key]["line_total"] += item["line_total"]
        return list(merged_items.values())

    @staticmethod
    def _extract_money(text: str, pattern: str) -> str:
        match = re.search(pattern, text, flags=re.IGNORECASE)
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

    def _extract_delivery_fee(self, text: str) -> str:
        match = re.search(
            r"(?:Free Shipping|Standard Delivery)\s+RM\s*([-\d,.]+)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return ""
        return str(parse_decimal(match.group(1)).quantize(Decimal("0.01")))
