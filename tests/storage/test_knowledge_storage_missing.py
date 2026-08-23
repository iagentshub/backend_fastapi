"""Cobertura de líneas faltantes en app/storage/knowledge.py.

Cubre:
- _TextParser (lines 26-48): __init__, handle_starttag, handle_endtag, handle_data, text()
- fetch_url_text() (lines 58-84): HTML, texto plano, charset, esquema inválido
- extract_document_text() — PDF con pypdf (lines 93-100)
- extract_document_text() — fallback errors=replace (line 107)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.config.security import resolve_safe_host
from app.storage.knowledge import (
    _download_safe_url,
    _TextParser,
    extract_document_text,
    fetch_url_text,
)

# ── _TextParser ────────────────────────────────────────────────────────────────


def test_textparser_init():
    p = _TextParser()
    assert p._parts == []
    assert p._skip == 0


def test_textparser_extracts_paragraph_text():
    p = _TextParser()
    p.feed("<p>Hola mundo</p>")
    assert "Hola" in p.text()
    assert "mundo" in p.text()


def test_textparser_skips_script_content():
    p = _TextParser()
    p.feed("<script>alert('xss')</script>")
    assert p.text().strip() == ""


def test_textparser_skips_style_content():
    p = _TextParser()
    p.feed("<style>body { color: red; }</style>")
    assert p.text().strip() == ""


def test_textparser_skips_nav_content():
    p = _TextParser()
    p.feed("<nav>Menu items</nav>visible text")
    assert "visible text" in p.text()
    assert "Menu" not in p.text()


def test_textparser_block_tags_add_newline():
    p = _TextParser()
    p.feed("<p>First</p><p>Second</p>")
    text = p.text()
    assert "First" in text
    assert "Second" in text


def test_textparser_nested_skip_decrements():
    """El contador _skip debe decrementarse correctamente al cerrar tags anidados."""
    p = _TextParser()
    p.feed("<script><style>inner</style></script>after")
    assert "after" in p.text()
    assert "inner" not in p.text()


def test_textparser_text_joins_parts():
    p = _TextParser()
    p.feed("Hello")
    p.feed(" World")
    assert "Hello" in p.text()
    assert "World" in p.text()


# ── fetch_url_text() ──────────────────────────────────────────────────────────


def test_fetch_url_text_invalid_scheme():
    with pytest.raises(ValueError, match="http/https"):
        fetch_url_text("ftp://example.com/file.txt")


def test_fetch_url_text_html_content():
    with patch(
        "app.storage.knowledge._download_safe_url",
        return_value=(b"<p>Contenido de prueba</p>", "text/html; charset=utf-8", False),
    ):
        result = fetch_url_text("https://example.com")
    assert "Contenido" in result


def test_fetch_url_text_plain_content():
    with patch(
        "app.storage.knowledge._download_safe_url",
        return_value=(b"Texto plano sin HTML", "text/plain; charset=utf-8", False),
    ):
        result = fetch_url_text("https://example.com/file.txt")
    assert "Texto plano" in result


def test_fetch_url_text_charset_extraction():
    """El charset del Content-Type debe usarse para decodificar."""
    body = "<p>Ñoño</p>".encode("latin-1")
    with patch(
        "app.storage.knowledge._download_safe_url",
        return_value=(body, "text/html; charset=latin-1", False),
    ):
        result = fetch_url_text("https://example.com")
    assert "Ñoño" in result


def test_fetch_url_text_no_charset_defaults_utf8():
    """Sin charset en Content-Type debe usarse utf-8 por defecto."""
    with patch(
        "app.storage.knowledge._download_safe_url",
        return_value=(b"<p>OK</p>", "text/html", False),
    ):
        result = fetch_url_text("https://example.com")
    assert "OK" in result


def test_resolve_safe_host_rejects_private_dns_result():
    answers = [(2, 1, 6, "", ("127.0.0.1", 80))]
    with patch("app.config.security.socket.getaddrinfo", return_value=answers):
        with pytest.raises(ValueError, match="privada"):
            resolve_safe_host("attacker.example", 80)


def test_download_connects_to_the_validated_ip():
    response = MagicMock(status=200)
    response.headers.get.return_value = "text/plain; charset=utf-8"
    response.read.return_value = b"seguro"
    connection = MagicMock()
    connection.getresponse.return_value = response
    with (
        patch("app.utils.safe_http.resolve_safe_host", return_value="93.184.216.34"),
        patch("app.utils.safe_http._PinnedHTTPConnection", return_value=connection) as cls,
    ):
        body, _content_type, truncated = _download_safe_url("http://example.com/data")
    cls.assert_called_once_with("example.com", "93.184.216.34", 80, 20)
    assert body == b"seguro"
    assert truncated is False


def test_download_revalidates_redirect_target():
    response = MagicMock(status=302)
    response.headers.get.side_effect = lambda name, default=None: (
        "http://127.0.0.1/admin" if name == "Location" else default
    )
    connection = MagicMock()
    connection.getresponse.return_value = response
    with (
        patch("app.utils.safe_http.resolve_safe_host", return_value="93.184.216.34"),
        patch("app.utils.safe_http._PinnedHTTPConnection", return_value=connection),
    ):
        with pytest.raises(ValueError, match="privada"):
            _download_safe_url("http://example.com/redirect")


# ── extract_document_text() ───────────────────────────────────────────────────


def test_extract_pdf_with_pypdf():
    """Extracción de PDF usando pypdf — mock de PdfReader."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Página uno del PDF"
    mock_reader_instance = MagicMock()
    mock_reader_instance.pages = [mock_page]
    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader_instance

    with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
        result = extract_document_text(b"%PDF-1.4 fake", "informe.pdf")

    assert "Página uno del PDF" in result


def test_extract_pdf_by_mime_type():
    """El parámetro mime=application/pdf también activa el parser PDF."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Contenido PDF"
    mock_reader_instance = MagicMock()
    mock_reader_instance.pages = [mock_page]
    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader_instance

    with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
        result = extract_document_text(
            b"binary data", "file.bin", mime="application/pdf"
        )

    assert "Contenido PDF" in result


def test_extract_document_fallback_errors_replace():
    """Si utf-8, latin-1 y cp1252 fallan, debe usarse errors='replace'."""

    class _UndecodableBytes(bytes):
        """Subclase de bytes que fuerza UnicodeDecodeError en modo strict."""

        def decode(self, encoding="utf-8", errors="strict"):
            if errors == "strict":
                raise UnicodeDecodeError(encoding, self, 0, 1, "forced")
            return super().decode("utf-8", errors=errors)

    bad = _UndecodableBytes(b"\xff\xfe data")
    result = extract_document_text(bad, "data.bin")
    assert isinstance(result, str)
