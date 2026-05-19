# Evaluation

## Goal
Catch ≥ 90 % of CRITICAL findings from real FAS decisions; ≤ 10 % false-positive rate on a held-out set of approved (FAS-cleared) creatives.

## Dataset (`case_law/regression_dataset/`)

Each line of any `*.jsonl` file is one case:

```json
{
  "id": "fas-2023-04-17-stomatofit",
  "input": "Стоматофит полностью безопасен и не имеет побочных эффектов...",
  "expected_rule_ids": ["ART24_P3_NO_SIDE_EFFECTS"],
  "source": "https://fas.gov.ru/documents/..."
}
```

- `input` — plain text of the ad (taken from the FAS decision verbatim where possible).
- `expected_rule_ids` — the **subset** of rules we expect the pipeline to detect. The case passes if every expected rule is reported (extra findings are allowed; the pipeline is permitted to be more conservative than the FAS verdict).
- `source` — link to the public FAS decision.

## Running

```bash
make eval                                                              # full dataset
.venv/bin/compliance eval --dataset case_law/regression_dataset/       # same, explicit
```

Each case runs the pipeline (LLM calls included) and reports PASS/FAIL. Total cost is ~3-5 LLM calls per case (parser is free for text, classifier + 6 checkers; editor skipped).

## Why CI doesn't run eval
The GitHub Actions runner has no Claude subscription. Eval is run locally before each PR; results are pasted into the PR description.

If you want eval in CI, set `ANTHROPIC_API_KEY` in repo secrets and add a step to `.github/workflows/regression-eval.yml`.

## Adding cases
1. Find a public FAS ruling on FZ-38 art. 24 at https://fas.gov.ru.
2. Extract the ad text quoted in the decision.
3. Add an entry to `case_law/regression_dataset/<topic>.jsonl`.
4. Run `make eval` locally — it must pass before merge.
