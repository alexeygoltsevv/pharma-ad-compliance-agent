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
        if existing is None:
            by_key[key] = v
            continue
        # When two findings collide, keep the higher-severity one but salvage
        # the better `suggested_fix` from the loser — the LLM sometimes returns
        # a richer rewrite on the lower-severity duplicate.
        winner = v if v.severity.rank < existing.severity.rank else existing
        loser = existing if winner is v else v
        merged_fix = _pick_better_fix(winner.suggested_fix, loser.suggested_fix)
        if merged_fix != winner.suggested_fix:
            # Violation is frozen, so rebuild via model_copy with the merged fix.
            winner = winner.model_copy(update={"suggested_fix": merged_fix})
        by_key[key] = winner

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
    # Strip surrounding quotation marks and punctuation so the model's
    # «цитата.» and a plain «цитата» dedupe to the same key.
    stripped = quote.strip(" \t\n\r«»\"'.,;:!?()[]{}")
    return " ".join(stripped.lower().split())


def _pick_better_fix(a: str | None, b: str | None) -> str | None:
    """Return the longer non-empty `suggested_fix` (or None if both empty)."""
    a_clean = (a or "").strip()
    b_clean = (b or "").strip()
    if not a_clean and not b_clean:
        return None
    if len(a_clean) >= len(b_clean):
        return a_clean or None
    return b_clean or None
