"""Tests de compartir un recurso con el workspace activo sin moverlo ni copiarlo.

Pensado sobre todo para conexiones (credenciales): el secreto nunca se duplica,
solo se concede acceso de uso al workspace en el que el dueño está trabajando.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


_CONN = {
    "type": "openai",
    "label": "Conn Compartida",
    "name": "Mi OpenAI",
    "api_key": "sk-test-key-share",
    "model": "gpt-4o",
}


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


def test_share_personal_connection_with_workspace_visible_to_member(client):
    _register("rws_owner_a")
    _register("rws_member_a")

    _set_cookie(client, "rws_owner_a")
    conn = client.post("/api/connections", json=_CONN).json()
    ws = client.post("/api/workspaces", json={"name": "Equipo Shares"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "rws_member_a", "role": "member"})

    _set_cookie(client, "rws_owner_a", workspace_id=ws["id"])
    r = client.post(f"/api/sharing/connection/{conn['id']}")
    assert r.status_code == 200

    _set_cookie(client, "rws_member_a", workspace_id=ws["id"])
    conns = client.get("/api/connections").json()
    assert any(c["id"] == conn["id"] for c in conns)

    # El original sigue siendo de rws_owner_a — no se duplicó el registro (mismo id,
    # sin crear una copia con otro owner_id)
    import asyncio
    from app.storage.db import open_db

    async def _owner_of(conn_id: str) -> str | None:
        async with open_db() as c:
            row = await c.fetchone("SELECT owner_id FROM connections WHERE id = ?", (conn_id,))
            return row[0] if row else None

    assert asyncio.run(_owner_of(conn["id"])) == "rws_owner_a"


def test_share_connection_without_ownership_forbidden(client):
    _register("rws_owner_b")
    _register("rws_bystander_b")

    _set_cookie(client, "rws_owner_b")
    conn = client.post("/api/connections", json=_CONN).json()

    _set_cookie(client, "rws_bystander_b")
    ws = client.post("/api/workspaces", json={"name": "Equipo Ajeno"}).json()

    _set_cookie(client, "rws_bystander_b", workspace_id=ws["id"])
    r = client.post(f"/api/sharing/connection/{conn['id']}")
    assert r.status_code == 403


def test_unshare_connection_revokes_access(client):
    _register("rws_owner_d")
    _register("rws_member_d")

    _set_cookie(client, "rws_owner_d")
    conn = client.post("/api/connections", json=_CONN).json()
    ws = client.post("/api/workspaces", json={"name": "Equipo Revoke"}).json()
    client.post(f"/api/workspaces/{ws['id']}/members",
                json={"username": "rws_member_d", "role": "member"})

    _set_cookie(client, "rws_owner_d", workspace_id=ws["id"])
    client.post(f"/api/sharing/connection/{conn['id']}")

    r = client.delete(f"/api/sharing/connection/{conn['id']}")
    assert r.status_code == 200

    _set_cookie(client, "rws_member_d", workspace_id=ws["id"])
    conns = client.get("/api/connections").json()
    assert not any(c["id"] == conn["id"] for c in conns)
