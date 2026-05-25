"""Tests for the multi-variant editor and pipeline integration.

These do NOT touch the LLM — they mock `run_text` (editor), `_collect_text`
(quality scorer), and `_recheck_one_variant` (compliance recheck) so we verify
just the wiring: that the pipeline produces one `RewriteVariant` per requested
frame, with the correct frame label, score, and recheck status.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pharma_ad_compliance.agents import editor_agent
from pharma_ad_compliance.agents.editor_agent import _extract_creative
from pharma_ad_compliance.schemas import (
    DrugClass,
    ParsedCreative,
    RewriteScore,
    RewriteVariant,
    RuleId,
    Severity,
    TextCreative,
    Violation,
)

_EDITOR_RUN_TEXT = "pharma_ad_compliance.agents.editor_agent.run_text"


def _parsed(text: str = "Оригинальный креатив") -> ParsedCreative:
    return ParsedCreative(source_kind="text", extracted_text=text)


def _violation(quote: str = "полностью безопасен") -> Violation:
    return Violation(
        rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
        severity=Severity.CRITICAL,
        quote=quote,
        explanation="Гарантия безопасности запрещена ч. 1 п. 8 ст. 24.",
        suggested_fix="хорошо переносится по данным КИ",
    )


async def test_rewrite_variants_returns_one_pair_per_frame(monkeypatch):
    """Editor returns 3 rewrites, one per requested frame, in the requested order."""
    texts = iter([
        "MECHANISM rewrite",
        "JTBD rewrite",
        "BENEFIT rewrite",
    ])

    async def fake_run_text(**_kwargs) -> str:
        return next(texts)

    monkeypatch.setattr(_EDITOR_RUN_TEXT, fake_run_text)

    out = await editor_agent.rewrite_variants(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        violations=[_violation()],
    )
    assert len(out) == 3
    assert [frame for _, frame in out] == ["mechanism", "jtbd", "benefit"]
    assert [text for text, _ in out] == [
        "MECHANISM rewrite",
        "JTBD rewrite",
        "BENEFIT rewrite",
    ]


async def test_rewrite_variants_empty_violations_returns_empty(monkeypatch):
    """No violations → no editor call, no variants."""
    called = {"n": 0}

    async def fake_run_text(**_kwargs) -> str:
        called["n"] += 1
        return "should not be called"

    monkeypatch.setattr(_EDITOR_RUN_TEXT, fake_run_text)
    out = await editor_agent.rewrite_variants(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        violations=[],
    )
    assert out == []
    assert called["n"] == 0


async def test_rewrite_variants_respects_custom_frame_subset(monkeypatch):
    """Caller can request a subset of frames (e.g. CLI --variants 1)."""
    monkeypatch.setattr(_EDITOR_RUN_TEXT, AsyncMock(return_value="only-mech"))
    out = await editor_agent.rewrite_variants(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        violations=[_violation()],
        frames=("mechanism",),
    )
    assert len(out) == 1
    assert out[0] == ("only-mech", "mechanism")


async def test_rewrite_alias_returns_variant_list(monkeypatch):
    """Public `rewrite` is a drop-in alias for `rewrite_variants`."""
    monkeypatch.setattr(_EDITOR_RUN_TEXT, AsyncMock(return_value="x"))
    out = await editor_agent.rewrite(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        violations=[_violation()],
        frames=("mechanism", "jtbd"),
    )
    assert isinstance(out, list)
    assert {frame for _, frame in out} == {"mechanism", "jtbd"}


# ─── Pipeline-level integration ──────────────────────────────────────────────


def _score(total: int = 80) -> RewriteScore:
    base, rem = divmod(total, 5)
    breakdown = {
        "concreteness": base + (1 if rem > 0 else 0),
        "mechanism": base + (1 if rem > 1 else 0),
        "jtbd": base + (1 if rem > 2 else 0),
        "voice_and_structure": base + (1 if rem > 3 else 0),
        "register": base,
    }
    return RewriteScore(
        total=sum(breakdown.values()),
        breakdown=breakdown,
        notes="ok",
    )


@pytest.fixture
def _pipeline_stubs(monkeypatch):
    """Wire stubs for parser, classifier, checkers, editor, recheck, scoring.

    Returns the monkeypatch fixture so individual tests can override pieces.
    """
    from pharma_ad_compliance import pipeline as pipeline_mod

    async def fake_parse(_creative):
        return _parsed("Оригинальный креатив")

    async def fake_classify(_parsed):
        return DrugClass.OTC

    async def fake_checker(_parsed_arg, _drug_class, **_):
        # Single CRITICAL finding so the editor runs.
        return [_violation()]

    async def fake_rewrite_variants(*, frames, **_kwargs):
        return [(f"{frame.upper()} rewrite", frame) for frame in frames]

    async def fake_recheck(*, text, **_kwargs):
        # Mark mechanism as passing, others as failing (1 residual violation).
        if "MECHANISM" in text:
            return ()
        return (_violation("residual issue"),)

    async def fake_score(*, original, rewrite, frame):
        return _score({"mechanism": 90, "jtbd": 70, "benefit": 60}[frame])

    monkeypatch.setattr(pipeline_mod.parser_agent, "parse", fake_parse)
    monkeypatch.setattr(pipeline_mod.drug_classifier_agent, "classify", fake_classify)
    # Override ALL_CHECKERS to a single stub.
    monkeypatch.setattr(pipeline_mod, "ALL_CHECKERS", (fake_checker,))
    monkeypatch.setattr(
        pipeline_mod.editor_agent, "rewrite_variants", fake_rewrite_variants
    )
    monkeypatch.setattr(pipeline_mod, "_recheck_one_variant", fake_recheck)
    monkeypatch.setattr(pipeline_mod, "_score_one_variant", fake_score)
    return monkeypatch


async def test_pipeline_yields_three_variants_with_correct_frames(_pipeline_stubs):
    from pharma_ad_compliance.pipeline import run_compliance

    creative = TextCreative(text="Какой-то рекламный текст")
    report = await run_compliance(creative)
    assert len(report.rewrite_variants) == 3
    assert [v.frame for v in report.rewrite_variants] == ["mechanism", "jtbd", "benefit"]


async def test_pipeline_recheck_marks_compliance_passed(_pipeline_stubs):
    from pharma_ad_compliance.pipeline import run_compliance

    creative = TextCreative(text="Какой-то рекламный текст")
    report = await run_compliance(creative)
    by_frame: dict[str, RewriteVariant] = {v.frame: v for v in report.rewrite_variants}
    assert by_frame["mechanism"].compliance_passed is True
    assert by_frame["mechanism"].recheck_violations == ()
    assert by_frame["jtbd"].compliance_passed is False
    assert len(by_frame["jtbd"].recheck_violations) == 1
    assert by_frame["benefit"].compliance_passed is False


async def test_pipeline_scores_each_variant(_pipeline_stubs):
    from pharma_ad_compliance.pipeline import run_compliance

    creative = TextCreative(text="Какой-то рекламный текст")
    report = await run_compliance(creative)
    by_frame = {v.frame: v for v in report.rewrite_variants}
    assert by_frame["mechanism"].quality_score is not None
    assert by_frame["mechanism"].quality_score.total == 90
    assert by_frame["jtbd"].quality_score.total == 70
    assert by_frame["benefit"].quality_score.total == 60


async def test_pipeline_scoring_failure_does_not_crash(monkeypatch, _pipeline_stubs):
    """If scoring raises for one variant, that variant keeps quality_score=None."""
    from pharma_ad_compliance import pipeline as pipeline_mod
    from pharma_ad_compliance.pipeline import run_compliance

    async def flaky_score(*, original, rewrite, frame):
        if frame == "jtbd":
            raise RuntimeError("transient scoring error")
        return _score(80)

    # The pipeline wraps the call in its own try/except, so we patch the inner
    # _score_one_variant to raise — the gather(return_exceptions=True) layer
    # must surface this safely.
    async def passthrough(*, original, rewrite, frame):
        return await flaky_score(original=original, rewrite=rewrite, frame=frame)

    # We patch the public score path: pipeline._score_one_variant already wraps
    # quality_agent.score_rewrite in try/except, so patching it to raise tests
    # that the gather layer absorbs the exception.
    monkeypatch.setattr(pipeline_mod, "_score_one_variant", passthrough)

    creative = TextCreative(text="Какой-то рекламный текст")
    report = await run_compliance(creative)
    by_frame = {v.frame: v for v in report.rewrite_variants}
    assert by_frame["mechanism"].quality_score is not None
    assert by_frame["jtbd"].quality_score is None
    assert by_frame["benefit"].quality_score is not None


async def test_pipeline_skips_rewrite_when_no_violations(monkeypatch, _pipeline_stubs):
    from pharma_ad_compliance import pipeline as pipeline_mod
    from pharma_ad_compliance.pipeline import run_compliance

    async def no_violations(_parsed_arg, _drug_class, **_):
        return []

    monkeypatch.setattr(pipeline_mod, "ALL_CHECKERS", (no_violations,))

    creative = TextCreative(text="Какой-то рекламный текст")
    report = await run_compliance(creative)
    assert report.rewrite_variants == ()
    assert report.rewritten_text is None


async def test_pipeline_respects_include_rewrite_false(monkeypatch, _pipeline_stubs):
    from pharma_ad_compliance.pipeline import run_compliance

    creative = TextCreative(text="Какой-то рекламный текст")
    report = await run_compliance(creative, include_rewrite=False)
    assert report.rewrite_variants == ()
    assert report.rewritten_text is None


# ─── _extract_creative — output-marker unwrap & fallback ─────────────────────
def test_extract_creative_unwraps_simple_tags() -> None:
    raw = "<creative>Чистый текст рекламы.</creative>"
    assert _extract_creative(raw) == "Чистый текст рекламы."


def test_extract_creative_strips_meta_prose_around_tags() -> None:
    raw = (
        "Я работаю как фарма-копирайтер. Вот результат:\n\n"
        "<creative>Препарат облегчает дыхание. Имеются противопоказания.</creative>\n\n"
        "**Обоснование:** заменил гарантию на механизм."
    )
    got = _extract_creative(raw)
    assert got == "Препарат облегчает дыхание. Имеются противопоказания."
    assert "Я работаю" not in got
    assert "Обоснование" not in got


def test_extract_creative_handles_multiline_body() -> None:
    raw = (
        "<creative>\nПрепарат, 200 мг, 10 таблеток.\n\n"
        "Способствует уменьшению боли при первых признаках.\n\n"
        "Имеются противопоказания, проконсультируйтесь со специалистом.\n</creative>"
    )
    got = _extract_creative(raw)
    assert got.startswith("Препарат, 200 мг")
    assert "Имеются противопоказания" in got
    assert "<creative>" not in got


def test_extract_creative_falls_back_when_markers_missing() -> None:
    raw = "Препарат хорошо переносится. Имеются противопоказания."
    # No <creative> tags — caller gets the raw text (logged as a warning).
    assert _extract_creative(raw) == raw


def test_extract_creative_case_insensitive_tags() -> None:
    raw = "<CREATIVE>Текст</CREATIVE>"
    assert _extract_creative(raw) == "Текст"
