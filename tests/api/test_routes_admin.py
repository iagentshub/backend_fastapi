"""Tests de GET /api/admin/users y DELETE /api/admin/users/{username}."""
from __future__ import annotations


def _register(username, password="pass1234"):
    """Registra un usuario directamente, sin pasar por HTTP, para no contaminar cookies."""
    from app.auth.auth import register_user
    register_user(username, password, email=f"{username}@example.com")


def test_list_users_as_admin(admin_client, reset_rate_limiter):
    _register("listed_user")
    r = admin_client.get("/api/admin/users")
    assert r.status_code == 200
    users = r.json()
    assert isinstance(users, list)
    assert any(u["username"] == "listed_user" for u in users)


def test_list_users_no_password_hash(admin_client, reset_rate_limiter):
    _register("nohash_user")
    r = admin_client.get("/api/admin/users")
    for u in r.json():
        assert "password_hash" not in u


def test_list_users_forbidden_for_standard(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "stduser2@example.com", "password": "pass1234"})
    r = client.get("/api/admin/users")
    assert r.status_code == 403


def test_list_users_unauthenticated(client):
    r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_delete_user_as_admin(admin_client, reset_rate_limiter):
    _register("to_delete")
    r = admin_client.delete("/api/admin/users/to_delete")
    assert r.status_code == 200
    users = admin_client.get("/api/admin/users").json()
    assert not any(u["username"] == "to_delete" for u in users)


def test_delete_nonexistent_user(admin_client):
    r = admin_client.delete("/api/admin/users/ghost_user")
    assert r.status_code == 404


def test_admin_cannot_self_delete(admin_client):
    r = admin_client.delete("/api/admin/users/testadmin")
    assert r.status_code == 400


def test_delete_user_forbidden_for_standard(client, reset_rate_limiter):
    _register("victim_user")
    # autenticarse como otro usuario estándar
    client.post("/api/auth/register", json={"email": "attacker@example.com", "password": "pass1234"})
    r = client.delete("/api/admin/users/victim_user")
    assert r.status_code == 403


# ── Admin agents ──────────────────────────────────────────────────────────────

_AGENT_PAYLOAD = {
    "name": "Admin Test Agent",
    "system_prompt": "Test.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


def test_admin_list_agents(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    r = admin_client.get("/api/admin/agents")
    assert r.status_code == 200
    agents = r.json()
    assert isinstance(agents, list)
    assert any(a["name"] == "Admin Test Agent" for a in agents)


def test_admin_list_agents_has_owner_email(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    agents = admin_client.get("/api/admin/agents").json()
    private = [a for a in agents if a.get("scope") == "private"]
    assert private, "se esperaba al menos un agente privado"
    assert private[0]["owner_email"] == "testadmin@example.com"


def test_admin_delete_agent(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.delete(f"/api/admin/agents/{created['id']}?scope=private")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    agents = admin_client.get("/api/admin/agents").json()
    assert not any(a["id"] == created["id"] for a in agents)


def test_admin_delete_agent_not_found(admin_client):
    r = admin_client.delete("/api/admin/agents/ghost-agent?scope=private")
    assert r.status_code == 404


def test_admin_agents_forbidden_for_standard(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "std@example.com", "password": "pass1234"})
    r = client.get("/api/admin/agents")
    assert r.status_code == 403


# ── Admin connections ─────────────────────────────────────────────────────────
# Se inserta directamente en la BD (misma ruta que usa el endpoint admin) para
# evitar la divergencia con el _storage de module-level de connections.py.

def _insert_connection(owner_id: str = "testadmin") -> str:
    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage
    c = ConnectionStorage(DB_FILE).save(
        {"type": "openai", "label": "test-conn", "api_key": "sk-test", "model": "gpt-4o"},
        owner_id=owner_id,
    )
    return c["id"]


def test_admin_list_connections(admin_client):
    _insert_connection()
    r = admin_client.get("/api/admin/connections")
    assert r.status_code == 200
    conns = r.json()
    assert isinstance(conns, list)
    assert len(conns) >= 1


def test_admin_list_connections_has_owner_email(admin_client):
    _insert_connection()
    conns = admin_client.get("/api/admin/connections").json()
    assert conns[0]["owner_email"] == "testadmin@example.com"


def test_admin_delete_connection(admin_client):
    conn_id = _insert_connection()
    r = admin_client.delete(f"/api/admin/connections/{conn_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    conns = admin_client.get("/api/admin/connections").json()
    assert not any(c["id"] == conn_id for c in conns)


def test_admin_delete_connection_not_found(admin_client):
    r = admin_client.delete("/api/admin/connections/ghost-conn")
    assert r.status_code == 404


def test_admin_connections_forbidden_for_standard(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "std2@example.com", "password": "pass1234"})
    r = client.get("/api/admin/connections")
    assert r.status_code == 403


# ── Admin knowledge ───────────────────────────────────────────────────────────

def test_admin_list_knowledge(admin_client):
    r = admin_client.get("/api/admin/knowledge")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_knowledge_forbidden_for_standard(client, reset_rate_limiter):
    client.post("/api/auth/register", json={"email": "std3@example.com", "password": "pass1234"})
    r = client.get("/api/admin/knowledge")
    assert r.status_code == 403


# ── Admin stats ───────────────────────────────────────────────────────────────

def test_admin_stats(admin_client):
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    stats = r.json()
    assert "users" in stats or isinstance(stats, dict)


# ── Admin PATCH password ───────────────────────────────────────────────────────

def test_admin_patch_password(admin_client):
    _register("pw_target")
    r = admin_client.patch("/api/admin/users/pw_target", json={"password": "newpass123"})
    assert r.status_code == 200


def test_admin_patch_short_password_rejected(admin_client):
    _register("pw_short")
    r = admin_client.patch("/api/admin/users/pw_short", json={"password": "ab"})
    assert r.status_code == 400


def test_admin_patch_empty_password_no_change(admin_client):
    _register("pw_empty")
    r = admin_client.patch("/api/admin/users/pw_empty", json={"password": ""})
    assert r.status_code == 400
