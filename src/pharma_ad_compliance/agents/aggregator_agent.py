"""Aggregator — deduplicate findings and sort by severity.

Pure-Python, no LLM call. Two checkers can flag the same span (e.g. art24_other
sometimes overlaps with a specialized checker). We dedupe by `(rule_id, quote)`
and prefer the higher-severity finding.
"""
from __future__ import annotations

from ..schemas import Violation


def aggregate(violations: list[Violation]) -> list[Violation]:
    by_key: dict[tuple[str, str | None], Violation] = {}
    for v in violations:
        key = (v.rule_id.value, _normalize_quote(v.quote))
        existing = by_key.get(key)
        if existing is None or v.severity.rank < existing.severity.rank:
            by_key[key] = v
    return sorted(
        by_key.values(),
        key=lambda v: (v.severity.rank, v.rule_id.value),
    )


def _normalize_quote(quote: str | None) -> str | None:
    if quote is None:
        return None
    return " ".join(quote.lower().split())
