# regression_dataset/

JSONL fixtures used by `make eval` (the CLI regression harness in
`pharma_ad_compliance.cli.eval_cmd`). One case per line; format:

```json
{
  "id": "fas-<date>-<slug> | synthetic-<topic>-<n>",
  "input": "<russian ad text>",
  "expected_rule_ids": ["ART24_P3_NO_SIDE_EFFECTS", ...],
  "source": "<citation or 'synthetic'>",
  "note": "<optional rationale, especially for clean-controls>"
}
```

`expected_rule_ids` is the **subset** of rules the pipeline must report (extra
findings are tolerated — the harness is conservative-friendly). An empty list
is a "clean control" — the pipeline must report **zero** findings, otherwise it
counts as a false positive.

## Clean-control rationale

Some FAS cases are intentionally wired with `expected_rule_ids: []` because the
violation is out of scope for this text-only pipeline. Including them as
clean-controls ensures the pipeline does not hallucinate findings on otherwise
compliant ad copy:

- **`fas-2019-04-02-mmh-anaferon-detsky`** — the actual FAS violation was
  ч. 7 ст. 24 in the visual-layout sense (disclaimer occupied less than 5% of
  ad area). A text-only pipeline cannot observe area percentages; the
  paraphrased ad text below carries a well-formed disclaimer, so the pipeline
  should be silent.
- **`fas-2023-04-05-diapharmedik-magoraltel`** — the FAS finding was
  ч. 2 ст. 5 ФЗ-38 (некорректное сравнение), which is general advertising law,
  not a pharma-specific subsection of ст. 24. There is intentionally no checker
  for it.
- **`fas-clean-*`** rows — synthesized compliant rewrites of the violating
  creatives, paired with their failing counterparts. They verify recall and
  precision in a single run.

## Adding a case
1. Find a public FAS ruling and create `case_law/fas_decisions/<YYYY-MM-DD>-<slug>.md`.
2. Append a JSONL row here whose `id` is `fas-<YYYY-MM-DD>-<slug>` (the wiring
   test `tests/test_regression_wiring.py` enforces this convention).
3. Paraphrase the violating phrasing — do not copy long adversarial quotes
   verbatim. The point is to exercise the checker, not to mirror the regulator.
4. If the violation is out of scope, set `expected_rule_ids: []` and explain
   why in a `note` here.
