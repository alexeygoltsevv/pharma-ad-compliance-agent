from __future__ import annotations

from pharma_ad_compliance.agents.aggregator_agent import aggregate
from pharma_ad_compliance.schemas import RuleId, Severity, Violation


def _v(rule: RuleId, sev: Severity, quote: str | None = None) -> Violation:
    return Violation(rule_id=rule, severity=sev, quote=quote, explanation="...")


def test_aggregate_dedupes_same_rule_and_quote_keeping_higher_severity():
    duplicates = [
        _v(RuleId.ART24_P3_NO_SIDE_EFFECTS, Severity.WARNING, "не имеет побочки"),
        _v(RuleId.ART24_P3_NO_SIDE_EFFECTS, Severity.CRITICAL, "не имеет побочки"),
    ]
    out = aggregate(duplicates)
    assert len(out) == 1
    assert out[0].severity is Severity.CRITICAL


def test_aggregate_drops_other_when_specialized_covers_same_quote():
    out = aggregate(
        [
            _v(RuleId.ART24_P1_MINORS, Severity.CRITICAL, "Рекомендуется детям"),
            _v(RuleId.ART24_OTHER, Severity.WARNING, "рекомендуется детям"),  # case/space-insensitive
        ]
    )
    assert len(out) == 1
    assert out[0].rule_id is RuleId.ART24_P1_MINORS


def test_aggregate_keeps_other_for_unique_quote():
    out = aggregate(
        [
            _v(RuleId.ART24_P1_MINORS, Severity.CRITICAL, "Рекомендуется детям"),
            _v(RuleId.ART24_OTHER, Severity.WARNING, "другая проблемная фраза"),
        ]
    )
    rule_ids = {v.rule_id for v in out}
    assert rule_ids == {RuleId.ART24_P1_MINORS, RuleId.ART24_OTHER}


def test_aggregate_keeps_absence_based_other():
    # ART24_OTHER with quote=None must not be dropped by the cross-rule pass.
    out = aggregate(
        [
            _v(RuleId.ART24_P1_MINORS, Severity.CRITICAL, "детям"),
            _v(RuleId.ART24_OTHER, Severity.WARNING, None),
        ]
    )
    assert len(out) == 2


def test_aggregate_keeps_different_quotes_separate():
    items = [
        _v(RuleId.ART24_P3_NO_SIDE_EFFECTS, Severity.CRITICAL, "не имеет побочки"),
        _v(RuleId.ART24_P3_NO_SIDE_EFFECTS, Severity.WARNING, "полностью безопасен"),
    ]
    out = aggregate(items)
    assert len(out) == 2


def test_aggregate_sorts_critical_first():
    items = [
        _v(RuleId.ART24_OTHER, Severity.RECOMMENDATION, "x"),
        _v(RuleId.ART24_P1_MINORS, Severity.CRITICAL, "детям"),
        _v(RuleId.ART24_P2_SPECIFIC_CASES, Severity.WARNING, "вылечилась"),
    ]
    out = aggregate(items)
    severities = [v.severity for v in out]
    assert severities == [Severity.CRITICAL, Severity.WARNING, Severity.RECOMMENDATION]


def test_aggregate_quote_normalization_strips_whitespace_and_case():
    items = [
        _v(RuleId.ART24_P3_NO_SIDE_EFFECTS, Severity.CRITICAL, "  Не Имеет Побочки "),
        _v(RuleId.ART24_P3_NO_SIDE_EFFECTS, Severity.CRITICAL, "не имеет побочки"),
    ]
    assert len(aggregate(items)) == 1


def test_aggregate_null_quote_is_its_own_bucket():
    items = [
        _v(RuleId.ART24_P5_MANDATORY_DISCLAIMER, Severity.CRITICAL, None),
        _v(RuleId.ART24_P5_MANDATORY_DISCLAIMER, Severity.WARNING, None),
    ]
    out = aggregate(items)
    assert len(out) == 1
    assert out[0].severity is Severity.CRITICAL


def test_aggregate_merges_longer_suggested_fix_from_loser():
    # The CRITICAL wins on severity, but its fix is shorter — we should
    # salvage the WARNING's richer rewrite so the user gets the better text.
    rich_fix = "Замените «полностью безопасен» на «хорошо переносится по данным КИ»."
    short_fix = "Удалить."
    items = [
        Violation(
            rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
            severity=Severity.CRITICAL,
            quote="полностью безопасен",
            explanation="...",
            suggested_fix=short_fix,
        ),
        Violation(
            rule_id=RuleId.ART24_P3_NO_SIDE_EFFECTS,
            severity=Severity.WARNING,
            quote="полностью безопасен",
            explanation="...",
            suggested_fix=rich_fix,
        ),
    ]
    out = aggregate(items)
    assert len(out) == 1
    assert out[0].severity is Severity.CRITICAL
    assert out[0].suggested_fix == rich_fix


def test_aggregate_normalizes_surrounding_punctuation():
    # «цитата.» and «цитата» should dedupe to the same bucket.
    items = [
        _v(RuleId.ART24_P2_SPECIFIC_CASES, Severity.CRITICAL, "«вылечилась за неделю.»"),
        _v(RuleId.ART24_P2_SPECIFIC_CASES, Severity.WARNING, "вылечилась за неделю"),
    ]
    out = aggregate(items)
    assert len(out) == 1
    assert out[0].severity is Severity.CRITICAL


def test_aggregate_empty_input_returns_empty():
    assert aggregate([]) == []
