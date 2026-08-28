from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber


@dataclass(frozen=True)
class PdfWord:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class PdfPage:
    number: int
    width: float
    height: float
    text: str
    words: tuple[PdfWord, ...]


@dataclass(frozen=True)
class PdfDocument:
    text: str
    pages: tuple[PdfPage, ...]


def read_pdf_document(pdf_path: str | Path, *, include_words: bool = True) -> PdfDocument:
    pages: list[PdfPage] = []
    text_chunks: list[str] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text:
                text_chunks.append(page_text)

            words: tuple[PdfWord, ...] = ()
            if include_words:
                extracted_words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=3,
                    keep_blank_chars=False,
                    use_text_flow=False,
                )
                words = tuple(
                    PdfWord(
                        text=str(word.get("text", "")),
                        x0=float(word.get("x0", 0)),
                        x1=float(word.get("x1", 0)),
                        top=float(word.get("top", 0)),
                        bottom=float(word.get("bottom", 0)),
                    )
                    for word in extracted_words
                    if str(word.get("text", "")).strip()
                )

            pages.append(
                PdfPage(
                    number=page_number,
                    width=float(page.width),
                    height=float(page.height),
                    text=page_text,
                    words=words,
                )
            )

    return PdfDocument(text="\n".join(text_chunks), pages=tuple(pages))


def read_pdf_document_selective_words(
    pdf_path: str | Path,
    should_include_words: Callable[[str], bool],
) -> PdfDocument:
    pages: list[PdfPage] = []
    page_data: list[tuple[int, float, float, str, Any]] = []
    text_chunks: list[str] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text:
                text_chunks.append(page_text)
            page_data.append((page_number, float(page.width), float(page.height), page_text, page))

        document_text = "\n".join(text_chunks)
        include_words = should_include_words(document_text)

        for page_number, width, height, page_text, page in page_data:
            words: tuple[PdfWord, ...] = ()
            if include_words:
                extracted_words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=3,
                    keep_blank_chars=False,
                    use_text_flow=False,
                )
                words = tuple(
                    PdfWord(
                        text=str(word.get("text", "")),
                        x0=float(word.get("x0", 0)),
                        x1=float(word.get("x1", 0)),
                        top=float(word.get("top", 0)),
                        bottom=float(word.get("bottom", 0)),
                    )
                    for word in extracted_words
                    if str(word.get("text", "")).strip()
                )

            pages.append(
                PdfPage(
                    number=page_number,
                    width=width,
                    height=height,
                    text=page_text,
                    words=words,
                )
            )

    return PdfDocument(text=document_text, pages=tuple(pages))
