# Architecture

## Pipeline

```
                ┌──────────────────────────┐
                │   Creative input         │
                │ Text | Image | URL | PDF │
                └─────────────┬────────────┘
                              │
                              ▼
                ┌──────────────────────────┐
                │ parser_agent             │
                │  text:  passthrough      │
                │  url:   httpx + BS4      │
                │  image: Claude Vision    │
                │  pdf:   PyMuPDF (+ OCR)  │
                └─────────────┬────────────┘
                              │  ParsedCreative
                              ▼
                ┌──────────────────────────┐
                │ drug_classifier_agent    │
                │  → DrugClass             │
                │    (RX | OTC | BAD)      │
                └─────────────┬────────────┘
                              │
        ┌─────────────────────┼─────────────────────────────────────┐
        │ asyncio.gather (6 rule_checkers run in parallel)          │
        │                                                           │
        │  art24_p1_minors          art24_p4_doctor_recommendation  │
        │  art24_p2_specific_cases  art24_p5_mandatory_disclaimer   │
        │  art24_p3_no_side_effects art24_other                     │
        │                                                           │
        │  each returns: list[Violation]                            │
        └─────────────────────┬─────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────┐
                │ aggregator_agent         │
                │  (pure Python)           │
                │  dedupe + sort by sev    │
                └─────────────┬────────────┘
                              │
                              ▼
                ┌──────────────────────────┐
                │ editor_agent             │
                │  rewrite → compliant     │
                └─────────────┬────────────┘
                              │
                              ▼
                       ComplianceReport
```

## Why this shape

- **Parallel per-rule checkers** rather than one mega-prompt: each checker has a tight system prompt focused on a single subsection of art. 24, which keeps false positives low. Inspired by the "many small graders" pattern from evaluations literature.
- **Aggregator is pure Python**, not an LLM. Dedupe and sorting are deterministic — letting the model do them would only add cost and variance.
- **Editor only runs when there's something to fix.** Skipped automatically when `violations == []`.
- **Drug classifier upstream of checkers** because `art24_p5_mandatory_disclaimer` is intentionally skipped for `DrugClass.BAD` — disclaimer requirements are governed by a different article for supplements.

## LLM transport

All LLM calls go through `claude-agent-sdk` (`agents/_llm.py`). Each call:
- Uses `permission_mode='bypassPermissions'`, `allowed_tools=[]` — the agent cannot read files, run shell, or hit the network.
- Sets `max_turns=1` — single response, no autonomous loop.
- Returns concatenated text from `AssistantMessage` blocks; we extract JSON via regex/balanced-bracket scan and validate against the Pydantic schema.

Auth is delegated to the local `claude` CLI (Claude Code subscription). No API key is required for local development.

## ComplianceReport shape

The terminal node of the pipeline is `ComplianceReport`
([`schemas/violation.py`](../src/pharma_ad_compliance/schemas/violation.py)):

- `creative_id`, `source_kind`, `drug_class`, `extracted_text` — provenance.
- `violations: list[Violation]` — deduped + sorted by severity.
- `rewritten_text: str | None` — set only when `include_rewrite=True` and the
  editor ran.
- `metadata: dict[str, str]` — `str→str` parser-side info (`page_count`,
  `ocr_pages`, `truncated`, …).
- `diagnostics: dict[str, Any]` — pipeline-level structured info kept
  **separate from `metadata`** so we can store lists / nested structures
  without breaking the parser's strict `str→str` contract. Today's only key is
  `failed_checkers: list[str]` (module-name slugs of checkers that raised);
  UIs use it to surface partial-results warnings.

## Failure modes (and what we do about them)

| Failure | Mitigation |
|---|---|
| Model returns prose around JSON | `_extract_json` finds the first fenced block or balanced-brace value |
| Model returns invalid JSON | `LLMOutputError` with the first 500 chars of raw output |
| Model picks the wrong `rule_id` | `_base.check_rule` overrides with the checker's own `rule_id` — applies to **all** checkers including `ART24_OTHER`, since the model otherwise invents sub-rule ids (e.g. `ART24_OTHER_P6`) that crash the enum validator |
| A single checker raises | `asyncio.gather(return_exceptions=True)` so partial results survive; failing checker names land in `ComplianceReport.diagnostics["failed_checkers"]` for the UI to surface |
| Two checkers flag the same span | aggregator dedupes by `(rule_id, normalized_quote)` and keeps higher severity |
| Network/auth failure | propagates `CLIConnectionError` from the SDK |
