"""Tests de desactivar/reactivar workspaces y borrado en cascada de su contenido.

Nota: `admin_client` reutiliza la misma instancia de `client` (ver conftest.py),
así que un test que necesite alternar entre un usuario normal y el admin debe
usar `admin_client` como único cliente y cambiar la cookie con `_set_cookie`
antes de cada llamada — nunca pedir `client` y `admin_client` a la vez (son el
mismo objeto y se pisarían las cookies).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _register(username: str) -> None:
    import asyncio
    from app.auth.auth import register_user
    try:
        asyncio.run(register_user(username, "pass1234", email=f"{username}@test.com"))
    except ValueError:
        pass


def _token(username: str, workspace_id: str | None = None) -> str:
    from app.auth.auth import create_token
    return create_token(username, workspace_id=workspace_id)


def _set_cookie(client: TestClient, username: str, workspace_id: str | None = None) -> None:
    client.cookies.set("ga_token", _token(username, workspace_id))


_AGENT = {
    "name": "Agente de prueba",
    "system_prompt": "Eres un asistente de pruebas.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


# ── PATCH de estado ──────────────────────────────────────────────────────────

def test_set_status_requires_admin(client):
    _register("wss_user_a")
    _set_cookie(client, "wss_user_a")
    ws = client.post("/api/workspaces", json={"name": "WS A"}).json()

    r = client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "disabled"})
    assert r.status_code == 403


def test_set_status_invalid_value_rejected(admin_client):
    ws = admin_client.post("/api/workspaces", json={"name": "WS B"}).json()
    r = admin_client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "paused"})
    assert r.status_code == 422


def test_set_status_nonexistent_workspace_404(admin_client):
    r = admin_client.post("/api/admin/workspaces/no-existe/status", json={"status": "disabled"})
    assert r.status_code == 404


# ── Efecto de desactivar ──────────────────────────────────────────────────────

def test_member_cannot_switch_into_disabled_workspace(admin_client):
    _register("wss_owner_c")
    _register("wss_member_c")

    _set_cookie(admin_client, "wss_owner_c")
    ws = admin_client.post("/api/workspaces", json={"name": "WS Disable C"}).json()
    admin_client.post(f"/api/workspaces/{ws['id']}/members",
                       json={"username": "wss_member_c", "role": "member"})

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "disabled"})

    _set_cookie(admin_client, "wss_member_c")
    r = admin_client.post(f"/api/workspaces/switch/{ws['id']}")
    assert r.status_code == 403


def test_active_member_falls_back_to_personal_when_disabled(admin_client):
    """Si el workspace se desactiva mientras un miembro está activo en él, la
    siguiente request con ese token cae de vuelta a su espacio personal."""
    _register("wss_owner_d")
    _set_cookie(admin_client, "wss_owner_d")
    ws = admin_client.post("/api/workspaces", json={"name": "WS Disable D"}).json()

    _set_cookie(admin_client, "wss_owner_d", workspace_id=ws["id"])
    r = admin_client.get("/api/auth/me")
    assert r.json()["workspace_id"] == ws["id"]

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "disabled"})

    # Mismo usuario, mismo workspace_id en el token — require_workspace cae a personal
    _set_cookie(admin_client, "wss_owner_d", workspace_id=ws["id"])
    r2 = admin_client.get("/api/auth/me")
    assert r2.json()["workspace_id"] == "wss_owner_d"


def test_reactivate_workspace_allows_switch_again(admin_client):
    _register("wss_owner_e")
    _set_cookie(admin_client, "wss_owner_e")
    ws = admin_client.post("/api/workspaces", json={"name": "WS Reactivate E"}).json()

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "disabled"})

    _set_cookie(admin_client, "wss_owner_e")
    r1 = admin_client.post(f"/api/workspaces/switch/{ws['id']}")
    assert r1.status_code == 403

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "active"})

    _set_cookie(admin_client, "wss_owner_e")
    r2 = admin_client.post(f"/api/workspaces/switch/{ws['id']}")
    assert r2.status_code == 200


def test_admin_list_workspaces_includes_status(admin_client):
    ws = admin_client.post("/api/workspaces", json={"name": "WS Status List"}).json()
    admin_client.post(f"/api/admin/workspaces/{ws['id']}/status", json={"status": "disabled"})

    r = admin_client.get("/api/admin/workspaces")
    found = next(w for w in r.json() if w["id"] == ws["id"])
    assert found["status"] == "disabled"


def test_shared_connection_blocked_when_source_workspace_disabled(admin_client):
    """Una conexión compartida desde un workspace desactivado deja de verse en el destino."""
    _register("wss_owner_f")
    _register("wss_member_f")

    _set_cookie(admin_client, "wss_owner_f")
    ws_source = admin_client.post("/api/workspaces", json={"name": "WS Source F"}).json()
    ws_target = admin_client.post("/api/workspaces", json={"name": "WS Target F"}).json()
    admin_client.post(f"/api/workspaces/{ws_target['id']}/members",
                       json={"username": "wss_member_f", "role": "member"})

    _set_cookie(admin_client, "wss_owner_f", workspace_id=ws_source["id"])
    conn = admin_client.post("/api/connections", json={
        "type": "openai", "label": "L", "name": "Conn F", "api_key": "sk-f", "model": "gpt-4o",
    }).json()

    _set_cookie(admin_client, "wss_owner_f", workspace_id=ws_target["id"])
    r = admin_client.post(
        f"/api/sharing/connection/{conn['id']}",
        json={"group_id": ws_target["id"]},
    )
    assert r.status_code == 200

    _set_cookie(admin_client, "wss_member_f", workspace_id=ws_target["id"])
    conns = admin_client.get("/api/connections").json()
    assert any(c["id"] == conn["id"] for c in conns)

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/workspaces/{ws_source['id']}/status", json={"status": "disabled"})

    _set_cookie(admin_client, "wss_member_f", workspace_id=ws_target["id"])
    conns_after = admin_client.get("/api/connections").json()
    assert not any(c["id"] == conn["id"] for c in conns_after)


# ── Borrado en cascada ─────────────────────────────────────────────────────────

def test_delete_workspace_removes_owned_content(admin_client):
    _register("wss_owner_g")
    _set_cookie(admin_client, "wss_owner_g")
    ws = admin_client.post("/api/workspaces", json={"name": "WS Delete G"}).json()

    _set_cookie(admin_client, "wss_owner_g", workspace_id=ws["id"])
    agent = admin_client.post("/api/agents", json=_AGENT).json()
    skill = admin_client.post("/api/skills/private", json={"name": "Skill G", "description": "d"}).json()
    know = admin_client.post("/api/knowledge/text", json={"title": "Doc G", "content": "c"}).json()
    conn = admin_client.post("/api/connections", json={
        "type": "openai", "label": "L", "name": "Conn G", "api_key": "sk-g", "model": "gpt-4o",
    }).json()
    assert agent["owner_id"] == ws["id"]

    _set_cookie(admin_client, "testadmin")
    r = admin_client.delete(f"/api/admin/workspaces/{ws['id']}")
    assert r.status_code == 200

    import asyncio
    from app.storage.db import open_db

    async def _counts():
        async with open_db() as conn_db:
            a = await conn_db.fetchval("SELECT COUNT(*) FROM agents WHERE owner_id = ?", (ws["id"],))
            s = await conn_db.fetchval("SELECT COUNT(*) FROM skills WHERE owner_id = ?", (ws["id"],))
            k = await conn_db.fetchval("SELECT COUNT(*) FROM knowledge_items WHERE owner_id = ?", (ws["id"],))
            c = await conn_db.fetchval("SELECT COUNT(*) FROM connections WHERE owner_id = ?", (ws["id"],))
            w = await conn_db.fetchval("SELECT COUNT(*) FROM workspaces WHERE id = ?", (ws["id"],))
            return a, s, k, c, w

    a, s, k, c, w = asyncio.run(_counts())
    assert (a, s, k, c, w) == (0, 0, 0, 0, 0)
    assert agent["id"] and skill["id"] and know["id"] and conn["id"]  # ids creados arriba


def test_delete_workspace_does_not_touch_shared_original(admin_client):
    """Al borrar el workspace en el que un agente personal fue compartido, el original sigue intacto."""
    _register("wss_h")

    _set_cookie(admin_client, "wss_h")
    original = admin_client.post("/api/agents", json=_AGENT).json()
    ws = admin_client.post("/api/workspaces", json={"name": "WS Linker H"}).json()

    _set_cookie(admin_client, "wss_h", workspace_id=ws["id"])
    r = admin_client.post(
        f"/api/sharing/agent/{original['id']}",
        json={"group_id": ws["id"]},
    )
    assert r.status_code == 200

    _set_cookie(admin_client, "testadmin")
    admin_client.delete(f"/api/admin/workspaces/{ws['id']}")

    _set_cookie(admin_client, "wss_h")
    still_there = admin_client.get(f"/api/agents/{original['id']}").json()
    assert still_there["owner_id"] == "wss_h"
