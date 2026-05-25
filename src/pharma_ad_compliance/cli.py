"""CLI entrypoint — `compliance check ...` after `pip install -e .`."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agents._llm import LLMOutputError, LLMUnavailableError
from .pipeline import run_compliance
from .schemas import (
    ComplianceReport,
    Creative,
    ImageCreative,
    PdfCreative,
    Severity,
    TextCreative,
    UrlCreative,
)

app = typer.Typer(
    name="compliance",
    help="Multi-agent FZ-38 art. 24 compliance checker for Russian pharma advertising.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def check(
    text: str | None = typer.Option(None, "--text", help="Inline ad text"),
    image: Path | None = typer.Option(None, "--image", help="Path to a banner image"),
    url: str | None = typer.Option(None, "--url", help="URL of a landing page"),
    pdf: Path | None = typer.Option(None, "--pdf", help="Path to a PDF (article, landing/email mockup)"),
    creative_id: str | None = typer.Option(None, "--id", help="Optional identifier for the creative"),
    no_rewrite: bool = typer.Option(False, "--no-rewrite", help="Skip the editor agent"),
    out: Path | None = typer.Option(None, "--out", help="Write the report to this path (JSON)"),
):
    """Run the compliance pipeline against a single creative."""
    sources = [s for s in (text, image, url, pdf) if s]
    if len(sources) != 1:
        raise typer.BadParameter("Provide exactly one of --text, --image, --url, --pdf")

    creative: Creative
    if text is not None:
        creative = TextCreative(text=text, creative_id=creative_id)
    elif image is not None:
        creative = ImageCreative(image_path=image, creative_id=creative_id)
    elif pdf is not None:
        creative = PdfCreative(pdf_path=pdf, creative_id=creative_id)
    else:
        creative = UrlCreative(url=url, creative_id=creative_id)  # type: ignore[arg-type]

    report = asyncio.run(run_compliance(creative, include_rewrite=not no_rewrite))
    _render(report)

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"\n[dim]Saved JSON to {out}[/dim]")


@app.command("eval")
def eval_cmd(
    dataset: Path = typer.Option(
        Path("case_law/regression_dataset"),
        "--dataset",
        help="Directory with .jsonl files of {input, expected_rule_ids}",
    ),
):
    """Run the regression dataset (LLM calls, subscription auth required)."""
    files = sorted(dataset.glob("*.jsonl"))
    if not files:
        console.print(f"[yellow]No .jsonl files in {dataset}[/yellow]")
        raise typer.Exit(code=1)

    cases: list[dict] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    console.print(f"Loaded {len(cases)} regression cases from {len(files)} file(s).")
    passed = 0
    errored = 0
    for i, case in enumerate(cases, 1):
        creative = TextCreative(text=case["input"], creative_id=case.get("id", f"case-{i}"))
        try:
            report = asyncio.run(run_compliance(creative, include_rewrite=False))
        except (LLMUnavailableError, LLMOutputError) as e:
            errored += 1
            console.print(
                f"  [magenta]ERROR[/magenta] case-{i}: pipeline aborted — "
                f"{type(e).__name__}: {e}"
            )
            continue
        found = {v.rule_id.value for v in report.violations}
        expected = set(case["expected_rule_ids"])
        # Recall: every expected rule must be found. Precision: a "clean" case
        # (no expected rules) must produce zero findings — that is how we catch
        # false positives, since `issubset` is trivially true for an empty set.
        recall_ok = expected.issubset(found)
        is_clean = not expected
        ok = recall_ok and (not is_clean or not found)
        unexpected = found - expected
        passed += int(ok)
        status = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
        line = f"  {status} case-{i}: expected={expected or '∅ (clean)'}, got={found or '∅'}"
        if unexpected:
            line += f" [yellow](extra: {unexpected})[/yellow]"
        console.print(line)

    summary = f"\n[bold]{passed}/{len(cases)} cases passed"
    if errored:
        summary += f", {errored} errored (infrastructure)"
    summary += ".[/bold]"
    console.print(summary)
    if passed != len(cases):
        raise typer.Exit(code=1)


_SEV_COLORS = {
    Severity.CRITICAL: "red",
    Severity.WARNING: "yellow",
    Severity.RECOMMENDATION: "blue",
}


def _render(report: ComplianceReport) -> None:
    header = (
        f"[bold]Compliance Report[/bold]\n"
        f"Source: {report.source_kind} | Drug class: {report.drug_class.value} | "
        f"Compliant: {'[green]YES[/green]' if report.is_compliant else '[red]NO[/red]'}"
    )
    console.print(Panel(header, expand=False))

    if not report.violations:
        console.print("[green]No violations found.[/green]")
    else:
        table = Table(title=f"{len(report.violations)} finding(s)", show_lines=True)
        table.add_column("Severity")
        table.add_column("Rule")
        table.add_column("Quote", overflow="fold")
        table.add_column("Why / Fix", overflow="fold")
        for v in report.violations:
            colour = _SEV_COLORS.get(v.severity, "white")
            why = v.explanation + (f"\n→ {v.suggested_fix}" if v.suggested_fix else "")
            table.add_row(
                f"[{colour}]{v.severity.value}[/{colour}]",
                v.rule_id.value,
                v.quote or "[dim](absence)[/dim]",
                why,
            )
        console.print(table)

    if report.rewritten_text:
        console.print(Panel(report.rewritten_text, title="Rewritten (compliant)", expand=False))


if __name__ == "__main__":
    app()
