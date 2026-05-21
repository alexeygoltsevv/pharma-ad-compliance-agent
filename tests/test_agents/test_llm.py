from __future__ import annotations

import base64
import json

from pharma_ad_compliance.agents._llm import _build_user_message, _extract_json


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
