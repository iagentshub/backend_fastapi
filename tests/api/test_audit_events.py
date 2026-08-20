"""Eventos sensibles que deben entrar estructurados en app_logs."""

from __future__ import annotations

from unittest.mock import patch


def _register(client, username: str, email: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": "pass1234"},
    )
    assert response.status_code == 200


def test_login_success_and_rejection_emit_audit(client, reset_rate_limiter):
    _register(client, "auditlogin", "auditlogin@example.com")

    with patch("app.api.routes.auth.session.flog.audit") as audit:
        rejected = client.post(
            "/api/auth/login",
            json={"email": "auditlogin@example.com", "password": "incorrecta"},
        )
        assert rejected.status_code == 401
        assert audit.call_args.args[0] == "auth.login.rejected"
        assert audit.call_args.kwargs["outcome"] == "DENIED"

        accepted = client.post(
            "/api/auth/login",
            json={"email": "auditlogin@example.com", "password": "pass1234"},
        )
        assert accepted.status_code == 200
        assert audit.call_args.args[0] == "auth.login.succeeded"
        assert audit.call_args.kwargs["resource_id"] == "auditlogin"


def test_password_change_emits_audit(client, reset_rate_limiter):
    _register(client, "auditpassword", "auditpassword@example.com")
    client.post(
        "/api/auth/login",
        json={"email": "auditpassword@example.com", "password": "pass1234"},
    )

    with patch("app.api.routes.auth.passwords.flog.audit") as audit:
        response = client.post(
            "/api/auth/change-password",
            json={"current_password": "pass1234", "new_password": "newpass1234"},
        )

    assert response.status_code == 200
    assert audit.call_args.args[0] == "auth.password.changed"
    assert audit.call_args.kwargs["resource_id"] == "auditpassword"


def test_admin_user_changes_and_impersonation_emit_audit(admin_client):
    created = admin_client.post(
        "/api/admin/users",
        json={
            "username": "audittarget",
            "email": "audittarget@example.com",
            "password": "pass1234",
            "role": "standard",
        },
    )
    assert created.status_code == 200

    with patch("app.api.routes.admin.users.flog.audit") as audit:
        changed = admin_client.patch(
            "/api/admin/users/audittarget", json={"role": "admin"}
        )
        assert changed.status_code == 200
        assert audit.call_args.args[0] == "admin.user.role_changed"
        assert audit.call_args.kwargs["details"] == {
            "from": "standard",
            "to": "admin",
        }

        impersonated = admin_client.post("/api/admin/impersonate/audittarget")
        assert impersonated.status_code == 200
        assert audit.call_args.args[0] == "admin.impersonation.started"
        assert audit.call_args.kwargs["resource_id"] == "audittarget"
