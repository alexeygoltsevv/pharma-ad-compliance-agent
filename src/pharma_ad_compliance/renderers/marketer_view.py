"""Brand-marketer persona view.

Layout priorities (different from the legal view):
1. Rewrite variant tabs FIRST with quality-score badges (marketer ships copy).
2. Findings table focused on intent_hypothesis + suggested_fix (what to change).
3. Law subpoints and precedents are demoted into compact expanders.

The view delegates to `_shared` for all formatting. Per-variant feedback uses
an optional callback so the host (`app.py`) keeps ownership of the pipeline
re-run flow.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from pharma_ad_compliance.schemas import ComplianceReport, RewriteVariant, Severity

from . import _shared

# Callback type: (variant, comment) -> None. The host re-runs the pipeline.
VariantFeedbackHandler = Callable[[RewriteVariant, str], None]


def _render_variant(
    variant: RewriteVariant,
    idx: int,
    on_feedback: VariantFeedbackHandler | None,
) -> None:
    """Render one rewrite variant: badge, text, residual-violations, feedback."""
    badge = _shared.score_badge(variant.quality_score)
    badge_col, _ = st.columns([1, 4])
    with badge_col:
        st.markdown(f"### {badge}")

    score = variant.quality_score
    if score is not None:
        sub = " · ".join(f"{n}: {v}/20" for n, v in score.breakdown.items())
        st.caption(sub)
        if score.notes:
            st.caption(f"📝 {score.notes}")
    else:
        st.caption("📊 Оценка качества недоступна (сбой scoring-пасса).")

    if variant.compliance_passed:
        st.success("✅ Прошёл compliance-проверку — можно отправлять")
    else:
        n = len(variant.recheck_violations)
        st.warning(f"⚠️ {n} остаточн. нарушени(й) после переписки")

    with st.container(border=True):
        st.write(variant.text)

    if not variant.compliance_passed and variant.recheck_violations:
        with st.expander(
            f"Остаточные нарушения ({len(variant.recheck_violations)})",
            expanded=False,
        ):
            for rv in variant.recheck_violations:
                rule_label = _shared.RULE_LABELS.get(rv.rule_id.value, rv.rule_id.value)
                st.markdown(f"**[{rv.severity.value}] {rule_label}**")
                if rv.quote:
                    st.markdown(f"> «{rv.quote}»")
                st.write(rv.explanation)

    if on_feedback is None:
        return
    # NOT wrapped in st.form so the submit can be reactively disabled until the
    # textarea has content. The host's on_feedback handler triggers st.rerun()
    # on success, so we clear the input via session_state before invoking it.
    with st.expander("💬 Комментарий по этому варианту", expanded=False):
        text_key = f"variant_feedback_text_{idx}"
        st.text_area(
            "Что доработать в этом варианте?",
            height=100,
            key=text_key,
            placeholder="Например: «Сделай чуть менее формально».",
        )
        comment = st.session_state.get(text_key, "").strip()
        if st.button(
            "Применить ко всему пайплайну",
            disabled=not comment,
            key=f"variant_feedback_submit_{idx}",
            help=(
                "Напишите комментарий выше — кнопка станет активной."
                if not comment
                else "Перезапустит пайплайн с этим комментарием."
            ),
        ):
            # Clear before on_feedback() — the host typically reruns after
            # pipeline succeeds, and we want the next render to show an
            # empty input rather than the just-submitted text.
            st.session_state[text_key] = ""
            on_feedback(variant, comment)


def _render_variant_tabs(
    report: ComplianceReport,
    container: Any,
    on_feedback: VariantFeedbackHandler | None,
) -> None:
    if not report.rewrite_variants:
        if report.rewritten_text:
            container.subheader("✍️ Compliant-переписанный вариант (целиком)")
            with container.container(border=True):
                container.write(report.rewritten_text)
        return
    container.subheader("✍️ Compliant-переписанные варианты")
    container.caption(
        "Три рамки одной и той же compliant-переписки. Сравните и выберите ту, "
        "что лучше соответствует tone-of-voice бренда."
    )
    tab_labels = [
        _shared.FRAME_LABELS.get(v.frame, v.frame) for v in report.rewrite_variants
    ]
    tabs = container.tabs(tab_labels)
    for tab, (idx, variant) in zip(
        tabs, enumerate(report.rewrite_variants), strict=True
    ):
        with tab:
            _render_variant(variant, idx, on_feedback)


def _render_precedents_compact(violations_with_prec: list, container: Any) -> None:
    """Marketer-side precedent rendering: compact expander, not inline blocks."""
    if not violations_with_prec:
        return
    total = sum(len(v.precedents) for v in violations_with_prec)
    with container.expander(
        f"⚖️ {total} похожих штраф(ов) ФАС по этим нарушениям", expanded=False
    ):
        for v in violations_with_prec:
            rule_label = _shared.RULE_LABELS.get(v.rule_id.value, v.rule_id.value)
            st.markdown(f"**{_shared.severity_emoji(v.severity)} {rule_label}**")
            line = _shared.format_precedents_line(v)
            if line:
                st.markdown(line)


def render_marketer_view(
    report: ComplianceReport,
    container: Any = st,
    on_variant_feedback: VariantFeedbackHandler | None = None,
) -> None:
    """Render the brand-marketer persona view.

    `on_variant_feedback` is an optional callback (variant, comment_text) that
    the host wires up to its pipeline re-run flow. When None (e.g. in tests),
    the comment expander is omitted.
    """
    counts = _shared.summary_counts(report)
    container.subheader("🎯 Готово к отправке: варианты переписки")
    container.caption(
        f"Найдено нарушений: 🔴 {counts['CRITICAL']} · 🟡 {counts['WARNING']} "
        f"· 🔵 {counts['RECOMMENDATION']}. Внизу — что и почему править."
    )

    _render_variant_tabs(report, container, on_variant_feedback)

    if report.violations:
        container.subheader("📋 Что менять в исходнике")
        container.caption(
            "Фокус на «было → стало» и почему бренд это написал. "
            "Подпункты закона — в свёрнутом блоке ниже."
        )
        rows = _shared.format_table_rows(
            report, include_intent=True, include_precedents=False
        )
        container.markdown(
            _shared.render_violations_table_html(rows), unsafe_allow_html=True
        )
    else:
        container.success("Нарушений не найдено — можно запускать.")

    with_prec = [
        v for v in report.violations
        if v.severity == Severity.CRITICAL and v.precedents
    ]
    _render_precedents_compact(with_prec, container)

    with container.expander("📖 Подпункты ст. 24, на которые ссылаются нарушения"):
        seen: dict[str, str] = {}
        for v in report.violations:
            rid = v.rule_id.value
            if rid in seen:
                continue
            seen[rid] = (
                f"**{_shared.RULE_LABELS.get(rid, rid)}** — "
                f"{_shared.RULE_ARTICLE_REFS.get(rid, '')} ФЗ-38"
            )
        if seen:
            for line in seen.values():
                st.markdown(f"- {line}")
        else:
            st.caption("Нарушений нет.")
