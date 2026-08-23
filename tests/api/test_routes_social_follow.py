"""Tests de la Fase 3 social: seguir usuarios y feed."""
from __future__ import annotations


def _login(client, username, password="pass1234"):
    import asyncio

    from app.auth.auth import create_token, register_user
    asyncio.run(register_user(username, password, email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _register(username, password="pass1234"):
    import asyncio

    from app.auth.auth import register_user
    asyncio.run(register_user(username, password, email=f"{username}@example.com"))


def _insert_resource_social(owner, resource_id, is_public=True):
    import asyncio

    from app.auth.auth import get_user_by_username
    from app.config.data import AGENTS_DIR
    from app.storage.agent_storage import AgentStorage
    from app.storage.db import open_db
    pub_val = 1 if is_public else 0

    async def _do() -> None:
        user = await get_user_by_username(owner)
        assert user is not None
        await AgentStorage(AGENTS_DIR).save(
            {
                "id": resource_id,
                "name": f"Agent of {owner}",
                "labels": ["public" if is_public else "private"],
            },
            owner_id=user["id"],
        )
        async with open_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                ("agent", resource_id, user["id"], f"Agent of {owner}", "desc", pub_val, "Coding", "warn"),
            )
            await conn.commit()

    asyncio.run(_do())


def test_seguir_usuario(client):
    _login(client, "flw_follower1")
    _register("flw_followed1")
    r = client.post("/api/users/flw_followed1/follow")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_seguir_uno_mismo_devuelve_400(client):
    _login(client, "flw_selfuser")
    r = client.post("/api/users/flw_selfuser/follow")
    assert r.status_code == 400


def test_seguir_usuario_inexistente_devuelve_404(client):
    _login(client, "flw_follower2")
    r = client.post("/api/users/flw_noexiste_xyz_99/follow")
    assert r.status_code == 404


def test_dejar_de_seguir_y_status_false(client):
    _login(client, "flw_follower3")
    _register("flw_followed3")
    client.post("/api/users/flw_followed3/follow")
    r = client.delete("/api/users/flw_followed3/follow")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    status = client.get("/api/users/flw_followed3/follow-status")
    assert status.status_code == 200
    assert status.json()["following"] is False


def test_follow_status_antes_y_despues_de_seguir(client):
    _login(client, "flw_follower4")
    _register("flw_followed4")

    r_before = client.get("/api/users/flw_followed4/follow-status")
    assert r_before.status_code == 200
    data_before = r_before.json()
    assert data_before["following"] is False
    assert data_before["followers_count"] == 0

    client.post("/api/users/flw_followed4/follow")

    r_after = client.get("/api/users/flw_followed4/follow-status")
    assert r_after.status_code == 200
    data_after = r_after.json()
    assert data_after["following"] is True
    assert data_after["followers_count"] == 1


def test_feed_vacio_si_no_sigo_a_nadie(client):
    _login(client, "flw_feedempty")
    r = client.get("/api/feed")
    assert r.status_code == 200
    assert r.json() == []


def test_feed_muestra_recursos_publicos_del_usuario_seguido(client):
    _login(client, "flw_feedfollower")
    _register("flw_feedfollowed")

    _insert_resource_social("flw_feedfollowed", "flw-test-agent-001", is_public=True)

    client.post("/api/users/flw_feedfollowed/follow")

    r = client.get("/api/feed")
    assert r.status_code == 200
    items = r.json()
    assert any(x["resource_id"] == "flw-test-agent-001" for x in items)
    found = next(x for x in items if x["resource_id"] == "flw-test-agent-001")
    assert found["owner"] != "flw_feedfollowed"
    assert found["owner_username"] == "flw_feedfollowed"
    assert "updated_at" in found


def test_feed_no_muestra_recursos_privados_del_usuario_seguido(client):
    _login(client, "flw_privfollower")
    _register("flw_privfollowed")

    _insert_resource_social("flw_privfollowed", "flw-private-agent-001", is_public=False)

    client.post("/api/users/flw_privfollowed/follow")

    r = client.get("/api/feed")
    assert r.status_code == 200
    items = r.json()
    assert not any(x["resource_id"] == "flw-private-agent-001" for x in items)
