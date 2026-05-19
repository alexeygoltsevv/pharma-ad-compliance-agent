"""ст. 24 ч. 1 п. 2 — запрет ссылок на конкретные случаи излечения."""
from __future__ import annotations

from ...schemas import DrugClass, ParsedCreative, RuleId, Violation
from ._base import check_rule

RULE_ID = RuleId.ART24_P2_SPECIFIC_CASES

FOCUS = """\
Признаки нарушения:
- Истории конкретных пациентов с именами/фамилиями/диагнозами ("Мария, 34 года, вылечила...").
- Указание сроков излечения как факта ("за 5 дней", "через неделю").
- Отзывы со ссылкой на конкретный диагноз и результат.
- Выражение благодарности от лица потребителя за результат лечения (это ч. 1 п. 3,
  но часто комбинируется — отметить тоже здесь как WARNING).
Допустимо: общие фармакологические свойства без привязки к конкретному случаю.
"""


async def check(parsed: ParsedCreative, drug_class: DrugClass) -> list[Violation]:
    return await check_rule(
        parsed=parsed,
        drug_class=drug_class,
        rule_id=RULE_ID,
        focus_instruction=FOCUS,
    )
