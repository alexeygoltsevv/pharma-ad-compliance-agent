from __future__ import annotations

import pytest

from pharma_ad_compliance.agents.parser_agent import (
    ParserInputError,
    _check_file_size,
    _guess_media_type,
    _validate_public_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "http://[::1]/",
        "ftp://example.com/",  # non-http scheme
        "file:///etc/passwd",
    ],
)
def test_validate_public_url_blocks_unsafe(url: str):
    with pytest.raises(ParserInputError):
        _validate_public_url(url)


def test_validate_public_url_allows_public(monkeypatch):
    # Hermetic: stub DNS so the test doesn't depend on real network/resolution.
    import pharma_ad_compliance.agents.parser_agent as parser

    def fake_getaddrinfo(host, port, **kwargs):
        return [(0, 0, 0, "", ("93.184.216.34", port))]  # a public IP

    monkeypatch.setattr(parser.socket, "getaddrinfo", fake_getaddrinfo)
    _validate_public_url("https://example.com/landing")  # no raise


def test_check_file_size_rejects_oversized(tmp_path, monkeypatch):
    import pharma_ad_compliance.agents.parser_agent as parser

    monkeypatch.setattr(parser, "_MAX_FILE_BYTES", 10)
    big = tmp_path / "big.pdf"
    big.write_bytes(b"x" * 100)
    with pytest.raises(ParserInputError):
        _check_file_size(big, "PDF")


def test_check_file_size_accepts_small(tmp_path):
    small = tmp_path / "ok.png"
    small.write_bytes(b"x" * 10)
    _check_file_size(small, "Изображение")  # no raise


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("banner.jpg", "image/jpeg"),
        ("banner.jpeg", "image/jpeg"),
        ("banner.webp", "image/webp"),
        ("banner.gif", "image/gif"),
        ("banner.png", "image/png"),
        ("banner.unknown", "image/png"),  # default
    ],
)
def test_guess_media_type(name: str, expected: str):
    assert _guess_media_type(name) == expected
