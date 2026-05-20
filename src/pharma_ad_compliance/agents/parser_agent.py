"""Parser agent — turns a Creative (text / image / url / pdf) into a ParsedCreative."""
from __future__ import annotations

import base64
import re
from pathlib import Path

import httpx
import pymupdf
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from ..schemas import (
    Creative,
    ImageCreative,
    ParsedCreative,
    PdfCreative,
    TextCreative,
    UrlCreative,
)
from ._llm import run_json

# Если текстовый слой PDF выдал меньше символов на страницу — переключаемся на Vision OCR.
_PDF_MIN_CHARS_PER_PAGE = 80
# Растеризация для Vision — 200 DPI хватает для распознавания мелкого шрифта в дисклеймерах.
_PDF_RENDER_DPI = 200

_URL_FETCH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_HTML_TAGS_TO_STRIP = ("script", "style", "noscript", "iframe", "svg", "footer", "header", "nav")

_VISION_SYSTEM_PROMPT = """\
Ты — OCR-агент для русскоязычной рекламы лекарственных средств.
Извлеки из изображения весь видимый текст в порядке чтения. Сохрани мелкий шрифт
(дисклеймеры, юридические оговорки). Не интерпретируй и не комментируй.

Верни строго JSON одного формата:
{"text": "<извлечённый текст одной строкой или с переносами \\n>"}
"""


class _OcrResult(BaseModel):
    text: str = Field(default="")


async def parse(creative: Creative) -> ParsedCreative:
    if isinstance(creative, TextCreative):
        return ParsedCreative(
            source_kind="text",
            extracted_text=creative.text,
            creative_id=creative.creative_id,
        )
    if isinstance(creative, UrlCreative):
        text, title = await _fetch_url(str(creative.url))
        return ParsedCreative(
            source_kind="url",
            extracted_text=text,
            creative_id=creative.creative_id,
            metadata={"url": str(creative.url), "title": title},
        )
    if isinstance(creative, ImageCreative):
        text = await _ocr_image_bytes(
            data=creative.image_path.read_bytes(),
            media_type=_guess_media_type(creative.image_path.name),
        )
        return ParsedCreative(
            source_kind="image",
            extracted_text=text,
            creative_id=creative.creative_id,
            metadata={"image_path": str(creative.image_path)},
        )
    if isinstance(creative, PdfCreative):
        text, meta = await _parse_pdf(creative.pdf_path)
        return ParsedCreative(
            source_kind="pdf",
            extracted_text=text,
            creative_id=creative.creative_id,
            metadata=meta,
        )
    raise TypeError(f"Unsupported creative type: {type(creative).__name__}")


async def _fetch_url(url: str) -> tuple[str, str]:
    async with httpx.AsyncClient(
        timeout=_URL_FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "pharma-ad-compliance/0.1 (+local research tool)"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(list(_HTML_TAGS_TO_STRIP)):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    # Prefer <main> / <article> if present; fall back to <body>.
    root = soup.find("main") or soup.find("article") or soup.body or soup
    raw = root.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", raw)).strip()
    return text, title


async def _ocr_image_bytes(*, data: bytes, media_type: str, hint: str = "изображения") -> str:
    """Run Claude Vision OCR over a single image blob (encoded inline as data URL)."""
    b64 = base64.b64encode(data).decode("ascii")
    # claude-agent-sdk's `query` does not yet take attachments directly, so we
    # inline the image as a data URL inside a markdown image — the underlying
    # CLI understands this pattern.
    prompt = (
        f"Извлеки весь текст с {hint}.\n\n"
        f"![image](data:{media_type};base64,{b64})"
    )
    result = await run_json(
        prompt=prompt,
        system_prompt=_VISION_SYSTEM_PROMPT,
        schema=_OcrResult,
    )
    return result.text


async def _parse_pdf(pdf_path: Path) -> tuple[str, dict[str, str]]:
    """Extract text from a PDF.

    Strategy: first try the embedded text layer (PyMuPDF). If a page yields too
    little text (< `_PDF_MIN_CHARS_PER_PAGE`), that page is likely image-only
    (designer-exported mockup, scan) — re-render it at `_PDF_RENDER_DPI` and pass
    through Claude Vision. Pages are processed in document order; OCR pages are
    run sequentially to keep rate limits sane on large mockups.
    """
    doc = pymupdf.open(pdf_path)
    try:
        page_count = doc.page_count
        per_page_text: list[str] = []
        ocr_pages: list[int] = []
        for i in range(page_count):
            page = doc.load_page(i)
            text = (page.get_text("text") or "").strip()
            if len(text) >= _PDF_MIN_CHARS_PER_PAGE:
                per_page_text.append(text)
            else:
                # Defer: rasterize + Vision-OCR this page below.
                pix = page.get_pixmap(dpi=_PDF_RENDER_DPI, alpha=False)
                png_bytes = pix.tobytes("png")
                ocr_text = await _ocr_image_bytes(
                    data=png_bytes,
                    media_type="image/png",
                    hint=f"страницы {i + 1} PDF",
                )
                per_page_text.append(ocr_text.strip())
                ocr_pages.append(i + 1)
    finally:
        doc.close()

    combined = "\n\n".join(
        f"--- стр. {idx + 1} ---\n{text}" for idx, text in enumerate(per_page_text) if text
    ).strip()
    meta: dict[str, str] = {
        "pdf_path": str(pdf_path),
        "page_count": str(page_count),
    }
    if ocr_pages:
        meta["ocr_pages"] = ",".join(str(p) for p in ocr_pages)
    return combined, meta


def _guess_media_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return "image/png"
