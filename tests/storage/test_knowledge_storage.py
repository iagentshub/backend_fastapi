"""Tests de KnowledgeStorage: CRUD, filtrado por tipo, aislamiento por owner."""
from __future__ import annotations

import pytest

from app.storage.knowledge import KnowledgeStorage, extract_document_text


@pytest.fixture()
async def storage(patch_data_dir):
    return KnowledgeStorage()


_URL_ITEM = dict(
    type="url",
    title="Ejemplo URL",
    source="https://example.com",
    content="Contenido de prueba para una URL.",
    owner_id="user1",
)

_DOC_ITEM = dict(
    type="document",
    title="Mi Documento",
    source="doc.txt",
    content="Texto plano del documento.",
    owner_id="user1",
)


# ── list ──────────────────────────────────────────────────────────────────────

async def test_list_empty(storage):
    assert await storage.list("user1") == []


async def test_list_returns_saved_items(storage):
    await storage.save(**_URL_ITEM)
    await storage.save(**_DOC_ITEM)
    items = await storage.list("user1")
    assert len(items) == 2


async def test_list_filter_by_type_url(storage):
    await storage.save(**_URL_ITEM)
    await storage.save(**_DOC_ITEM)
    urls = await storage.list("user1", type="url")
    assert len(urls) == 1
    assert urls[0]["type"] == "url"


async def test_list_filter_by_type_document(storage):
    await storage.save(**_URL_ITEM)
    await storage.save(**_DOC_ITEM)
    docs = await storage.list("user1", type="document")
    assert len(docs) == 1
    assert docs[0]["source"] == "doc.txt"


async def test_list_owner_isolation(storage):
    await storage.save(**_URL_ITEM)
    await storage.save(**{**_URL_ITEM, "owner_id": "user2"})
    assert len(await storage.list("user1")) == 1
    assert len(await storage.list("user2")) == 1


async def test_list_admin_sees_all(storage):
    await storage.save(**_URL_ITEM)
    await storage.save(**{**_URL_ITEM, "owner_id": "user2"})
    assert len(await storage.list(None)) == 2


# ── save ──────────────────────────────────────────────────────────────────────

async def test_save_returns_item_with_id(storage):
    item = await storage.save(**_URL_ITEM)
    assert "id" in item
    assert item["title"] == "Ejemplo URL"
    assert item["char_count"] == len(_URL_ITEM["content"])


async def test_save_sets_timestamps(storage):
    item = await storage.save(**_URL_ITEM)
    assert item["created_at"]
    assert item["updated_at"]
    assert item["created_at"] == item["updated_at"]


async def test_save_char_count_matches_content(storage):
    content = "a" * 500
    item = await storage.save(**{**_URL_ITEM, "content": content})
    assert item["char_count"] == 500


# ── get ───────────────────────────────────────────────────────────────────────

async def test_get_existing_item(storage):
    saved = await storage.save(**_URL_ITEM)
    found = await storage.get(saved["id"])
    assert found is not None
    assert found["id"] == saved["id"]
    assert found["content"] == _URL_ITEM["content"]


async def test_get_nonexistent_returns_none(storage):
    assert await storage.get("nonexistent-id") is None


async def test_get_with_owner_isolation(storage):
    saved = await storage.save(**_URL_ITEM)
    assert await storage.get(saved["id"], owner_id="user1") is not None
    assert await storage.get(saved["id"], owner_id="user2") is None


async def test_get_without_owner_finds_any(storage):
    saved = await storage.save(**_URL_ITEM)
    assert await storage.get(saved["id"], owner_id=None) is not None


# ── delete ────────────────────────────────────────────────────────────────────

async def test_delete_existing_item(storage):
    saved = await storage.save(**_URL_ITEM)
    assert await storage.delete(saved["id"], owner_id="user1") is True
    assert await storage.get(saved["id"]) is None


async def test_delete_nonexistent_returns_false(storage):
    assert await storage.delete("ghost-id", owner_id="user1") is False


async def test_delete_wrong_owner_returns_false(storage):
    saved = await storage.save(**_URL_ITEM)
    assert await storage.delete(saved["id"], owner_id="user2") is False
    assert await storage.get(saved["id"]) is not None


async def test_delete_admin_no_owner(storage):
    saved = await storage.save(**_URL_ITEM)
    assert await storage.delete(saved["id"], owner_id=None) is True
    assert await storage.get(saved["id"]) is None


# ── extract_document_text ─────────────────────────────────────────────────────

def test_extract_txt_utf8():
    text = extract_document_text(b"Hola mundo", "file.txt")
    assert text == "Hola mundo"


def test_extract_md_content():
    md = b"# Titulo\n\nParrafo de prueba."
    text = extract_document_text(md, "readme.md")
    assert "Titulo" in text
    assert "Parrafo" in text


def test_extract_txt_latin1():
    content = "Ñoño".encode("latin-1")
    text = extract_document_text(content, "file.txt", mime="text/plain")
    assert "Ñoño" in text


def test_extract_pdf_missing_dep():
    """Si pypdf no está instalado, debe lanzar ValueError descriptivo."""
    import importlib
    pypdf_available = importlib.util.find_spec("pypdf") is not None
    if pypdf_available:
        pytest.skip("pypdf instalado — no se puede simular ausencia")
    with pytest.raises(ValueError, match="pypdf"):
        extract_document_text(b"%PDF-1.4", "doc.pdf")
