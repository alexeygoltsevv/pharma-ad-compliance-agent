"""Pure-Python branches in the individual rule_checkers."""
from __future__ import annotations

import inspect

import pytest

from pharma_ad_compliance.agents.rule_checkers import ALL_CHECKERS, art24_p5_mandatory_disclaimer
from pharma_ad_compliance.schemas import DrugClass, ParsedCreative


def _parsed(text: str = "Любой текст") -> ParsedCreative:
    return ParsedCreative(source_kind="text", extracted_text=text)


async def test_art24_p5_skipped_for_bad_without_llm_call(monkeypatch):
    """BAD short-circuits before any model call — `_collect_text` must never be invoked."""
    def _explode(*_args, **_kwargs):
        raise AssertionError("LLM was called for BAD — disclaimer checker should short-circuit")

    monkeypatch.setattr(
        "pharma_ad_compliance.agents.rule_checkers._base._collect_text",
        _explode,
    )
    out = await art24_p5_mandatory_disclaimer.check(_parsed(), DrugClass.BAD)
    assert out == []


@pytest.mark.parametrize("checker", list(ALL_CHECKERS))
def test_all_checkers_have_pipeline_compatible_signature(checker):
    """Pipeline calls `checker(parsed, drug_class, user_feedback=...)` — every checker must match."""
    sig = inspect.signature(checker)
    params = list(sig.parameters.values())
    # Two positional args + keyword-only user_feedback (or POSITIONAL_OR_KEYWORD).
    assert [p.name for p in params[:2]] == ["parsed", "drug_class"]
    assert "user_feedback" in sig.parameters
    user_feedback_param = sig.parameters["user_feedback"]
    assert user_feedback_param.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    # Must be a coroutine function (pipeline runs them via asyncio.gather).
    assert inspect.iscoroutinefunction(checker)


def test_all_checkers_count_matches_registry():
    """If a checker is added/removed the registry should stay in sync with RuleId coverage.

    This is a smoke check — six entries is the current Phase-1 contract. Updating
    it requires also extending docs/agents.md.
    """
    assert len(ALL_CHECKERS) == 6
