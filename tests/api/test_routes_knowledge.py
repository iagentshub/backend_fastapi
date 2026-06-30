"""Tests de API para /api/knowledge — items y carpetas de conocimiento."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.auth.auth import create_token, register_user


# ── Helpers ────────────────────────────────────────────────────────────────────

def _setup_user(username: str) -> None:
    asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))


def _auth_client(client, username: str):
    client.cookies.set("ga_token", create_token(username))
    return client


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def alice(client, patch_data_dir):
    _setup_user("alice")
    return _auth_client(client, "alice")


# ── Autenticación ──────────────────────────────────────────────────────────────

def test_list_items_requires_auth(client):
    r = client.get("/api/knowledge")
    assert r.status_code == 401


def test_list_folders_requires_auth(client):
    r = client.get("/api/knowledge/folders")
    assert r.status_code == 401


def test_add_text_requires_auth(client):
    r = client.post("/api/knowledge/text", json={"title": "T", "content": "C"})
    assert r.status_code == 401


# ── Items: GET /api/knowledge ──────────────────────────────────────────────────

def test_list_items_empty(alice):
    r = alice.get("/api/knowledge")
    assert r.status_code == 200
    assert r.json() == []


def test_list_items_after_add(alice):
    alice.post("/api/knowledge/text", json={"title": "Doc A", "content": "contenido A"})
    alice.post("/api/knowledge/text", json={"title": "Doc B", "content": "contenido B"})
    r = alice.get("/api/knowledge")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_items_filter_by_type(alice):
    alice.post("/api/knowledge/text", json={"title": "Texto", "content": "contenido de texto"})
    # URL item via mock
    with patch("app.api.routes.knowledge.fetch_url_text", return_value="contenido url"):
        alice.post("/api/knowledge/url", json={"url": "https://example.com", "title": "Web"})

    r_text = alice.get("/api/knowledge?type=text")
    assert r_text.status_code == 200
    items_text = r_text.json()
    assert all(i["type"] == "text" for i in items_text)
    assert len(items_text) == 1

    r_url = alice.get("/api/knowledge?type=url")
    assert r_url.status_code == 200
    items_url = r_url.json()
    assert all(i["type"] == "url" for i in items_url)
    assert len(items_url) == 1


def test_list_items_pagination(alice):
    for i in range(5):
        alice.post("/api/knowledge/text", json={"title": f"Doc {i}", "content": f"contenido {i}"})

    r = alice.get("/api/knowledge?limit=3&offset=0")
    assert r.status_code == 200
    assert len(r.json()) == 3

    r2 = alice.get("/api/knowledge?limit=3&offset=3")
    assert r2.status_code == 200
    assert len(r2.json()) == 2


# ── Items: POST /api/knowledge/text ───────────────────────────────────────────

def test_add_text_item(alice):
    r = alice.post(
        "/api/knowledge/text",
        json={"title": "Mi nota", "content": "Este es el contenido de prueba."},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "Mi nota"
    assert data["type"] == "text"
    assert data["char_count"] == len("Este es el contenido de prueba.")
    assert "id" in data


def test_add_text_item_missing_title(alice):
    r = alice.post("/api/knowledge/text", json={"title": "", "content": "Algo"})
    assert r.status_code == 422


def test_add_text_item_missing_content(alice):
    r = alice.post("/api/knowledge/text", json={"title": "Título", "content": ""})
    assert r.status_code == 422


def test_add_text_item_with_folder(alice):
    folder = alice.post(
        "/api/knowledge/folders",
        json={"section": "document", "name": "Mis docs"},
    ).json()
    r = alice.post(
        "/api/knowledge/text",
        json={"title": "En carpeta", "content": "contenido", "folder_id": folder["id"]},
    )
    assert r.status_code == 200
    assert r.json()["folder_id"] == folder["id"]


# ── Items: POST /api/knowledge/url ────────────────────────────────────────────

def test_add_url_item_mocked(alice):
    with patch("app.api.routes.knowledge.fetch_url_text", return_value="texto extraído de la web"):
        r = alice.post(
            "/api/knowledge/url",
            json={"url": "https://example.com", "title": "Ejemplo"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "url"
    assert data["title"] == "Ejemplo"
    assert data["source"] == "https://example.com"


def test_add_url_item_missing_url(alice):
    r = alice.post("/api/knowledge/url", json={"url": ""})
    assert r.status_code == 422


def test_add_url_item_fetch_error(alice):
    with patch(
        "app.api.routes.knowledge.fetch_url_text",
        side_effect=Exception("timeout"),
    ):
        r = alice.post("/api/knowledge/url", json={"url": "https://bad.example.com"})
    assert r.status_code == 422


# ── Items: DELETE /api/knowledge/{id} ─────────────────────────────────────────

def test_delete_item(alice):
    item = alice.post(
        "/api/knowledge/text", json={"title": "Borrar", "content": "contenido"}
    ).json()
    r = alice.delete(f"/api/knowledge/{item['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Ya no aparece en la lista
    items = alice.get("/api/knowledge").json()
    assert all(i["id"] != item["id"] for i in items)


def test_delete_item_not_found(alice):
    r = alice.delete("/api/knowledge/nonexistent-id")
    assert r.status_code == 404


# ── Items: PATCH /api/knowledge/{id} (mover) ──────────────────────────────────

def test_move_item_to_folder(alice):
    item = alice.post(
        "/api/knowledge/text", json={"title": "Mover", "content": "contenido"}
    ).json()
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Destino"}
    ).json()

    r = alice.patch(f"/api/knowledge/{item['id']}", json={"folder_id": folder["id"]})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_move_item_to_null_folder(alice):
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Origen"}
    ).json()
    item = alice.post(
        "/api/knowledge/text",
        json={"title": "Sacar", "content": "contenido", "folder_id": folder["id"]},
    ).json()

    r = alice.patch(f"/api/knowledge/{item['id']}", json={"folder_id": None})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_move_item_not_found(alice):
    r = alice.patch("/api/knowledge/nonexistent-id", json={"folder_id": None})
    assert r.status_code == 404


# ── Carpetas: GET /api/knowledge/folders ──────────────────────────────────────

def test_list_folders_empty(alice):
    r = alice.get("/api/knowledge/folders")
    assert r.status_code == 200
    assert r.json() == []


def test_list_folders_after_create(alice):
    alice.post("/api/knowledge/folders", json={"section": "document", "name": "Carpeta 1"})
    alice.post("/api/knowledge/folders", json={"section": "url", "name": "Carpeta 2"})
    r = alice.get("/api/knowledge/folders")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_folders_filter_by_section(alice):
    alice.post("/api/knowledge/folders", json={"section": "document", "name": "Docs"})
    alice.post("/api/knowledge/folders", json={"section": "url", "name": "URLs"})

    r = alice.get("/api/knowledge/folders?section=document")
    assert r.status_code == 200
    folders = r.json()
    assert len(folders) == 1
    assert folders[0]["section"] == "document"


# ── Carpetas: POST /api/knowledge/folders ─────────────────────────────────────

def test_create_folder(alice):
    r = alice.post(
        "/api/knowledge/folders",
        json={"section": "document", "name": "Nueva carpeta"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Nueva carpeta"
    assert data["section"] == "document"
    assert "id" in data


def test_create_folder_invalid_section(alice):
    r = alice.post(
        "/api/knowledge/folders",
        json={"section": "invalida", "name": "Carpeta"},
    )
    assert r.status_code == 422


def test_create_folder_empty_name(alice):
    r = alice.post(
        "/api/knowledge/folders",
        json={"section": "document", "name": "   "},
    )
    assert r.status_code == 422


def test_create_folder_valid_sections(alice):
    valid_sections = ["document", "url", "skill", "agents", "memory"]
    for section in valid_sections:
        r = alice.post(
            "/api/knowledge/folders",
            json={"section": section, "name": f"Carpeta {section}"},
        )
        assert r.status_code == 200, f"Sección '{section}' debería ser válida"


# ── Carpetas: PATCH /api/knowledge/folders/{id} ───────────────────────────────

def test_rename_folder(alice):
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Original"}
    ).json()

    r = alice.patch(f"/api/knowledge/folders/{folder['id']}", json={"name": "Renombrada"})
    assert r.status_code == 200
    assert r.json()["name"] == "Renombrada"


def test_rename_folder_empty_name(alice):
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Original"}
    ).json()

    r = alice.patch(f"/api/knowledge/folders/{folder['id']}", json={"name": "  "})
    assert r.status_code == 422


def test_rename_folder_not_found(alice):
    r = alice.patch("/api/knowledge/folders/nonexistent-id", json={"name": "Nombre"})
    assert r.status_code == 404


# ── Carpetas: DELETE /api/knowledge/folders/{id} ──────────────────────────────

def test_delete_folder(alice):
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Borrar"}
    ).json()

    r = alice.delete(f"/api/knowledge/folders/{folder['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    folders = alice.get("/api/knowledge/folders").json()
    assert all(f["id"] != folder["id"] for f in folders)


def test_delete_folder_not_found(alice):
    r = alice.delete("/api/knowledge/folders/nonexistent-id")
    assert r.status_code == 404


def test_delete_folder_without_cascade_preserves_items(alice):
    """Al borrar sin cascade, los items pierden folder_id pero no se eliminan."""
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Carpeta"}
    ).json()
    alice.post(
        "/api/knowledge/text",
        json={"title": "Item", "content": "contenido", "folder_id": folder["id"]},
    )

    r = alice.delete(f"/api/knowledge/folders/{folder['id']}?cascade=false")
    assert r.status_code == 200

    items = alice.get("/api/knowledge").json()
    assert len(items) == 1  # el item sigue existiendo


def test_delete_folder_with_cascade_removes_items(alice):
    """Al borrar con cascade=true, los items de la carpeta también se eliminan."""
    folder = alice.post(
        "/api/knowledge/folders", json={"section": "document", "name": "Carpeta cascade"}
    ).json()
    alice.post(
        "/api/knowledge/text",
        json={"title": "Item 1", "content": "contenido", "folder_id": folder["id"]},
    )
    alice.post(
        "/api/knowledge/text",
        json={"title": "Item 2", "content": "contenido", "folder_id": folder["id"]},
    )

    r = alice.delete(f"/api/knowledge/folders/{folder['id']}?cascade=true")
    assert r.status_code == 200

    items = alice.get("/api/knowledge").json()
    assert len(items) == 0


# ── Documento: POST /api/knowledge/document ───────────────────────────────────

def test_upload_txt_document(alice):
    content = b"Este es un documento de texto plano."
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("nota.txt", content, "text/plain")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "document"
    assert data["title"] == "nota.txt"


def test_upload_document_unsupported_format(alice):
    r = alice.post(
        "/api/knowledge/document",
        files={"file": ("imagen.png", b"\x89PNG", "image/png")},
    )
    assert r.status_code == 422


def test_upload_document_requires_auth(client):
    r = client.post(
        "/api/knowledge/document",
        files={"file": ("nota.txt", b"contenido", "text/plain")},
    )
    assert r.status_code == 401
