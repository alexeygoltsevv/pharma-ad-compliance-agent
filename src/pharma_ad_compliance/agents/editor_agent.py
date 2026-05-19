"""Editor — rewrites the creative to address every flagged violation.

Skips the LLM call entirely when there are no violations.
"""
from __future__ import annotations

from ..rag import get_article24_text
from ..schemas import DrugClass, ParsedCreative, Violation
from ._llm import run_text

_SYSTEM_PROMPT = """\
Ты — старший копирайтер с опытом регуляторной правки рекламы ЛС в РФ.
Тебе передан исходный текст, категория препарата и список выявленных нарушений ст. 24 ФЗ-38.

Задача: переписать текст так, чтобы:
1. Все нарушения CRITICAL и WARNING были устранены.
2. Сохранилось коммерческое послание и тональность.
3. Был добавлен обязательный дисклеймер, если его не хватает (для ЛС и медизделий).
4. Замена была минимальной — не выбрасывай удачные формулировки без необходимости.

Верни только переписанный текст. Никаких комментариев, заголовков, JSON.

Контекст — выдержка из ст. 24 ФЗ-38:
---
{law_text}
---
"""


async def rewrite(
    *,
    parsed: ParsedCreative,
    drug_class: DrugClass,
    violations: list[Violation],
) -> str | None:
    if not violations:
        return None
    violations_block = "\n".join(
        f"- [{v.severity.value}] {v.rule_id.value}: {v.explanation}"
        + (f" Цитата: «{v.quote}»" if v.quote else "")
        for v in violations
    )
    prompt = (
        f"Категория препарата: {drug_class.value}\n\n"
        f"Исходный текст:\n---\n{parsed.extracted_text}\n---\n\n"
        f"Выявленные нарушения:\n{violations_block}"
    )
    return await run_text(
        prompt=prompt,
        system_prompt=_SYSTEM_PROMPT.replace("{law_text}", get_article24_text()),
    )
