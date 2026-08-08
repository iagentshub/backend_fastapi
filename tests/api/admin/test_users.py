"""GET/POST/DELETE /api/admin/users y PATCH de contraseña."""

from __future__ import annotations

from tests.api.admin._helpers import _register


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
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser2",
            "email": "stduser2@example.com",
            "password": "pass1234",
        },
    )
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


def test_create_user_rejects_invalid_email(admin_client):
    response = admin_client.post(
        "/api/admin/users",
        json={"username": "invaliduser", "email": "invalid", "password": "pass1234"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_field",
        "message": "Email no válido",
        "field": "email",
    }


def test_create_user_accepts_valid_email(admin_client):
    response = admin_client.post(
        "/api/admin/users",
        json={
            "username": "createduser",
            "email": "created@example.com",
            "password": "pass1234",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "username": "createduser",
        "email": "created@example.com",
        "role": "standard",
    }


def test_admin_cannot_self_delete(admin_client):
    r = admin_client.delete("/api/admin/users/testadmin")
    assert r.status_code == 400


def test_delete_user_forbidden_for_standard(client, reset_rate_limiter):
    _register("victim_user")
    # autenticarse como otro usuario estándar
    client.post(
        "/api/auth/register",
        json={
            "username": "attacker",
            "email": "attacker@example.com",
            "password": "pass1234",
        },
    )
    r = client.delete("/api/admin/users/victim_user")
    assert r.status_code == 403


# ── Admin PATCH password ───────────────────────────────────────────────────────


def test_admin_patch_password(admin_client):
    _register("pw_target")
    r = admin_client.patch(
        "/api/admin/users/pw_target", json={"password": "newpass123"}
    )
    assert r.status_code == 200


def test_admin_patch_short_password_rejected(admin_client):
    _register("pw_short")
    r = admin_client.patch("/api/admin/users/pw_short", json={"password": "ab"})
    assert r.status_code == 400


def test_admin_patch_empty_password_no_change(admin_client):
    _register("pw_empty")
    r = admin_client.patch("/api/admin/users/pw_empty", json={"password": ""})
    assert r.status_code == 400
