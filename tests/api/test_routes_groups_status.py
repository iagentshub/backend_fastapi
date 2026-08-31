"""Tests de desactivar/reactivar groups y borrado en cascada de su contenido.

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


def _token(username: str, group_id: str | None = None) -> str:
    from app.auth.auth import create_token
    return create_token(username, group_id=group_id)


def _set_cookie(client: TestClient, username: str, group_id: str | None = None) -> None:
    client.cookies.set("ga_token", _token(username, group_id))


def _user_id(username: str) -> str:
    import asyncio

    from app.auth.auth import get_user_by_username
    user = asyncio.run(get_user_by_username(username))
    assert user is not None
    return str(user["id"])


_AGENT = {
    "name": "Agente de prueba",
    "system_prompt": "Eres un asistente de pruebas.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


# ── PATCH de estado ──────────────────────────────────────────────────────────

def test_set_status_requires_admin(client):
    _register("group_status_user_a")
    _set_cookie(client, "group_status_user_a")
    group = client.post("/api/groups", json={"name": "Grupo A"}).json()

    r = client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "disabled"})
    assert r.status_code == 403


def test_set_status_invalid_value_rejected(admin_client):
    group = admin_client.post("/api/groups", json={"name": "Grupo B"}).json()
    r = admin_client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "paused"})
    assert r.status_code == 422


def test_set_status_nonexistent_group_404(admin_client):
    r = admin_client.post("/api/admin/groups/no-existe/status", json={"status": "disabled"})
    assert r.status_code == 404


# ── Efecto de desactivar ──────────────────────────────────────────────────────

def test_member_cannot_switch_into_disabled_group(admin_client):
    _register("group_status_owner_c")
    _register("group_status_member_c")

    _set_cookie(admin_client, "group_status_owner_c")
    group = admin_client.post("/api/groups", json={"name": "Grupo Disable C"}).json()
    admin_client.post(f"/api/groups/{group['id']}/members",
                       json={"username": "group_status_member_c", "role": "member"})

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "disabled"})

    _set_cookie(admin_client, "group_status_member_c")
    r = admin_client.post(f"/api/groups/switch/{group['id']}")
    assert r.status_code == 403


def test_active_member_falls_back_to_personal_when_disabled(admin_client):
    """Si el group se desactiva mientras un miembro está activo en él, la
    siguiente request con ese token cae de vuelta a su espacio personal."""
    _register("group_status_owner_d")
    _set_cookie(admin_client, "group_status_owner_d")
    group = admin_client.post("/api/groups", json={"name": "Grupo Disable D"}).json()

    _set_cookie(admin_client, "group_status_owner_d", group_id=group["id"])
    r = admin_client.get("/api/auth/me")
    assert r.json()["group_id"] == group["id"]

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "disabled"})

    # Mismo usuario, mismo group_id en el token — require_group cae a personal
    _set_cookie(admin_client, "group_status_owner_d", group_id=group["id"])
    r2 = admin_client.get("/api/auth/me")
    assert r2.json()["group_id"] == _user_id("group_status_owner_d")


def test_reactivate_group_allogroup_switch_again(admin_client):
    _register("group_status_owner_e")
    _set_cookie(admin_client, "group_status_owner_e")
    group = admin_client.post("/api/groups", json={"name": "Grupo Reactivate E"}).json()

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "disabled"})

    _set_cookie(admin_client, "group_status_owner_e")
    r1 = admin_client.post(f"/api/groups/switch/{group['id']}")
    assert r1.status_code == 403

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "active"})

    _set_cookie(admin_client, "group_status_owner_e")
    r2 = admin_client.post(f"/api/groups/switch/{group['id']}")
    assert r2.status_code == 200


def test_admin_list_groups_includes_status(admin_client):
    group = admin_client.post("/api/groups", json={"name": "Grupo Status List"}).json()
    admin_client.post(f"/api/admin/groups/{group['id']}/status", json={"status": "disabled"})

    r = admin_client.get("/api/admin/groups")
    found = next(w for w in r.json() if w["id"] == group["id"])
    assert found["status"] == "disabled"


def test_shared_connection_blocked_when_source_group_disabled(admin_client):
    """Una conexión legacy de un group desactivado deja de verse en el destino."""
    _register("group_status_owner_f")
    _register("group_status_member_f")

    _set_cookie(admin_client, "group_status_owner_f")
    group_source = admin_client.post("/api/groups", json={"name": "Grupo Source F"}).json()
    group_target = admin_client.post("/api/groups", json={"name": "Grupo Target F"}).json()
    admin_client.post(f"/api/groups/{group_target['id']}/members",
                       json={"username": "group_status_member_f", "role": "member"})

    # Las conexiones nuevas ya no pueden pertenecer a un group. Insertamos una
    # conexión legacy para mantener cubierta la compatibilidad con datos
    # creados antes de ese cambio.
    import asyncio

    from app.storage.connection_storage import ConnectionStorage

    conn = asyncio.run(ConnectionStorage().save({
        "type": "openai", "label": "L", "name": "Conn F", "api_key": "sk-f", "model": "gpt-4o",
    }, owner_id=group_source["id"]))

    _set_cookie(admin_client, "group_status_owner_f", group_id=group_target["id"])
    r = admin_client.post(
        f"/api/sharing/connection/{conn['id']}",
        json={"group_id": group_target["id"]},
    )
    assert r.status_code == 200

    _set_cookie(admin_client, "group_status_member_f", group_id=group_target["id"])
    conns = admin_client.get("/api/v2/connections").json()["items"]
    assert any(c["id"] == conn["id"] for c in conns)

    _set_cookie(admin_client, "testadmin")
    admin_client.post(f"/api/admin/groups/{group_source['id']}/status", json={"status": "disabled"})

    _set_cookie(admin_client, "group_status_member_f", group_id=group_target["id"])
    conns_after = admin_client.get("/api/v2/connections").json()["items"]
    assert not any(c["id"] == conn["id"] for c in conns_after)


# ── Borrado en cascada ─────────────────────────────────────────────────────────

def test_delete_group_removes_owned_content(admin_client):
    _register("group_status_owner_g")
    _set_cookie(admin_client, "group_status_owner_g")
    group = admin_client.post("/api/groups", json={"name": "Grupo Delete G"}).json()

    _set_cookie(admin_client, "group_status_owner_g", group_id=group["id"])
    agent = admin_client.post("/api/agents", json=_AGENT).json()
    skill = admin_client.post("/api/skills/private", json={"name": "Skill G", "description": "d"}).json()
    know = admin_client.post("/api/knowledge/text", json={"title": "Doc G", "content": "c"}).json()
    conn = admin_client.post("/api/connections", json={
        "type": "openai", "label": "L", "name": "Conn G", "api_key": "sk-g", "model": "gpt-4o",
    }).json()
    assert agent["owner_id"] == group["id"]

    _set_cookie(admin_client, "testadmin")
    r = admin_client.delete(f"/api/admin/groups/{group['id']}")
    assert r.status_code == 200

    import asyncio

    from app.storage.db import open_db

    async def _counts():
        async with open_db() as conn_db:
            a = await conn_db.fetchval("SELECT COUNT(*) FROM agents WHERE owner_id = ?", (group["id"],))
            s = await conn_db.fetchval("SELECT COUNT(*) FROM skills WHERE owner_id = ?", (group["id"],))
            k = await conn_db.fetchval("SELECT COUNT(*) FROM knowledge_items WHERE owner_id = ?", (group["id"],))
            c = await conn_db.fetchval("SELECT COUNT(*) FROM connections WHERE owner_id = ?", (group["id"],))
            w = await conn_db.fetchval("SELECT COUNT(*) FROM groups WHERE id = ?", (group["id"],))
            return a, s, k, c, w

    a, s, k, c, w = asyncio.run(_counts())
    assert (a, s, k, c, w) == (0, 0, 0, 0, 0)
    assert agent["id"] and skill["id"] and know["id"] and conn["id"]  # ids creados arriba


def test_delete_group_does_not_touch_shared_original(admin_client):
    """Al borrar el group en el que un agente personal fue compartido, el original sigue intacto."""
    _register("group_status_h")

    _set_cookie(admin_client, "group_status_h")
    original = admin_client.post("/api/agents", json=_AGENT).json()
    group = admin_client.post("/api/groups", json={"name": "Grupo Linker H"}).json()

    _set_cookie(admin_client, "group_status_h", group_id=group["id"])
    r = admin_client.post(
        f"/api/sharing/agent/{original['id']}",
        json={"group_id": group["id"]},
    )
    assert r.status_code == 200

    _set_cookie(admin_client, "testadmin")
    admin_client.delete(f"/api/admin/groups/{group['id']}")

    _set_cookie(admin_client, "group_status_h")
    still_there = admin_client.get(f"/api/agents/{original['id']}").json()
    assert still_there["owner_id"] == _user_id("group_status_h")
