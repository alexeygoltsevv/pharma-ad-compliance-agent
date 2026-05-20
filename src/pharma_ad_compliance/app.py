"""Streamlit demo. Запуск: `streamlit run src/pharma_ad_compliance/app.py`."""
from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from pharma_ad_compliance.pipeline import run_compliance
from pharma_ad_compliance.schemas import (
    ImageCreative,
    PdfCreative,
    Severity,
    TextCreative,
    UrlCreative,
)

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

DRUG_CLASS_LABELS: dict[str, str] = {
    "RX": "Рецептурный (Rx)",
    "OTC": "Безрецептурный (OTC)",
    "BAD": "БАД",
    "UNKNOWN": "Не определена",
}

st.set_page_config(
    page_title="AI Агент: Помощник по комплаенсу",
    page_icon="💊",
    layout="wide",
)

# ── Sidebar ──────────────────────────────────────────────────────────────────
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

# ── Header ───────────────────────────────────────────────────────────────────
st.title("💊 AI Агент: Помощник по комплаенсу")
st.caption(
    "Инструмент для **медсоветников и бренд-менеджеров**: проверяет рекламу ЛС на "
    "соответствие ст. 24 ФЗ-38 за минуту вместо 3–7 дней внешней юр-экспертизы."
)

# ── Input ────────────────────────────────────────────────────────────────────
mode = st.radio(
    "Тип входных данных",
    ["📝 Текст", "🖼 Баннер", "🔗 Ссылка", "📄 PDF (статья / макет / рассылка)"],
    horizontal=True,
    label_visibility="visible",
)

creative = None
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
    value=True,
    help="Если снять галочку — пропустит этап редактора и завершится на ~10 секунд быстрее.",
)

st.info(
    "⏱ Анализ занимает **до 1 минуты** — пайплайн делает 8 параллельных LLM-вызовов "
    "(классификатор, 6 чекеров правил и редактор)."
)

# ── Run ──────────────────────────────────────────────────────────────────────
if st.button(
    "▶ Запустить проверку соответствия",
    type="primary",
    disabled=creative is None,
    use_container_width=True,
):
    started = time.monotonic()
    is_pdf = isinstance(creative, PdfCreative)
    is_image = isinstance(creative, ImageCreative)
    with st.status("Запускаю пайплайн…", expanded=True) as status:
        if is_pdf:
            st.write("📄 Извлекаю текст из PDF (текстовый слой + Vision для страниц-картинок)…")
        elif is_image:
            st.write("🖼 Распознаю текст с баннера через Claude Vision…")
        else:
            st.write("📥 Извлекаю текст из креатива…")
        st.write("🧬 Классифицирую препарат (Rx / OTC / БАД)…")
        st.write("🔍 Запускаю 6 чекеров правил параллельно…")
        if include_rewrite:
            st.write("✍️ Готовлю compliant-переписку…")
        report = asyncio.run(run_compliance(creative, include_rewrite=include_rewrite))
        elapsed = time.monotonic() - started
        status.update(label=f"Готово за {elapsed:.1f} с", state="complete", expanded=False)

    # ── Summary metrics ──────────────────────────────────────────────────────
    cols = st.columns(4)
    cols[0].metric("Категория препарата", DRUG_CLASS_LABELS.get(report.drug_class.value, report.drug_class.value))
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

    # ── Extracted text (collapsible) ─────────────────────────────────────────
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

    # ── Findings ─────────────────────────────────────────────────────────────
    if report.violations:
        st.subheader("Найденные нарушения")
        for sev in (Severity.CRITICAL, Severity.WARNING, Severity.RECOMMENDATION):
            chunk = report.by_severity(sev)
            if not chunk:
                continue
            label, sublabel = SEVERITY_LABELS[sev]
            with st.expander(
                f"{label} — {len(chunk)} шт.",
                expanded=sev is Severity.CRITICAL,
            ):
                st.caption(sublabel)
                for v in chunk:
                    with st.container(border=True):
                        rule_name = RULE_LABELS.get(v.rule_id.value, v.rule_id.value)
                        article_ref = RULE_ARTICLE_REFS.get(v.rule_id.value, "")
                        st.markdown(f"**{rule_name}**  \n_{article_ref}_")
                        if v.quote:
                            st.markdown(
                                f"<div style='border-left:3px solid #888;padding-left:12px;"
                                f"margin:8px 0;color:#ccc;font-style:italic'>"
                                f"«{v.quote}»</div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.caption("_Нарушение по отсутствию (нет требуемой фразы)_")
                        st.markdown(f"**Почему:** {v.explanation}")
                        if v.suggested_fix:
                            st.markdown(f"**Как исправить:** {v.suggested_fix}")

    # ── Rewritten text ───────────────────────────────────────────────────────
    if report.rewritten_text:
        st.subheader("✍️ Compliant-переписанный вариант")
        st.markdown(
            "Текст ниже сгенерирован редактор-агентом с учётом всех найденных нарушений. "
            "Это **черновик** — обязательно покажите юристу перед запуском."
        )
        with st.container(border=True):
            st.write(report.rewritten_text)

    # ── Raw JSON ─────────────────────────────────────────────────────────────
    with st.expander("🛠 Сырой JSON-отчёт (для интеграций)"):
        st.code(report.model_dump_json(indent=2), language="json")
        st.download_button(
            "💾 Скачать отчёт (JSON)",
            data=report.model_dump_json(indent=2),
            file_name=f"compliance_report_{int(time.time())}.json",
            mime="application/json",
        )
