"""Quality agent — scores a rewrite variant against a 5-criterion rubric.

Runs as a separate Haiku pass after the editor + compliance recheck so the
score reflects the final text the user will see. The pipeline calls this
best-effort per variant; a failure here leaves `RewriteVariant.quality_score`
None and never aborts the report.
"""
from __future__ import annotations

import json
import logging
import re
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..schemas import REWRITE_SCORE_KEYS, RewriteScore
from ._llm import HAIKU_MODEL, LLMOutputError, _collect_text, _extract_json

logger = logging.getLogger(__name__)

_MODEL = HAIKU_MODEL

# Static system prompt — the rubric itself. Cached so we don't re-read the
# Markdown file on every pipeline call (3 variants per creative would otherwise
# do 3 syscalls per run).
_RUBRIC_FILE = Path(__file__).resolve().parents[1] / "prompts" / "rewrite_quality_rubric.md"


@cache
def _rubric_prompt() -> str:
    return _RUBRIC_FILE.read_text(encoding="utf-8")


# Soft caps so a creative with a 10 000-word landing page can't blow up the
# scorer. The scorer compares *style*, not exhaustive content — a few thousand
# chars per side is plenty of signal.
_MAX_CHARS_PER_SIDE = 6000

# Required breakdown keys — bound to the schema's single source of truth so the
# normalizer below and RewriteScore validation can never drift (a mismatch would
# silently turn every score into None). Kept as a local alias for readability.
_BREAKDOWN_KEYS = REWRITE_SCORE_KEYS
_SCORE_MIN = 0
_SCORE_MAX = 20
_NOTES_MAX_CHARS = 500

# Pulls content between <score>...</score> tags. Mirrors the editor's
# <creative>...</creative> contract so the model has a single, unambiguous
# output shape across both agents.
_SCORE_TAG_RE = re.compile(
    r"<\s*score\s*>(.*?)<\s*/\s*score\s*>",
    re.DOTALL | re.IGNORECASE,
)


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS_PER_SIDE:
        return text
    return text[:_MAX_CHARS_PER_SIDE] + "\n\n[...текст обрезан для оценки...]"


def _unwrap_score(raw: str) -> str:
    """Return the JSON body — content between <score>...</score> tags if present,
    else fall back to _extract_json so we still salvage clean payloads from
    models that ignored the wrapping contract.
    """
    match = _SCORE_TAG_RE.search(raw)
    if match:
        return match.group(1).strip()
    logger.warning(
        "quality_agent: response missing <score> markers (len=%d); falling back to JSON extractor",
        len(raw),
    )
    return _extract_json(raw)


def _coerce_int_score(value: Any) -> int:
    """Best-effort coerce a single breakdown value to an int in [0, 20]."""
    if isinstance(value, bool):  # bool subclasses int — reject explicitly
        return 0
    if isinstance(value, int):
        n = value
    elif isinstance(value, float):
        n = round(value)
    elif isinstance(value, str):
        try:
            n = round(float(value.strip()))
        except (ValueError, TypeError):
            return 0
    else:
        return 0
    return max(_SCORE_MIN, min(_SCORE_MAX, n))


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a model-emitted payload into a shape `RewriteScore` will accept.

    The model frequently gets arithmetic wrong (`total` ≠ sum), adds an extra
    `summary` key, omits one of the 5 breakdown keys, or returns scores as
    strings. We salvage what we can:
      - drop unknown top-level keys
      - fill missing breakdown keys with 0
      - clamp each breakdown value into [0, 20]
      - recompute `total` from the normalized breakdown (ignore model's `total`)
      - truncate `notes` to 500 chars, default ""
    """
    raw_breakdown = payload.get("breakdown")
    if not isinstance(raw_breakdown, dict):
        raw_breakdown = {}
    breakdown: dict[str, int] = {
        key: _coerce_int_score(raw_breakdown.get(key, 0)) for key in _BREAKDOWN_KEYS
    }
    total = sum(breakdown.values())
    notes_raw = payload.get("notes")
    if not isinstance(notes_raw, str):
        notes_raw = ""
    notes = notes_raw.strip()[:_NOTES_MAX_CHARS]
    return {"total": total, "breakdown": breakdown, "notes": notes}


async def score_rewrite(original: str, rewrite: str, frame: str) -> RewriteScore:
    """Score one rewrite variant against the 5-criterion rubric.

    Best-effort: model output is normalized (auto-recomputes `total`, clamps
    out-of-range subscores, fills missing keys with 0) before validation. Only
    raises `LLMOutputError` when the output is so malformed we can't extract
    JSON at all. The pipeline catches and downgrades that to
    `quality_score=None` for that variant.
    """
    prompt = (
        f"Рамка переписки: `{frame}`\n\n"
        f"=== ОРИГИНАЛ ===\n{_truncate(original)}\n\n"
        f"=== ПЕРЕПИСКА ({frame}-led) ===\n{_truncate(rewrite)}"
    )
    raw = await _collect_text(
        prompt=prompt,
        system_prompt=_rubric_prompt(),
        model=_MODEL,
    )
    body = _unwrap_score(raw)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise LLMOutputError(
            f"quality_agent: invalid JSON from model:\n{raw[:500]}"
        ) from e
    if not isinstance(data, dict):
        raise LLMOutputError(
            f"quality_agent: payload is not a JSON object: {type(data).__name__}"
        )
    normalized = _normalize_payload(data)
    try:
        return RewriteScore.model_validate(normalized)
    except ValidationError as e:  # pragma: no cover — normalization should prevent
        raise LLMOutputError(
            f"quality_agent: normalized payload still invalid: {e}\nRaw: {raw[:500]}"
        ) from e
