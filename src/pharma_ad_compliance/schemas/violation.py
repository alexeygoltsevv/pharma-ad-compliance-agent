from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

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

    Mapping references the consolidated text of art. 24 ч. 1 ФЗ-38 «О рекламе».
    See `prompts/fz38_article24.md` for the full text used by the agents.
    """

    ART24_P1_MINORS = "ART24_P1_MINORS"                          # п. 1 — обращение к несовершеннолетним
    ART24_P2_SPECIFIC_CASES = "ART24_P2_SPECIFIC_CASES"          # п. 2 — ссылки на конкретные случаи излечения
    ART24_P3_NO_SIDE_EFFECTS = "ART24_P3_NO_SIDE_EFFECTS"        # п. 6 — утверждение об отсутствии побочки
    ART24_P4_DOCTOR_RECOMMENDATION = "ART24_P4_DOCTOR_RECOMMENDATION"  # п. 4 — представление в виде рекомендации врача/фармацевта
    ART24_P5_MANDATORY_DISCLAIMER = "ART24_P5_MANDATORY_DISCLAIMER"    # ч. 7 — обязательное предупреждение
    ART24_OTHER = "ART24_OTHER"                                  # прочие нарушения ст. 24


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
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_compliant(self) -> bool:
        return not any(v.severity == Severity.CRITICAL for v in self.violations)

    def by_severity(self, severity: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity == severity]
