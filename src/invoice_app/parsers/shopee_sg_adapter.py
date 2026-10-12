"""Shopee SG source boundary for the shared Shopee PDF parsing core."""

from __future__ import annotations

from dataclasses import replace
import re

from ..pdf_document import PdfDocument, PdfPage, PdfWord


SHOPEE_SG_SOURCE_HOST = "seller.shopee.sg"
_SG_HOST = re.compile(r"\bseller\.shopee\.sg\b", flags=re.IGNORECASE)
_SG_MONEY = re.compile(
    r"(?<![A-Za-z0-9])(?:S\s*)?\$(?=\s*[-+]?\s*[\d,])",
    flags=re.IGNORECASE,
)


def has_shopee_sg_source_evidence(text: str) -> bool:
    """Return whether the audited SG Seller Centre host is present."""

    return bool(_SG_HOST.search(text or ""))


def normalize_shopee_sg_text(text: str) -> str:
    """Normalize only SG source money prefixes for the shared parser core."""

    return _SG_MONEY.sub("RM", text or "")


def normalize_shopee_sg_document(document: PdfDocument) -> PdfDocument:
    """Return an immutable SG-normalized document, preserving coordinates."""

    return PdfDocument(
        text=normalize_shopee_sg_text(document.text),
        pages=tuple(_normalize_page(page) for page in document.pages),
    )


def _normalize_page(page: PdfPage) -> PdfPage:
    return replace(
        page,
        text=normalize_shopee_sg_text(page.text),
        words=tuple(_normalize_word(word) for word in page.words),
        styled_words=tuple(_normalize_word(word) for word in page.styled_words),
    )


def _normalize_word(word: PdfWord) -> PdfWord:
    return replace(word, text=normalize_shopee_sg_text(word.text))
