"""End-to-end pipeline: Creative → parser → classifier → 6 checkers (parallel) → aggregator → editor."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager

from .agents import (
    aggregator_agent,
    drug_classifier_agent,
    editor_agent,
    parser_agent,
    quality_agent,
)
from .agents._llm import _TIMING
from .agents.rule_checkers import ALL_CHECKERS
from .case_law_matcher import enrich_with_precedents
from .schemas import (
    ComplianceReport,
    Creative,
    ParsedCreative,
    RewriteFrame,
    RewriteScore,
    RewriteVariant,
    Severity,
    Violation,
)

logger = logging.getLogger(__name__)


@contextmanager
def _stage(name: str):
    """Log wall-clock time for a pipeline stage when PHARMA_AD_TIMING is set."""
    started = time.perf_counter()
    try:
        yield
    finally:
        if _TIMING:
            logger.info("stage %s: %.1fs", name, time.perf_counter() - started)


async def _recheck_one_variant(
    *,
    text: str,
    drug_class,  # DrugClass — kept untyped here to avoid circular hints
    user_feedback: list[str] | None,
) -> tuple[Violation, ...]:
    """Run all rule checkers against one rewrite variant and return its violations.

    Per-checker failure (transient LLM error, malformed JSON) is swallowed —
    we treat that checker as "no findings" rather than letting it knock the
    whole recheck out. We deliberately do NOT re-run the aggregator/precedent
    enrichment here: the recheck is a pass/fail gate on the rewrite, not a
    second full report.
    """
    # source_kind is a Literal of the original input kinds — the rewrite is
    # plain text from our editor, so "text" is the honest label.
    fake_parsed = ParsedCreative(source_kind="text", extracted_text=text)
    coros = [c(fake_parsed, drug_class, user_feedback=user_feedback) for c in ALL_CHECKERS]
    results = await asyncio.gather(*coros, return_exceptions=True)
    flat: list[Violation] = []
    for c, res in zip(ALL_CHECKERS, results, strict=True):
        if isinstance(res, BaseException):
            logger.warning(
                "recheck: checker %s failed: %s — treating as no findings",
                c.__qualname__, res,
            )
            continue
        flat.extend(res)
    return tuple(flat)


async def _score_one_variant(
    *, original: str, rewrite: str, frame: RewriteFrame
) -> RewriteScore | None:
    """Best-effort rewrite-quality score. Returns None on any failure."""
    try:
        return await quality_agent.score_rewrite(
            original=original, rewrite=rewrite, frame=frame
        )
    except Exception as exc:  # noqa: BLE001 — scoring is non-essential
        logger.warning(
            "quality_agent: scoring failed for frame=%s: %s — leaving quality_score=None",
            frame, exc,
        )
        return None


async def run_compliance(
    creative: Creative,
    *,
    include_rewrite: bool = True,
    user_feedback: list[str] | None = None,
    rewrite_frames: tuple[RewriteFrame, ...] | None = None,
) -> ComplianceReport:
    """Run the full compliance pipeline.

    `user_feedback` — комментарии от пользователя из предыдущих итераций UI ("Доработать
    с учётом комментария"). Они подмешиваются и в чекеры, и в редактор как
    дополнительный контекст, чтобы каждая следующая итерация уточняла отчёт.

    `rewrite_frames` — какие framing-варианты переписки сгенерировать. None →
    дефолтный набор `editor_agent.DEFAULT_FRAMES` (mechanism / jtbd / benefit).
    """
    with _stage("parser"):
        parsed = await parser_agent.parse(creative)
    with _stage("classifier"):
        drug_class = await drug_classifier_agent.classify(parsed)

    checker_runs = [
        checker(parsed, drug_class, user_feedback=user_feedback) for checker in ALL_CHECKERS
    ]
    with _stage("checkers"):
        # return_exceptions=True so a single checker crashing (transient LLM error,
        # bad JSON, etc.) doesn't abort the whole report — partial results are
        # more useful to the reviewer than nothing. We surface which checkers
        # failed via ComplianceReport.diagnostics for the UI to warn on.
        results = await asyncio.gather(*checker_runs, return_exceptions=True)
    flat: list[Violation] = []
    failed_checkers: list[str] = []
    for checker, res in zip(ALL_CHECKERS, results, strict=True):
        if isinstance(res, BaseException):
            logger.error("checker %s failed: %s", checker.__qualname__, res)
            failed_checkers.append(checker.__module__.rsplit(".", 1)[-1])
        else:
            flat.extend(res)
    if failed_checkers:
        logger.info(
            "checkers stage: %d/%d failed (partial results): %s",
            len(failed_checkers),
            len(ALL_CHECKERS),
            ", ".join(failed_checkers),
        )
    violations = aggregator_agent.aggregate(flat)

    # Attach FAS / approved-report precedents to CRITICAL findings. Best-effort:
    # if the matcher raises (malformed frontmatter in any one file), it logs a
    # warning per failing file and returns the violations unchanged.
    with _stage("precedents"):
        try:
            violations = enrich_with_precedents(
                violations,
                creative_text=parsed.extracted_text,
                severity_floor=Severity.CRITICAL,
            )
        except Exception as exc:  # noqa: BLE001 — precedents are non-essential
            logger.warning("precedent enrichment failed: %s — proceeding without", exc)

    rewrite_variants: tuple[RewriteVariant, ...] = ()
    if include_rewrite and violations:
        frames = rewrite_frames if rewrite_frames is not None else editor_agent.DEFAULT_FRAMES
        with _stage("editor-variants"):
            variant_pairs = await editor_agent.rewrite_variants(
                parsed=parsed,
                drug_class=drug_class,
                violations=violations,
                user_feedback=user_feedback,
                frames=frames,
            )

        # Compliance recheck per variant — parallel, best-effort.
        with _stage("recheck"):
            recheck_results = await asyncio.gather(
                *(
                    _recheck_one_variant(
                        text=text, drug_class=drug_class, user_feedback=user_feedback
                    )
                    for text, _ in variant_pairs
                ),
                return_exceptions=True,
            )

        # Quality scoring per variant — parallel, best-effort.
        with _stage("scoring"):
            score_results = await asyncio.gather(
                *(
                    _score_one_variant(
                        original=parsed.extracted_text, rewrite=text, frame=frame
                    )
                    for text, frame in variant_pairs
                ),
                return_exceptions=True,
            )

        built: list[RewriteVariant] = []
        for (text, frame), recheck_res, score_res in zip(
            variant_pairs, recheck_results, score_results, strict=True
        ):
            if isinstance(recheck_res, BaseException):
                logger.warning(
                    "recheck: variant frame=%s raised — defaulting to compliance_passed=False (no recorded violations)",
                    frame,
                )
                rv: tuple[Violation, ...] = ()
                passed = False
            else:
                rv = recheck_res
                passed = len(rv) == 0
            score: RewriteScore | None
            if isinstance(score_res, BaseException):
                logger.warning(
                    "scoring: variant frame=%s raised — leaving quality_score=None",
                    frame,
                )
                score = None
            else:
                score = score_res
            built.append(
                RewriteVariant(
                    text=text,
                    frame=frame,
                    compliance_passed=passed,
                    recheck_violations=rv,
                    quality_score=score,
                )
            )
        rewrite_variants = tuple(built)

    diagnostics: dict[str, object] = {}
    if failed_checkers:
        diagnostics["failed_checkers"] = failed_checkers

    return ComplianceReport(
        creative_id=parsed.creative_id,
        source_kind=parsed.source_kind,
        drug_class=drug_class,
        extracted_text=parsed.extracted_text,
        violations=violations,
        rewrite_variants=rewrite_variants,
        # rewritten_text auto-populated by the ComplianceReport model_validator
        # from the best-scoring variant; passing None lets that logic run.
        rewritten_text=None,
        metadata=parsed.metadata,
        diagnostics=diagnostics,
    )
