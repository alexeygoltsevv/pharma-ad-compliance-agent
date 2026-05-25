"""Unit tests for the persona-renderer package.

The pure-Python helpers in `renderers._shared` get full coverage. The
Streamlit-facing modules (`legal_view`, `marketer_view`) are only smoke-tested
for clean import and signature — full UI rendering is verified manually.
"""
from __future__ import annotations

import pytest

from pharma_ad_compliance.renderers import (
    DRUG_CLASS_LABELS,
    FRAME_LABELS,
    RULE_ARTICLE_REFS,
    RULE_LABELS,
    SEVERITY_BADGE,
    format_precedent_full,
    format_precedents_line,
    format_severity_badge,
    format_table_rows,
    render_legal_view,
    render_marketer_view,
    render_violations_table_html,
    score_badge,
    severity_emoji,
    sort_violations_by_priority,
    summary_counts,
)
from pharma_ad_compliance.schemas import (
    CaseRef,
    ComplianceReport,
    DrugClass,
    RewriteScore,
    RewriteVariant,
    RuleId,
    Severity,
    Violation,
)

# ─── fixtures ─────────────────────────────────────────────────────────────────


def _v(
    severity: Severity,
    rule_id: RuleId = RuleId.ART24_P3_NO_SIDE_EFFECTS,
    quote: str | None = "примерная цитата",
    suggested_fix: str | None = "compliant вариант",
    intent: str | None = None,
    precedents: tuple[CaseRef, ...] = (),
) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity=severity,
        quote=quote,
        explanation="Объяснение нарушения.",
        suggested_fix=suggested_fix,
        intent_hypothesis=intent,
        precedents=precedents,
    )


def _report(*violations: Violation, variants: tuple[RewriteVariant, ...] = ()) -> ComplianceReport:
    return ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="исходный креатив",
        violations=list(violations),
        rewrite_variants=variants,
    )


@pytest.fixture
def case_ref() -> CaseRef:
    return CaseRef(
        case_id="kanefron-2020",
        date="2020-06-15",
        party="Канефрон Н",
        fine_rub=200_000,
        source="fas",
        url="https://fas.gov.ru/case/123",
        short_quote="Гарантия безопасности БАД",
    )


@pytest.fixture
def case_ref_no_fine() -> CaseRef:
    return CaseRef(
        case_id="artra-2024",
        date="2024-01-01",
        party="Артра",
        source="fas",
    )


# ─── label tables ─────────────────────────────────────────────────────────────


def test_rule_labels_cover_every_rule_id():
    """Every RuleId value must have a human label and an article ref."""
    for rid in RuleId:
        assert rid.value in RULE_LABELS, f"missing RULE_LABELS[{rid.value}]"
        assert rid.value in RULE_ARTICLE_REFS, f"missing RULE_ARTICLE_REFS[{rid.value}]"


def test_drug_class_labels_cover_every_class():
    for cls in DrugClass:
        assert cls.value in DRUG_CLASS_LABELS


def test_frame_labels_known_frames():
    assert FRAME_LABELS["mechanism"].startswith("Mechanism")
    assert FRAME_LABELS["jtbd"].startswith("JTBD")
    assert FRAME_LABELS["benefit"].startswith("Benefit")


# ─── severity helpers ─────────────────────────────────────────────────────────


def test_severity_emoji_known_values():
    assert severity_emoji(Severity.CRITICAL) == SEVERITY_BADGE[Severity.CRITICAL]
    assert severity_emoji(Severity.WARNING) == "🟡"
    assert severity_emoji(Severity.RECOMMENDATION) == "🔵"


def test_format_severity_badge_includes_label():
    assert "КРИТИЧНО" in format_severity_badge(Severity.CRITICAL)
    assert "ПРЕДУПРЕЖДЕНИЕ" in format_severity_badge(Severity.WARNING)
    assert "РЕКОМЕНДАЦИЯ" in format_severity_badge(Severity.RECOMMENDATION)


# ─── summary_counts ───────────────────────────────────────────────────────────


def test_summary_counts_empty_report_zeros_all_keys():
    counts = summary_counts(_report())
    assert counts == {"CRITICAL": 0, "WARNING": 0, "RECOMMENDATION": 0}


def test_summary_counts_groups_by_severity():
    counts = summary_counts(
        _report(
            _v(Severity.CRITICAL),
            _v(Severity.CRITICAL),
            _v(Severity.WARNING),
            _v(Severity.RECOMMENDATION),
        )
    )
    assert counts == {"CRITICAL": 2, "WARNING": 1, "RECOMMENDATION": 1}


# ─── format_precedents_line ───────────────────────────────────────────────────


def test_format_precedents_line_none_when_empty():
    v = _v(Severity.CRITICAL, precedents=())
    assert format_precedents_line(v) is None
    assert format_precedents_line(()) is None


def test_format_precedents_line_accepts_tuple_directly(case_ref: CaseRef):
    """The helper accepts a raw tuple as well as a Violation (legal-view path)."""
    line = format_precedents_line((case_ref,))
    assert line is not None
    assert "Канефрон Н" in line


def test_format_precedents_line_with_fine(case_ref: CaseRef):
    v = _v(Severity.CRITICAL, precedents=(case_ref,))
    line = format_precedents_line(v)
    assert line is not None
    assert "Похожие дела" in line
    assert "Канефрон Н" in line
    # Year extracted from ISO date
    assert "2020" in line
    # Pretty-printed fine with thin-space thousands grouping
    assert "200 000 ₽" in line


def test_format_precedents_line_without_fine(case_ref_no_fine: CaseRef):
    line = format_precedents_line((case_ref_no_fine,))
    assert line is not None
    assert "Артра 2024" in line
    assert "₽" not in line


def test_format_precedents_line_caps_at_three():
    refs = tuple(
        CaseRef(
            case_id=f"case-{i}",
            date=f"202{i}-01-01",
            party=f"Party{i}",
            source="fas",
        )
        for i in range(5)
    )
    v = _v(Severity.CRITICAL, precedents=refs)
    line = format_precedents_line(v)
    assert line is not None
    # Three names separated by "; " — count separators = 2.
    assert line.count(";") == 2
    assert "Party4" not in line


# ─── format_precedent_full ────────────────────────────────────────────────────


def test_format_precedent_full_includes_party_date_fine(case_ref: CaseRef):
    out = format_precedent_full(case_ref)
    assert "Канефрон Н" in out
    assert "2020-06-15" in out
    assert "200 000 ₽" in out
    assert "Гарантия безопасности" in out
    assert "Источник" in out


def test_format_precedent_full_minimal(case_ref_no_fine: CaseRef):
    out = format_precedent_full(case_ref_no_fine)
    assert "Артра" in out
    assert "₽" not in out
    assert "Источник" not in out  # no URL


# ─── score_badge ──────────────────────────────────────────────────────────────


def _score(total: int) -> RewriteScore:
    """Build a RewriteScore with `total` distributed across the five subscores.

    The schema enforces `total == sum(breakdown)` and a fixed key set
    (concreteness / jtbd / mechanism / register / voice_and_structure), so we
    spread `total` evenly and dump the remainder into `voice_and_structure`.
    """
    keys = ["concreteness", "jtbd", "mechanism", "register", "voice_and_structure"]
    per = total // 5
    breakdown = dict.fromkeys(keys, per)
    breakdown["voice_and_structure"] = per + (total - per * 5)
    return RewriteScore(total=total, breakdown=breakdown)


def test_score_badge_green_for_high_scores():
    assert score_badge(_score(80)).startswith("🟢")


def test_score_badge_yellow_for_mid_scores():
    assert score_badge(_score(60)).startswith("🟡")


def test_score_badge_red_for_low_scores():
    assert score_badge(_score(40)).startswith("🔴")


def test_score_badge_handles_none():
    assert score_badge(None) == "📊 —/100"


def test_score_badge_boundary_70_is_green():
    assert score_badge(_score(70)).startswith("🟢")


def test_score_badge_boundary_50_is_yellow():
    assert score_badge(_score(50)).startswith("🟡")


# ─── sort_violations_by_priority ──────────────────────────────────────────────


def test_sort_violations_by_priority_critical_first():
    r = _report(
        _v(Severity.RECOMMENDATION, quote="rec"),
        _v(Severity.CRITICAL, quote="crit"),
        _v(Severity.WARNING, quote="warn"),
    )
    sorted_v = sort_violations_by_priority(r)
    assert [v.quote for v in sorted_v] == ["crit", "warn", "rec"]


def test_sort_violations_by_priority_stable_within_same_severity():
    r = _report(
        _v(Severity.WARNING, quote="w1"),
        _v(Severity.WARNING, quote="w2"),
    )
    sorted_v = sort_violations_by_priority(r)
    assert [v.quote for v in sorted_v] == ["w1", "w2"]


# ─── format_table_rows ────────────────────────────────────────────────────────


def test_format_table_rows_skeleton():
    r = _report(_v(Severity.CRITICAL, quote="плохо", suggested_fix="хорошо"))
    rows = format_table_rows(r)
    assert len(rows) == 1
    row = rows[0]
    assert row["Исходный текст"] == "плохо"
    assert row["Compliant вариант"] == "хорошо"
    assert "ФЗ-38" in row["Комментарий (источник)"]


def test_format_table_rows_handles_missing_quote_and_fix():
    r = _report(_v(Severity.CRITICAL, quote=None, suggested_fix=None))
    row = format_table_rows(r)[0]
    assert row["Исходный текст"] == "(отсутствует в креативе)"
    assert row["Compliant вариант"] == "—"


def test_format_table_rows_includes_intent_when_toggled(case_ref: CaseRef):
    r = _report(_v(Severity.CRITICAL, intent="хотели запомниться", precedents=(case_ref,)))
    row = format_table_rows(r, include_intent=True, include_precedents=False)[0]
    assert "Замысел бренда" in row["Комментарий (источник)"]
    assert "Похожие дела" not in row["Комментарий (источник)"]


def test_format_table_rows_includes_precedents_when_toggled(case_ref: CaseRef):
    r = _report(_v(Severity.CRITICAL, intent="x", precedents=(case_ref,)))
    row = format_table_rows(r, include_intent=False, include_precedents=True)[0]
    assert "Похожие дела" in row["Комментарий (источник)"]
    assert "Замысел бренда" not in row["Комментарий (источник)"]


def test_format_table_rows_orders_critical_first():
    r = _report(
        _v(Severity.RECOMMENDATION, quote="r"),
        _v(Severity.CRITICAL, quote="c"),
    )
    rows = format_table_rows(r)
    assert rows[0]["Исходный текст"] == "c"
    assert rows[1]["Исходный текст"] == "r"


# ─── render_violations_table_html ─────────────────────────────────────────────


def test_render_violations_table_html_escapes_html():
    rows = [
        {
            "Исходный текст": "<script>alert(1)</script>",
            "Compliant вариант": "ok",
            "Комментарий (источник)": "x",
        }
    ]
    html = render_violations_table_html(rows)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_violations_table_html_converts_soft_newlines():
    rows = [
        {
            "Исходный текст": "line1  \nline2",
            "Compliant вариант": "ok",
            "Комментарий (источник)": "x",
        }
    ]
    html = render_violations_table_html(rows)
    assert "<br>line2" in html


def test_render_violations_table_html_includes_table_styles():
    html = render_violations_table_html([])
    assert ".violations-table" in html
    assert "<thead>" in html


# ─── smoke-test the Streamlit-facing renderers ────────────────────────────────


def test_render_legal_view_callable():
    assert callable(render_legal_view)


def test_render_marketer_view_callable():
    assert callable(render_marketer_view)


def test_renderer_module_imports_cleanly():
    """Importing the modules shouldn't fail (no top-level Streamlit side-effects)."""
    import pharma_ad_compliance.renderers as r  # noqa: F401
    import pharma_ad_compliance.renderers.legal_view as lv  # noqa: F401
    import pharma_ad_compliance.renderers.marketer_view as mv  # noqa: F401
