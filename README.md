# pharma-ad-compliance-agent

Multi-agent compliance checker for Russian pharmaceutical advertising. Input — ad text, banner image, or landing-page URL. Output — a structured report of violations of **Federal Law 38-ФЗ "On Advertising", Article 24** ("Реклама лекарственных средств, медицинских изделий и медицинских услуг, методов профилактики, диагностики, лечения и медицинской реабилитации") and a rewritten, compliance-safe version.

> **Status:** v0.1 — single happy-path pipeline working on Russian text. Banner OCR/URL parsing implemented but lightly tested.

## Why
Drug advertising in Russia is policed by FAS under FZ-38 art. 24. Manual pre-launch review by external counsel costs 50–150k₽ per creative and takes 3–7 days. This agent reproduces the most common checks in seconds and flags critical violations before they reach FAS.

## Architecture

```
Creative (text | image | url)
    │
    ▼
parser_agent ───► drug_classifier (Rx / OTC / БАД)
    │
    ├──► art24_p1_minors          ┐
    ├──► art24_p2_specific_cases  │ run in parallel
    ├──► art24_p3_no_side_effects │ (asyncio.gather)
    ├──► art24_p4_doctor_recommendation
    ├──► art24_p5_mandatory_disclaimer
    └──► art24_other              ┘
    │
    ▼
aggregator (dedupe + prioritize: CRITICAL / WARNING / RECOMMENDATION)
    │
    ▼
editor (rewrite to pass compliance)
    │
    ▼
ComplianceReport (Pydantic) → JSON / Markdown / Streamlit
```

All LLM calls go through the **[Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)** using the local `claude` CLI for auth (Claude Code subscription). No `ANTHROPIC_API_KEY` required for local development.

## Quick start

```bash
make install               # creates .venv, installs deps
make demo                  # runs a sample non-compliant creative through the pipeline
make streamlit             # opens the demo UI on localhost:8501
```

### CLI

```bash
compliance check --text "Этот препарат полностью безопасен и не имеет побочных эффектов."
compliance check --image banner.png
compliance check --url https://example.com/landing
```

## Example: before / after

**Input:**

> Этот препарат полностью безопасен и не имеет побочных эффектов. Рекомендуется детям.

**Output (excerpt):**

```json
{
  "drug_class": "OTC",
  "violations": [
    {
      "rule_id": "ART24_P3_NO_SIDE_EFFECTS",
      "severity": "CRITICAL",
      "quote": "полностью безопасен и не имеет побочных эффектов",
      "explanation": "Утверждение об отсутствии побочных эффектов запрещено ст. 24 ч. 1 п. 6 ФЗ-38.",
      "suggested_fix": "Удалить утверждение, добавить дисклеймер о противопоказаниях."
    },
    {
      "rule_id": "ART24_P1_MINORS",
      "severity": "CRITICAL",
      "quote": "Рекомендуется детям",
      "explanation": "Обращение к несовершеннолетним в рекламе ЛС запрещено."
    },
    {
      "rule_id": "ART24_P5_MANDATORY_DISCLAIMER",
      "severity": "CRITICAL",
      "explanation": "Отсутствует обязательное предупреждение «Имеются противопоказания, проконсультируйтесь со специалистом»."
    }
  ],
  "rewritten_text": "Препарат показан при ... Имеются противопоказания, проконсультируйтесь со специалистом."
}
```

## Repository layout

```
src/pharma_ad_compliance/
  agents/
    parser_agent.py
    drug_classifier_agent.py
    rule_checkers/        # 6 parallel checkers, one per subsection of art. 24
    aggregator_agent.py
    editor_agent.py
  schemas/                # Pydantic v2 models for Creative, Violation, Report
  rag/                    # FZ-38 text loader (no vector store in v0.1)
  prompts/                # versioned system prompts (Markdown)
  cli.py                  # typer CLI
  app.py                  # Streamlit demo
case_law/
  fas_decisions/          # public FAS rulings on FZ-38 art. 24 violations
  regression_dataset/     # input + expected output for evaluation
docs/
tests/
```

## Evaluation

```bash
make eval                  # runs the regression dataset locally (requires `claude` subscription auth)
```

CI runs only non-LLM tests (schema validation, aggregator logic). The full regression eval against `case_law/regression_dataset/` is run locally because the GitHub Actions runner has no Claude subscription.

## Sources

- [ФЗ-38 «О рекламе» статья 24](http://www.consultant.ru/document/cons_doc_LAW_58968/) — текст закона.
- [Решения ФАС по рекламе ЛС](https://fas.gov.ru/) — публичная база, основа `case_law/`.

## License
MIT — see [LICENSE](LICENSE).
