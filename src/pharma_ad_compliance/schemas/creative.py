from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DrugClass(str, Enum):
    """Pharmaceutical product category — defines which restrictions of FZ-38 art. 24 apply."""

    RX = "RX"        # Prescription-only — most restrictions apply, ads outside professional outlets are largely forbidden
    OTC = "OTC"      # Over-the-counter — most common case for advertising
    BAD = "BAD"      # БАД (dietary supplement) — separate, slightly looser rules
    UNKNOWN = "UNKNOWN"


class TextCreative(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["text"] = "text"
    text: str = Field(..., min_length=1)
    creative_id: str | None = None


class ImageCreative(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["image"] = "image"
    image_path: Path
    creative_id: str | None = None


class UrlCreative(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["url"] = "url"
    url: HttpUrl
    creative_id: str | None = None


Creative = Annotated[
    TextCreative | ImageCreative | UrlCreative,
    Field(discriminator="kind"),
]


class ParsedCreative(BaseModel):
    """Normalized creative after parser_agent has extracted plain text from any source."""

    model_config = ConfigDict(frozen=True)

    source_kind: Literal["text", "image", "url"]
    extracted_text: str
    creative_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
