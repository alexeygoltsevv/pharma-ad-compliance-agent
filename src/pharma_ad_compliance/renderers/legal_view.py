"""Legal-reviewer persona view.

Layout priorities (different from the marketer view):
1. Severity summary card (blocker / risky / stylistic counts).
2. Findings table sorted by priority, with precedents expanded inline under
   each CRITICAL finding.
3. Compliant rewrites collapsed into a single expander (secondary context).

The view delegates to `_shared` for all formatting so the marketer view stays
in lock-step on label wording.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from pharma_ad_compliance.schemas import ComplianceReport, Severity

from . import _shared


def _render_summary_card(report: ComplianceReport, container: Any) -> None:
    """Three-column metric card: 🔴 blockers / 🟡 risky / 🔵 stylistic."""
    counts = _shared.summary_counts(report)
    cols = container.columns(3)
    cols[0].metric(
        "🔴 Блокеры запуска",
        counts["CRITICAL"],
        delta_color="inverse",
        help="CRITICAL: ФАС с высокой вероятностью оштрафует.",
    )
    cols[1].metric(
        "🟡 Рискованные",
        counts["WARNING"],
        help="WARNING: рискованные формулировки, требуется юр-проверка.",
    )
    cols[2].metric(
        "🔵 Стилистические",
        counts["RECOMMENDATION"],
        help="RECOMMENDATION: правки на усмотрение автора.",
    )


def _render_precedents_block(report: ComplianceReport, container: Any) -> None:
    """Expanded precedent blocks under each CRITICAL finding."""
    critical = report.by_severity(Severity.CRITICAL)
    with_prec = [v for v in critical if v.precedents]
    if not with_prec:
        return
    container.subheader("⚖️ Прецеденты ФАС по критичным нарушениям")
    container.caption(
        "Аналогичные дела из базы `case_law/fas_decisions/` и утверждённых "
        "ранее отчётов — для оценки регуляторного риска."
    )
    for v in with_prec:
        rule_label = _shared.RULE_LABELS.get(v.rule_id.value, v.rule_id.value)
        art_ref = _shared.RULE_ARTICLE_REFS.get(v.rule_id.value, "")
        with container.expander(
            f"🔴 {rule_label} · {art_ref} — {len(v.precedents)} прецедент(ов)",
            expanded=True,
        ):
            if v.quote:
                st.markdown(f"> «{v.quote}»")
            for ref in v.precedents:
                st.markdown("- " + _shared.format_precedent_full(ref))


def _render_rewrites_collapsed(report: ComplianceReport, container: Any) -> None:
    """Compliant rewrites are secondary for the legal view — hide in expander."""
    if not (report.rewrite_variants or report.rewritten_text):
        return
    n = len(report.rewrite_variants) or 1
    with container.expander(
        f"✍️ Compliant-переписанные варианты ({n}) — справочно", expanded=False
    ):
        if report.rewrite_variants:
            for variant in report.rewrite_variants:
                frame_label = _shared.FRAME_LABELS.get(variant.frame, variant.frame)
                badge = _shared.score_badge(variant.quality_score)
                st.markdown(f"**{frame_label}** · {badge}")
                if variant.compliance_passed:
                    st.caption("✅ Прошёл compliance-проверку")
                else:
                    st.caption(
                        f"⚠️ {len(variant.recheck_violations)} остаточн. нарушений"
                    )
                with st.container(border=True):
                    st.write(variant.text)
        elif report.rewritten_text:
            with st.container(border=True):
                st.write(report.rewritten_text)


def render_legal_view(
    report: ComplianceReport,
    container: Any = st,
) -> None:
    """Render the legal-reviewer persona view.

    `container` defaults to the module-level `st` so callers can just pass
    `render_legal_view(report)`; tests can pass a mock container.
    """
    container.subheader("⚖️ Сводка для юриста")
    container.caption(
        "Приоритизация по тяжести нарушений. Полные цитаты статей и "
        "прецеденты ФАС развёрнуты ниже."
    )
    _render_summary_card(report, container)

    if report.violations:
        container.subheader("📋 Нарушения (по приоритету)")
        rows = _shared.format_table_rows(
            report, include_intent=False, include_precedents=True
        )
        container.markdown(
            _shared.render_violations_table_html(rows), unsafe_allow_html=True
        )
    else:
        container.info("Нарушений не найдено.")

    _render_precedents_block(report, container)
    _render_rewrites_collapsed(report, container)
