"""Thin wrapper around `claude-agent-sdk` for structured-output LLM calls.

Auth is delegated to the local `claude` CLI (Claude Code subscription) — no API
key is required. Each call runs in `permission_mode='bypassPermissions'` with an
empty `allowed_tools` list, so the agent is pure text-in / text-out and never
touches the filesystem or the network on its own.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfigDisabled,
    query,
)
from pydantic import BaseModel, ValidationError

# (media_type, raw_bytes) pairs for vision input, e.g. ("image/png", b"...").
ImageInput = tuple[str, bytes]

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = os.environ.get("PHARMA_AD_MODEL", "claude-sonnet-4-6")
# Shared Haiku constant for fast, mechanical tasks (classifier, editor rewrite).
# ~3x faster than Sonnet with no quality loss on extract/classify/rewrite work.
HAIKU_MODEL = "claude-haiku-4-5-20251001"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


# Per-call latency logging (model + elapsed + whether the model emitted any
# thinking blocks). Off by default; enable with PHARMA_AD_TIMING=1 to attribute
# where pipeline time goes.
_TIMING = _env_flag("PHARMA_AD_TIMING", False)
if _TIMING and not logging.getLogger().handlers:
    # Opt-in: surface our INFO timing logs (the CLI/Streamlit don't configure logging).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

# Cap concurrent CLI subprocesses. 8 simultaneous `claude` subprocesses
# overwhelmed the local CLI ("error result: success"); the retry layer below
# plus disabled thinking (lighter calls) keep 6 stable — enough to run all six
# rule_checkers in a single wave instead of 5+1. Drop back to 5 via the env var
# if the CLI starts surfacing transient errors.
_CONCURRENT_LLM_CALLS = int(os.environ.get("PHARMA_AD_MAX_CONCURRENCY", "6"))
_SEMAPHORE: asyncio.Semaphore | None = None

# Extended thinking is ON by default through the CLI transport and added ~10s+
# per call (even Haiku classification) for no quality gain on these mechanical
# extract/classify/rewrite tasks. Disable it; re-enable with PHARMA_AD_THINKING=1
# if a regression shows up on `make eval`.
_THINKING_ENABLED = _env_flag("PHARMA_AD_THINKING", False)
_THINKING_CONFIG = None if _THINKING_ENABLED else ThinkingConfigDisabled(type="disabled")

# Constrain model output to a JSON schema (CLI `--json-schema`) for `run_json`
# callers. DEFAULT OFF: the CLI implements `--json-schema` via an extra
# structured-output turn, which collides with our max_turns=1 single-shot setup
# ("Reached maximum number of turns (1)"). `_extract_json` + Pydantic validation
# already give robust parsing, so this stays opt-in (PHARMA_AD_JSON_SCHEMA=1)
# until the turn handling is reworked.
_JSON_SCHEMA_ENABLED = _env_flag("PHARMA_AD_JSON_SCHEMA", False)

# How many times to retry an LLM call before giving up. Targets transient
# "Claude Code returned an error result: success" cases the SDK surfaces when
# the CLI exits non-zero without a structured error.
_MAX_RETRIES = 2
_RETRY_INITIAL_DELAY = 1.5  # seconds; doubles on each retry

# Hard ceiling per LLM call so a hung CLI subprocess can't hold a semaphore slot
# forever. Counts model time only (the semaphore wait is outside the timeout).
_LLM_TIMEOUT = float(os.environ.get("PHARMA_AD_LLM_TIMEOUT", "120"))

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)

# Latched after we've logged the "no prompt caching observed" hint at most once
# per process — without this, every checker call in a clean cache state would
# spam the same INFO line.
_PROMPT_CACHE_WARNED = False


class LLMOutputError(RuntimeError):
    """Raised when the model returns text that cannot be parsed into the expected schema."""


class LLMUnavailableError(RuntimeError):
    """Raised when all retries to the underlying Claude CLI fail."""


def _get_semaphore() -> asyncio.Semaphore:
    """Lazy semaphore — must be created inside a running event loop."""
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(_CONCURRENT_LLM_CALLS)
    return _SEMAPHORE


def _is_transient_sdk_error(exc: BaseException) -> bool:
    """Decide whether a failed call is worth retrying.

    Prefer typed SDK errors; fall back to string heuristics for the non-actionable
    "error result: success" the SDK surfaces when the CLI exits oddly.
    """
    if isinstance(exc, CLINotFoundError):
        return False  # CLI is missing/misconfigured — retrying won't help.
    if isinstance(exc, TimeoutError | ProcessError | CLIConnectionError | CLIJSONDecodeError):
        return True
    msg = str(exc)
    return (
        "error result: success" in msg
        or "Unknown error" in msg
        or "error result: unknown" in msg
    )


def _build_user_message(text: str, images: list[ImageInput]) -> dict[str, Any]:
    """Build a streaming-input user message with text + image content blocks.

    Images are sent as proper Anthropic content blocks (base64 source) so the
    model actually *sees* them. Passing a base64 data URL inside the text prompt
    does NOT work — the CLI never decodes it and the model hallucinates.
    """
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for media_type, data in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
        )
    return {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
    }


async def _consume_query(
    prompt: str | AsyncIterator[dict[str, Any]], options: ClaudeAgentOptions
) -> tuple[str, bool, dict[str, Any] | None]:
    """Drain a single-turn query into (concatenated_text, saw_thinking, usage_dict).

    `usage_dict` is the raw `ResultMessage.usage` dict (or None if the SDK didn't
    emit one) — used by the caller for observability only; never required.
    """
    chunks: list[str] = []
    saw_thinking = False
    usage: dict[str, Any] | None = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
                elif isinstance(block, ThinkingBlock):
                    saw_thinking = True
        elif isinstance(msg, ResultMessage):
            # Defensive: SDK shape may shift; never let observability break a call.
            try:
                if msg.usage is not None:
                    usage = msg.usage
            except (AttributeError, KeyError):
                usage = None
    return "".join(chunks).strip(), saw_thinking, usage


async def _collect_text(
    prompt: str,
    system_prompt: str,
    model: str | None = None,
    images: list[ImageInput] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> str:
    """Run a single-turn query and concatenate all assistant text blocks.

    Wrapped with a concurrency semaphore (to keep parallel CLI subprocesses
    sane) and a small retry loop for transient SDK errors. When `images` are
    given, the prompt is sent as a streaming user message with image content
    blocks (text-only callers keep the plain-string fast path). When
    `output_schema` is given (and PHARMA_AD_JSON_SCHEMA is on), the CLI is asked
    to constrain output to that JSON schema.
    """
    output_format = (
        {"type": "json_schema", "schema": output_schema}
        if output_schema and _JSON_SCHEMA_ENABLED
        else None
    )
    # SAFETY INVARIANT: permission_mode="bypassPermissions" skips ALL permission
    # prompts, so it is only safe because allowed_tools=[] makes the agent pure
    # text-in/text-out (it cannot run Bash, edit files, or hit the network). These
    # two MUST stay together — never grant tools here without also dropping
    # bypassPermissions, or untrusted creative text could drive tool calls.
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model or DEFAULT_MODEL,
        allowed_tools=[],
        max_turns=1,
        permission_mode="bypassPermissions",
        # None keeps the CLI default (thinking on); ThinkingConfigDisabled turns
        # it off. Controlled by PHARMA_AD_THINKING.
        thinking=_THINKING_CONFIG,
        output_format=output_format,
    )

    def _make_prompt() -> str | AsyncIterator[dict[str, Any]]:
        # Plain string for text-only calls; a fresh single-message stream when we
        # have images (the async iterable is consumed once, so rebuild per retry).
        if not images:
            return prompt

        async def _stream() -> AsyncIterator[dict[str, Any]]:
            yield _build_user_message(prompt, images)

        return _stream()

    resolved_model = model or DEFAULT_MODEL
    last_exc: BaseException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with _get_semaphore():
                # Semaphore wait is outside wait_for, so a queued call isn't charged
                # the timeout — only actual model time is.
                started = time.perf_counter()
                text, saw_thinking, usage = await asyncio.wait_for(
                    _consume_query(_make_prompt(), options), timeout=_LLM_TIMEOUT
                )
                elapsed = time.perf_counter() - started
                _log_call_observability(
                    resolved_model=resolved_model,
                    elapsed=elapsed,
                    saw_thinking=saw_thinking,
                    out_chars=len(text),
                    usage=usage,
                    has_system_prompt=bool(system_prompt),
                )
                return text
        except Exception as e:  # noqa: BLE001 — SDK raises bare Exception
            last_exc = e
            if attempt < _MAX_RETRIES and _is_transient_sdk_error(e):
                # Exponential backoff + small uniform jitter so a wave of 6
                # parallel retries doesn't lockstep into the CLI all at once.
                delay = _RETRY_INITIAL_DELAY * (2**attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "Transient Claude SDK error (attempt %s/%s): %s — retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    e,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            break

    raise LLMUnavailableError(
        f"Claude CLI failed after {_MAX_RETRIES + 1} attempt(s): {last_exc}"
    ) from last_exc


def _log_call_observability(
    *,
    resolved_model: str,
    elapsed: float,
    saw_thinking: bool,
    out_chars: int,
    usage: dict[str, Any] | None,
    has_system_prompt: bool,
) -> None:
    """Log per-call timing + token usage. Defensive — never raises."""
    global _PROMPT_CACHE_WARNED
    try:
        in_tokens = (usage or {}).get("input_tokens")
        out_tokens = (usage or {}).get("output_tokens")
        cache_read = (usage or {}).get("cache_read_input_tokens")
        cache_create = (usage or {}).get("cache_creation_input_tokens")
    except (AttributeError, KeyError):
        in_tokens = out_tokens = cache_read = cache_create = None

    # Promote to INFO when cache_read is observed so it stands out; otherwise
    # respect the existing PHARMA_AD_TIMING gate.
    cache_hit = isinstance(cache_read, int) and cache_read > 0
    if _TIMING or cache_hit:
        logger.info(
            "llm call: model=%s elapsed=%.1fs thinking=%s out_chars=%d "
            "in=%s out=%s cache_read=%s cache_create=%s",
            resolved_model,
            elapsed,
            saw_thinking,
            out_chars,
            in_tokens,
            out_tokens,
            cache_read,
            cache_create,
        )

    # One-shot hint when we have a non-empty system prompt but never see a cache
    # hit — likely means the static prompt isn't being cached (too short, or
    # changing on every call). Cheap signal, no auto-fix.
    if (
        not _PROMPT_CACHE_WARNED
        and has_system_prompt
        and isinstance(cache_read, int)
        and cache_read == 0
    ):
        logger.info(
            "no prompt caching observed; consider trimming static system prompt "
            "or checking PHARMA_AD_THINKING / model settings"
        )
        _PROMPT_CACHE_WARNED = True


def _extract_json(raw: str) -> str:
    """Pull the first JSON object/array out of free-form model output.

    Models sometimes wrap JSON in ```json ... ``` fences or prefix it with prose.
    We try fenced blocks first, then fall back to the first balanced { ... } / [ ... ].
    Raises LLMOutputError with a self-describing message if no JSON span is found —
    that's clearer than letting the caller chase a downstream JSONDecodeError.
    """
    fenced = _JSON_BLOCK_RE.search(raw)
    if fenced:
        return fenced.group(1)
    # Find first top-level JSON value.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(raw)):
            ch = raw[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return raw[start : i + 1]
    raise LLMOutputError(
        f"no JSON object or array found in model output: {raw[:200]!r}"
    )


async def run_json(
    *,
    prompt: str,
    system_prompt: str,
    schema: type[T],
    model: str | None = None,
    images: list[ImageInput] | None = None,
) -> T:
    """Call Claude, parse the response as JSON, validate against `schema`."""
    # Skip model_json_schema() unless the CLI's structured-output path is on —
    # otherwise the schema is computed only to be discarded inside _collect_text.
    output_schema = schema.model_json_schema() if _JSON_SCHEMA_ENABLED else None
    raw = await _collect_text(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        images=images,
        output_schema=output_schema,
    )
    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"Model returned invalid JSON:\n{raw[:500]}") from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMOutputError(f"Model JSON did not match {schema.__name__}: {e}\nRaw: {raw[:500]}") from e


async def run_text(
    *,
    prompt: str,
    system_prompt: str,
    model: str | None = None,
    images: list[ImageInput] | None = None,
) -> str:
    """Same as run_json but returns raw text — used by the editor agent."""
    return await _collect_text(
        prompt=prompt, system_prompt=system_prompt, model=model, images=images
    )
