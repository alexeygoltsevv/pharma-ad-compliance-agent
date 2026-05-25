"""CLI smoke tests — never touch the LLM, just exercise Typer wiring."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pharma_ad_compliance.cli import app
from pharma_ad_compliance.schemas import ComplianceReport, DrugClass


def _fake_report() -> ComplianceReport:
    return ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="заглушка",
        violations=[],
        generated_at=datetime.now(UTC),
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_root_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "compliance" in result.stdout.lower() or "usage" in result.stdout.lower()


def test_check_help_lists_input_options(runner: CliRunner) -> None:
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    for flag in ("--text", "--image", "--url", "--pdf"):
        assert flag in result.stdout


def test_check_with_no_input_fails(runner: CliRunner) -> None:
    # `check` with no source flags should be rejected by the BadParameter raise.
    result = runner.invoke(app, ["check"])
    assert result.exit_code != 0


def test_check_with_text_invokes_pipeline_and_writes_out(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    """Monkeypatch pipeline.run_compliance to dodge the LLM; verify JSON is written."""

    async def fake_run_compliance(*_args, **_kwargs) -> ComplianceReport:
        return _fake_report()

    monkeypatch.setattr(
        "pharma_ad_compliance.cli.run_compliance", fake_run_compliance
    )

    out_path = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "check",
            "--text",
            "Какой-то текст",
            "--no-rewrite",
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["source_kind"] == "text"
    assert payload["drug_class"] == "OTC"


def test_eval_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "dataset" in result.stdout.lower()
