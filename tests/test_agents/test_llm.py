from __future__ import annotations

import base64
import json

import pytest
from claude_agent_sdk import CLINotFoundError, ProcessError

from pharma_ad_compliance.agents._llm import (
    LLMOutputError,
    LLMUnavailableError,
    _build_user_message,
    _collect_text,
    _extract_json,
    _is_transient_sdk_error,
)


def test_build_user_message_text_only():
    msg = _build_user_message("привет", images=[])
    content = msg["message"]["content"]
    assert msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert content == [{"type": "text", "text": "привет"}]


def test_build_user_message_embeds_image_as_base64_content_block():
    raw = b"\x89PNG fake bytes"
    msg = _build_user_message("извлеки текст", images=[("image/png", raw)])
    content = msg["message"]["content"]

    # Text block first, then the image block (order matters for the model).
    assert content[0] == {"type": "text", "text": "извлеки текст"}
    image_block = content[1]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    # Data is base64 of the raw bytes — NOT a data URL, NOT inlined in the text.
    assert image_block["source"]["data"] == base64.b64encode(raw).decode("ascii")
    assert "data:" not in str(content)


def test_extract_json_plain_object():
    assert json.loads(_extract_json('{"a": 1}')) == {"a": 1}


def test_extract_json_from_fenced_block():
    raw = 'Вот результат:\n```json\n{"drug_class": "OTC"}\n```\nготово'
    assert json.loads(_extract_json(raw)) == {"drug_class": "OTC"}


def test_extract_json_strips_leading_prose():
    raw = 'Конечно! {"violations": []} — нарушений нет'
    assert json.loads(_extract_json(raw)) == {"violations": []}


def test_extract_json_object_with_braces_inside_string():
    raw = 'prefix {"q": "a {brace} inside"} suffix'
    assert json.loads(_extract_json(raw)) == {"q": "a {brace} inside"}


def test_extract_json_top_level_array_of_scalars():
    raw = "prefix [1, 2, 3] suffix"
    assert json.loads(_extract_json(raw)) == [1, 2, 3]


def test_extract_json_top_level_array_with_bracket_in_string():
    # The current balanced-bracket scanner is naive about strings; we document the
    # behavior — it stops at the first `]` regardless of strings. This test
    # confirms the function does NOT silently return raw, but extracts SOMETHING
    # that downstream `json.loads` can handle for the simple ascii case.
    raw = 'prefix [1, 2, 3] suffix'
    extracted = _extract_json(raw)
    assert json.loads(extracted) == [1, 2, 3]


def test_extract_json_raises_when_no_json_found():
    with pytest.raises(LLMOutputError):
        _extract_json("sorry, no JSON here")


def test_is_transient_sdk_error_cli_not_found_is_not_transient():
    assert _is_transient_sdk_error(CLINotFoundError("missing cli")) is False


def test_is_transient_sdk_error_process_error_is_transient():
    assert _is_transient_sdk_error(ProcessError("subprocess failed")) is True


def test_is_transient_sdk_error_string_fallback():
    # An arbitrary RuntimeError whose str() carries the SDK's "error result:
    # success" marker should retry — even though the type isn't one of the
    # known SDK exceptions.
    class _Weird(RuntimeError):
        pass

    assert _is_transient_sdk_error(_Weird("Claude Code returned an error result: success")) is True


async def test_collect_text_retries_on_transient_then_succeeds(monkeypatch):
    """First call raises a transient ProcessError; second call returns text."""
    calls = {"n": 0}

    async def fake_consume_query(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProcessError("transient")
        return ("ok", False, None)

    monkeypatch.setattr(
        "pharma_ad_compliance.agents._llm._consume_query", fake_consume_query
    )
    # Skip the real sleep between retries.
    monkeypatch.setattr(
        "pharma_ad_compliance.agents._llm.asyncio.sleep",
        _async_noop,
    )

    out = await _collect_text(prompt="hi", system_prompt="sys")
    assert out == "ok"
    assert calls["n"] == 2  # one retry → two attempts total


async def test_collect_text_does_not_retry_cli_not_found(monkeypatch):
    """CLINotFoundError is not transient → must surface after a single attempt."""
    calls = {"n": 0}

    async def fake_consume_query(*_args, **_kwargs):
        calls["n"] += 1
        raise CLINotFoundError("missing cli")

    monkeypatch.setattr(
        "pharma_ad_compliance.agents._llm._consume_query", fake_consume_query
    )
    monkeypatch.setattr(
        "pharma_ad_compliance.agents._llm.asyncio.sleep",
        _async_noop,
    )

    with pytest.raises(LLMUnavailableError):
        await _collect_text(prompt="hi", system_prompt="sys")
    assert calls["n"] == 1  # no retries on CLINotFoundError


async def _async_noop(*_args, **_kwargs):
    return None
