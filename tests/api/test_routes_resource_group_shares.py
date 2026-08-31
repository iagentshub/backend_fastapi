"""Tests de compartir un recurso con el group activo sin moverlo ni copiarlo.

Pensado sobre todo para conexiones (credenciales): el secreto nunca se duplica,
solo se concede acceso de uso al group en el que el dueño está trabajando.
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


def _token(username: str, group_id: str | None = None) -> str:
    from app.auth.auth import create_token
    return create_token(username, group_id=group_id)


def _set_cookie(client: TestClient, username: str, group_id: str | None = None) -> None:
    client.cookies.set("ga_token", _token(username, group_id))


def test_share_personal_connection_with_group_visible_to_member(client):
    _register("rgroup_owner_a")
    _register("rgroup_member_a")

    _set_cookie(client, "rgroup_owner_a")
    conn = client.post("/api/connections", json=_CONN).json()
    group = client.post("/api/groups", json={"name": "Equipo Shares"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "rgroup_member_a", "role": "member"})

    _set_cookie(client, "rgroup_owner_a", group_id=group["id"])
    r = client.post(f"/api/sharing/connection/{conn['id']}")
    assert r.status_code == 200
    own = next(c for c in client.get("/api/v2/connections").json()["items"] if c["id"] == conn["id"])
    assert own["origin_type"] == "owner"

    _set_cookie(client, "rgroup_member_a", group_id=group["id"])
    conns = client.get("/api/v2/connections").json()["items"]
    assert any(c["id"] == conn["id"] for c in conns)
    shared = next(c for c in conns if c["id"] == conn["id"])
    assert shared["origin_type"] == "linked"

    # El original sigue siendo de rgroup_owner_a — no se duplicó el registro (mismo id,
    # sin crear una copia con otro owner_id)
    import asyncio

    from app.storage.db import open_db

    async def _owner_of(conn_id: str) -> str | None:
        async with open_db() as c:
            row = await c.fetchone("SELECT owner_id FROM connections WHERE id = ?", (conn_id,))
            return row[0] if row else None

    from app.auth.auth import get_user_by_username
    assert asyncio.run(_owner_of(conn["id"])) == asyncio.run(
        get_user_by_username("rgroup_owner_a")
    )["id"]


def test_shared_connection_only_appears_and_works_in_recipient_group(client):
    _register("rgroup_owner_context")
    _register("rgroup_member_context")

    _set_cookie(client, "rgroup_owner_context")
    conn = client.post("/api/connections", json=_CONN).json()
    group = client.post("/api/groups", json={"name": "Equipo Contexto"}).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "rgroup_member_context", "role": "member"},
    )

    _set_cookie(client, "rgroup_owner_context", group_id=group["id"])
    assert client.post(f"/api/sharing/connection/{conn['id']}").status_code == 200

    _set_cookie(client, "rgroup_member_context")
    personal_ids = {item["id"] for item in client.get("/api/v2/connections").json()["items"]}
    assert conn["id"] not in personal_ids

    _set_cookie(client, "rgroup_member_context", group_id=group["id"])
    team_ids = {item["id"] for item in client.get("/api/v2/connections").json()["items"]}
    assert conn["id"] in team_ids

    specification = (
        "Eres un agente senior especializado en Python y FastAPI. "
        "Conserva todos los requisitos técnicos, valida seguridad y añade pruebas. "
    ) * 5
    response = client.post(
        "/api/agent-builder/chat",
        json={
            "connection_id": conn["id"],
            "mode": "expert",
            "messages": [{"role": "user", "content": specification}],
        },
    )
    assert response.status_code == 200
    assert '"type": "builder_done"' in response.text


def test_share_connection_without_ownership_forbidden(client):
    _register("rgroup_owner_b")
    _register("rgroup_bystander_b")

    _set_cookie(client, "rgroup_owner_b")
    conn = client.post("/api/connections", json=_CONN).json()

    _set_cookie(client, "rgroup_bystander_b")
    group = client.post("/api/groups", json={"name": "Equipo Ajeno"}).json()

    _set_cookie(client, "rgroup_bystander_b", group_id=group["id"])
    r = client.post(f"/api/sharing/connection/{conn['id']}")
    assert r.status_code == 403


def test_unshare_connection_revokes_access(client):
    _register("rgroup_owner_d")
    _register("rgroup_member_d")

    _set_cookie(client, "rgroup_owner_d")
    conn = client.post("/api/connections", json=_CONN).json()
    group = client.post("/api/groups", json={"name": "Equipo Revoke"}).json()
    client.post(f"/api/groups/{group['id']}/members",
                json={"username": "rgroup_member_d", "role": "member"})

    _set_cookie(client, "rgroup_owner_d", group_id=group["id"])
    client.post(f"/api/sharing/connection/{conn['id']}")

    r = client.delete(f"/api/sharing/connection/{conn['id']}")
    assert r.status_code == 200

    _set_cookie(client, "rgroup_member_d", group_id=group["id"])
    conns = client.get("/api/v2/connections").json()["items"]
    assert not any(c["id"] == conn["id"] for c in conns)
