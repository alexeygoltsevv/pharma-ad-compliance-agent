from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .creative import DrugClass


class Severity(str, Enum):
    """Priority of a finding for the brand-side reviewer."""

    CRITICAL = "CRITICAL"        # Almost certainly a FAS violation — block before launch
    WARNING = "WARNING"          # Risky / ambiguous wording — needs legal review
    RECOMMENDATION = "RECOMMENDATION"  # Stylistic / borderline — author's discretion

    @property
    def rank(self) -> int:
        return {"CRITICAL": 0, "WARNING": 1, "RECOMMENDATION": 2}[self.value]


class RuleId(str, Enum):
    """Subsections of FZ-38 art. 24 we explicitly check.

    NOTE on naming: the `Pn` suffix is an internal checker label, NOT the law's
    пункт number — e.g. ART24_P3_NO_SIDE_EFFECTS maps to ч. 1 п. 8. The
    authoritative article reference per rule is in the inline comment below (and
    in `app.py:RULE_ARTICLE_REFS`). See `prompts/fz38_article24.md` for the text.
    """

    ART24_P1_MINORS = "ART24_P1_MINORS"                          # ч. 1 п. 1 — «обращаться к несовершеннолетним»
    ART24_P2_SPECIFIC_CASES = "ART24_P2_SPECIFIC_CASES"          # ч. 1 п. 2 — «содержать ссылки на конкретные случаи излечения»
    ART24_P3_NO_SIDE_EFFECTS = "ART24_P3_NO_SIDE_EFFECTS"        # ч. 1 п. 8 — «гарантировать положительное действие, безопасность, эффективность и отсутствие побочных действий» (имя `P3` — внутренний legacy-лейбл, не номер пункта закона)
    ART24_P4_DOCTOR_RECOMMENDATION = "ART24_P4_DOCTOR_RECOMMENDATION"  # ложная экспертная рекомендация (врач/фармацевт/актёр в халате) — собирательный чекер на стыке ч. 1 п. 4 и ст. 5 ФЗ-38; буквальный п. 4 («ссылка на факт исследований при госрегистрации») вынесен в ART24_OTHER
    ART24_P5_MANDATORY_DISCLAIMER = "ART24_P5_MANDATORY_DISCLAIMER"    # ч. 7 — обязательное предупреждение «Имеются противопоказания, проконсультируйтесь со специалистом»
    ART24_OTHER = "ART24_OTHER"                                  # подпункты ч. 1, не покрытые отдельными чекерами: п. 3 (благодарность), п. 4 (ссылка на исследования при регистрации), п. 5 (навязывание диагноза), п. 6 (необходимость у здорового), п. 7 (ненужность врача), п. 9 (БАД ↔ ЛС), п. 10 (безопасность через «естественное происхождение»)


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: RuleId
    severity: Severity
    quote: str | None = Field(
        default=None,
        description="Exact span of the creative that triggered the rule. None for absence-based rules (e.g. missing disclaimer).",
    )
    explanation: str = Field(..., description="Why this is a violation, in Russian, referencing the article.")
    suggested_fix: str | None = Field(
        default=None,
        description="Concrete rewrite or removal suggestion.",
    )


class ComplianceReport(BaseModel):
    model_config = ConfigDict()

    creative_id: str | None = None
    source_kind: str
    drug_class: DrugClass
    extracted_text: str
    violations: list[Violation] = Field(default_factory=list)
    rewritten_text: str | None = None
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Carried over from the parsed creative (page_count, ocr_pages, truncated, ...).",
    )
    # Loosely-typed dict for pipeline-level diagnostics (e.g. failed_checkers).
    # Separate from `metadata` (which is str→str, carried from the parser) so we
    # can store lists/structured info without breaking the parser's contract.
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_compliant(self) -> bool:
        return not any(v.severity == Severity.CRITICAL for v in self.violations)

    def by_severity(self, severity: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity == severity]
