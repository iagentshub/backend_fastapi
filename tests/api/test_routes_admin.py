"""Tests de GET /api/admin/users y DELETE /api/admin/users/{username}."""
from __future__ import annotations


def _register(username, password="pass1234"):
    """Registra un usuario directamente, sin pasar por HTTP, para no contaminar cookies."""
    from app.auth.auth import register_user
    register_user(username, password)


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
    _register("stduser2")
    client.post("/api/auth/login", json={"username": "stduser2", "password": "pass1234"})
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
    r = admin_client.delete("/api/admin/users/admin")
    assert r.status_code == 400


def test_delete_user_forbidden_for_standard(client, reset_rate_limiter):
    _register("victim_user")
    # autenticarse como otro usuario estándar
    client.post("/api/auth/register", json={"username": "attacker", "password": "pass1234"})
    r = client.delete("/api/admin/users/victim_user")
    assert r.status_code == 403
