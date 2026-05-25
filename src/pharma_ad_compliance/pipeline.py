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
)
from .agents._llm import _TIMING
from .agents.rule_checkers import ALL_CHECKERS
from .case_law_matcher import enrich_with_precedents
from .schemas import ComplianceReport, Creative, Severity, Violation

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


async def run_compliance(
    creative: Creative,
    *,
    include_rewrite: bool = True,
    user_feedback: list[str] | None = None,
) -> ComplianceReport:
    """Run the full compliance pipeline.

    `user_feedback` — комментарии от пользователя из предыдущих итераций UI ("Доработать
    с учётом комментария"). Они подмешиваются и в чекеры, и в редактор как
    дополнительный контекст, чтобы каждая следующая итерация уточняла отчёт.
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

    with _stage("editor"):
        rewritten = (
            await editor_agent.rewrite(
                parsed=parsed,
                drug_class=drug_class,
                violations=violations,
                user_feedback=user_feedback,
            )
            if include_rewrite
            else None
        )

    diagnostics: dict[str, object] = {}
    if failed_checkers:
        diagnostics["failed_checkers"] = failed_checkers

    return ComplianceReport(
        creative_id=parsed.creative_id,
        source_kind=parsed.source_kind,
        drug_class=drug_class,
        extracted_text=parsed.extracted_text,
        violations=violations,
        rewritten_text=rewritten,
        metadata=parsed.metadata,
        diagnostics=diagnostics,
    )
