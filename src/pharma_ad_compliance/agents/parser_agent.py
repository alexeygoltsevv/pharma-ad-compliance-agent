"""Parser agent — turns a Creative (text / image / url) into a ParsedCreative."""
from __future__ import annotations

import base64
import re

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from ..schemas import (
    Creative,
    ImageCreative,
    ParsedCreative,
    TextCreative,
    UrlCreative,
)
from ._llm import run_json

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
        text = await _ocr_image(creative.image_path)
        return ParsedCreative(
            source_kind="image",
            extracted_text=text,
            creative_id=creative.creative_id,
            metadata={"image_path": str(creative.image_path)},
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


async def _ocr_image(image_path) -> str:
    # Encode locally so we never reveal the absolute path to the model.
    data = image_path.read_bytes()
    media_type = _guess_media_type(image_path.name)
    b64 = base64.b64encode(data).decode("ascii")
    # claude-agent-sdk's `query` does not yet take attachments directly, so we
    # inline the image as a data URL inside a markdown image — the underlying
    # CLI understands this pattern.
    prompt = (
        "Извлеки весь текст с изображения.\n\n"
        f"![banner](data:{media_type};base64,{b64})"
    )
    result = await run_json(
        prompt=prompt,
        system_prompt=_VISION_SYSTEM_PROMPT,
        schema=_OcrResult,
    )
    return result.text


def _guess_media_type(filename: str) -> str:
    name = filename.lower()
    if name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".gif"):
        return "image/gif"
    return "image/png"
