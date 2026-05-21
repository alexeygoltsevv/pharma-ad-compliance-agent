"""Aggregator — deduplicate findings and sort by severity.

Pure-Python, no LLM call. Two checkers can flag the same span (e.g. art24_other
sometimes overlaps with a specialized checker). We dedupe in two passes:
1. exact `(rule_id, quote)` duplicates → keep the higher-severity one;
2. cross-rule: ART24_OTHER is a catch-all, so if a specialized checker already
   flagged the same quote, drop the redundant ART24_OTHER finding.
"""
from __future__ import annotations

from ..schemas import RuleId, Violation


def aggregate(violations: list[Violation]) -> list[Violation]:
    by_key: dict[tuple[str, str | None], Violation] = {}
    for v in violations:
        key = (v.rule_id.value, _normalize_quote(v.quote))
        existing = by_key.get(key)
        if existing is None or v.severity.rank < existing.severity.rank:
            by_key[key] = v

    deduped = list(by_key.values())
    # Quotes already covered by a specialized (non-OTHER) checker.
    specialized_quotes = {
        _normalize_quote(v.quote)
        for v in deduped
        if v.rule_id is not RuleId.ART24_OTHER and v.quote is not None
    }
    result = [
        v
        for v in deduped
        if not (
            v.rule_id is RuleId.ART24_OTHER
            and v.quote is not None
            and _normalize_quote(v.quote) in specialized_quotes
        )
    ]
    return sorted(
        result,
        key=lambda v: (v.severity.rank, v.rule_id.value),
    )


def _normalize_quote(quote: str | None) -> str | None:
    if quote is None:
        return None
    return " ".join(quote.lower().split())
