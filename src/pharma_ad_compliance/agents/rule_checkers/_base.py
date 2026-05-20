"""Shared helper for single-rule checkers.

Each rule_checker module defines its own `RULE_ID` and `FOCUS` and exports
`async def check(parsed, drug_class) -> list[Violation]`. The orchestrator
gathers them in parallel via `asyncio.gather`.

We parse the model's JSON manually (rather than via `run_json`) so we can
**force `rule_id`** to the checker's own value before Pydantic validation.
Models occasionally invent sub-rule ids like `ART24_OTHER_P6`, which would
otherwise crash the enum validator.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from ...rag import get_article24_text
from ...schemas import DrugClass, ParsedCreative, RuleId, Severity, Violation
from .._llm import LLMOutputError, _collect_text, _extract_json

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

Верни строго JSON одного формата (поле `rule_id` НЕ заполняй — оно будет проставлено автоматически):
{
  "violations": [
    {
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
    user_feedback: list[str] | None = None,
) -> list[Violation]:
    user_prompt = (
        f"Категория препарата: {drug_class.value}\n\n"
        f"Текст креатива:\n---\n{parsed.extracted_text}\n---"
    )
    if user_feedback:
        feedback_block = "\n".join(f"{i + 1}. {fb}" for i, fb in enumerate(user_feedback))
        user_prompt += (
            "\n\nДополнительные указания от пользователя из предыдущих итераций "
            "(обязательно учти):\n" + feedback_block
        )
    raw = await _collect_text(
        prompt=user_prompt,
        system_prompt=_build_system_prompt(rule_id, focus_instruction),
    )
    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"{rule_id.value}: invalid JSON from model:\n{raw[:500]}") from e

    raw_violations = data.get("violations", []) if isinstance(data, dict) else []
    violations: list[Violation] = []
    for item in raw_violations:
        if not isinstance(item, dict):
            continue
        item["rule_id"] = rule_id.value  # force consistency before validation
        try:
            violations.append(Violation.model_validate(item))
        except ValidationError as e:
            raise LLMOutputError(
                f"{rule_id.value}: violation payload did not match schema: {e}\nItem: {item}"
            ) from e
    return violations


__all__ = ["check_rule", "Severity", "RuleId"]
