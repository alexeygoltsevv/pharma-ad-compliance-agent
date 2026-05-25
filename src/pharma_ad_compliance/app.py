"""Streamlit demo. Запуск: `streamlit run src/pharma_ad_compliance/app.py`."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from pharma_ad_compliance.pipeline import run_compliance
from pharma_ad_compliance.schemas import (
    ComplianceReport,
    Creative,
    ImageCreative,
    PdfCreative,
    RewriteVariant,
    Severity,
    TextCreative,
    UrlCreative,
    Violation,
)

# Папка-база знаний для одобренных пользователем отчётов.
# Resolution order (first match wins):
#   1. PHARMA_AD_APPROVED_DIR env var — explicit override (Docker, sandbox).
#   2. <repo>/case_law/approved_reports/ — dev-checkout default.
#   3. <cwd>/case_law/approved_reports/ — packaged install / arbitrary working dir.
_env_dir = os.environ.get("PHARMA_AD_APPROVED_DIR", "").strip()
if _env_dir:
    _APPROVED_REPORTS_DIR = Path(_env_dir)
else:
    _APPROVED_REPORTS_DIR = (
        Path(__file__).resolve().parents[2].parent / "case_law" / "approved_reports"
    )
    if not _APPROVED_REPORTS_DIR.parent.exists():
        _APPROVED_REPORTS_DIR = Path.cwd() / "case_law" / "approved_reports"

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
    Severity.CRITICAL: ("🔴 КРИТИЧНО", "Блокирует запуск — ФАС с высокой вероятностью оштрафует."),
    Severity.WARNING: ("🟡 ПРЕДУПРЕЖДЕНИЕ", "Рискованная формулировка — требуется юридическая проверка."),
    Severity.RECOMMENDATION: ("🔵 РЕКОМЕНДАЦИЯ", "Стилистическая правка на усмотрение автора."),
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


# ─── session-state init ──────────────────────────────────────────────────────
def _init_state() -> None:
    defaults: dict[str, object] = {
        "creative": None,
        "report": None,
        "feedback_history": [],
        "show_refine": False,
        "include_rewrite": True,
        "last_elapsed": None,
        "approved_path": None,
        "running": False,
        "run_error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_run_state() -> None:
    """Очищает результат, но НЕ creative — позволяет повторно нажать «Запустить» после approve."""
    st.session_state.report = None
    st.session_state.feedback_history = []
    st.session_state.show_refine = False
    st.session_state.approved_path = None


def _persist_upload(data: bytes, suffix: str) -> Path:
    """Write an uploaded file to a content-addressed temp path.

    Streamlit reruns the whole script on every interaction, so a plain
    NamedTemporaryFile(delete=False) per rerun leaks one file each time. Keying
    the path on a content hash makes repeated reruns of the same upload reuse a
    single file (bounded by the number of distinct uploads).

    Raises ValueError if the upload exceeds the parser's file-size cap — we want
    to fail loudly *before* writing 100 MB to /tmp, not after.
    """
    # Pragmatic private import — the parser owns this constant and the security
    # agent may rename it; if so, update both sides in one pass.
    from pharma_ad_compliance.agents.parser_agent import _MAX_FILE_BYTES

    if len(data) > _MAX_FILE_BYTES:
        raise ValueError(
            f"upload too large: {len(data)} bytes "
            f"(cap {_MAX_FILE_BYTES} = {_MAX_FILE_BYTES // (1024 * 1024)} МБ)"
        )
    digest = hashlib.sha1(data).hexdigest()[:16]
    upload_dir = Path(tempfile.gettempdir()) / "pharma_ad_uploads"
    upload_dir.mkdir(exist_ok=True)
    path = upload_dir / f"{digest}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    return path


_init_state()

st.set_page_config(
    page_title="AI Агент: Помощник по комплаенсу",
    page_icon="💊",
    layout="wide",
)

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("О проекте")
    st.markdown(
        "Мультиагентный анализатор рекламы лекарственных средств на соответствие "
        "**ФЗ-38 «О рекламе», ст. 24**.\n\n"
        "На вход — текст, баннер, ссылка или PDF (статья / макет лендинга / шаблон "
        "email-рассылки). На выходе — отчёт с приоритезацией нарушений и автоматический "
        "переписанный compliant-вариант."
    )

    st.divider()
    st.subheader("Что проверяется")
    for rid, label in RULE_LABELS.items():
        st.markdown(f"• **{label}**  \n_{RULE_ARTICLE_REFS[rid]}_")

    st.divider()
    st.caption(
        "LLM-вызовы идут через Claude Agent SDK по подписке Claude Code. "
        "Один прогон — 8 параллельных вызовов: классификатор, 6 чекеров правил "
        "и редактор."
    )
    st.caption("Источник: [fas.gov.ru](https://fas.gov.ru) · ФЗ-38 ст. 24")

    if _APPROVED_REPORTS_DIR.exists():
        approved_count = len(list(_APPROVED_REPORTS_DIR.glob("approved-*.json")))
        if approved_count:
            st.divider()
            st.caption(f"📚 База знаний: **{approved_count}** утверждённых отчётов")

# ─── Header ──────────────────────────────────────────────────────────────────
st.title("💊 AI Агент: Помощник по комплаенсу")
st.caption(
    "Инструмент для **медсоветников и бренд-менеджеров**: проверяет рекламу ЛС на "
    "соответствие ст. 24 ФЗ-38 и последней инструкции или вкладышу."
)

# ─── Input ───────────────────────────────────────────────────────────────────
mode = st.radio(
    "Тип входных данных",
    ["📝 Текст", "🖼 Баннер", "🔗 Ссылка", "📄 PDF (статья / макет / рассылка)"],
    horizontal=True,
    label_visibility="visible",
)

creative: Creative | None = None
if mode == "📝 Текст":
    text = st.text_area(
        "Текст рекламного объявления",
        height=200,
        placeholder="Вставьте сюда текст рекламного объявления, баннера или лендинга…",
    )
    if text.strip():
        creative = TextCreative(text=text)
elif mode == "🖼 Баннер":
    uploaded = st.file_uploader(
        "Загрузите изображение баннера",
        type=["png", "jpg", "jpeg", "webp", "gif"],
        help="Текст с изображения будет извлечён через Claude Vision.",
    )
    if uploaded:
        suffix = Path(uploaded.name).suffix or ".png"
        try:
            img_path = _persist_upload(uploaded.getvalue(), suffix)
        except ValueError as e:
            st.error(f"❌ Файл слишком большой: {e}")
        else:
            creative = ImageCreative(image_path=img_path)
            st.image(str(img_path), caption=uploaded.name, width=360)
elif mode == "📄 PDF (статья / макет / рассылка)":
    uploaded_pdf = st.file_uploader(
        "Загрузите PDF",
        type=["pdf"],
        help=(
            "Поддерживаются: научно-популярные статьи, макеты лендингов и "
            "email-рассылок, экспорт презентаций. Если в PDF есть текстовый слой — "
            "он считается напрямую; если только картинки (макет из дизайн-софта) — "
            "каждая страница автоматически растеризуется и распознаётся Claude Vision."
        ),
    )
    if uploaded_pdf:
        try:
            pdf_path = _persist_upload(uploaded_pdf.getvalue(), ".pdf")
        except ValueError as e:
            st.error(f"❌ PDF слишком большой: {e}")
        else:
            creative = PdfCreative(pdf_path=pdf_path)
            st.caption(f"📎 {uploaded_pdf.name} · {uploaded_pdf.size // 1024} КБ")
else:
    url = st.text_input(
        "Ссылка на лендинг или страницу с рекламой",
        placeholder="https://example.com/landing  (можно без https://)",
    )
    raw = url.strip()
    if raw:
        if "://" not in raw:
            raw = f"https://{raw}"
        try:
            creative = UrlCreative(url=raw)  # type: ignore[arg-type]
        except ValidationError as e:
            st.error(f"Некорректная ссылка: {e.errors()[0]['msg']}")

include_rewrite = st.checkbox(
    "Сгенерировать compliant-переписанный вариант",
    value=st.session_state.include_rewrite,
    help="Если снять галочку — пропустит этап редактора и завершится на ~10 секунд быстрее.",
)
st.session_state.include_rewrite = include_rewrite

st.info(
    "⏱ Анализ занимает **до 1 минуты** — пайплайн делает 8 параллельных LLM-вызовов "
    "(классификатор, 6 чекеров правил и редактор)."
)


# ─── Helper: actually run the pipeline ───────────────────────────────────────
def _run_pipeline(
    creative_arg: Creative,
    feedback: list[str],
) -> tuple[ComplianceReport, float]:
    started = time.monotonic()
    is_pdf = isinstance(creative_arg, PdfCreative)
    is_image = isinstance(creative_arg, ImageCreative)
    with st.status(
        "Запускаю пайплайн…" if not feedback else f"Повторный прогон с {len(feedback)} комментарием(ями)…",
        expanded=True,
    ) as status:
        if is_pdf:
            st.write("📄 Извлекаю текст из PDF (текстовый слой + Vision для страниц-картинок)…")
        elif is_image:
            st.write("🖼 Распознаю текст с баннера через Claude Vision…")
        else:
            st.write("📥 Извлекаю текст из креатива…")
        st.write("🧬 Классифицирую препарат (Rx / OTC / БАД)…")
        st.write("🔍 Запускаю 6 чекеров правил параллельно…")
        if st.session_state.include_rewrite:
            st.write("✍️ Готовлю compliant-переписку…")
        report = asyncio.run(
            run_compliance(
                creative_arg,
                include_rewrite=st.session_state.include_rewrite,
                user_feedback=feedback or None,
            )
        )
        elapsed = time.monotonic() - started
        status.update(label=f"Готово за {elapsed:.1f} с", state="complete", expanded=False)
    return report, elapsed


# ─── Run button ──────────────────────────────────────────────────────────────
# A failure from the previous run is stashed in session_state and shown here so it
# survives the rerun that re-enables the button.
if st.session_state.run_error:
    st.error(st.session_state.run_error)
    st.session_state.run_error = None

running = st.session_state.running
run_clicked = st.button(
    "⏳ Идёт проверка соответствия…" if running else "▶ Запустить проверку соответствия",
    type="primary",
    disabled=creative is None or running,
    use_container_width=True,
)

# Click → flip into the running state and rerun immediately, so the button renders
# disabled ("⏳ …") BEFORE the blocking pipeline starts (Streamlit is synchronous,
# so without this the primary button stays lit the whole time the pipeline runs).
if run_clicked and creative is not None and not running:
    _reset_run_state()
    st.session_state.creative = creative
    st.session_state.running = True
    st.rerun()

# Execute on the rerun where running=True (the button above is now greyed out).
if st.session_state.running and st.session_state.creative is not None:
    try:
        report, elapsed = _run_pipeline(st.session_state.creative, feedback=[])
    except Exception as e:  # noqa: BLE001 — surface any pipeline failure to the user
        st.session_state.run_error = (
            "❌ Не удалось получить ответ от Claude. Возможные причины: "
            "временный сбой подписки, исчерпан лимит запросов или CLI потерял "
            "авторизацию. Запустите проверку ещё раз через 30–60 секунд.\n\n"
            f"**Техническая ошибка:** `{type(e).__name__}: {e}`"
        )
    else:
        st.session_state.report = report
        st.session_state.last_elapsed = elapsed
    finally:
        st.session_state.running = False
    st.rerun()


# ─── Result rendering ────────────────────────────────────────────────────────
def _format_precedents_line(v: Violation) -> str | None:
    """Render CRITICAL violation precedents as a compact one-liner.

    Example: "⚖️ Похожие дела: Канефрон Н 2020 (200 000 ₽); Артра 2024"
    Returns None when there are no precedents (most violations) or for
    non-CRITICAL severity (the matcher only enriches CRITICAL by default).
    """
    if not v.precedents:
        return None
    pieces: list[str] = []
    for ref in v.precedents[:3]:
        year = ref.date.split("-", 1)[0] if ref.date else ""
        label = f"{ref.party} {year}".strip()
        if ref.fine_rub:
            # Pretty-print fine with thin-space thousands grouping.
            fine_fmt = f"{ref.fine_rub:,}".replace(",", " ")
            label += f" ({fine_fmt} ₽)"
        pieces.append(label)
    return "⚖️ Похожие дела: " + "; ".join(pieces)


def _format_table_rows(report: ComplianceReport) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for v in report.violations:
        comment_parts = [
            f"{SEVERITY_BADGE[v.severity]} {RULE_LABELS.get(v.rule_id.value, v.rule_id.value)}",
            f"📖 {RULE_ARTICLE_REFS.get(v.rule_id.value, '')} ФЗ-38",
            v.explanation,
        ]
        if v.intent_hypothesis:
            comment_parts.append(f"🧠 Замысел бренда: {v.intent_hypothesis}")
        precedents_line = _format_precedents_line(v)
        if precedents_line:
            comment_parts.append(precedents_line)
        rows.append(
            {
                "Исходный текст": v.quote or "(отсутствует в креативе)",
                "Compliant вариант": v.suggested_fix or "—",
                "Комментарий (источник)": "  \n".join(part for part in comment_parts if part),
            }
        )
    return rows


def _render_violations_table(rows: list[dict[str, str]]) -> None:
    from html import escape

    headers = ["Исходный текст", "Compliant вариант", "Комментарий (источник)"]

    def cell(text: str) -> str:
        return escape(text).replace("  \n", "<br>").replace("\n", "<br>")

    thead = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(r[h])}</td>" for h in headers) + "</tr>"
        for r in rows
    )

    st.markdown(
        f"""
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
        """,
        unsafe_allow_html=True,
    )


def _format_plain_text_report(report: ComplianceReport, feedback: list[str]) -> str:
    """Простой text-формат для копирования в буфер.

    Структура (для удобства бренд-менеджера в письме к юристу/команде):
    1. Шапка: категория, кол-во нарушений, вердикт.
    2. Список нарушений с цитатой, источником и предложением правки.
    3. Compliant-переписанный вариант — ТОЛЬКО исправленные фрагменты
       (пары «было / стало»), а не весь креатив целиком.
    4. История комментариев (если были итерации).
    """
    out: list[str] = []
    out.append("ОТЧЁТ О КОМПЛАЕНС-ПРОВЕРКЕ")
    out.append("=" * 60)
    out.append(f"Категория препарата: {DRUG_CLASS_LABELS.get(report.drug_class.value, report.drug_class.value)}")
    out.append(f"Нарушений найдено: {len(report.violations)}")
    out.append(f"Соответствует ст. 24 ФЗ-38: {'Да' if report.is_compliant else 'Нет'}")
    if feedback:
        out.append(f"Итераций с комментариями: {len(feedback)}")
    out.append("")

    if report.violations:
        out.append("НАЙДЕННЫЕ НАРУШЕНИЯ")
        out.append("-" * 60)
        for i, v in enumerate(report.violations, 1):
            out.append(f"{i}. [{v.severity.value}] {RULE_LABELS.get(v.rule_id.value, v.rule_id.value)}")
            out.append(f"   Источник: {RULE_ARTICLE_REFS.get(v.rule_id.value, '')} ФЗ-38")
            if v.quote:
                out.append(f"   Цитата: «{v.quote}»")
            out.append(f"   Почему: {v.explanation}")
            if v.suggested_fix:
                out.append(f"   Как исправить: {v.suggested_fix}")
            out.append("")

    # Только исправленные фрагменты — пары «было / стало».
    fixes = [v for v in report.violations if v.suggested_fix]
    if fixes:
        out.append("COMPLIANT-ПЕРЕПИСАННЫЙ ВАРИАНТ")
        out.append("-" * 60)
        for i, v in enumerate(fixes, 1):
            original = v.quote or "(отсутствует в креативе)"
            out.append(f"{i}. Было: «{original}»")
            out.append(f"   Стало: {v.suggested_fix}")
            out.append("")

    if feedback:
        out.append("КОММЕНТАРИИ ПОЛЬЗОВАТЕЛЯ")
        out.append("-" * 60)
        for i, fb in enumerate(feedback, 1):
            out.append(f"{i}. {fb}")
    return "\n".join(out)


def _save_approved(report: ComplianceReport, feedback: list[str]) -> Path:
    _APPROVED_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    digest = hashlib.sha1(report.extracted_text.encode("utf-8")).hexdigest()[:8]
    out_path = _APPROVED_REPORTS_DIR / f"approved-{timestamp}-{digest}.json"
    payload = {
        "id": f"approved-{timestamp}-{digest}",
        "approved_at": datetime.now(UTC).isoformat(),
        "input": report.extracted_text,
        "drug_class": report.drug_class.value,
        "source_kind": report.source_kind,
        "violations": [v.model_dump(mode="json") for v in report.violations],
        "rewritten_text": report.rewritten_text,
        "user_feedback_history": feedback,
        "iterations": len(feedback),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ─── Rewrite-variant rendering helpers ───────────────────────────────────────
# Russian-language labels for the three editor framings. Centralised so the
# CLI / Streamlit / future UIs render the same wording.
_FRAME_LABELS: dict[str, str] = {
    "mechanism": "Mechanism (механизм)",
    "jtbd": "JTBD (ситуация)",
    "benefit": "Benefit (результат)",
}


def _score_badge(total: int) -> str:
    """Color-coded badge for a rewrite quality score (0-100)."""
    if total >= 70:
        return f"🟢 📊 {total}/100"
    if total >= 50:
        return f"🟡 📊 {total}/100"
    return f"🔴 📊 {total}/100"


def _render_one_variant(variant: RewriteVariant, idx: int) -> None:
    score = variant.quality_score
    if score is not None:
        badge_col, _ = st.columns([1, 4])
        with badge_col:
            st.markdown(f"### {_score_badge(score.total)}")
        sub = " · ".join(
            f"{name}: {value}/20"
            for name, value in score.breakdown.items()
        )
        st.caption(sub)
        if score.notes:
            st.caption(f"📝 {score.notes}")
    else:
        st.caption("📊 Оценка качества недоступна (сбой scoring-пасса).")

    if variant.compliance_passed:
        st.success("✅ Прошёл compliance-проверку")
    else:
        n = len(variant.recheck_violations)
        st.warning(f"⚠️ {n} остаточн{'ое' if n == 1 else ('ых' if 2 <= n <= 4 else 'ых')} нарушение(й) после переписки")

    with st.container(border=True):
        st.write(variant.text)

    if not variant.compliance_passed and variant.recheck_violations:
        with st.expander(f"Остаточные нарушения ({len(variant.recheck_violations)})", expanded=False):
            for rv in variant.recheck_violations:
                st.markdown(
                    f"**[{rv.severity.value}] {RULE_LABELS.get(rv.rule_id.value, rv.rule_id.value)}**"
                )
                if rv.quote:
                    st.markdown(f"> «{rv.quote}»")
                st.write(rv.explanation)

    # Per-variant feedback / accept controls — reuse the existing feedback flow.
    with st.expander("💬 Комментарий по этому варианту", expanded=False):
        with st.form(f"variant_feedback_{idx}", clear_on_submit=True):
            comment = st.text_area(
                "Что доработать в этом варианте?",
                height=100,
                key=f"variant_feedback_text_{idx}",
            )
            submitted = st.form_submit_button("Применить ко всему пайплайну")
        if submitted:
            text_clean = (comment or "").strip()
            if not text_clean:
                st.warning("Комментарий пустой — напишите, что нужно учесть.")
            else:
                tagged = f"[вариант {variant.frame}-led] {text_clean}"
                st.session_state.feedback_history.append(tagged)
                st.session_state.show_refine = False
                try:
                    report2, elapsed2 = _run_pipeline(
                        st.session_state.creative,
                        feedback=st.session_state.feedback_history,
                    )
                except Exception as e:  # noqa: BLE001
                    st.session_state.feedback_history.pop()
                    st.error(
                        "❌ Перезапуск с комментарием не удался. "
                        f"`{type(e).__name__}: {e}`"
                    )
                else:
                    st.session_state.report = report2
                    st.session_state.last_elapsed = elapsed2
                    st.rerun()


def _render_rewrite_variant_tabs(report: ComplianceReport) -> None:
    """Render mechanism / jtbd / benefit tabs for the rewrite variants."""
    tab_labels = [_FRAME_LABELS.get(v.frame, v.frame) for v in report.rewrite_variants]
    tabs = st.tabs(tab_labels)
    for tab, (idx, variant) in zip(tabs, enumerate(report.rewrite_variants), strict=True):
        with tab:
            _render_one_variant(variant, idx)


# ─── If we have a report, show it ────────────────────────────────────────────
if st.session_state.report is not None and not st.session_state.running:
    report = st.session_state.report
    feedback = st.session_state.feedback_history

    if st.session_state.last_elapsed is not None:
        st.success(f"✅ Проверка завершена за {st.session_state.last_elapsed:.1f} с")

    if feedback:
        st.caption(
            f"🔄 Итерация **№{len(feedback) + 1}** — учтено комментариев: {len(feedback)}"
        )

    # Summary metrics
    cols = st.columns(4)
    cols[0].metric(
        "Категория препарата",
        DRUG_CLASS_LABELS.get(report.drug_class.value, report.drug_class.value),
    )
    cols[1].metric("Нарушений найдено", len(report.violations))
    critical_count = len(report.by_severity(Severity.CRITICAL))
    cols[2].metric("Критичных", critical_count, delta_color="inverse")
    cols[3].metric(
        "Соответствует ФЗ-38",
        "✅ Да" if report.is_compliant else "❌ Нет",
    )

    if report.is_compliant and not report.violations:
        st.success(
            "🎉 Нарушений по ст. 24 ФЗ-38 не выявлено. Тем не менее, "
            "перед запуском рекомендуем юридическую проверку — "
            "ст. 24 не покрывает весь объём требований к рекламе ЛС."
        )
    elif report.is_compliant:
        st.success(
            "Критических нарушений нет, но есть предупреждения / "
            "рекомендации — см. ниже."
        )
    else:
        st.error(
            f"❌ Найдено **{critical_count}** критическ"
            f"{'ое' if critical_count == 1 else ('их' if 2 <= critical_count <= 4 else 'их')} "
            f"нарушени{'е' if critical_count == 1 else ('я' if 2 <= critical_count <= 4 else 'й')} — "
            "запускать рекламу в текущем виде нельзя."
        )

    # Extracted text
    extracted_title = "📄 Извлечённый текст креатива"
    if report.source_kind == "pdf":
        pages = report.metadata.get("page_count")
        ocr = report.metadata.get("ocr_pages")
        bits = []
        if pages:
            bits.append(f"страниц: {pages}")
        if ocr:
            bits.append(f"OCR через Vision: стр. {ocr}")
        if bits:
            extracted_title += f" ({'; '.join(bits)})"
    elif report.source_kind == "url" and report.metadata.get("truncated") == "true":
        original = report.metadata.get("original_length", "?")
        limit = report.metadata.get("truncated_to", "?")
        extracted_title += f" (обрезано до {limit} из {original} символов)"
    with st.expander(extracted_title, expanded=False):
        if report.source_kind == "url" and report.metadata.get("truncated") == "true":
            st.warning(
                "Лендинг слишком большой — для скорости проверены только первые "
                f"{report.metadata.get('truncated_to')} символов основного текста. "
                "Если важно проверить footer / FAQ — скопируйте этот раздел отдельно "
                "и прогоните через режим «📝 Текст»."
            )
        st.write(report.extracted_text)

    # ── Main result: table ───────────────────────────────────────────────────
    if report.violations:
        st.subheader("📋 Разбор нарушений")
        st.caption(
            "Сравнение по каждому проблемному фрагменту: что было → как исправить → "
            "ссылка на закон. **Скопируйте отдельные ячейки** мышью или нажмите "
            "кнопку «Скопировать весь отчёт» ниже."
        )
        rows = _format_table_rows(report)
        _render_violations_table(rows)
    else:
        st.info("Нарушений не найдено — таблица пустая.")

    # Compliant rewrite — multi-variant view (mechanism / jtbd / benefit) when
    # the editor ran, with quality-score badges and per-variant recheck status.
    # Falls back to the legacy single rewritten_text panel for old/imported
    # reports where rewrite_variants is empty but rewritten_text is set.
    if report.rewrite_variants:
        st.subheader("✍️ Compliant-переписанные варианты")
        st.caption(
            "Три рамки одной и той же compliant-переписки. Сравните и выберите "
            "ту, что лучше соответствует tone-of-voice бренда."
        )
        _render_rewrite_variant_tabs(report)
    elif report.rewritten_text:
        st.subheader("✍️ Compliant-переписанный вариант (целиком)")
        with st.container(border=True):
            st.write(report.rewritten_text)

    # User feedback history
    if feedback:
        with st.expander(f"💬 История комментариев пользователя ({len(feedback)})", expanded=False):
            for i, fb in enumerate(feedback, 1):
                st.markdown(f"**Комментарий №{i}:**")
                st.markdown(f"> {fb}")

    # ── Action buttons ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Что делать дальше?")

    plain_report = _format_plain_text_report(report, feedback)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**📋 Скопировать**")
        st.caption("Текстовый отчёт для буфера обмена")
        # st.code shows a built-in copy icon
        st.code(plain_report, language=None)
    with c2:
        st.markdown("**✅ Утвердить ответ**")
        st.caption("Сохранить в базу знаний `case_law/approved_reports/`")
        if st.button("Нормальный ответ", key="approve_btn", use_container_width=True):
            path = _save_approved(report, feedback)
            st.session_state.approved_path = str(path.relative_to(path.parents[2]))
            st.success(f"✅ Сохранено: `{st.session_state.approved_path}`")
    with c3:
        st.markdown("**🔄 Доработать**")
        st.caption("Дать комментарий и перезапустить с его учётом")
        if st.button("Доработать с учётом комментария", key="refine_btn", use_container_width=True):
            st.session_state.show_refine = True

    # ── Refine form ──────────────────────────────────────────────────────────
    if st.session_state.show_refine:
        st.divider()
        with st.form("refine_form", clear_on_submit=True):
            st.markdown("**💬 Комментарий для следующей итерации**")
            user_comment = st.text_area(
                "Что нужно учесть / переделать?",
                height=140,
                placeholder=(
                    "Например: «Дисклеймер уже есть в footer лендинга — не нужно "
                    "флагировать его как отсутствующий» или «Замени слово 'безопасен' "
                    "на 'хорошо переносится по данным КИ' вместо удаления»."
                ),
            )
            submitted = st.form_submit_button("Отправить и перезапустить", type="primary")
        if submitted:
            comment = (user_comment or "").strip()
            if not comment:
                st.warning("Комментарий пустой — напишите, что нужно учесть.")
            else:
                st.session_state.feedback_history.append(comment)
                st.session_state.show_refine = False
                # Re-run pipeline with accumulated feedback
                try:
                    report2, elapsed = _run_pipeline(
                        st.session_state.creative,
                        feedback=st.session_state.feedback_history,
                    )
                except Exception as e:  # noqa: BLE001
                    # Rollback the just-added comment so the user can retry without duplicating it.
                    st.session_state.feedback_history.pop()
                    st.error(
                        "❌ Перезапуск с комментарием не удался. "
                        "Попробуйте ещё раз через 30–60 секунд "
                        "(комментарий не сохранён в истории).\n\n"
                        f"**Техническая ошибка:** `{type(e).__name__}: {e}`"
                    )
                else:
                    st.session_state.report = report2
                    st.session_state.last_elapsed = elapsed
                    st.rerun()

    # ── Approved confirmation ────────────────────────────────────────────────
    if st.session_state.approved_path:
        st.divider()
        st.success(
            f"📚 Этот отчёт добавлен в базу знаний: `{st.session_state.approved_path}`.\n\n"
            "Файл содержит исходный текст, все нарушения, compliant-вариант и "
            "историю комментариев пользователя. Используйте «▶ Запустить проверку» "
            "снова для следующего креатива."
        )

    # Raw JSON
    with st.expander("🛠 Сырой JSON-отчёт (для интеграций)"):
        st.code(report.model_dump_json(indent=2), language="json")
        st.download_button(
            "💾 Скачать отчёт (JSON)",
            data=report.model_dump_json(indent=2),
            file_name=f"compliance_report_{int(time.time())}.json",
            mime="application/json",
        )
