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

The GitHub Actions runner has no `claude` CLI authenticated against a Claude
Code subscription, and this project intentionally does not use
`ANTHROPIC_API_KEY` (the SDK delegates auth to the local CLI). The previous
`.github/workflows/regression-eval.yml` was gated on `ANTHROPIC_API_KEY` and
was therefore a no-op on every run — it has been removed. Regression eval is
the developer's local responsibility: run `make eval` before each PR and paste
the result into the PR description.

CI still runs `ruff`, `mypy`, and `pytest -m "not llm"` (which covers schemas,
the aggregator, parser security/SSRF, `_llm` extraction/retry logic, the
rule_checker rule-id-override path, and CLI smoke tests).

## Adding cases
1. Find a public FAS ruling on FZ-38 art. 24 at https://fas.gov.ru.
2. Extract the ad text quoted in the decision.
3. Add an entry to `case_law/regression_dataset/<topic>.jsonl` (id must match
   the `fas_decisions/*.md` filename — `tests/test_regression_wiring.py`
   enforces this).
4. Run `make eval` locally — it must pass before merge.

## Known scope gaps

The regression dataset includes several **clean controls** for cases the
pipeline intentionally does not catch. These ensure the pipeline does not
hallucinate findings on otherwise compliant copy:

1. **Visual-layout violations** — e.g. ч. 7 ст. 24 "disclaimer occupies less
   than 5 % of ad area" (FAS vs. ООО «НПФ Материа Медика Холдинг» / Анаферон
   детский, 2019). The pipeline is text-only and cannot observe area
   percentages or font sizes. Such cases are wired as clean controls.
2. **ст. 25 ФЗ-38 «не является ЛС» on БАД** — partially addressed via
   `art24_other` (the БАД-without-marker branch maps to `ART24_OTHER` /
   ч. 1 п. 9 ст. 24). Full coverage of ст. 25 in isolation is out of scope.
3. **ст. 5 ч. 2 ФЗ-38 (некорректное сравнение)** — general advertising law,
   not pharma-specific. Intentionally not covered by any checker (FAS vs.
   ООО «Диафармедик Плюс» / Магорел, 2023, is wired as a clean control).
