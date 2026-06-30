"""Cobertura de líneas faltantes en app/storage/knowledge.py.

Cubre:
- _TextParser (lines 26-48): __init__, handle_starttag, handle_endtag, handle_data, text()
- fetch_url_text() (lines 58-84): HTML, texto plano, charset, esquema inválido
- extract_document_text() — PDF con pypdf (lines 93-100)
- extract_document_text() — fallback errors=replace (line 107)
- FolderStorage.get() con owner_id (line 210)
- FolderStorage CRUD completo
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from app.storage.knowledge import (
    FolderStorage,
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

def _mock_urlopen_resp(body: bytes, content_type: str = "text/html; charset=utf-8"):
    resp = MagicMock()
    resp.headers = MagicMock()
    resp.headers.get = MagicMock(return_value=content_type)
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_url_text_invalid_scheme():
    with pytest.raises(ValueError, match="http/https"):
        fetch_url_text("ftp://example.com/file.txt")


def test_fetch_url_text_html_content():
    resp = _mock_urlopen_resp(b"<p>Contenido de prueba</p>")
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_text("https://example.com")
    assert "Contenido" in result


def test_fetch_url_text_plain_content():
    resp = _mock_urlopen_resp(b"Texto plano sin HTML", "text/plain; charset=utf-8")
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_text("https://example.com/file.txt")
    assert "Texto plano" in result


def test_fetch_url_text_charset_extraction():
    """El charset del Content-Type debe usarse para decodificar."""
    body = "<p>Ñoño</p>".encode("latin-1")
    resp = _mock_urlopen_resp(body, "text/html; charset=latin-1")
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_text("https://example.com")
    assert "Ñoño" in result


def test_fetch_url_text_no_charset_defaults_utf8():
    """Sin charset en Content-Type debe usarse utf-8 por defecto."""
    resp = _mock_urlopen_resp(b"<p>OK</p>", "text/html")
    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_text("https://example.com")
    assert "OK" in result


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
        result = extract_document_text(b"binary data", "file.bin", mime="application/pdf")

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


# ── FolderStorage ─────────────────────────────────────────────────────────────

@pytest.fixture()
async def fs(patch_data_dir):
    from app.config.data import DB_FILE
    return FolderStorage(DB_FILE)


async def test_folder_list_empty(fs):
    folders = await fs.list("user1")
    assert folders == []


async def test_folder_create_returns_dict(fs):
    folder = await fs.create("user1", "docs", "Mi carpeta")
    assert "id" in folder
    assert folder["name"] == "Mi carpeta"
    assert folder["owner_id"] == "user1"
    assert folder["section"] == "docs"


async def test_folder_list_returns_created(fs):
    await fs.create("user1", "docs", "Carpeta A")
    await fs.create("user1", "docs", "Carpeta B")
    folders = await fs.list("user1")
    assert len(folders) == 2


async def test_folder_list_filter_by_section(fs):
    await fs.create("user1", "docs", "Doc Folder")
    await fs.create("user1", "urls", "URL Folder")
    docs = await fs.list("user1", section="docs")
    assert len(docs) == 1
    assert docs[0]["section"] == "docs"


async def test_folder_get_with_owner_id(fs):
    """Cubre line 210: fetchone con owner_id en FolderStorage.get()."""
    folder = await fs.create("user1", "docs", "Carpeta con owner")
    found = await fs.get(folder["id"], owner_id="user1")
    assert found is not None
    assert found["id"] == folder["id"]


async def test_folder_get_with_owner_id_wrong_owner(fs):
    """Buscar con owner incorrecto debe devolver None."""
    folder = await fs.create("user1", "docs", "Carpeta")
    result = await fs.get(folder["id"], owner_id="user2")
    assert result is None


async def test_folder_get_without_owner_id(fs):
    """Sin owner_id se busca solo por id (else branch, line 215)."""
    folder = await fs.create("user1", "docs", "Carpeta sin owner")
    found = await fs.get(folder["id"])
    assert found is not None
    assert found["name"] == "Carpeta sin owner"


async def test_folder_get_nonexistent(fs):
    result = await fs.get("nonexistent-id")
    assert result is None


async def test_folder_rename(fs):
    folder = await fs.create("user1", "docs", "Nombre viejo")
    updated = await fs.rename(folder["id"], "user1", "Nombre nuevo")
    assert updated is not None
    assert updated["name"] == "Nombre nuevo"


async def test_folder_rename_not_found(fs):
    result = await fs.rename("ghost-id", "user1", "Nuevo")
    assert result is None


async def test_folder_delete(fs):
    folder = await fs.create("user1", "docs", "Para borrar")
    ok = await fs.delete(folder["id"], "user1")
    assert ok is True
    assert await fs.get(folder["id"]) is None


async def test_folder_delete_not_found(fs):
    ok = await fs.delete("nonexistent", "user1")
    assert ok is False


async def test_folder_delete_cascade_removes_items(fs, patch_data_dir):
    """cascade=True debe eliminar los knowledge_items dentro de la carpeta."""
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    ks = KnowledgeStorage(DB_FILE)
    folder = await fs.create("user1", "docs", "Carpeta cascade")
    item = await ks.save(
        type="document",
        title="Doc en carpeta",
        source="doc.txt",
        content="contenido",
        owner_id="user1",
        folder_id=folder["id"],
    )
    ok = await fs.delete(folder["id"], "user1", cascade=True)
    assert ok is True
    assert await ks.get(item["id"]) is None


async def test_folder_delete_no_cascade_nullifies_folder_id(fs, patch_data_dir):
    """cascade=False debe poner folder_id=NULL en los items, no borrarlos."""
    from app.config.data import DB_FILE
    from app.storage.knowledge import KnowledgeStorage

    ks = KnowledgeStorage(DB_FILE)
    folder = await fs.create("user1", "docs", "Carpeta no-cascade")
    item = await ks.save(
        type="document",
        title="Doc sin cascade",
        source="doc.txt",
        content="texto",
        owner_id="user1",
        folder_id=folder["id"],
    )
    ok = await fs.delete(folder["id"], "user1", cascade=False)
    assert ok is True
    retrieved = await ks.get(item["id"])
    assert retrieved is not None
    assert retrieved["folder_id"] is None
