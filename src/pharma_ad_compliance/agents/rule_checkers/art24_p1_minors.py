"""ст. 24 ч. 1 п. 1 — реклама ЛС не должна обращаться к несовершеннолетним."""
from __future__ import annotations

from ...schemas import DrugClass, ParsedCreative, RuleId, Violation
from ._base import check_rule

RULE_ID = RuleId.ART24_P1_MINORS

FOCUS = """\
Признаки нарушения:
- Прямое обращение к детям/подросткам ("ребята", "школьники", "детям", "подросткам").
- Использование детских образов как целевой аудитории сообщения.
- Призывы родителям "купите ребёнку", если в самом сообщении адресат — ребёнок.
- Игровая лексика, апеллирующая к детской аудитории.
Не путать с указанием возрастной группы применения препарата (это допустимо, если
оформлено как мед-информация, а не как обращение).
"""


async def check(parsed: ParsedCreative, drug_class: DrugClass) -> list[Violation]:
    return await check_rule(
        parsed=parsed,
        drug_class=drug_class,
        rule_id=RULE_ID,
        focus_instruction=FOCUS,
    )
