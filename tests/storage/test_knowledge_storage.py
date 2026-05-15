"""Tests de KnowledgeStorage: CRUD, filtrado por tipo, aislamiento por owner."""
from __future__ import annotations

import pytest

from app.storage.knowledge import KnowledgeStorage, extract_document_text


@pytest.fixture()
def storage(tmp_path):
    return KnowledgeStorage(tmp_path / "test_knowledge.db")


@pytest.fixture()
def storage_b(tmp_path):
    """Segundo storage sobre la misma DB para probar aislamiento por owner."""
    return KnowledgeStorage(tmp_path / "test_knowledge.db")


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

def test_list_empty(storage):
    assert storage.list("user1") == []


def test_list_returns_saved_items(storage):
    storage.save(**_URL_ITEM)
    storage.save(**_DOC_ITEM)
    items = storage.list("user1")
    assert len(items) == 2


def test_list_filter_by_type_url(storage):
    storage.save(**_URL_ITEM)
    storage.save(**_DOC_ITEM)
    urls = storage.list("user1", type="url")
    assert len(urls) == 1
    assert urls[0]["type"] == "url"


def test_list_filter_by_type_document(storage):
    storage.save(**_URL_ITEM)
    storage.save(**_DOC_ITEM)
    docs = storage.list("user1", type="document")
    assert len(docs) == 1
    assert docs[0]["source"] == "doc.txt"


def test_list_owner_isolation(storage):
    storage.save(**_URL_ITEM)
    storage.save(**{**_URL_ITEM, "owner_id": "user2"})
    assert len(storage.list("user1")) == 1
    assert len(storage.list("user2")) == 1


def test_list_admin_sees_all(storage):
    storage.save(**_URL_ITEM)
    storage.save(**{**_URL_ITEM, "owner_id": "user2"})
    # owner_id=None → sin filtro de propietario
    assert len(storage.list(None)) == 2


# ── save ──────────────────────────────────────────────────────────────────────

def test_save_returns_item_with_id(storage):
    item = storage.save(**_URL_ITEM)
    assert "id" in item
    assert item["title"] == "Ejemplo URL"
    assert item["char_count"] == len(_URL_ITEM["content"])


def test_save_sets_timestamps(storage):
    item = storage.save(**_URL_ITEM)
    assert item["created_at"]
    assert item["updated_at"]
    assert item["created_at"] == item["updated_at"]


def test_save_char_count_matches_content(storage):
    content = "a" * 500
    item = storage.save(**{**_URL_ITEM, "content": content})
    assert item["char_count"] == 500


# ── get ───────────────────────────────────────────────────────────────────────

def test_get_existing_item(storage):
    saved = storage.save(**_URL_ITEM)
    found = storage.get(saved["id"])
    assert found is not None
    assert found["id"] == saved["id"]
    assert found["content"] == _URL_ITEM["content"]


def test_get_nonexistent_returns_none(storage):
    assert storage.get("nonexistent-id") is None


def test_get_with_owner_isolation(storage):
    saved = storage.save(**_URL_ITEM)
    # owner correcto → encuentra
    assert storage.get(saved["id"], owner_id="user1") is not None
    # owner incorrecto → no encuentra
    assert storage.get(saved["id"], owner_id="user2") is None


def test_get_without_owner_finds_any(storage):
    saved = storage.save(**_URL_ITEM)
    assert storage.get(saved["id"], owner_id=None) is not None


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_existing_item(storage):
    saved = storage.save(**_URL_ITEM)
    assert storage.delete(saved["id"], owner_id="user1") is True
    assert storage.get(saved["id"]) is None


def test_delete_nonexistent_returns_false(storage):
    assert storage.delete("ghost-id", owner_id="user1") is False


def test_delete_wrong_owner_returns_false(storage):
    saved = storage.save(**_URL_ITEM)
    assert storage.delete(saved["id"], owner_id="user2") is False
    # item sigue existiendo
    assert storage.get(saved["id"]) is not None


def test_delete_admin_no_owner(storage):
    saved = storage.save(**_URL_ITEM)
    assert storage.delete(saved["id"], owner_id=None) is True
    assert storage.get(saved["id"]) is None


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
    # Simula pypdf ausente si no está instalado
    pypdf_available = importlib.util.find_spec("pypdf") is not None
    if pypdf_available:
        pytest.skip("pypdf instalado — no se puede simular ausencia")
    with pytest.raises(ValueError, match="pypdf"):
        extract_document_text(b"%PDF-1.4", "doc.pdf")
