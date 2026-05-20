"""Thin wrapper around `claude-agent-sdk` for structured-output LLM calls.

Auth is delegated to the local `claude` CLI (Claude Code subscription) — no API
key is required. Each call runs in `permission_mode='bypassPermissions'` with an
empty `allowed_tools` list, so the agent is pure text-in / text-out and never
touches the filesystem or the network on its own.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import TypeVar

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = os.environ.get("PHARMA_AD_MODEL", "claude-sonnet-4-6")

# Cap concurrent CLI subprocesses. Running 8 `claude` subprocesses at once
# (8 agents × asyncio.gather) overwhelms the local CLI and sometimes returns
# a flaky "error result: success" response. 3 is a safe sweet spot — still
# meaningfully parallel but each call has space to complete cleanly.
_CONCURRENT_LLM_CALLS = int(os.environ.get("PHARMA_AD_MAX_CONCURRENCY", "3"))
_SEMAPHORE: asyncio.Semaphore | None = None

# How many times to retry an LLM call before giving up. Targets transient
# "Claude Code returned an error result: success" cases the SDK surfaces when
# the CLI exits non-zero without a structured error.
_MAX_RETRIES = 2
_RETRY_INITIAL_DELAY = 1.5  # seconds; doubles on each retry

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


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
    """Heuristic: SDK occasionally surfaces a non-actionable error from the CLI."""
    msg = str(exc)
    return (
        "error result: success" in msg
        or "Unknown error" in msg
        or "error result: unknown" in msg
    )


async def _collect_text(prompt: str, system_prompt: str, model: str | None = None) -> str:
    """Run a single-turn query and concatenate all assistant text blocks.

    Wrapped with a concurrency semaphore (to keep parallel CLI subprocesses
    sane) and a small retry loop for transient SDK errors.
    """
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model or DEFAULT_MODEL,
        allowed_tools=[],
        max_turns=1,
        permission_mode="bypassPermissions",
    )

    last_exc: BaseException | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with _get_semaphore():
                chunks: list[str] = []
                async for msg in query(prompt=prompt, options=options):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                chunks.append(block.text)
                return "".join(chunks).strip()
        except Exception as e:  # noqa: BLE001 — SDK raises bare Exception
            last_exc = e
            if attempt < _MAX_RETRIES and _is_transient_sdk_error(e):
                delay = _RETRY_INITIAL_DELAY * (2**attempt)
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


def _extract_json(raw: str) -> str:
    """Pull the first JSON object/array out of free-form model output.

    Models sometimes wrap JSON in ```json ... ``` fences or prefix it with prose.
    We try fenced blocks first, then fall back to the first balanced { ... } / [ ... ].
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
    return raw  # Let json.loads fail with a useful error.


async def run_json(
    *,
    prompt: str,
    system_prompt: str,
    schema: type[T],
    model: str | None = None,
) -> T:
    """Call Claude, parse the response as JSON, validate against `schema`."""
    raw = await _collect_text(prompt=prompt, system_prompt=system_prompt, model=model)
    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"Model returned invalid JSON:\n{raw[:500]}") from e
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMOutputError(f"Model JSON did not match {schema.__name__}: {e}\nRaw: {raw[:500]}") from e


async def run_text(*, prompt: str, system_prompt: str, model: str | None = None) -> str:
    """Same as run_json but returns raw text — used by the editor agent."""
    return await _collect_text(prompt=prompt, system_prompt=system_prompt, model=model)
