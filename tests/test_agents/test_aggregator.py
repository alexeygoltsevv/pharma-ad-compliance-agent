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
