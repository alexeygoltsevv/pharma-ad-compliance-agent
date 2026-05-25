"""LLM-free schema validation for regression_dataset/*.jsonl."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pharma_ad_compliance.schemas import RuleId

REPO_ROOT = Path(__file__).resolve().parents[2]
REGRESSION_DIR = REPO_ROOT / "case_law" / "regression_dataset"

VALID_RULE_IDS = {r.value for r in RuleId}


def _all_jsonl_files() -> list[Path]:
    return sorted(REGRESSION_DIR.glob("*.jsonl"))


def test_at_least_one_jsonl_file_exists() -> None:
    assert _all_jsonl_files(), "no *.jsonl files in case_law/regression_dataset/"


@pytest.mark.parametrize("path", _all_jsonl_files(), ids=lambda p: p.name)
def test_jsonl_lines_parse(path: Path) -> None:
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"{path.name}:{lineno} invalid JSON: {e}")


def _iter_rows():
    for path in _all_jsonl_files():
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            yield path, lineno, json.loads(line)


def test_each_row_has_required_keys() -> None:
    for path, lineno, row in _iter_rows():
        assert "id" in row, f"{path.name}:{lineno} missing `id`"
        # The CLI's eval_cmd reads `input`; some legacy rows use `creative_text`.
        assert (
            "input" in row or "creative_text" in row
        ), f"{path.name}:{lineno} needs `input` or `creative_text`"
        assert (
            "expected_rule_ids" in row
        ), f"{path.name}:{lineno} missing `expected_rule_ids`"
        assert isinstance(
            row["expected_rule_ids"], list
        ), f"{path.name}:{lineno} expected_rule_ids must be a list"


def test_expected_rule_ids_are_valid_enum_values() -> None:
    for path, lineno, row in _iter_rows():
        for rid in row["expected_rule_ids"]:
            assert rid in VALID_RULE_IDS, (
                f"{path.name}:{lineno} unknown rule_id {rid!r} "
                f"(valid: {sorted(VALID_RULE_IDS)})"
            )


def test_no_duplicate_ids() -> None:
    seen: dict[str, tuple[str, int]] = {}
    for path, lineno, row in _iter_rows():
        rid = row["id"]
        if rid in seen:
            prev_path, prev_lineno = seen[rid]
            pytest.fail(
                f"duplicate id {rid!r} at {path.name}:{lineno} "
                f"and {prev_path}:{prev_lineno}"
            )
        seen[rid] = (path.name, lineno)
