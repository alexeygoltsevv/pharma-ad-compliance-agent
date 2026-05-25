"""Smoke-test that every FAS decision Markdown file has a regression row.

LLM-free. Walks `case_law/fas_decisions/*.md` and asserts that an `id` of the
form `fas-<date>-<slug>` (matching the filename) appears in at least one row of
`case_law/regression_dataset/*.jsonl`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FAS_DECISIONS_DIR = REPO_ROOT / "case_law" / "fas_decisions"
REGRESSION_DIR = REPO_ROOT / "case_law" / "regression_dataset"


def _load_regression_ids() -> set[str]:
    ids: set[str] = set()
    for jl in REGRESSION_DIR.glob("*.jsonl"):
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rid = row.get("id")
            if rid:
                ids.add(rid)
    return ids


def _fas_case_ids() -> list[str]:
    """Every fas_decisions/*.md → 'fas-<filename-without-extension>'."""
    return sorted(
        f"fas-{p.stem}"
        for p in FAS_DECISIONS_DIR.glob("*.md")
        if not p.name.startswith(".")
    )


@pytest.mark.parametrize("case_id", _fas_case_ids())
def test_every_fas_decision_has_regression_row(case_id: str) -> None:
    ids = _load_regression_ids()
    assert case_id in ids, (
        f"{case_id} has a fas_decisions/*.md file but no regression row in "
        f"case_law/regression_dataset/*.jsonl"
    )


def test_at_least_one_fas_case_exists() -> None:
    # Guard against the parametrize silently skipping if the dir is empty.
    assert _fas_case_ids(), "no fas_decisions/*.md files found"
