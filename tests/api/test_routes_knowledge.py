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
    alice.post(
        "/api/knowledge/text", json={"title": "Texto", "content": "contenido de texto"}
    )
    # URL item via mock
    with patch("app.api.routes.knowledge.fetch_url_text", return_value="contenido url"):
        alice.post(
            "/api/knowledge/url", json={"url": "https://example.com", "title": "Web"}
        )

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
        alice.post(
            "/api/knowledge/text",
            json={"title": f"Doc {i}", "content": f"contenido {i}"},
        )

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


# ── Items: POST /api/knowledge/url ────────────────────────────────────────────


def test_add_url_item_mocked(alice):
    with patch(
        "app.api.routes.knowledge.fetch_url_text",
        return_value="texto extraído de la web",
    ):
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


def test_folder_crud_assignment_and_detach(alice):
    created = alice.post(
        "/api/knowledge/folders",
        json={"section": "url", "name": "Fuentes"},
    )
    assert created.status_code == 200
    folder = created.json()

    with patch("app.api.routes.knowledge.fetch_url_text", return_value="contenido"):
        item = alice.post(
            "/api/knowledge/url",
            json={"url": "https://example.com/folder", "title": "Fuente"},
        ).json()

    moved = alice.patch(
        f"/api/knowledge/{item['id']}",
        json={"folder_id": folder["id"]},
    )
    assert moved.status_code == 200
    assert moved.json()["folder_id"] == folder["id"]

    listed = alice.get("/api/knowledge/folders", params={"section": "url"})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [folder["id"]]

    renamed = alice.patch(
        f"/api/knowledge/folders/{folder['id']}",
        json={"name": "Referencias"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Referencias"

    visibility = alice.put(
        f"/api/knowledge/folders/{folder['id']}/visibility",
        json={"visibility": "private"},
    )
    assert visibility.status_code == 200
    assert visibility.json()["visibility"] == "private"

    deleted = alice.delete(
        f"/api/knowledge/folders/{folder['id']}",
        params={"cascade": False},
    )
    assert deleted.status_code == 200
    refreshed = alice.get("/api/knowledge").json()
    assert next(row for row in refreshed if row["id"] == item["id"])["folder_id"] is None


def test_folders_are_isolated_by_owner(alice, client):
    folder = alice.post(
        "/api/knowledge/folders",
        json={"section": "document", "name": "Privada"},
    ).json()
    _setup_user("folder_bob")
    _auth_client(client, "folder_bob")

    assert client.get(
        "/api/knowledge/folders", params={"section": "document"}
    ).json() == []
    assert client.patch(
        f"/api/knowledge/folders/{folder['id']}",
        json={"name": "Intrusión"},
    ).status_code == 404


def test_folder_cascade_deletes_its_content(alice):
    folder = alice.post(
        "/api/knowledge/folders",
        json={"section": "url", "name": "Temporal"},
    ).json()
    with patch("app.api.routes.knowledge.fetch_url_text", return_value="contenido"):
        item = alice.post(
            "/api/knowledge/url",
            json={"url": "https://example.com/delete", "title": "Eliminar"},
        ).json()
    alice.patch(
        f"/api/knowledge/{item['id']}",
        json={"folder_id": folder["id"]},
    )

    deleted = alice.delete(
        f"/api/knowledge/folders/{folder['id']}",
        params={"cascade": True},
    )
    assert deleted.status_code == 200
    assert all(row["id"] != item["id"] for row in alice.get("/api/knowledge").json())
