from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .creative import DrugClass

# Three editorial framings the multi-variant rewriter targets. Each frame keeps
# the same compliance constraints but rearranges the message so reviewers can
# pick the angle that best fits their brand voice.
RewriteFrame = Literal["mechanism", "jtbd", "benefit"]

# Required keys for the 5-criterion rewrite-quality rubric (0-20 each).
_REWRITE_SCORE_KEYS: tuple[str, ...] = (
    "concreteness",
    "mechanism",
    "jtbd",
    "voice_and_structure",
    "register",
)


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


class CaseRef(BaseModel):
    """Reference to a past FAS ruling or in-house approved report.

    Surfaced for CRITICAL violations as "⚖️ Похожие дела" — gives the legal
    reviewer a precedent and the brand-manager a trust-signal ("кто-то уже на
    этом горел"). Built by `case_law_matcher` from YAML frontmatter in
    `case_law/fas_decisions/*.md` and from the `findings[].rule_id` of each
    `case_law/approved_reports/*.json`.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(..., description="kebab-case file stem (no .md/.json).")
    date: str = Field(..., description="ISO date YYYY-MM-DD when the case was decided / report approved.")
    party: str = Field(..., description="Defendant / advertiser brand or company.")
    fine_rub: int | None = Field(
        default=None,
        description="Fine in rubles if published; None when unknown.",
    )
    source: Literal["fas", "approved"]
    url: str | None = None
    short_quote: str | None = Field(
        default=None,
        description="1-line summary of the violation text in the precedent case.",
    )


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
    intent_hypothesis: str | None = Field(
        default=None,
        description=(
            "Краткая (1 предложение) гипотеза на русском о том, что бренд "
            "пытался коммуницировать этой формулировкой — JTBD/коммерческое "
            "намерение, не оправдание нарушения. Помогает editor'у сохранить "
            "посыл при переформулировке, а UI — показать tooltip."
        ),
    )
    precedents: tuple[CaseRef, ...] = Field(
        default_factory=tuple,
        description=(
            "Случаи из case_law/ с тем же rule_id (FAS rulings + одобренные "
            "внутренние отчёты). Заполняется case_law_matcher после aggregator "
            "ТОЛЬКО для CRITICAL-нарушений. Хранится как tuple (а не list), "
            "чтобы сохранить hashability frozen Violation — aggregator складывает "
            "Violation в set/dict при дедупликации."
        ),
    )


class RewriteScore(BaseModel):
    """5-criterion 0-100 quality score for a single rewrite variant.

    Each criterion is graded 0-20 by a separate Haiku pass; `total` is the
    arithmetic sum and is validated against `breakdown.values()` so the model
    cannot return an inconsistent rollup.
    """

    model_config = ConfigDict(frozen=True)

    total: int = Field(..., ge=0, le=100)
    breakdown: dict[str, int] = Field(
        ...,
        description=(
            "Per-criterion scores keyed by: "
            "concreteness, mechanism, jtbd, voice_and_structure, register. "
            "Each value is in 0..20."
        ),
    )
    notes: str = Field(
        default="",
        description="Free-form 1-2 sentence justification in Russian.",
    )

    @model_validator(mode="after")
    def _check_breakdown(self) -> RewriteScore:
        keys = set(self.breakdown.keys())
        expected = set(_REWRITE_SCORE_KEYS)
        if keys != expected:
            missing = expected - keys
            extra = keys - expected
            raise ValueError(
                f"breakdown must have exactly keys {sorted(expected)}; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        for name, value in self.breakdown.items():
            if not isinstance(value, int) or not (0 <= value <= 20):
                raise ValueError(
                    f"breakdown[{name!r}] must be int in 0..20, got {value!r}"
                )
        expected_total = sum(self.breakdown.values())
        if self.total != expected_total:
            raise ValueError(
                f"total={self.total} must equal sum(breakdown.values())={expected_total}"
            )
        return self


class RewriteVariant(BaseModel):
    """One of the multi-frame compliant rewrites returned by the editor.

    `recheck_violations` is a tuple (not a list) so the model stays hashable —
    consistent with `Violation.precedents` — and `quality_score` is optional
    because scoring is best-effort (a failed Haiku call must not crash the
    whole report).
    """

    model_config = ConfigDict(frozen=True)

    text: str
    frame: RewriteFrame
    compliance_passed: bool
    recheck_violations: tuple[Violation, ...] = ()
    quality_score: RewriteScore | None = None


class ComplianceReport(BaseModel):
    model_config = ConfigDict()

    creative_id: str | None = None
    source_kind: str
    drug_class: DrugClass
    extracted_text: str
    violations: list[Violation] = Field(default_factory=list)
    # DEPRECATED — kept for backwards-compat with existing CLI / JSON consumers.
    # Auto-populated from the best-scoring `rewrite_variants` entry by the
    # `_populate_best_rewrite` model_validator below; new code should read
    # `rewrite_variants` directly so it can show the user all three framings.
    rewritten_text: str | None = None
    rewrite_variants: tuple[RewriteVariant, ...] = Field(
        default_factory=tuple,
        description=(
            "Multi-frame compliant rewrites (mechanism / jtbd / benefit). "
            "Empty when the editor was skipped (--no-rewrite) or when there "
            "were no violations to fix."
        ),
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Carried over from the parsed creative (page_count, ocr_pages, truncated, ...).",
    )
    # Loosely-typed dict for pipeline-level diagnostics (e.g. failed_checkers).
    # Separate from `metadata` (which is str→str, carried from the parser) so we
    # can store lists/structured info without breaking the parser's contract.
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _populate_best_rewrite(self) -> ComplianceReport:
        """Backfill `rewritten_text` from the best-scoring variant.

        Priority for picking "best":
        1. variants that passed compliance recheck over those that didn't;
        2. higher `quality_score.total` over lower (unscored treated as 0);
        3. earlier frame in the editor's natural order (mechanism, jtbd, benefit).

        Only fires when `rewritten_text` is None and there is at least one
        variant — never overwrites an explicitly-set value.
        """
        if self.rewritten_text is None and self.rewrite_variants:
            best = max(
                enumerate(self.rewrite_variants),
                key=lambda iv: (
                    1 if iv[1].compliance_passed else 0,
                    iv[1].quality_score.total if iv[1].quality_score else -1,
                    -iv[0],  # tie-break: earlier index wins
                ),
            )[1]
            # ComplianceReport is not frozen and validate_assignment is off, so
            # this neither triggers re-validation nor recursion into us.
            self.rewritten_text = best.text
        return self

    @property
    def is_compliant(self) -> bool:
        return not any(v.severity == Severity.CRITICAL for v in self.violations)

    def by_severity(self, severity: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity == severity]
