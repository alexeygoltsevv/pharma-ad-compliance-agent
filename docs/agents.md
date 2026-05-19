# Agents

| Agent | Input | Output | Role |
|---|---|---|---|
| `parser_agent` | `Creative` (text / image / url) | `ParsedCreative` | Normalize input to plain Russian text. Vision for images, BS4 for URLs. |
| `drug_classifier_agent` | `ParsedCreative` | `DrugClass` | Decide Rx / OTC / БАД / Unknown — determines which rules apply. |
| `rule_checkers/art24_p1_minors` | `ParsedCreative`, `DrugClass` | `list[Violation]` | Detects appeals to minors. |
| `rule_checkers/art24_p2_specific_cases` | same | same | Detects testimonials referencing specific cures. |
| `rule_checkers/art24_p3_no_side_effects` | same | same | Detects guarantees of safety / absence of side effects (covers ст. 24 ч. 1 п. 8 and п. 10). |
| `rule_checkers/art24_p4_doctor_recommendation` | same | same | Detects pseudo-doctor or pseudo-pharmacist endorsements. |
| `rule_checkers/art24_p5_mandatory_disclaimer` | same | same | Detects missing or distorted disclaimer (ст. 24 ч. 7). Skipped for `BAD`. |
| `rule_checkers/art24_other` | same | same | Catch-all for ст. 24 ч. 1 п. 3, 5, 6, 7, 9. |
| `aggregator_agent` | `list[Violation]` | `list[Violation]` | Pure-Python: dedupe by `(rule_id, quote)`, sort by severity. |
| `editor_agent` | `ParsedCreative`, `DrugClass`, `list[Violation]` | `str \| None` | Rewrite the creative to address every finding. |

## Adding a new checker

1. Create `agents/rule_checkers/<rule_name>.py` with `RULE_ID`, `FOCUS`, and an `async def check(parsed, drug_class)`.
2. Add a corresponding member to `RuleId` in `schemas/violation.py`.
3. Append the new `check` callable to `ALL_CHECKERS` in `agents/rule_checkers/__init__.py`.
4. Update `case_law/regression_dataset/` with at least one positive example.
