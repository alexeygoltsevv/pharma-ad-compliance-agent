"""Index of past cases (FAS rulings + approved in-house reports) keyed by RuleId.

The pipeline calls `enrich_with_precedents(...)` after the aggregator. For each
CRITICAL violation we attach a small list of `CaseRef`s with the same `rule_id`
— the legal reviewer gets a precedent, the brand-manager gets social proof.

Index sources (built once at first call, cached via `functools.cache`):
- `case_law/fas_decisions/*.md` — YAML frontmatter at the top of each file:
  ```yaml
  ---
  case_id: bayer-teraflex-2017
  date: 2017-03-14
  party: Bayer
  product: Терафлю
  rule_ids: [ART24_P3_NO_SIDE_EFFECTS, ART24_P5_MANDATORY_DISCLAIMER]
  fine_rub: 100000
  url: https://fas.gov.ru/...
  short_quote: "Препарат назван безопасным без указания противопоказаний"
  ---
  ```
- `case_law/approved_reports/*.json` — produced by the Streamlit "Утвердить
  ответ" button. We walk `violations[].rule_id` and emit one CaseRef per file.

The matcher is best-effort: a malformed YAML block or missing required field
logs a warning and skips that file rather than crashing the pipeline.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .schemas import CaseRef, RuleId, Severity, Violation

logger = logging.getLogger(__name__)


# ─── Locating the case_law/ tree ─────────────────────────────────────────────
# Mirrors the resolution order in app.py: env override → repo checkout → cwd.
def _resolve_case_law_root() -> Path | None:
    env = os.environ.get("PHARMA_AD_CASE_LAW_DIR", "").strip()
    if env:
        return Path(env)
    # src/pharma_ad_compliance/case_law_matcher.py → repo/case_law/
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "case_law"
    if candidate.exists():
        return candidate
    cwd_candidate = Path.cwd() / "case_law"
    if cwd_candidate.exists():
        return cwd_candidate
    return None


# ─── Frontmatter parser (no external dependency) ─────────────────────────────
# We deliberately avoid pyyaml to keep the dependency surface small. The
# frontmatter we generate is structurally simple: top-level `key: value` with
# one optional inline list (`rule_ids: [A, B]`). Anything more exotic is a
# project-style bug — fail noisily.
_FRONTMATTER_FENCE = "---"


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Return parsed frontmatter dict, or None if no frontmatter block found.

    Raises ValueError if the block is malformed (opens but never closes, or has
    a line that isn't `key: value`).
    """
    lines = text.splitlines()
    # Allow optional leading blank lines / BOM.
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != _FRONTMATTER_FENCE:
        return None
    # Find closing fence.
    start = i + 1
    end = None
    for j in range(start, len(lines)):
        if lines[j].strip() == _FRONTMATTER_FENCE:
            end = j
            break
    if end is None:
        raise ValueError("frontmatter block opened with --- but never closed")

    out: dict[str, Any] = {}
    for raw in lines[start:end]:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter line missing ':' — {line!r}")
        key, _, value = line.partition(":")
        out[key.strip()] = _coerce_scalar(value.strip())
    return out


def _coerce_scalar(value: str) -> Any:
    """Best-effort scalar coercion: null, int, list, or stripped string."""
    if not value:
        return None
    lower = value.lower()
    if lower in {"null", "~"}:
        return None
    # Inline list `[a, b, c]`.
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(item.strip()) for item in inner.split(",") if item.strip()]
    # Int (fine_rub).
    if value.lstrip("-").isdigit():
        try:
            return int(value)
        except ValueError:
            pass
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


# ─── FAS markdown ingestion ──────────────────────────────────────────────────
def _load_fas_file(path: Path) -> list[tuple[RuleId, CaseRef]]:
    """Parse one FAS markdown file → (rule_id, CaseRef) pairs.

    Returns [] if the file has no rule_ids (out-of-scope decisions). Raises
    ValueError on malformed frontmatter — the caller logs+skips so a single bad
    file doesn't take down the whole index.
    """
    text = path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text)
    if meta is None:
        raise ValueError("missing YAML frontmatter")

    case_id = str(meta.get("case_id") or path.stem)
    date = meta.get("date")
    if not date:
        raise ValueError("`date` is required in frontmatter")
    party = str(meta.get("party") or "Неизвестно")
    fine_rub_raw = meta.get("fine_rub")
    fine_rub = int(fine_rub_raw) if isinstance(fine_rub_raw, int) else None
    url = meta.get("url")
    short_quote = meta.get("short_quote")
    rule_ids_raw = meta.get("rule_ids") or []
    if not isinstance(rule_ids_raw, list):
        raise ValueError(f"`rule_ids` must be a list, got {type(rule_ids_raw).__name__}")

    pairs: list[tuple[RuleId, CaseRef]] = []
    for rid_str in rule_ids_raw:
        try:
            rid = RuleId(rid_str)
        except ValueError:
            logger.warning(
                "case_law %s: unknown rule_id %r — skipping this rule entry",
                path.name, rid_str,
            )
            continue
        ref = CaseRef(
            case_id=case_id,
            date=str(date),
            party=party,
            fine_rub=fine_rub,
            source="fas",
            url=str(url) if url else None,
            short_quote=str(short_quote) if short_quote else None,
        )
        pairs.append((rid, ref))
    return pairs


# ─── Approved-report JSON ingestion ──────────────────────────────────────────
def _load_approved_file(path: Path) -> list[tuple[RuleId, CaseRef, str | None]]:
    """Parse one approved-report JSON → (rule_id, CaseRef, original_text) triples.

    The third element (`original_text`) is the creative the report was about
    — used by `find_precedents` for self-reference exclusion. Returns [] if
    the file has no rule_ids we recognize.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("approved report must be a JSON object")

    # Date from filename: approved-2026-05-21_122319-<digest>.json
    date = _extract_date_from_approved_filename(path.stem)
    party = _extract_party(payload)
    findings = payload.get("violations") or payload.get("findings") or []
    if not isinstance(findings, list):
        raise ValueError("`violations` must be a list")
    original_text = payload.get("input") or payload.get("extracted_text")
    if original_text is not None and not isinstance(original_text, str):
        original_text = None

    short_quote = None
    for finding in findings:
        if isinstance(finding, dict):
            expl = finding.get("explanation")
            if isinstance(expl, str) and expl.strip():
                short_quote = expl.strip()[:80]
                break

    url = payload.get("url")

    triples: list[tuple[RuleId, CaseRef, str | None]] = []
    seen_rules: set[RuleId] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rid_raw = finding.get("rule_id")
        if not rid_raw:
            continue
        try:
            rid = RuleId(rid_raw)
        except ValueError:
            logger.warning(
                "approved report %s: unknown rule_id %r — skipping",
                path.name, rid_raw,
            )
            continue
        # Dedupe: one CaseRef per (file, rule_id) — many reports flag the same
        # rule multiple times and we don't want to surface the same CaseRef
        # three times for one violation.
        if rid in seen_rules:
            continue
        seen_rules.add(rid)
        ref = CaseRef(
            case_id=path.stem,
            date=date,
            party=party,
            fine_rub=None,
            source="approved",
            url=str(url) if url else None,
            short_quote=short_quote,
        )
        triples.append((rid, ref, original_text))
    return triples


def _extract_date_from_approved_filename(stem: str) -> str:
    """approved-2026-05-21_122319-<digest> → '2026-05-21'."""
    parts = stem.split("-")
    # parts = ["approved", "2026", "05", "21_122319", "<digest>"]
    if len(parts) >= 4 and parts[0] == "approved":
        try:
            year = int(parts[1])
            month = int(parts[2])
            day_raw = parts[3].split("_", 1)[0]
            day = int(day_raw)
            return f"{year:04d}-{month:02d}-{day:02d}"
        except (ValueError, IndexError):
            pass
    # Fallback: today's date is meaningless; use a sentinel that still sorts.
    return "1970-01-01"


def _extract_party(payload: dict[str, Any]) -> str:
    source = payload.get("source")
    if isinstance(source, dict):
        sid = source.get("id") or source.get("name")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    # Reports produced by the Streamlit UI today don't carry brand metadata —
    # fall back to a neutral label.
    return "Внутренний отчёт"


# ─── Index build (cached) ────────────────────────────────────────────────────
# Keyed on a frozenset of (RuleId, CaseRef, original_text) so a re-build picks
# up new approved reports if the cache is invalidated (clear_cache()).
@functools.cache
def _build_index(
    root: Path | None = None,
) -> dict[RuleId, list[tuple[CaseRef, str | None]]]:
    """Build {RuleId: [(CaseRef, original_text_if_approved), ...]}.

    `original_text` is non-None only for `source="approved"` entries; used by
    `find_precedents` to drop self-references when the user re-checks an ad
    they previously approved.
    """
    case_root = root or _resolve_case_law_root()
    if case_root is None or not case_root.exists():
        logger.info("case_law root not found — precedent index will be empty")
        return {}

    index: dict[RuleId, list[tuple[CaseRef, str | None]]] = {}

    fas_dir = case_root / "fas_decisions"
    if fas_dir.exists():
        for md in sorted(fas_dir.glob("*.md")):
            try:
                pairs = _load_fas_file(md)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                logger.warning("case_law %s: skipped (%s)", md.name, exc)
                continue
            for rid, ref in pairs:
                index.setdefault(rid, []).append((ref, None))

    approved_dir = case_root / "approved_reports"
    if approved_dir.exists():
        for jf in sorted(approved_dir.glob("approved-*.json")):
            try:
                triples = _load_approved_file(jf)
            except Exception as exc:  # noqa: BLE001
                logger.warning("approved_report %s: skipped (%s)", jf.name, exc)
                continue
            for rid, ref, text in triples:
                index.setdefault(rid, []).append((ref, text))

    return index


def clear_cache() -> None:
    """Drop the cached index. Call from tests after writing new fixture files."""
    _build_index.cache_clear()


# ─── Public lookup API ───────────────────────────────────────────────────────
def find_precedents(
    violation: Violation,
    *,
    limit: int = 3,
    creative_text: str | None = None,
    root: Path | None = None,
) -> list[CaseRef]:
    """Return precedents matching this violation's rule_id, sorted date-desc.

    `creative_text`, when provided, excludes self-references — any approved
    report whose original `input` equals `creative_text` is filtered out so the
    user doesn't see "this looks like the report you just produced."
    """
    if limit <= 0:
        return []
    index = _build_index(root)
    entries = index.get(violation.rule_id, [])
    if not entries:
        return []
    normalized_creative = (creative_text or "").strip()

    filtered: list[CaseRef] = []
    for ref, original_text in entries:
        if (
            normalized_creative
            and ref.source == "approved"
            and original_text is not None
            and original_text.strip() == normalized_creative
        ):
            continue
        filtered.append(ref)

    filtered.sort(key=lambda r: r.date, reverse=True)
    return filtered[:limit]


def enrich_with_precedents(
    violations: Iterable[Violation],
    *,
    creative_text: str | None = None,
    severity_floor: Severity = Severity.CRITICAL,
    limit: int = 3,
    root: Path | None = None,
) -> list[Violation]:
    """Return new Violation instances with `precedents` populated.

    Only violations at `severity_floor` or worse (lower rank) are enriched;
    others are returned unchanged. Failure of the matcher (malformed file,
    missing dir, …) is swallowed with a warning — precedents are a nice-to-have,
    not a pipeline gate.
    """
    out: list[Violation] = []
    for v in violations:
        if v.severity.rank > severity_floor.rank:
            out.append(v)
            continue
        try:
            refs = find_precedents(v, limit=limit, creative_text=creative_text, root=root)
        except Exception as exc:  # noqa: BLE001 — must never break the pipeline
            logger.warning(
                "case_law lookup failed for rule_id=%s: %s — continuing with no precedents",
                v.rule_id.value, exc,
            )
            refs = []
        if refs:
            out.append(v.model_copy(update={"precedents": refs}))
        else:
            out.append(v)
    return out
