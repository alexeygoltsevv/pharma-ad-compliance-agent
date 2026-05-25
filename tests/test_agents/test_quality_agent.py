"""Tests for the rewrite-quality scorer (quality_agent.score_rewrite)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pharma_ad_compliance.agents import quality_agent
from pharma_ad_compliance.agents._llm import LLMOutputError
from pharma_ad_compliance.schemas import RewriteScore

# The agent imports _collect_text into its own namespace; patch it there.
_COLLECT_TEXT_PATH = "pharma_ad_compliance.agents.quality_agent._collect_text"


def _valid_payload(total: int = 75) -> dict[str, object]:
    # Split total across the 5 keys 0..20 each.
    base, rem = divmod(total, 5)
    breakdown = {
        "concreteness": min(20, base + (1 if rem > 0 else 0)),
        "mechanism": min(20, base + (1 if rem > 1 else 0)),
        "jtbd": min(20, base + (1 if rem > 2 else 0)),
        "voice_and_structure": min(20, base + (1 if rem > 3 else 0)),
        "register": min(20, base),
    }
    return {
        "total": sum(breakdown.values()),
        "breakdown": breakdown,
        "notes": "Тестовая запись.",
    }


async def test_score_rewrite_returns_validated_score(monkeypatch):
    payload = _valid_payload(80)
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(payload)))

    score = await quality_agent.score_rewrite(
        original="оригинальный текст", rewrite="переписанный текст", frame="mechanism"
    )
    assert isinstance(score, RewriteScore)
    assert score.total == sum(score.breakdown.values())
    assert set(score.breakdown.keys()) == {
        "concreteness",
        "mechanism",
        "jtbd",
        "voice_and_structure",
        "register",
    }
    for value in score.breakdown.values():
        assert 0 <= value <= 20


async def test_score_rewrite_handles_fenced_json(monkeypatch):
    payload = _valid_payload(60)
    fenced = f"Конечно, держи:\n```json\n{json.dumps(payload)}\n```\n"
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=fenced))
    score = await quality_agent.score_rewrite(
        original="o", rewrite="r", frame="jtbd"
    )
    assert score.total == 60


async def test_score_rewrite_raises_on_total_sum_mismatch(monkeypatch):
    # Model lies about the total: breakdown sums to 60 but total claims 90.
    bad = {
        "total": 90,
        "breakdown": {
            "concreteness": 12,
            "mechanism": 12,
            "jtbd": 12,
            "voice_and_structure": 12,
            "register": 12,
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(bad)))
    with pytest.raises(LLMOutputError):
        await quality_agent.score_rewrite(
            original="o", rewrite="r", frame="mechanism"
        )


async def test_score_rewrite_raises_on_missing_key(monkeypatch):
    # Missing "register"; total adjusted so sum is consistent — the key-set
    # validator must still reject.
    bad = {
        "total": 48,
        "breakdown": {
            "concreteness": 12,
            "mechanism": 12,
            "jtbd": 12,
            "voice_and_structure": 12,
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(bad)))
    with pytest.raises(LLMOutputError):
        await quality_agent.score_rewrite(
            original="o", rewrite="r", frame="benefit"
        )


async def test_score_rewrite_raises_on_out_of_range_value(monkeypatch):
    # 25/20 for one criterion is out of range.
    bad = {
        "total": 85,
        "breakdown": {
            "concreteness": 25,
            "mechanism": 15,
            "jtbd": 15,
            "voice_and_structure": 15,
            "register": 15,
        },
        "notes": "",
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(bad)))
    with pytest.raises(LLMOutputError):
        await quality_agent.score_rewrite(
            original="o", rewrite="r", frame="mechanism"
        )


async def test_score_rewrite_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        _COLLECT_TEXT_PATH, AsyncMock(return_value="не могу ответить, извините")
    )
    with pytest.raises(LLMOutputError):
        await quality_agent.score_rewrite(
            original="o", rewrite="r", frame="jtbd"
        )
