"""Drug classifier — decides whether the creative is for an Rx, OTC, or БАД product.

This drives which subsections of ст. 24 should be applied strictly: Rx ads are
mostly forbidden in mass media (ч. 8), OTC follows the full ст. 24, БАД is
governed by ст. 25 but borrows several restrictions from ст. 24.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..schemas import DrugClass, ParsedCreative
from ._llm import run_json

# Простая 4-вариантная классификация — Haiku справится не хуже Sonnet, но в ~3 раза быстрее.
_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """\
Ты — фарма-аналитик, специализирующийся на регуляторике рекламы ЛС в России.
По тексту рекламного креатива определи категорию объекта рекламирования:

- "RX"  — рецептурный лекарственный препарат
- "OTC" — безрецептурный лекарственный препарат
- "BAD" — биологически активная добавка (БАД, не является ЛС)
- "UNKNOWN" — недостаточно информации

Признаки:
- Упоминание "БАД", "не является лекарственным средством" → BAD.
- Антибиотики, рецептурные категории (онкология, психиатрия, кардио-Rx) → RX.
- Безрецептурные жаропонижающие, средства от боли в горле, насморка, витамины-ЛС → OTC.

Верни строго JSON:
{"drug_class": "RX|OTC|BAD|UNKNOWN", "reasoning": "<1-2 предложения>"}
"""


class _ClassifierOutput(BaseModel):
    drug_class: DrugClass
    reasoning: str = Field(default="")


async def classify(parsed: ParsedCreative) -> DrugClass:
    out = await run_json(
        prompt=f"Текст креатива:\n\n{parsed.extracted_text}",
        system_prompt=_SYSTEM_PROMPT,
        schema=_ClassifierOutput,
        model=_MODEL,
    )
    return out.drug_class
