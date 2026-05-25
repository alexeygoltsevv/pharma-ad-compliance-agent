"""Pure-Python helpers shared by the Streamlit persona views.

Kept Streamlit-free so they can be unit-tested without spinning up a Streamlit
runtime — the actual `render_*_view` modules at the package level depend on
`streamlit` and are smoke-tested only.

Anything used by both `legal_view` and `marketer_view` lives here. UI-only
concerns (column layouts, expanders, tabs) live in the per-persona modules.
"""
from __future__ import annotations

from html import escape

from pharma_ad_compliance.schemas import (
    CaseRef,
    ComplianceReport,
    RewriteScore,
    Severity,
    Violation,
)

# ─── Static label tables (centralised so both views render the same wording) ──

RULE_LABELS: dict[str, str] = {
    "ART24_P1_MINORS": "Обращение к несовершеннолетним",
    "ART24_P2_SPECIFIC_CASES": "Ссылки на конкретные случаи излечения",
    "ART24_P3_NO_SIDE_EFFECTS": "Гарантия безопасности / отсутствия побочки",
    "ART24_P4_DOCTOR_RECOMMENDATION": "Псевдо-рекомендация врача / фармацевта",
    "ART24_P5_MANDATORY_DISCLAIMER": "Отсутствует обязательное предупреждение",
    "ART24_OTHER": "Прочие нарушения ст. 24",
}

RULE_ARTICLE_REFS: dict[str, str] = {
    "ART24_P1_MINORS": "ст. 24 ч. 1 п. 1",
    "ART24_P2_SPECIFIC_CASES": "ст. 24 ч. 1 п. 2",
    "ART24_P3_NO_SIDE_EFFECTS": "ст. 24 ч. 1 п. 8",
    "ART24_P4_DOCTOR_RECOMMENDATION": "ст. 24 ч. 1 п. 4",
    "ART24_P5_MANDATORY_DISCLAIMER": "ст. 24 ч. 7",
    "ART24_OTHER": "ст. 24 (различные подпункты)",
}

SEVERITY_LABELS: dict[Severity, tuple[str, str]] = {
    Severity.CRITICAL: (
        "🔴 КРИТИЧНО",
        "Блокирует запуск — ФАС с высокой вероятностью оштрафует.",
    ),
    Severity.WARNING: (
        "🟡 ПРЕДУПРЕЖДЕНИЕ",
        "Рискованная формулировка — требуется юридическая проверка.",
    ),
    Severity.RECOMMENDATION: (
        "🔵 РЕКОМЕНДАЦИЯ",
        "Стилистическая правка на усмотрение автора.",
    ),
}

SEVERITY_BADGE: dict[Severity, str] = {
    Severity.CRITICAL: "🔴",
    Severity.WARNING: "🟡",
    Severity.RECOMMENDATION: "🔵",
}

DRUG_CLASS_LABELS: dict[str, str] = {
    "RX": "Рецептурный (Rx)",
    "OTC": "Безрецептурный (OTC)",
    "BAD": "БАД",
    "UNKNOWN": "Не определена",
}

FRAME_LABELS: dict[str, str] = {
    "mechanism": "Mechanism (механизм)",
    "jtbd": "JTBD (ситуация)",
    "benefit": "Benefit (результат)",
}


# ─── Pure formatters ──────────────────────────────────────────────────────────

def severity_emoji(sev: Severity) -> str:
    """Return the single-emoji badge for a severity (used in compact contexts)."""
    return SEVERITY_BADGE.get(sev, "⚪")


def format_severity_badge(sev: Severity) -> str:
    """Return the full "🔴 КРИТИЧНО" label for a severity."""
    return SEVERITY_LABELS.get(sev, (sev.value, ""))[0]


def summary_counts(report: ComplianceReport) -> dict[str, int]:
    """Count violations grouped by severity.

    Returns keys "CRITICAL", "WARNING", "RECOMMENDATION" with zero defaults so
    callers can render metric cards without conditional guards.
    """
    out = {"CRITICAL": 0, "WARNING": 0, "RECOMMENDATION": 0}
    for v in report.violations:
        out[v.severity.value] = out.get(v.severity.value, 0) + 1
    return out


def format_precedents_line(prec: tuple[CaseRef, ...] | Violation) -> str | None:
    """Render precedent CaseRefs as a compact "⚖️ Похожие дела: ..." one-liner.

    Accepts either a tuple of CaseRefs (preferred) or a Violation (for
    backwards-compat with the old `_format_precedents_line(v)` callsite in
    `app.py`). Returns None when there are no precedents.

    Example: "⚖️ Похожие дела: Канефрон Н 2020 (200 000 ₽); Артра 2024"
    """
    refs = prec.precedents if isinstance(prec, Violation) else prec
    if not refs:
        return None
    pieces: list[str] = []
    for ref in refs[:3]:
        year = ref.date.split("-", 1)[0] if ref.date else ""
        label = f"{ref.party} {year}".strip()
        if ref.fine_rub:
            fine_fmt = f"{ref.fine_rub:,}".replace(",", " ")
            label += f" ({fine_fmt} ₽)"
        pieces.append(label)
    return "⚖️ Похожие дела: " + "; ".join(pieces)


def format_precedent_full(ref: CaseRef) -> str:
    """Verbose precedent rendering for the legal view (party, date, fine, source)."""
    parts = [f"**{ref.party}**"]
    if ref.date:
        parts.append(ref.date)
    if ref.fine_rub:
        fine_fmt = f"{ref.fine_rub:,}".replace(",", " ")
        parts.append(f"штраф {fine_fmt} ₽")
    if ref.short_quote:
        parts.append(f"«{ref.short_quote}»")
    line = " · ".join(parts)
    if ref.url:
        line += f"  \n[Источник]({ref.url})"
    return line


def score_badge(score: RewriteScore | None) -> str:
    """Color-coded badge for a rewrite quality score (0-100). Empty if no score."""
    if score is None:
        return "📊 —/100"
    total = score.total
    if total >= 70:
        return f"🟢 📊 {total}/100"
    if total >= 50:
        return f"🟡 📊 {total}/100"
    return f"🔴 📊 {total}/100"


def sort_violations_by_priority(report: ComplianceReport) -> list[Violation]:
    """Stable sort of violations by severity rank (CRITICAL first)."""
    return sorted(report.violations, key=lambda v: v.severity.rank)


def format_table_rows(
    report: ComplianceReport,
    *,
    include_intent: bool = True,
    include_precedents: bool = True,
) -> list[dict[str, str]]:
    """Build the violation table rows used by both views.

    `include_intent` / `include_precedents` toggle what extra context lines
    appear under the law-citation header — the legal view leans on precedents,
    the marketer view leans on intent_hypothesis.
    """
    rows: list[dict[str, str]] = []
    for v in sort_violations_by_priority(report):
        comment_parts = [
            f"{SEVERITY_BADGE[v.severity]} {RULE_LABELS.get(v.rule_id.value, v.rule_id.value)}",
            f"📖 {RULE_ARTICLE_REFS.get(v.rule_id.value, '')} ФЗ-38",
            v.explanation,
        ]
        if include_intent and v.intent_hypothesis:
            comment_parts.append(f"🧠 Замысел бренда: {v.intent_hypothesis}")
        if include_precedents:
            precedents_line = format_precedents_line(v)
            if precedents_line:
                comment_parts.append(precedents_line)
        rows.append(
            {
                "Исходный текст": v.quote or "(отсутствует в креативе)",
                "Compliant вариант": v.suggested_fix or "—",
                "Комментарий (источник)": "  \n".join(p for p in comment_parts if p),
            }
        )
    return rows


def render_violations_table_html(rows: list[dict[str, str]]) -> str:
    """Render the violation table as a self-contained HTML string.

    Returned HTML is wrapped in a <style> + <table> block; callers pass it to
    `st.markdown(..., unsafe_allow_html=True)`. Extracted here so both views
    share one table style and so the HTML can be snapshot-tested.
    """
    headers = ["Исходный текст", "Compliant вариант", "Комментарий (источник)"]

    def cell(text: str) -> str:
        return escape(text).replace("  \n", "<br>").replace("\n", "<br>")

    thead = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(r[h])}</td>" for h in headers) + "</tr>"
        for r in rows
    )
    return f"""
        <style>
        .violations-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.92rem;
            margin: 0.5rem 0 1rem 0;
            table-layout: fixed;
        }}
        .violations-table col.col-src {{ width: 28%; }}
        .violations-table col.col-fix {{ width: 28%; }}
        .violations-table col.col-cmt {{ width: 44%; }}
        .violations-table th,
        .violations-table td {{
            border: 1px solid rgba(128,128,128,0.25);
            padding: 0.6rem 0.75rem;
            vertical-align: top;
            text-align: left;
            white-space: pre-wrap;
            word-break: break-word;
            overflow-wrap: anywhere;
            line-height: 1.45;
        }}
        .violations-table th {{
            background: rgba(128,128,128,0.10);
            font-weight: 600;
        }}
        .violations-table tr:nth-child(even) td {{
            background: rgba(128,128,128,0.04);
        }}
        </style>
        <table class="violations-table">
          <colgroup>
            <col class="col-src"><col class="col-fix"><col class="col-cmt">
          </colgroup>
          <thead><tr>{thead}</tr></thead>
          <tbody>{body}</tbody>
        </table>
        """
