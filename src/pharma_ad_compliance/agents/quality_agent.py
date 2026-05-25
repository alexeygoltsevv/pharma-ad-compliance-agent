"""Quality agent — scores a rewrite variant against a 5-criterion rubric.

Runs as a separate Haiku pass after the editor + compliance recheck so the
score reflects the final text the user will see. The pipeline calls this
best-effort per variant; a failure here leaves `RewriteVariant.quality_score`
None and never aborts the report.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from pydantic import ValidationError

from ..schemas import RewriteScore
from ._llm import HAIKU_MODEL, LLMOutputError, _collect_text, _extract_json

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


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS_PER_SIDE:
        return text
    return text[:_MAX_CHARS_PER_SIDE] + "\n\n[...текст обрезан для оценки...]"


async def score_rewrite(original: str, rewrite: str, frame: str) -> RewriteScore:
    """Score one rewrite variant against the 5-criterion rubric.

    Raises `LLMOutputError` if the model output cannot be parsed or violates the
    `RewriteScore` invariants (sum of breakdown ≠ total, wrong keys, etc.). The
    pipeline catches and downgrades this to `quality_score=None` per variant.
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
    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise LLMOutputError(
            f"quality_agent: invalid JSON from model:\n{raw[:500]}"
        ) from e
    try:
        return RewriteScore.model_validate(data)
    except ValidationError as e:
        raise LLMOutputError(
            f"quality_agent: payload did not match RewriteScore: {e}\nRaw: {raw[:500]}"
        ) from e
