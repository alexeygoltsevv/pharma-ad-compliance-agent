"""ст. 24 ч. 1 п. 8 — запрет гарантий безопасности и отсутствия побочки.

(Внутренний RuleId сохранён как ART24_P3_NO_SIDE_EFFECTS по историческим причинам —
это самая частая категория нарушений, ловится как «отсутствие побочных».)
"""
from __future__ import annotations

from ...schemas import DrugClass, ParsedCreative, RuleId, Violation
from ._base import check_rule

RULE_ID = RuleId.ART24_P3_NO_SIDE_EFFECTS

FOCUS = """\
Признаки нарушения (ст. 24 ч. 1 п. 8):
- "Не имеет побочных эффектов", "полностью безопасен", "без побочек".
- "Гарантирует выздоровление", "100% эффективность".
- "Безопасен для всех", "подходит каждому без исключений".
- "Естественное происхождение → безопасность" (это отдельный п. 10, но логика та же).
- Эвфемизмы той же сути: "не вызывает реакций", "переносится всеми".
Допустимо: ссылки на профиль безопасности из инструкции БЕЗ гарантий
("хорошая переносимость по данным клинических исследований" — на грани, ставить WARNING).
"""


async def check(parsed: ParsedCreative, drug_class: DrugClass) -> list[Violation]:
    return await check_rule(
        parsed=parsed,
        drug_class=drug_class,
        rule_id=RULE_ID,
        focus_instruction=FOCUS,
    )
