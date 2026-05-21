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
from .schemas import ComplianceReport, Creative

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
        chunked_violations = await asyncio.gather(*checker_runs)
    flat = [v for chunk in chunked_violations for v in chunk]
    violations = aggregator_agent.aggregate(flat)

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

    return ComplianceReport(
        creative_id=parsed.creative_id,
        source_kind=parsed.source_kind,
        drug_class=drug_class,
        extracted_text=parsed.extracted_text,
        violations=violations,
        rewritten_text=rewritten,
        metadata=parsed.metadata,
    )
