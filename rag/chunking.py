"""PDF → chunks, block-aware (not naive token windows).

Uses PyMuPDF's block extraction (paragraph-level text blocks w/ bounding
boxes) and groups consecutive blocks on the same page up to a target word
count. Chunks never cross a page boundary — keeps the stored bbox meaningful
for citation highlighting (one page, one rectangle union).

~500-800 token chunks per PLAN.md; approximated here as ~375-600 words
(English: ~0.75 words/token).
"""

from __future__ import annotations

from typing import Iterator

import fitz  # PyMuPDF

TARGET_WORDS = 450
MAX_WORDS = 650
MIN_CHUNK_WORDS = 15  # drop trivial page-number/footer-only chunks


def _extract_page_blocks(pdf_path: str) -> Iterator[tuple[int, tuple[float, float, float, float], str]]:
    doc = fitz.open(pdf_path)
    try:
        for page_no in range(doc.page_count):
            page = doc[page_no]
            for block in page.get_text("blocks"):
                x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
                text = text.strip()
                if text:
                    yield page_no, (x0, y0, x1, y1), text
    finally:
        doc.close()


def _union_bbox(a: list[float] | None, b: tuple[float, float, float, float]) -> list[float]:
    if a is None:
        return [b[0], b[1], b[2], b[3]]
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def chunk_pdf(pdf_path: str) -> list[dict]:
    """Returns list of {page, bbox: {x0,y0,x1,y1}, text} dicts."""
    chunks: list[dict] = []
    page_no_cur: int | None = None
    text_parts: list[str] = []
    bbox_cur: list[float] | None = None
    words_cur = 0

    def flush() -> None:
        nonlocal text_parts, bbox_cur, words_cur
        text = "\n".join(text_parts).strip()
        if text and words_cur >= MIN_CHUNK_WORDS:
            chunks.append(
                {
                    "page": page_no_cur,
                    "bbox": {
                        "x0": bbox_cur[0], "y0": bbox_cur[1],
                        "x1": bbox_cur[2], "y1": bbox_cur[3],
                    },
                    "text": text,
                }
            )
        text_parts = []
        bbox_cur = None
        words_cur = 0

    for page_no, bbox, text in _extract_page_blocks(pdf_path):
        word_count = len(text.split())

        if page_no_cur is not None and page_no != page_no_cur:
            flush()
        page_no_cur = page_no

        if words_cur > 0 and words_cur + word_count > MAX_WORDS:
            flush()

        text_parts.append(text)
        words_cur += word_count
        bbox_cur = _union_bbox(bbox_cur, bbox)

        if words_cur >= TARGET_WORDS:
            flush()

    flush()
    return chunks
