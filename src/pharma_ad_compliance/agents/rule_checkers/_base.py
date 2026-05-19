"""Shared helper for single-rule checkers.

Each rule_checker module defines its own `RULE_ID`, `SYSTEM_PROMPT_SUFFIX` and
exports `async def check(parsed, drug_class) -> list[Violation]`. The orchestrator
gathers them in parallel via `asyncio.gather`.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ...rag import get_article24_text
from ...schemas import DrugClass, ParsedCreative, RuleId, Severity, Violation
from .._llm import run_json

_BASE_SYSTEM_PROMPT = """\
Ты — комплаенс-аналитик ФАС, проверяющий рекламу лекарственных средств на
соответствие ст. 24 ФЗ-38 «О рекламе».

Тебе передан **текст** рекламного креатива и категория препарата (RX/OTC/BAD/UNKNOWN).
Твоя задача — проверить ОДИН конкретный подпункт статьи (см. ниже).

Правила работы:
- Цитируй проблемные фрагменты дословно, в поле `quote`.
- Объяснение пиши коротко (1-2 предложения), со ссылкой на пункт статьи.
- `suggested_fix` — конкретная переформулировка или указание удалить.
- Severity:
  - CRITICAL — почти наверняка нарушение, ФАС оштрафует.
  - WARNING — рискованная формулировка, требует юридической проверки.
  - RECOMMENDATION — стилистическая правка, на усмотрение автора.
- Если нарушений по данному подпункту НЕТ — верни пустой список violations.

Верни строго JSON одного формата:
{
  "violations": [
    {
      "rule_id": "<RuleId>",
      "severity": "CRITICAL|WARNING|RECOMMENDATION",
      "quote": "<точная цитата или null>",
      "explanation": "<почему это нарушение>",
      "suggested_fix": "<как исправить или null>"
    }
  ]
}

Контекст — выдержка из ст. 24 ФЗ-38:
---
{law_text}
---
"""


class _CheckerOutput(BaseModel):
    violations: list[Violation] = Field(default_factory=list)


def _build_system_prompt(rule_id: RuleId, focus_instruction: str) -> str:
    law_text = get_article24_text()
    base = _BASE_SYSTEM_PROMPT.replace("{law_text}", law_text)
    return (
        f"{base}\n\n"
        f"**ТВОЙ ФОКУС:** проверяй ТОЛЬКО `{rule_id.value}`.\n\n"
        f"{focus_instruction}\n"
    )


async def check_rule(
    *,
    parsed: ParsedCreative,
    drug_class: DrugClass,
    rule_id: RuleId,
    focus_instruction: str,
) -> list[Violation]:
    user_prompt = (
        f"Категория препарата: {drug_class.value}\n\n"
        f"Текст креатива:\n---\n{parsed.extracted_text}\n---"
    )
    output = await run_json(
        prompt=user_prompt,
        system_prompt=_build_system_prompt(rule_id, focus_instruction),
        schema=_CheckerOutput,
    )
    # Force rule_id consistency — the model occasionally substitutes ART24_OTHER.
    return [
        v.model_copy(update={"rule_id": rule_id})
        if v.rule_id != rule_id and rule_id is not RuleId.ART24_OTHER
        else v
        for v in output.violations
    ]


__all__ = ["check_rule", "Severity", "RuleId"]
