"""Tests de fork de agentes y skills."""
from __future__ import annotations


def _login(client, username: str) -> str:
    from app.auth.auth import create_token, register_user
    register_user(username, "pass1234", email=f"{username}@fork.test")
    client.cookies.set("ga_token", create_token(username))
    return username


def _make_agent_public(client, agent_id: str, owner: str) -> None:
    from app.config.data import DB_FILE
    from app.storage.db import PH, close_db, open_db
    conn = open_db(DB_FILE)
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO resource_social "
            f"(resource_type, resource_id, owner, name, is_public, category, trial_missing_deps) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, 1, 'Coding', 'warn')",
            ("agent", agent_id, owner, agent_id),
        )
        conn.commit()
    finally:
        close_db(conn)


def _make_skill_public(client, skill_id: str, owner: str) -> None:
    from app.config.data import DB_FILE
    from app.storage.db import PH, close_db, open_db
    conn = open_db(DB_FILE)
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO resource_social "
            f"(resource_type, resource_id, owner, name, is_public, category, trial_missing_deps) "
            f"VALUES ({PH}, {PH}, {PH}, {PH}, 1, 'Coding', 'warn')",
            ("skill", skill_id, owner, skill_id),
        )
        conn.commit()
    finally:
        close_db(conn)


def test_fork_agente_publico_devuelve_200(client):
    owner = _login(client, "forkowner01")
    r = client.post("/api/agents", json={"name": "Forkable Agent One", "description": "agente forkeable"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(client, agent_id, owner)

    r2 = client.post(f"/api/agents/private/{agent_id}/fork")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert "agent_id" in body
    assert body["name"].startswith("Fork of ")


def test_fork_agente_no_publico_devuelve_403(client):
    _login(client, "forkowner02")
    r = client.post("/api/agents", json={"name": "Private Only Agent Fork", "description": "privado"})
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.post(f"/api/agents/private/{agent_id}/fork")
    assert r2.status_code == 403


def test_fork_agente_inexistente_devuelve_404(client):
    _login(client, "forkowner03")
    r = client.post("/api/agents/private/this-agent-does-not-exist/fork")
    assert r.status_code == 404


def test_fork_agente_scope_public_no_necesita_resource_social(client):
    from app.config.data import AGENTS_DIR
    import json

    _login(client, "forkowner04")

    sys_dir = AGENTS_DIR / "public" / "fork-sys-agent"
    sys_dir.mkdir(parents=True, exist_ok=True)
    (sys_dir / "config.json").write_text(
        json.dumps({"id": "fork-sys-agent", "name": "Fork Sys Agent", "scope": "public"}),
        encoding="utf-8",
    )

    r = client.post("/api/agents/public/fork-sys-agent/fork")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["agent_id"]


def test_fork_skill_publico_devuelve_200(client):
    owner = _login(client, "forkowner05")
    r = client.post("/api/skills/private", json={
        "name": "Forkable Skill Five",
        "description": "skill forkeable",
        "content": "# instrucciones",
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]
    _make_skill_public(client, skill_id, owner)

    r2 = client.post(f"/api/skills/private/{skill_id}/fork")
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True
    assert "skill_id" in body
    assert body["name"].startswith("Fork of ")


def test_fork_agente_aparece_en_lista_privada(client):
    owner = _login(client, "forkowner06")
    r = client.post("/api/agents", json={"name": "Listable Fork Agent Six", "description": "agente listable"})
    assert r.status_code == 200
    agent_id = r.json()["id"]
    _make_agent_public(client, agent_id, owner)

    fork_r = client.post(f"/api/agents/private/{agent_id}/fork")
    assert fork_r.status_code == 200
    fork_id = fork_r.json()["agent_id"]

    list_r = client.get("/api/agents", params={"scope": "private"})
    assert list_r.status_code == 200
    ids = [a["id"] for a in list_r.json()]
    assert fork_id in ids
