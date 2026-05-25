"""Unit tests for case_law_matcher — fully synthetic fixtures.

We don't read the real case_law/ tree here; each test builds a small tmp_path
directory with hand-rolled FAS markdown and approved-report JSON so the test
suite is independent of editorial drift in the real corpus.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from pharma_ad_compliance import case_law_matcher
from pharma_ad_compliance.case_law_matcher import (
    _build_index,
    clear_cache,
    enrich_with_precedents,
    find_precedents,
)
from pharma_ad_compliance.schemas import RuleId, Severity, Violation


# ─── Fixture builders ────────────────────────────────────────────────────────
def _write_fas(dir_: Path, name: str, *, date: str, party: str, rule_ids: list[str],
               fine_rub: int | None = None, short_quote: str | None = None,
               url: str | None = None) -> Path:
    """Write a synthetic FAS markdown file with the expected frontmatter shape."""
    dir_.mkdir(parents=True, exist_ok=True)
    rule_ids_yaml = "[" + ", ".join(rule_ids) + "]"
    fine_yaml = "null" if fine_rub is None else str(fine_rub)
    lines = [
        "---",
        f"case_id: {name}",
        f"date: {date}",
        f"party: {party}",
        f"rule_ids: {rule_ids_yaml}",
        f"fine_rub: {fine_yaml}",
    ]
    if url:
        lines.append(f"url: {url}")
    if short_quote:
        lines.append(f'short_quote: "{short_quote}"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}")
    lines.append("Synthetic body content.")
    path = dir_ / f"{name}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_approved(dir_: Path, name: str, *, input_text: str,
                    rule_ids: list[str]) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": name,
        "approved_at": "2026-05-21T12:23:19.872917+00:00",
        "input": input_text,
        "drug_class": "OTC",
        "source_kind": "text",
        "violations": [
            {
                "rule_id": rid,
                "severity": "WARNING",
                "quote": "x",
                "explanation": "Some explanation for finding " + rid,
            }
            for rid in rule_ids
        ],
        "rewritten_text": None,
        "user_feedback_history": [],
        "iterations": 0,
    }
    path = dir_ / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clear_index_cache():
    """Each test rebuilds the index against its own fixture dir."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def case_law_root(tmp_path: Path) -> Path:
    """Three FAS files + one approved report covering distinct rule_ids."""
    fas = tmp_path / "fas_decisions"
    approved = tmp_path / "approved_reports"
    _write_fas(
        fas, "2020-09-07-bionorica-canephron",
        date="2020-09-07", party="Бионорика",
        rule_ids=["ART24_P3_NO_SIDE_EFFECTS", "ART24_P5_MANDATORY_DISCLAIMER"],
        fine_rub=200000,
        short_quote="Канефрон Н — гарантия эффекта",
        url="https://example.com/canephron",
    )
    _write_fas(
        fas, "2023-09-06-evalar-sabelnik",
        date="2023-09-06", party="Эвалар",
        rule_ids=["ART24_P3_NO_SIDE_EFFECTS"],
        fine_rub=350000,
    )
    _write_fas(
        fas, "2024-05-07-nizhfarm-artra",
        date="2024-05-07", party="Нижфарм",
        rule_ids=["ART24_OTHER"],
    )
    _write_approved(
        approved, "approved-2026-05-21_122319-aaaaaaaa",
        input_text="ПРЕВЕНТИВНЫЙ КРЕАТИВ КОТОРЫЙ УЖЕ УТВЕРЖДЁН",
        rule_ids=["ART24_P5_MANDATORY_DISCLAIMER"],
    )
    return tmp_path


# ─── _build_index ────────────────────────────────────────────────────────────
def test_index_maps_rule_ids_correctly(case_law_root: Path):
    idx = _build_index(case_law_root)
    assert set(idx.keys()) == {
        RuleId.ART24_P3_NO_SIDE_EFFECTS,
        RuleId.ART24_P5_MANDATORY_DISCLAIMER,
        RuleId.ART24_OTHER,
    }
    # 2 FAS files map to ART24_P3_NO_SIDE_EFFECTS.
    assert len(idx[RuleId.ART24_P3_NO_SIDE_EFFECTS]) == 2
    # 1 FAS + 1 approved → 2 entries for ART24_P5_MANDATORY_DISCLAIMER.
    assert len(idx[RuleId.ART24_P5_MANDATORY_DISCLAIMER]) == 2
    assert len(idx[RuleId.ART24_OTHER]) == 1


def test_index_builds_quickly(case_law_root: Path):
    # Sanity perf check — synthetic dir has 4 files, must finish well under 100ms.
    clear_cache()
    started = time.perf_counter()
    _build_index(case_law_root)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1, f"index build took {elapsed*1000:.1f}ms (>100ms)"


def test_index_skips_files_with_malformed_frontmatter(
    case_law_root: Path, caplog: pytest.LogCaptureFixture,
):
    bad = case_law_root / "fas_decisions" / "bad.md"
    bad.write_text("---\nthis-is-not-yaml: but ok\nbroken line without colon\n---\n# x\n",
                   encoding="utf-8")
    clear_cache()
    with caplog.at_level(logging.WARNING, logger="pharma_ad_compliance.case_law_matcher"):
        idx = _build_index(case_law_root)
    # Good files still indexed.
    assert RuleId.ART24_P3_NO_SIDE_EFFECTS in idx
    # And the bad one logged.
    assert any("bad.md" in rec.message for rec in caplog.records)


def test_index_skips_files_with_missing_frontmatter(
    case_law_root: Path, caplog: pytest.LogCaptureFixture,
):
    plain = case_law_root / "fas_decisions" / "plain.md"
    plain.write_text("# Just a heading, no frontmatter\nbody only\n", encoding="utf-8")
    clear_cache()
    with caplog.at_level(logging.WARNING, logger="pharma_ad_compliance.case_law_matcher"):
        _build_index(case_law_root)
    assert any("plain.md" in rec.message for rec in caplog.records)


def test_index_handles_unknown_rule_id_gracefully(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    fas = tmp_path / "fas_decisions"
    _write_fas(
        fas, "bogus", date="2020-01-01", party="x",
        rule_ids=["ART24_P3_NO_SIDE_EFFECTS", "NONSENSE_RULE"],
    )
    clear_cache()
    with caplog.at_level(logging.WARNING, logger="pharma_ad_compliance.case_law_matcher"):
        idx = _build_index(tmp_path)
    # Good rule_id still got indexed.
    assert len(idx[RuleId.ART24_P3_NO_SIDE_EFFECTS]) == 1
    assert any("NONSENSE_RULE" in rec.message for rec in caplog.records)


def test_empty_rule_ids_file_is_silently_skipped(tmp_path: Path):
    """Out-of-scope cases (visual-only, ст. 5) → `rule_ids: []` and no matches."""
    fas = tmp_path / "fas_decisions"
    _write_fas(fas, "visual-only", date="2019-04-02", party="MMH", rule_ids=[])
    clear_cache()
    idx = _build_index(tmp_path)
    # No rule pulls this file in.
    assert idx == {}


# ─── find_precedents ─────────────────────────────────────────────────────────
def _violation(rule_id: RuleId, severity: Severity = Severity.CRITICAL) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity=severity,
        quote="полностью безопасен",
        explanation="Гарантия безопасности запрещена.",
    )


def test_find_precedents_returns_date_descending(case_law_root: Path):
    v = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS)
    refs = find_precedents(v, root=case_law_root)
    assert len(refs) == 2
    # Most recent first.
    assert refs[0].date == "2023-09-06"
    assert refs[1].date == "2020-09-07"


def test_find_precedents_limit_honored(case_law_root: Path):
    v = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS)
    refs = find_precedents(v, root=case_law_root, limit=1)
    assert len(refs) == 1
    assert refs[0].date == "2023-09-06"


def test_find_precedents_empty_for_unknown_rule(case_law_root: Path):
    v = _violation(RuleId.ART24_P1_MINORS)
    assert find_precedents(v, root=case_law_root) == []


def test_find_precedents_excludes_self_reference(case_law_root: Path):
    """Same creative_text as an approved report → that approved CaseRef is dropped."""
    v = _violation(RuleId.ART24_P5_MANDATORY_DISCLAIMER)
    # Without creative_text — see both entries (1 FAS + 1 approved).
    refs_all = find_precedents(v, root=case_law_root)
    assert any(r.source == "approved" for r in refs_all)
    # With creative_text matching the approved report's input — approved excluded.
    refs_filtered = find_precedents(
        v, root=case_law_root,
        creative_text="ПРЕВЕНТИВНЫЙ КРЕАТИВ КОТОРЫЙ УЖЕ УТВЕРЖДЁН",
    )
    assert all(r.source != "approved" for r in refs_filtered)


def test_find_precedents_limit_zero_returns_empty(case_law_root: Path):
    v = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS)
    assert find_precedents(v, root=case_law_root, limit=0) == []


# ─── enrich_with_precedents ──────────────────────────────────────────────────
def test_enrich_only_touches_critical_by_default(case_law_root: Path):
    crit = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS, severity=Severity.CRITICAL)
    warn = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS, severity=Severity.WARNING)
    rec = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS, severity=Severity.RECOMMENDATION)
    out = enrich_with_precedents([crit, warn, rec], root=case_law_root)
    assert len(out) == 3
    # CRITICAL gets enriched.
    assert out[0].severity == Severity.CRITICAL
    assert len(out[0].precedents) == 2
    # WARNING / RECOMMENDATION untouched.
    assert out[1].precedents == ()
    assert out[2].precedents == ()


def test_enrich_preserves_other_fields(case_law_root: Path):
    v = Violation(
        rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
        severity=Severity.CRITICAL,
        quote="безопасен",
        explanation="x",
        suggested_fix="хорошо переносится",
        intent_hypothesis="Снять страх побочки",
    )
    out = enrich_with_precedents([v], root=case_law_root)
    assert len(out) == 1
    enriched = out[0]
    assert enriched.quote == "безопасен"
    assert enriched.suggested_fix == "хорошо переносится"
    assert enriched.intent_hypothesis == "Снять страх побочки"
    assert len(enriched.precedents) == 2


def test_enrich_returns_unchanged_when_no_precedents(case_law_root: Path):
    v = _violation(RuleId.ART24_P1_MINORS)  # no precedents for this rule
    out = enrich_with_precedents([v], root=case_law_root)
    assert out[0].precedents == ()
    # Same instance (no model_copy needed when nothing to attach).
    assert out[0] is v


def test_enrich_swallows_matcher_exceptions(
    monkeypatch: pytest.MonkeyPatch, case_law_root: Path,
    caplog: pytest.LogCaptureFixture,
):
    """If `find_precedents` raises mid-call, the violation is returned untouched."""
    def _boom(*_a, **_kw):
        raise RuntimeError("simulated matcher failure")

    monkeypatch.setattr(case_law_matcher, "find_precedents", _boom)
    v = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS)
    with caplog.at_level(logging.WARNING, logger="pharma_ad_compliance.case_law_matcher"):
        out = enrich_with_precedents([v], root=case_law_root)
    assert out[0].precedents == ()
    assert any("simulated matcher failure" in rec.message for rec in caplog.records)


def test_enrich_severity_floor_widens_to_warning(case_law_root: Path):
    """Caller can opt into WARNING-level enrichment via `severity_floor`."""
    warn = _violation(RuleId.ART24_P3_NO_SIDE_EFFECTS, severity=Severity.WARNING)
    out = enrich_with_precedents(
        [warn], root=case_law_root, severity_floor=Severity.WARNING,
    )
    assert len(out[0].precedents) == 2
