"""v0.1 "RAG" — just loads the local Markdown excerpt of FZ-38 art. 24.

Future versions will index FAS rulings (`case_law/fas_decisions/`) into a vector
store and inject the most relevant passages per rule.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

_LAW_FILE = Path(__file__).resolve().parents[1] / "prompts" / "fz38_article24.md"


@cache
def get_article24_text() -> str:
    return _LAW_FILE.read_text(encoding="utf-8")
