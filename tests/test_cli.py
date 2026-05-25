"""CLI smoke tests — never touch the LLM, just exercise Typer wiring."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pharma_ad_compliance.cli import app
from pharma_ad_compliance.schemas import (
    ComplianceReport,
    DrugClass,
    RewriteScore,
    RewriteVariant,
)

# Typer's Rich-formatted --help can split flags with ANSI sequences
# (e.g. "--variants" → "-" + ESC[...] + "-variants"), which breaks naive
# substring asserts on result.stdout. Local terminals where Rich detects
# no TTY may skip coloring entirely; CI runners typically do not. Strip
# ANSI before substring checks so the tests behave the same in both.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _plain(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _fake_report() -> ComplianceReport:
    return ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="заглушка",
        violations=[],
        generated_at=datetime.now(UTC),
    )


def _valid_breakdown(total: int = 75) -> dict[str, int]:
    base, rem = divmod(total, 5)
    return {
        "concreteness": base + (1 if rem > 0 else 0),
        "mechanism": base + (1 if rem > 1 else 0),
        "jtbd": base + (1 if rem > 2 else 0),
        "voice_and_structure": base + (1 if rem > 3 else 0),
        "register": base,
    }


def _fake_report_with_variants(frames: tuple[str, ...]) -> ComplianceReport:
    variants = tuple(
        RewriteVariant(
            text=f"{frame}-rewrite",
            frame=frame,  # type: ignore[arg-type]
            compliance_passed=True,
            quality_score=RewriteScore(
                total=sum(_valid_breakdown(75).values()),
                breakdown=_valid_breakdown(75),
                notes="",
            ),
        )
        for frame in frames
    )
    return ComplianceReport(
        source_kind="text",
        drug_class=DrugClass.OTC,
        extracted_text="заглушка",
        violations=[],
        rewrite_variants=variants,
        generated_at=datetime.now(UTC),
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_root_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _plain(result.stdout).lower()
    assert "compliance" in out or "usage" in out


def test_check_help_lists_input_options(runner: CliRunner) -> None:
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    out = _plain(result.stdout)
    for flag in ("--text", "--image", "--url", "--pdf"):
        assert flag in out


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
    assert "dataset" in _plain(result.stdout).lower()


def test_check_help_lists_variants_option(runner: CliRunner) -> None:
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    assert "--variants" in _plain(result.stdout)


def test_check_with_variants_1_passes_single_frame(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_compliance(*_args, **kwargs) -> ComplianceReport:
        captured["rewrite_frames"] = kwargs.get("rewrite_frames")
        return _fake_report_with_variants(("mechanism",))

    monkeypatch.setattr(
        "pharma_ad_compliance.cli.run_compliance", fake_run_compliance
    )
    out_path = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "check", "--text", "x",
            "--variants", "1",
            "--out", str(out_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["rewrite_frames"] == ("mechanism",)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert isinstance(payload["rewrite_variants"], list)
    assert len(payload["rewrite_variants"]) == 1
    assert payload["rewrite_variants"][0]["frame"] == "mechanism"


def test_check_with_variants_3_passes_all_frames(
    runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_compliance(*_args, **kwargs) -> ComplianceReport:
        captured["rewrite_frames"] = kwargs.get("rewrite_frames")
        return _fake_report_with_variants(("mechanism", "jtbd", "benefit"))

    monkeypatch.setattr(
        "pharma_ad_compliance.cli.run_compliance", fake_run_compliance
    )
    out_path = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "check", "--text", "x",
            "--variants", "3",
            "--out", str(out_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["rewrite_frames"] == ("mechanism", "jtbd", "benefit")
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(payload["rewrite_variants"]) == 3
    assert {v["frame"] for v in payload["rewrite_variants"]} == {
        "mechanism", "jtbd", "benefit"
    }


def test_check_with_variants_out_of_range_rejected(
    runner: CliRunner, monkeypatch
) -> None:
    """Typer min/max validators must reject 0 and 4."""
    async def fake_run_compliance(*_args, **_kwargs) -> ComplianceReport:
        return _fake_report()
    monkeypatch.setattr(
        "pharma_ad_compliance.cli.run_compliance", fake_run_compliance
    )
    for bad in ("0", "4"):
        result = runner.invoke(app, ["check", "--text", "x", "--variants", bad])
        assert result.exit_code != 0, f"--variants {bad} should be rejected"
