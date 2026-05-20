"""Streamlit demo. Запуск: `streamlit run src/pharma_ad_compliance/app.py`."""
from __future__ import annotations

import asyncio
import hashlib
import json
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
    Severity,
    TextCreative,
    UrlCreative,
)

# Папка-база знаний для одобренных пользователем отчётов.
_APPROVED_REPORTS_DIR = Path(__file__).resolve().parents[2].parent / "case_law" / "approved_reports"
# Если запущено из dev-checkout, путь выше указывает на репо. Иначе создаём в cwd/case_law/.
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
    defaults = {
        "creative": None,
        "report": None,
        "feedback_history": [],
        "show_refine": False,
        "include_rewrite": True,
        "last_elapsed": None,
        "approved_path": None,
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
    "соответствие ст. 24 ФЗ-38 за минуту вместо 3–7 дней внешней юр-экспертизы."
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
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(uploaded.read())
        tmp.close()
        creative = ImageCreative(image_path=Path(tmp.name))
        st.image(tmp.name, caption=uploaded.name, width=360)
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
        tmp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_pdf.write(uploaded_pdf.read())
        tmp_pdf.close()
        creative = PdfCreative(pdf_path=Path(tmp_pdf.name))
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
run_clicked = st.button(
    "▶ Запустить проверку соответствия",
    type="primary",
    disabled=creative is None,
    use_container_width=True,
)

if run_clicked and creative is not None:
    _reset_run_state()
    st.session_state.creative = creative
    try:
        report, elapsed = _run_pipeline(creative, feedback=[])
    except Exception as e:  # noqa: BLE001 — surface any pipeline failure to the user
        st.error(
            "❌ Не удалось получить ответ от Claude. Возможные причины: "
            "временный сбой подписки, исчерпан лимит запросов или CLI потерял "
            "авторизацию. Запустите проверку ещё раз через 30–60 секунд.\n\n"
            f"**Техническая ошибка:** `{type(e).__name__}: {e}`"
        )
    else:
        st.session_state.report = report
        st.session_state.last_elapsed = elapsed


# ─── Result rendering ────────────────────────────────────────────────────────
def _format_table_rows(report: ComplianceReport) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for v in report.violations:
        comment_parts = [
            f"{SEVERITY_BADGE[v.severity]} {RULE_LABELS.get(v.rule_id.value, v.rule_id.value)}",
            f"📖 {RULE_ARTICLE_REFS.get(v.rule_id.value, '')} ФЗ-38",
            v.explanation,
        ]
        rows.append(
            {
                "Исходный текст": v.quote or "(отсутствует в креативе)",
                "Compliant вариант": v.suggested_fix or "—",
                "Комментарий (источник)": "  \n".join(part for part in comment_parts if part),
            }
        )
    return rows


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


# ─── If we have a report, show it ────────────────────────────────────────────
if st.session_state.report is not None:
    report = st.session_state.report
    feedback = st.session_state.feedback_history

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
    with st.expander(extracted_title, expanded=False):
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
        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Исходный текст": st.column_config.TextColumn(width="medium"),
                "Compliant вариант": st.column_config.TextColumn(width="medium"),
                "Комментарий (источник)": st.column_config.TextColumn(width="large"),
            },
        )
    else:
        st.info("Нарушений не найдено — таблица пустая.")

    # Compliant rewrite (full text)
    if report.rewritten_text:
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
