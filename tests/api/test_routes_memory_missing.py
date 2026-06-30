"""Tests de memoria — casos no cubiertos: patch, guest, move_folder."""
from __future__ import annotations


from app.auth.auth import create_token
from app.storage.guest import new_guest_id


# ── Helpers ────────────────────────────────────────────────────────────────────

def _guest_client(client):
    gid = new_guest_id()
    token = create_token(gid)
    client.cookies.set("ga_token", token)
    return client, gid


# ── PATCH (move_folder) — usuario registrado ───────────────────────────────────

def test_patch_memory_move_folder(admin_client):
    admin_client.post("/api/memory/mover.md", json={"content": "datos"})
    r = admin_client.patch("/api/memory/mover.md", json={"folder_id": "folder-abc"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_patch_memory_not_found(admin_client):
    r = admin_client.patch("/api/memory/fantasma.md", json={"folder_id": "folder-abc"})
    assert r.status_code == 404


def test_patch_memory_sin_auth(client):
    r = client.patch("/api/memory/algo.md", json={"folder_id": "x"})
    assert r.status_code == 401


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


# ── Guest — PATCH (devuelve ok sin hacer nada) ─────────────────────────────────

def test_guest_patch_memory_returns_ok(client):
    _guest_client(client)
    r = client.patch("/api/memory/cualquier.md", json={"folder_id": "xyz"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


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
