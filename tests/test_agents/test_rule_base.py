"""Tests for the load-bearing rule_checkers._base.check_rule.

These cover the two things `_base` does on top of plain `run_json`:
1. It forces `rule_id` to the registered enum value (models otherwise invent
   sub-rule ids like `ART24_OTHER_P6` which would crash the enum validator).
2. It raises `LLMOutputError` on bad JSON / schema mismatches instead of leaking
   a downstream JSONDecodeError or ValidationError.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pharma_ad_compliance.agents._llm import LLMOutputError
from pharma_ad_compliance.agents.rule_checkers._base import check_rule
from pharma_ad_compliance.schemas import (
    DrugClass,
    ParsedCreative,
    RuleId,
    Severity,
)

# Module path used by `monkeypatch.setattr` — `_collect_text` is imported into
# `_base`'s own namespace, so we patch it there (not in `_llm`).
_COLLECT_TEXT_PATH = "pharma_ad_compliance.agents.rule_checkers._base._collect_text"


def _parsed(text: str = "Текст рекламы") -> ParsedCreative:
    return ParsedCreative(source_kind="text", extracted_text=text)


async def test_check_rule_overrides_invented_rule_id(monkeypatch):
    """Model returns ART24_OTHER_P6 — the output Violation must use the registered enum."""
    payload = {
        "violations": [
            {
                "rule_id": "ART24_OTHER_P6",  # invented, not in the enum
                "severity": "WARNING",
                "quote": "каждому здоровому нужно принимать X",
                "explanation": "ст. 24 ч. 1 п. 6",
                "suggested_fix": "удалить",
            }
        ]
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(payload)))

    out = await check_rule(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        rule_id=RuleId.ART24_OTHER,
        focus_instruction="(focus stub)",
    )
    assert len(out) == 1
    assert out[0].rule_id is RuleId.ART24_OTHER
    assert out[0].severity is Severity.WARNING


async def test_check_rule_multiple_violations_keep_separate_with_forced_rule_id(monkeypatch):
    """Two violations with different invented rule_ids both get rewritten — none lost."""
    payload = {
        "violations": [
            {
                "rule_id": "ART24_OTHER_P6",
                "severity": "WARNING",
                "quote": "каждому здоровому",
                "explanation": "п. 6",
            },
            {
                "rule_id": "ART24_OTHER_P7",
                "severity": "CRITICAL",
                "quote": "не нужно идти к врачу",
                "explanation": "п. 7",
            },
        ]
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(payload)))

    out = await check_rule(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        rule_id=RuleId.ART24_OTHER,
        focus_instruction="(focus stub)",
    )
    assert len(out) == 2
    assert {v.rule_id for v in out} == {RuleId.ART24_OTHER}
    assert {v.severity for v in out} == {Severity.WARNING, Severity.CRITICAL}


async def test_check_rule_empty_violations_returns_empty_list(monkeypatch):
    monkeypatch.setattr(
        _COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps({"violations": []}))
    )
    out = await check_rule(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        rule_id=RuleId.ART24_P1_MINORS,
        focus_instruction="(focus stub)",
    )
    assert out == []


async def test_check_rule_missing_violations_key_returns_empty(monkeypatch):
    # `{}` is valid JSON but has no `violations` key — `_base` treats as empty.
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value="{}"))
    out = await check_rule(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        rule_id=RuleId.ART24_P1_MINORS,
        focus_instruction="(focus stub)",
    )
    assert out == []


async def test_check_rule_top_level_array_returns_empty(monkeypatch):
    # `_extract_json` happily extracts the array, but it's not a dict → no
    # `violations` key, so we return empty rather than raising on schema.
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value="[1, 2, 3]"))
    out = await check_rule(
        parsed=_parsed(),
        drug_class=DrugClass.OTC,
        rule_id=RuleId.ART24_P1_MINORS,
        focus_instruction="(focus stub)",
    )
    assert out == []


async def test_check_rule_malformed_json_raises_llm_output_error(monkeypatch):
    """No JSON in the output at all → LLMOutputError from _extract_json."""
    monkeypatch.setattr(
        _COLLECT_TEXT_PATH, AsyncMock(return_value="Извините, не могу ответить.")
    )
    with pytest.raises(LLMOutputError):
        await check_rule(
            parsed=_parsed(),
            drug_class=DrugClass.OTC,
            rule_id=RuleId.ART24_P1_MINORS,
            focus_instruction="(focus stub)",
        )


async def test_check_rule_invalid_json_after_extraction_raises(monkeypatch):
    """Valid braces but invalid JSON inside → LLMOutputError from the json.loads path."""
    # `_extract_json` will return `{not json}` (balanced braces), `json.loads` fails.
    monkeypatch.setattr(
        _COLLECT_TEXT_PATH, AsyncMock(return_value="prefix {not json} suffix")
    )
    with pytest.raises(LLMOutputError):
        await check_rule(
            parsed=_parsed(),
            drug_class=DrugClass.OTC,
            rule_id=RuleId.ART24_P1_MINORS,
            focus_instruction="(focus stub)",
        )


async def test_check_rule_violation_schema_violation_raises(monkeypatch):
    """A violation with an invalid severity string → LLMOutputError."""
    payload = {
        "violations": [
            {
                "rule_id": "ART24_OTHER",  # legit
                "severity": "MAYBE",  # not a Severity enum value
                "quote": "что-то",
                "explanation": "что-то",
            }
        ]
    }
    monkeypatch.setattr(_COLLECT_TEXT_PATH, AsyncMock(return_value=json.dumps(payload)))
    with pytest.raises(LLMOutputError):
        await check_rule(
            parsed=_parsed(),
            drug_class=DrugClass.OTC,
            rule_id=RuleId.ART24_OTHER,
            focus_instruction="(focus stub)",
        )
