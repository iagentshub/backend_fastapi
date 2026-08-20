"""Tests de memoria — casos no cubiertos: patch, guest, move_folder."""
from __future__ import annotations

import asyncio

from app.auth.auth import create_token
from app.storage.guest import create_guest_user

# ── Helpers ────────────────────────────────────────────────────────────────────

def _guest_client(client):
    """Invitado con fila en la BD y cookie puesta.

    Antes bastaba con firmar un token para un id inventado: no había fila que
    crear. Desde que el invitado es un usuario efímero, un token sin fila es una
    credencial de alguien que no existe, y la respuesta correcta es 401.
    """
    gid = asyncio.run(create_guest_user())
    client.cookies.set("ga_token", create_token(gid))
    return client, gid


# ── Guest — list ───────────────────────────────────────────────────────────────

def test_guest_list_memory_vacio(client):
    _guest_client(client)
    r = client.get("/api/memory")
    assert r.status_code == 200
    assert r.json() == []


def test_guest_list_memory_con_datos(client):
    _guest_client(client)
    client.post("/api/memory/g1.md", json={"content": "hola"})
    r = client.get("/api/memory")
    assert r.status_code == 200
    items = r.json()
    assert any(i["filename"] == "g1.md" for i in items)


# ── Guest — GET ────────────────────────────────────────────────────────────────

def test_guest_get_memory(client):
    _guest_client(client)
    client.post("/api/memory/g2.md", json={"content": "contenido"})
    r = client.get("/api/memory/g2.md")
    assert r.status_code == 200
    assert r.json()["content"] == "contenido"


def test_guest_get_memory_not_found(client):
    _guest_client(client)
    r = client.get("/api/memory/noexiste.md")
    assert r.status_code == 404


# ── Guest — POST (save) ────────────────────────────────────────────────────────

def test_guest_save_memory(client):
    _guest_client(client)
    r = client.post("/api/memory/g3.md", json={"content": "guardado"})
    assert r.status_code == 200
    assert r.json()["filename"] == "g3.md"


def test_guest_save_memory_empty_content(client):
    _guest_client(client)
    r = client.post("/api/memory/g4.md", json={"content": ""})
    assert r.status_code == 200


# ── Guest — DELETE ─────────────────────────────────────────────────────────────

def test_guest_delete_memory(client):
    _guest_client(client)
    client.post("/api/memory/g5.md", json={"content": "temp"})
    r = client.delete("/api/memory/g5.md")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_guest_delete_memory_not_found(client):
    _guest_client(client)
    r = client.delete("/api/memory/noexiste.md")
    assert r.status_code == 404
