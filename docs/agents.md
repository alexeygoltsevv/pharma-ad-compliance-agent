# Agents

| Agent | Input | Output | Role |
|---|---|---|---|
| `parser_agent` | `Creative` (text / image / url / pdf) | `ParsedCreative` | Normalize input to plain Russian text. Vision for images, BS4 for URLs, PyMuPDF + Vision fallback for PDFs. |
| `drug_classifier_agent` | `ParsedCreative` | `DrugClass` | Decide Rx / OTC / БАД / Unknown — determines which rules apply. |
| `rule_checkers/art24_p1_minors` | `ParsedCreative`, `DrugClass` | `list[Violation]` | Detects appeals to minors. |
| `rule_checkers/art24_p2_specific_cases` | same | same | Detects testimonials referencing specific cures. |
| `rule_checkers/art24_p3_no_side_effects` | same | same | Detects guarantees of safety / absence of side effects (covers ст. 24 ч. 1 п. 8). |
| `rule_checkers/art24_p4_doctor_recommendation` | same | same | Detects pseudo-doctor or pseudo-pharmacist endorsements (ч. 1 п. 4 в части ложной экспертизы; буквальный п. 4 про регистрационные исследования вынесен в `art24_other`). |
| `rule_checkers/art24_p5_mandatory_disclaimer` | same | same | Detects missing or distorted disclaimer (ст. 24 ч. 7). Skipped for `BAD`. |
| `rule_checkers/art24_other` | same | same | Catch-all for ст. 24 ч. 1 **п. 3, 4, 5, 6, 7, 9, 10** — including the registration-studies-as-benefit rule (was lifted out of `art24_p4_doctor_recommendation` during the scope split) and the natural-origin-safety rule (п. 10). |
| `aggregator_agent` | `list[Violation]` | `list[Violation]` | Pure-Python: dedupe by `(rule_id, quote)`, sort by severity. |
| `editor_agent` | `ParsedCreative`, `DrugClass`, `list[Violation]` | `str \| None` | Rewrite the creative to address every finding. |

## Checker signature

Every rule checker exports:

```python
async def check(
    parsed: ParsedCreative,
    drug_class: DrugClass,
    *,
    user_feedback: list[str] | None = None,
) -> list[Violation]: ...
```

The `user_feedback` keyword threads UI comments from earlier iterations into
both the checker and the editor prompts. The pipeline always passes it (None
when no feedback) — never add a checker that omits the parameter.

## Adding a new checker

Three edits are required (not two — the registry alone is not enough):

1. **New module** in `agents/rule_checkers/<rule_name>.py` with `RULE_ID`,
   `FOCUS`, and an `async def check(parsed, drug_class, *, user_feedback=None)`.
2. **Append to `ALL_CHECKERS`** in `agents/rule_checkers/__init__.py` so the
   pipeline picks it up.
3. **Add the enum value to `RuleId`** in `schemas/violation.py` — `_base`
   forces every output `Violation.rule_id` to the registered enum value via
   `Violation.model_validate`, so an unknown id will raise `LLMOutputError`.
4. Update `case_law/regression_dataset/` with at least one positive example
   and a clean control.

## Partial-results behavior

The pipeline runs all six checkers under `asyncio.gather(return_exceptions=True)`.
A single checker that raises (transient LLM error, malformed JSON, etc.) no
longer aborts the whole report — its exception is logged and the failing
checker's module name is recorded in `ComplianceReport.diagnostics["failed_checkers"]`.
The UI (Streamlit / CLI) is expected to surface this as a partial-results
warning when present.
