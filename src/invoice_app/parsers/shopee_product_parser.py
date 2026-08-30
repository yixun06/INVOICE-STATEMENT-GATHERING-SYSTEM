from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from ..pdf_document import PdfDocument, PdfPage, PdfWord
from ..utils.normalize import normalize_whitespace, parse_decimal, parse_quantity


PRODUCT_STOP_MARKERS = (
    "Hide Income Details",
    "Merchandise Subtotal",
    "Order Adjustment",
    "Buyer Payment",
    "Estimated Order Income",
    "Order Income",
    "Final Amount",
)


@dataclass(frozen=True)
class _Row:
    top: float
    bottom: float
    words: tuple[PdfWord, ...]

    @property
    def text(self) -> str:
        return normalize_whitespace(" ".join(word.text for word in self.words))


@dataclass(frozen=True)
class _Columns:
    product_left: float
    unit_left: float
    unit_quantity_boundary: float
    quantity_subtotal_boundary: float


def parse_positioned_products(document: PdfDocument) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in document.pages:
        items.extend(_parse_positioned_page(page))
    return items


def parse_text_products(text: str) -> list[dict[str, Any]]:
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    header_indexes = [
        index
        for index, line in enumerate(lines)
        if "Product(s)" in line and "Quantity" in line and "Subtotal" in line
    ]
    if not header_indexes:
        return _parse_complete_rows(lines)

    items: list[dict[str, Any]] = []
    for header_position, header_index in enumerate(header_indexes):
        next_header = header_indexes[header_position + 1] if header_position + 1 < len(header_indexes) else len(lines)
        section_end = next_header
        for index in range(header_index + 1, next_header):
            if _is_stop_line(lines[index]):
                section_end = index
                break

        section = lines[header_index + 1 : section_end]
        sku_indexes = [index for index, line in enumerate(section) if re.search(r"\bSKU\s*:", line, re.I)]
        previous_sku = -1
        for sku_index in sku_indexes:
            block = section[previous_sku + 1 : sku_index + 1]
            item = _parse_text_item_block(block)
            if item:
                items.append(item)
            previous_sku = sku_index

    return items or _parse_complete_rows(lines)


def reconcile_product_candidates(
    positioned: list[dict[str, Any]],
    text_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not positioned:
        return text_items
    if not text_items:
        return positioned

    text_by_sku = {
        str(item.get("seller_sku", "")).strip(): item
        for item in text_items
        if str(item.get("seller_sku", "")).strip()
    }
    result: list[dict[str, Any]] = []
    seen_skus: set[str] = set()

    for item in positioned:
        merged = dict(item)
        sku = str(merged.get("seller_sku", "")).strip()
        fallback = text_by_sku.get(sku)
        if fallback:
            # Text flow preserves multilingual product-name order more reliably,
            # while positioned words remain authoritative for numeric columns.
            if fallback.get("product_name") and int(fallback.get("quantity", 0) or 0) > 0:
                merged["product_name"] = fallback.get("product_name", "")
            if int(merged.get("quantity", 0) or 0) <= 0:
                merged["quantity"] = fallback.get("quantity", 0)
            if merged.get("unit_price", Decimal("0")) == 0:
                merged["unit_price"] = fallback.get("unit_price", Decimal("0"))
            if merged.get("line_total", Decimal("0")) == 0:
                merged["line_total"] = fallback.get("line_total", Decimal("0"))
            if merged.get("source_line_subtotal") is None:
                merged["source_line_subtotal"] = fallback.get("source_line_subtotal")
        result.append(merged)
        if sku:
            seen_skus.add(sku)

    for item in text_items:
        sku = str(item.get("seller_sku", "")).strip()
        if sku and sku not in seen_skus and _is_complete_item(item):
            result.append(item)

    return result


def _parse_positioned_page(page: PdfPage) -> list[dict[str, Any]]:
    rows = _group_words_into_rows(page.words)
    header_indexes = [index for index, row in enumerate(rows) if _is_product_header(row.text)]
    items: list[dict[str, Any]] = []

    for header_position, header_index in enumerate(header_indexes):
        columns = _columns_from_header(rows[header_index])
        if columns is None:
            continue

        next_header = header_indexes[header_position + 1] if header_position + 1 < len(header_indexes) else len(rows)
        section_end = next_header
        for index in range(header_index + 1, next_header):
            if _is_stop_line(rows[index].text):
                section_end = index
                break

        sku_indexes = [
            index
            for index in range(header_index + 1, section_end)
            if re.search(r"\bSKU\s*:", rows[index].text, flags=re.IGNORECASE)
        ]
        section_items: list[dict[str, Any]] = []
        previous_sku = header_index
        for sku_index in sku_indexes:
            block = rows[previous_sku + 1 : sku_index + 1]
            item = _parse_positioned_item_block(block, columns)
            if item:
                item["source_page"] = page.number
                section_items.append(item)
            previous_sku = sku_index

        if not sku_indexes:
            section_items = _parse_positioned_items_without_sku(
                rows[header_index + 1 : section_end],
                columns,
                page.number,
            )

        _apply_group_promotion(
            section_items,
            promotion_group_id=_promotion_group_id(page.number, header_position + 1),
        )
        items.extend(section_items)

    return items


def _group_words_into_rows(words: tuple[PdfWord, ...]) -> list[_Row]:
    rows: list[list[Any]] = []
    for word in sorted(words, key=lambda current: (current.top, current.x0)):
        row = next((candidate for candidate in reversed(rows[-4:]) if abs(candidate[0] - word.top) <= 2.0), None)
        if row is None:
            rows.append([word.top, word.bottom, [word]])
            continue
        row[1] = max(float(row[1]), word.bottom)
        row[2].append(word)

    return [
        _Row(
            top=float(top),
            bottom=float(bottom),
            words=tuple(sorted(row_words, key=lambda current: current.x0)),
        )
        for top, bottom, row_words in rows
    ]


def _columns_from_header(header: _Row) -> _Columns | None:
    product_words = [word for word in header.words if "product" in word.text.lower()]
    unit_words = [word for word in header.words if word.text.lower() in {"unit", "price"}]
    quantity_words = [word for word in header.words if "quantity" in word.text.lower()]
    subtotal_words = [word for word in header.words if "subtotal" in word.text.lower()]
    if not product_words or not unit_words or not quantity_words or not subtotal_words:
        return None

    unit_center = (min(word.x0 for word in unit_words) + max(word.x1 for word in unit_words)) / 2
    quantity_center = (min(word.x0 for word in quantity_words) + max(word.x1 for word in quantity_words)) / 2
    subtotal_center = (min(word.x0 for word in subtotal_words) + max(word.x1 for word in subtotal_words)) / 2
    return _Columns(
        product_left=min(word.x0 for word in product_words),
        unit_left=min(word.x0 for word in unit_words) - 20,
        unit_quantity_boundary=(unit_center + quantity_center) / 2,
        quantity_subtotal_boundary=(quantity_center + subtotal_center) / 2,
    )


def _parse_positioned_item_block(block: list[_Row], columns: _Columns) -> dict[str, Any] | None:
    if not block:
        return None

    sku_match = re.search(r"\bSKU\s*:\s*([^\s]+)", block[-1].text, flags=re.IGNORECASE)
    if not sku_match:
        return None

    metric_row: _Row | None = None
    unit_price = Decimal("0")
    quantity = 0
    line_total = Decimal("0")
    source_line_subtotal: Decimal | None = None
    for row in reversed(block[:-1]):
        metrics = _metrics_from_positioned_row(row, columns)
        if metrics is None:
            continue
        unit_price, quantity, source_line_subtotal = metrics
        line_total = source_line_subtotal if source_line_subtotal is not None else Decimal("0")
        metric_row = row
        break

    name_parts: list[str] = []
    variation = ""
    promotion = ""
    for row in block[:-1]:
        if _promotion_details(row.text) is not None:
            promotion = row.text
            continue
        product_words = [
            word
            for word in row.words
            if word.x0 >= columns.product_left - 2 and word.center_x < columns.unit_left
        ]
        candidate = normalize_whitespace(" ".join(word.text for word in product_words))
        if not candidate or _is_product_noise(candidate):
            continue
        if candidate.lower().startswith("variation:"):
            variation = candidate
            continue
        if _promotion_details(candidate) is not None:
            promotion = candidate
            continue
        name_parts.append(candidate)

    product_name = normalize_whitespace(" ".join(name_parts))
    if variation and variation.lower() not in product_name.lower():
        product_name = normalize_whitespace(f"{product_name} {variation}")

    promotion_container_subtotal = (
        _subtotal_column_decimal(block[-1], columns)
        if promotion and source_line_subtotal is None
        else None
    )
    return {
        "product_name": product_name,
        "seller_sku": sku_match.group(1).strip(),
        "quantity": quantity,
        "unit_price": unit_price,
        "line_total": line_total,
        "source_line_subtotal": source_line_subtotal,
        "promotion_container_subtotal": promotion_container_subtotal,
        "variation": variation,
        "promotion": promotion,
        "evidence": "positioned",
        "metric_top": metric_row.top if metric_row else None,
    }


def _metrics_from_positioned_row(
    row: _Row,
    columns: _Columns,
) -> tuple[Decimal, int, Decimal | None] | None:
    unit_words = [
        word
        for word in row.words
        if columns.unit_left <= word.center_x < columns.unit_quantity_boundary
    ]
    quantity_words = [
        word
        for word in row.words
        if columns.unit_quantity_boundary <= word.center_x < columns.quantity_subtotal_boundary
    ]
    subtotal_words = [word for word in row.words if word.center_x >= columns.quantity_subtotal_boundary]

    unit_price = _first_decimal(" ".join(word.text for word in unit_words))
    quantity_match = re.search(r"\b(\d+)\b", " ".join(word.text for word in quantity_words))
    line_total = _first_decimal(" ".join(word.text for word in subtotal_words))
    if unit_price is None or not quantity_match:
        return None

    quantity = parse_quantity(quantity_match.group(1))
    if quantity <= 0:
        return None
    return unit_price, quantity, line_total



def _subtotal_column_decimal(row: _Row, columns: _Columns) -> Decimal | None:
    subtotal_words = [
        word
        for word in row.words
        if word.center_x >= columns.quantity_subtotal_boundary
    ]
    return _first_decimal(" ".join(word.text for word in subtotal_words))

def _parse_positioned_items_without_sku(
    rows: list[_Row],
    columns: _Columns,
    page_number: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    previous_metric = -1
    for metric_index, row in enumerate(rows):
        metrics = _metrics_from_positioned_row(row, columns)
        if metrics is None or metrics[2] is None or metrics[2] == 0:
            continue
        unit_price, quantity, line_total = metrics
        name_parts: list[str] = []
        variation = ""
        for candidate_row in rows[previous_metric + 1 : metric_index + 1]:
            product_words = [
                word
                for word in candidate_row.words
                if word.x0 >= columns.product_left - 2 and word.center_x < columns.unit_left
            ]
            candidate = normalize_whitespace(" ".join(word.text for word in product_words))
            if not candidate or _is_product_noise(candidate):
                continue
            if candidate.lower().startswith("variation:"):
                variation = candidate
            elif not re.search(r"(?:Any\s+)?\d+\s+at\s+RM", candidate, flags=re.IGNORECASE):
                name_parts.append(candidate)

        product_name = normalize_whitespace(" ".join(name_parts))
        if variation and variation.lower() not in product_name.lower():
            product_name = normalize_whitespace(f"{product_name} {variation}")
        items.append(
            {
                "product_name": product_name,
                "seller_sku": "",
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "source_line_subtotal": line_total,
                "variation": variation,
                "promotion": "",
                "evidence": "positioned-no-sku",
                "source_page": page_number,
                "sku_missing_in_source": True,
            }
        )
        previous_metric = metric_index
    return items


def _apply_group_promotion(
    items: list[dict[str, Any]],
    *,
    promotion_group_id: str,
) -> None:
    """Preserve promotion facts only when a positioned container is clear."""
    group_number = 0
    index = 0
    while index < len(items):
        item = items[index]
        promotion = _promotion_details(str(item.get("promotion", "")))
        if promotion is None:
            index += 1
            continue

        promotion_label, target_qty, advertised_amount, discount_percent = promotion
        source_group_total = item.get("promotion_container_subtotal")
        if not isinstance(source_group_total, Decimal):
            _mark_incomplete_promotion(
                item, promotion_label, target_qty, advertised_amount, discount_percent,
                "Promotion container has no coordinate-confirmed source group subtotal.",
            )
            index += 1
            continue

        members = [item]
        next_index = index + 1
        while next_index < len(items):
            next_item = items[next_index]
            if _promotion_details(str(next_item.get("promotion", ""))) is not None:
                break
            if next_item.get("source_line_subtotal") is not None:
                break
            members.append(next_item)
            next_index += 1

        if any(int(member.get("quantity", 0) or 0) <= 0 for member in members):
            _mark_incomplete_promotion(
                item, promotion_label, target_qty, advertised_amount, discount_percent,
                "Promotion container has a member with unavailable quantity.",
            )
            index += 1
            continue

        group_number += 1
        group_id = f"{promotion_group_id}:group{group_number}"
        participating_qty = sum(int(member.get("quantity", 0) or 0) for member in members)
        for member in members:
            member["promotion_group_id"] = group_id
            member["promotion_label"] = promotion_label
            member["promotion_target_qty"] = target_qty
            if advertised_amount is not None:
                member["promotion_advertised_amount"] = advertised_amount
            if discount_percent is not None:
                member["promotion_discount_percent"] = discount_percent
            member["source_group_total"] = source_group_total
            member["promotion_group_total"] = source_group_total
            member["participating_qty"] = participating_qty
            member["promotion_member_qty"] = int(member.get("quantity", 0) or 0)
            member["promotion"] = promotion_label
            member["source_line_subtotal"] = None
            member["line_total"] = None
        index = next_index


def _promotion_details(
    promotion: str,
) -> tuple[str, int, Decimal | None, Decimal | None] | None:
    amount_match = re.search(
        r"Any\s+(\d+)\s+at\s+RM\s*([\d,]+(?:\.\d+)?)",
        promotion,
        re.I,
    )
    if amount_match:
        return (
            normalize_whitespace(amount_match.group(0)),
            int(amount_match.group(1)),
            parse_decimal(amount_match.group(2)).quantize(Decimal("0.01")),
            None,
        )
    percent_match = re.search(
        r"Any\s+(\d+)\s+enjoy\s+([\d,]+(?:\.\d+)?)\s*%\s*off",
        promotion,
        re.I,
    )
    if percent_match:
        return (
            normalize_whitespace(percent_match.group(0)),
            int(percent_match.group(1)),
            None,
            parse_decimal(percent_match.group(2)),
        )
    return None


def _mark_incomplete_promotion(
    item: dict[str, Any],
    promotion_label: str,
    target_qty: int,
    advertised_amount: Decimal | None,
    discount_percent: Decimal | None,
    reason: str,
) -> None:
    item["promotion_metadata_status"] = "incomplete"
    item["promotion_label"] = promotion_label
    item["promotion_target_qty"] = target_qty
    if advertised_amount is not None:
        item["promotion_advertised_amount"] = advertised_amount
    if discount_percent is not None:
        item["promotion_discount_percent"] = discount_percent
    item["promotion_incomplete_reason"] = reason


def _promotion_group_id(page_number: int, section_number: int) -> str:
    return f"shopee-promotion:p{page_number}:section{section_number}"


def _parse_text_item_block(block: list[str]) -> dict[str, Any] | None:
    if not block:
        return None
    sku_match = re.search(r"\bSKU\s*:\s*([^\s]+)", block[-1], flags=re.IGNORECASE)
    if not sku_match:
        return None

    metric_index = -1
    unit_price = Decimal("0")
    quantity = 0
    line_total = Decimal("0")
    source_line_subtotal: Decimal | None = None
    metric_body = ""
    for index in range(len(block) - 2, -1, -1):
        match = re.match(
            r"^\s*\d+\s+(?P<body>.*?)\s*(?P<unit>-?\d+(?:\.\d+)?)\s+"
            r"(?P<qty>\d+)\s+(?P<subtotal>.+?)\s*$",
            block[index],
        )
        if not match:
            continue
        subtotal = _first_decimal(match.group("subtotal"))
        if subtotal is None:
            continue
        metric_index = index
        metric_body = normalize_whitespace(match.group("body"))
        unit_price = parse_decimal(match.group("unit"))
        quantity = parse_quantity(match.group("qty"))
        line_total = subtotal
        source_line_subtotal = subtotal
        break

    name_parts: list[str] = []
    variation = ""
    promotion = ""
    for index, line in enumerate(block[:-1]):
        candidates = [metric_body] if index == metric_index else [line]
        for candidate in candidates:
            candidate = normalize_whitespace(candidate)
            if not candidate or _is_product_noise(candidate):
                continue
            if candidate.lower().startswith("variation:"):
                variation = candidate
            elif re.match(r"^Any\s+\d+\s+at\s+RM", candidate, flags=re.IGNORECASE):
                promotion = candidate
            else:
                name_parts.append(candidate)

    product_name = normalize_whitespace(" ".join(name_parts))
    if variation and variation.lower() not in product_name.lower():
        product_name = normalize_whitespace(f"{product_name} {variation}")

    return {
        "product_name": product_name,
        "seller_sku": sku_match.group(1).strip(),
        "quantity": quantity,
        "unit_price": unit_price,
        "line_total": line_total,
        "source_line_subtotal": source_line_subtotal,
        "variation": variation,
        "promotion": promotion,
        "evidence": "text",
    }


def _parse_complete_rows(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for line in lines:
        row = re.match(
            r"^\s*\d+\s+(?P<name>.+?)\s+(?P<unit>-?\d+(?:\.\d+)?)\s+"
            r"(?P<qty>\d+)\s+(?P<subtotal>-?\d+(?:\.\d+)?)\s*$",
            line,
        )
        if row:
            pending = {
                "product_name": normalize_whitespace(row.group("name")),
                "seller_sku": "",
                "quantity": parse_quantity(row.group("qty")),
                "unit_price": parse_decimal(row.group("unit")),
                "line_total": parse_decimal(row.group("subtotal")),
                "source_line_subtotal": parse_decimal(row.group("subtotal")),
                "variation": "",
                "promotion": "",
                "evidence": "complete-row",
            }
            continue
        sku = re.search(r"\bSKU\s*:\s*([^\s]+)", line, flags=re.IGNORECASE)
        if sku and pending:
            pending["seller_sku"] = sku.group(1).strip()
            items.append(pending)
            pending = None
    return items


def _first_decimal(value: str) -> Decimal | None:
    cleaned = value.replace(",", "")
    match = re.search(r"[-+]?\d+\.\d{2}", cleaned)
    if not match:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _promotion_total(promotion: str) -> Decimal | None:
    match = re.search(r"at\s+RM\s*([\d,]+(?:\.\d+)?)", promotion, flags=re.IGNORECASE)
    return parse_decimal(match.group(1)) if match else None


def _is_product_header(line: str) -> bool:
    lowered = line.lower()
    return "product(s)" in lowered and "quantity" in lowered and "subtotal" in lowered


def _is_stop_line(line: str) -> bool:
    lowered = line.lower()
    return any(marker.lower() in lowered for marker in PRODUCT_STOP_MARKERS) or "seller.shopee.com" in lowered


def _is_product_noise(value: str) -> bool:
    lowered = value.lower().strip()
    if lowered in {
        "payment information",
        "view transaction history",
        "chat now",
        "unit price",
        "quantity",
        "subtotal",
    }:
        return True
    if re.match(r"^(?:any\s+)?\d+\s+at\s+rm", lowered):
        return True
    return bool(re.fullmatch(r"(?:no\.?)?\s*product\(s\)", lowered))


def _is_complete_item(item: dict[str, Any]) -> bool:
    return bool(
        str(item.get("product_name", "")).strip()
        and str(item.get("seller_sku", "")).strip()
        and int(item.get("quantity", 0) or 0) > 0
    )
