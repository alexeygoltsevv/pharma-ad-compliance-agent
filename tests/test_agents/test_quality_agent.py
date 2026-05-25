"""Tests for the rewrite-quality scorer (quality_agent.score_rewrite).

The scorer is best-effort: it normalises model output (auto-recomputes total,
clamps out-of-range subscores, fills missing breakdown keys with 0) before
validation, so it only raises LLMOutputError when JSON can't be extracted at
all. The pipeline downgrades any raise to `quality_score=None`.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pharma_ad_compliance.agents import quality_agent
from pharma_ad_compliance.agents._llm import LLMOutputError
from pharma_ad_compliance.agents.quality_agent import (
    _BREAKDOWN_KEYS,
    _coerce_int_score,
    _normalize_payload,
    _unwrap_score,
)
from pharma_ad_compliance.schemas import RewriteScore

# The agent imports _collect_text into its own namespace; patch it there.
_COLLECT_TEXT_PATH = "pharma_ad_compliance.agents.quality_agent._collect_text"


def _wrapped(payload: Any) -> str:
    """Wrap a JSON-serializable payload in <score>...</score> as the prompt asks
    the model to do. Accepts dicts (happy path) and lists (negative test).
    """
    return f"<score>\n{json.dumps(payload, ensure_ascii=False)}\n</score>"


def _valid_breakdown(total: int = 75) -> dict[str, int]:
    base, rem = divmod(total, 5)
    return {
        "concreteness": min(20, base + (1 if rem > 0 else 0)),
        "mechanism": min(20, base + (1 if rem > 1 else 0)),
        "jtbd": min(20, base + (1 if rem > 2 else 0)),
        "voice_and_structure": min(20, base + (1 if rem > 3 else 0)),
        "register": min(20, base),
    }


def _valid_payload(total: int = 75) -> dict[str, object]:
    bd = _valid_breakdown(total)
    return {"breakdown": bd, "notes": "Тестовая запись."}


# ─── happy paths ─────────────────────────────────────────────────────────────


async def test_score_rewrite_unwraps_tagged_response(monkeypatch):
    payload = _valid_payload(80)
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(payload)))
    score = await quality_agent.score_rewrite(
        original="оригинал", rewrite="переписка", frame="mechanism"
    )
    assert isinstance(score, RewriteScore)
    assert score.total == 80
    assert score.total == sum(score.breakdown.values())
    assert set(score.breakdown.keys()) == set(_BREAKDOWN_KEYS)
    for v in score.breakdown.values():
        assert 0 <= v <= 20


async def test_score_rewrite_falls_back_to_extract_json_without_tags(monkeypatch):
    payload = _valid_payload(60)
    fenced = f"Конечно, держи:\n```json\n{json.dumps(payload)}\n```\n"
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=fenced))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="jtbd"
    )
    assert score.total == 60


async def test_score_rewrite_handles_prose_around_tags(monkeypatch):
    payload = _valid_payload(55)
    raw = (
        "Я оценил переписку, вот результат:\n\n"
        + _wrapped(payload)
        + "\n\nЕсли нужно подробнее — спросите."
    )
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=raw))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="benefit"
    )
    assert score.total == 55


# ─── auto-correction (the whole point of best-effort) ────────────────────────


async def test_score_rewrite_recomputes_total_from_breakdown(monkeypatch):
    """Model lies about total: returns total=90 but breakdown sums to 60.
    Best-effort scorer recomputes from breakdown and proceeds (no raise).
    """
    bad = {
        "total": 90,  # lie — ignored
        "breakdown": {
            "concreteness": 12, "mechanism": 12, "jtbd": 12,
            "voice_and_structure": 12, "register": 12,
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(bad)))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="mechanism"
    )
    assert score.total == 60  # real sum, not the model's claimed 90


async def test_score_rewrite_fills_missing_breakdown_key_with_zero(monkeypatch):
    bad = {
        "breakdown": {
            "concreteness": 15, "mechanism": 15, "jtbd": 15, "voice_and_structure": 15,
            # "register" missing
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(bad)))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="benefit"
    )
    assert score.breakdown["register"] == 0
    assert score.total == 60  # 4 × 15 + 0


async def test_score_rewrite_clamps_out_of_range_value(monkeypatch):
    bad = {
        "breakdown": {
            "concreteness": 25,  # clamped → 20
            "mechanism": -5,     # clamped → 0
            "jtbd": 15,
            "voice_and_structure": 15,
            "register": 15,
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(bad)))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="mechanism"
    )
    assert score.breakdown["concreteness"] == 20
    assert score.breakdown["mechanism"] == 0
    assert score.total == 20 + 0 + 15 + 15 + 15


async def test_score_rewrite_drops_extra_top_level_keys(monkeypatch):
    bad = {
        "breakdown": _valid_breakdown(70),
        "notes": "ok",
        "summary": "some extra commentary the model invented",
        "confidence": 0.8,
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(bad)))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="jtbd"
    )
    assert score.total == 70


async def test_score_rewrite_accepts_string_scores(monkeypatch):
    bad = {
        "breakdown": {
            "concreteness": "15", "mechanism": "15", "jtbd": "15",
            "voice_and_structure": "15", "register": "15",
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(bad)))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="mechanism"
    )
    assert score.total == 75


async def test_score_rewrite_truncates_notes(monkeypatch):
    long_notes = "x" * 2000
    bad = {"breakdown": _valid_breakdown(50), "notes": long_notes}
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped(bad)))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="benefit"
    )
    assert len(score.notes) == 500


# ─── hard failures (do raise) ────────────────────────────────────────────────


async def test_score_rewrite_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        _COLLECT_TEXT_PATH, AsyncMock(return_value="не могу ответить, извините")
    )
    with pytest.raises(LLMOutputError):
        await quality_agent.score_rewrite(
            original="o", rewrite="r", frame="jtbd"
        )


async def test_score_rewrite_raises_when_json_is_not_object(monkeypatch):
    # Model returns a top-level array — we can't normalize that into RewriteScore.
    monkeypatch.setattr(
        _COLLECT_TEXT_PATH, AsyncMock(return_value=_wrapped([1, 2, 3]))
    )
    with pytest.raises(LLMOutputError):
        await quality_agent.score_rewrite(
            original="o", rewrite="r", frame="mechanism"
        )


# ─── unit tests on helpers (pure-Python, no LLM) ─────────────────────────────


def test_unwrap_score_extracts_between_tags():
    raw = "prose <score>{\"a\": 1}</score> tail"
    assert _unwrap_score(raw).strip() == '{"a": 1}'


def test_unwrap_score_case_insensitive_tags():
    raw = "<SCORE>{\"a\": 1}</SCORE>"
    assert _unwrap_score(raw).strip() == '{"a": 1}'


def test_unwrap_score_falls_back_when_no_tags():
    raw = '```json\n{"a": 1}\n```'
    # No <score> tags — should fall through to _extract_json which strips fences.
    assert _unwrap_score(raw).strip().startswith("{")


@pytest.mark.parametrize(
    "value, expected",
    [
        (15, 15),
        (25, 20),       # clamped to max
        (-3, 0),        # clamped to min
        (12.6, 13),     # rounded
        ("17", 17),     # string accepted
        ("17.4", 17),   # string float rounded
        (True, 0),      # bool rejected (would otherwise pass as 1)
        (None, 0),      # None rejected
        ("abc", 0),     # garbage string
        ({"v": 5}, 0),  # dict rejected
    ],
)
def test_coerce_int_score(value, expected):
    assert _coerce_int_score(value) == expected


def test_normalize_payload_fills_missing_recomputes_total_drops_extras():
    payload = {
        "total": 999,  # lie
        "breakdown": {"concreteness": 10, "mechanism": 8, "jtbd": 5},  # 2 missing
        "notes": "abc",
        "extra_key": "ignored",
    }
    out = _normalize_payload(payload)
    assert set(out["breakdown"].keys()) == set(_BREAKDOWN_KEYS)
    assert out["breakdown"]["voice_and_structure"] == 0
    assert out["breakdown"]["register"] == 0
    assert out["total"] == sum(out["breakdown"].values()) == 23
    assert out["notes"] == "abc"
    assert "extra_key" not in out


def test_normalize_payload_empty_input_returns_all_zeros():
    out = _normalize_payload({})
    assert out["total"] == 0
    assert all(v == 0 for v in out["breakdown"].values())
    assert out["notes"] == ""
