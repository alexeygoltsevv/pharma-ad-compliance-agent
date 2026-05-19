# case_law

Real-world basis for the agents: public FAS decisions on FZ-38 art. 24 violations,
plus a regression dataset derived from them.

```
case_law/
  fas_decisions/         # raw, one .md per decision (link + verbatim ad text + verdict)
  regression_dataset/    # *.jsonl — automated test cases for `make eval`
```

## Why this folder exists

Most "compliance with LLMs" repos are wrappers over a generic safety prompt.
This one is grounded in actual FAS practice: every checker has been validated
against rulings the regulator has already published.

Sources are taken from https://fas.gov.ru only — public, no closed access required.

## Adding a decision

1. Find the ruling on https://fas.gov.ru (search: «нарушение статьи 24 закона о рекламе»).
2. Create `fas_decisions/<YYYY-MM-DD>-<short-slug>.md` with:
   - Permalink to the FAS page
   - The ad text quoted in the decision (verbatim)
   - The rules cited by FAS (mapped to our `RuleId` enum)
   - The verdict and fine amount
3. Add at least one `regression_dataset/*.jsonl` line referencing it.

See `docs/evaluation.md` for the regression file format.
