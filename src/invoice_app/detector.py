from __future__ import annotations

import re


PLATFORM_PATTERNS = {
    "Shopee": [
        r"Order ID",
        r"New Order",
        r"No\. Product\(s\) Unit Price Quantity Subtotal",
        r"Merchandise Subtotal",
        r"Shipping Fee Paid by Buyer",
        r"SKU:",
    ],
    "Lazada": [
        r"Invoice Number:",
        r"Order Number:",
        r"Order Date:",
        r"Invoice Date:",
        r"Seller SKU",
        r"Paid Price",
        r"Shipping:",
    ],
    "ZENXIN": [
        r"Invoice No\.",
        r"Order No\.",
        r"Date:",
        r"Amount:",
        r"SKU:",
    ],
}


def detect_platform(text: str) -> str | None:
    cleaned = (text or "").replace("\r", " ").replace("\n", " ")
    scores = {}
    for platform, patterns in PLATFORM_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if re.search(pattern, cleaned, flags=re.IGNORECASE):
                score += 1
        if score:
            scores[platform] = score
    if not scores:
        return None
    best_platform, best_score = max(scores.items(), key=lambda item: item[1])
    return best_platform if best_score >= 2 else None
