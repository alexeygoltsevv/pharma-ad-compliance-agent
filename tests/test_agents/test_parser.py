from __future__ import annotations

import httpx
import pymupdf
import pytest

from pharma_ad_compliance.agents.parser_agent import (
    ParserInputError,
    _check_file_size,
    _fetch_url,
    _guess_media_type,
    _parse_pdf,
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


def _patch_public_dns(monkeypatch) -> None:
    """Resolve example.com → public IP. Pass IP literals through (e.g. 127.0.0.1).

    This is host-aware so a redirect to http://127.0.0.1/ still hits the real
    `is_global` rejection path in `_validate_public_url` instead of being
    spoofed back to a public IP by an over-broad stub.
    """
    import ipaddress

    import pharma_ad_compliance.agents.parser_agent as parser

    def fake_getaddrinfo(host, port, **_kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Hostname — pretend it resolved to a public address.
            return [(0, 0, 0, "", ("93.184.216.34", port))]
        # IP literal — pass through unchanged so SSRF guard sees the real IP.
        return [(0, 0, 0, "", (host, port))]

    monkeypatch.setattr(parser.socket, "getaddrinfo", fake_getaddrinfo)


def _patch_httpx_with_mock_transport(monkeypatch, handler) -> None:
    """Replace httpx.AsyncClient in parser_agent with a wrapper using MockTransport.

    Also disables the post-hoc server_addr pin (MockTransport has no real socket).
    """
    import pharma_ad_compliance.agents.parser_agent as parser

    # The pin check requires a `network_stream` extension that MockTransport
    # doesn't provide. The check is already skipped when `_ALLOW_PRIVATE_URLS`
    # is true — but that ALSO short-circuits IP validation. To keep validation
    # on while skipping the pin, monkeypatch the resolver to return [] for the
    # post-hoc check via a separate path: set _ALLOW_PRIVATE_URLS for the pin
    # check only is not possible without touching code, so we let pinned_ips be
    # set normally and rely on `server_addr is None` (no network_stream) to skip
    # the pin assertion. Reading _fetch_url confirms: if server_addr is None it
    # bypasses the pin check.
    transport = httpx.MockTransport(handler)

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(parser.httpx, "AsyncClient", _client)


async def test_fetch_url_rejects_redirect_to_private_ip(monkeypatch):
    """A 302 to http://127.0.0.1/ must be rejected on the second-hop validation."""
    _patch_public_dns(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/leak"},
            )
        # If we ever reach the loopback target, this is a bug — fail loudly.
        raise AssertionError(f"Unexpected request to {request.url}")

    _patch_httpx_with_mock_transport(monkeypatch, handler)

    with pytest.raises(ParserInputError):
        await _fetch_url("https://example.com/landing")


async def test_fetch_url_rejects_non_text_content_type(monkeypatch):
    """200 + Content-Type: application/octet-stream must be refused."""
    _patch_public_dns(monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00\x01\x02\x03",
            headers={"content-type": "application/octet-stream"},
        )

    _patch_httpx_with_mock_transport(monkeypatch, handler)

    with pytest.raises(ParserInputError):
        await _fetch_url("https://example.com/binary")


async def test_pdf_page_geometry_cap_skips_oversize_page(tmp_path, monkeypatch):
    """A page with an absurd MediaBox (~200x200 inch) is not rasterized."""
    # Stub OCR so a regression that DOES try to call it surfaces — assertion below
    # checks the call counter.
    ocr_calls = {"n": 0}

    async def fake_ocr(*_args, **_kwargs):
        ocr_calls["n"] += 1
        return "should not be called"

    monkeypatch.setattr(
        "pharma_ad_compliance.agents.parser_agent._ocr_image_bytes", fake_ocr
    )

    pdf_path = tmp_path / "huge.pdf"
    # 14400 PDF units = 200 inches (PDF unit = 1/72 inch).
    # 200in * 200dpi = 40000px; 40000^2 = 1.6e9 px ≈ 1600 MP ≫ 50 MP cap.
    doc = pymupdf.open()
    doc.new_page(width=14400, height=14400)
    doc.save(pdf_path)
    doc.close()

    text, meta = await _parse_pdf(pdf_path)
    # No text was extracted (page had no text layer + oversize geometry rejected).
    assert text == ""
    assert meta["ocr_skipped_pages"] == "1"
    assert "ocr_pages" not in meta
    assert ocr_calls["n"] == 0
