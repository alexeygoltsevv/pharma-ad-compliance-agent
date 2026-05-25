"""Persona-specific Streamlit renderers for `ComplianceReport`.

Two views over the *same* `ComplianceReport`:
- `render_legal_view`    — severity-first summary, precedents inline, rewrites
                           demoted to an expander. Optimised for risk review.
- `render_marketer_view` — rewrite variants up top with quality badges,
                           findings table focused on intent + suggested fix,
                           law subpoints and precedents collapsed.

Pure-Python helpers live in `_shared` (Streamlit-free, easy to unit-test).
"""
from __future__ import annotations

from ._shared import (
    DRUG_CLASS_LABELS,
    FRAME_LABELS,
    RULE_ARTICLE_REFS,
    RULE_LABELS,
    SEVERITY_BADGE,
    SEVERITY_LABELS,
    format_precedent_full,
    format_precedents_line,
    format_severity_badge,
    format_table_rows,
    render_violations_table_html,
    score_badge,
    severity_emoji,
    sort_violations_by_priority,
    summary_counts,
)
from .legal_view import render_legal_view
from .marketer_view import render_marketer_view

__all__ = [
    "DRUG_CLASS_LABELS",
    "FRAME_LABELS",
    "RULE_ARTICLE_REFS",
    "RULE_LABELS",
    "SEVERITY_BADGE",
    "SEVERITY_LABELS",
    "format_precedent_full",
    "format_precedents_line",
    "format_severity_badge",
    "format_table_rows",
    "render_legal_view",
    "render_marketer_view",
    "render_violations_table_html",
    "score_badge",
    "severity_emoji",
    "sort_violations_by_priority",
    "summary_counts",
]
