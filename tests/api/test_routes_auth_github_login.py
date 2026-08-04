"""Tests de login con GitHub (OAuth Device Flow) — sin sesión previa.

Distinto de `/api/accounts/github/device-*` (que vincula una cuenta
proveedor para un usuario ya logueado): aquí no hace falta estar
autenticado, y el resultado es una sesión nueva (cookie `ga_token`),
creando el usuario local la primera vez que alguien entra así.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx


def _mock_post_json(payload):
    mock_response = MagicMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json.return_value = payload

    async def fake_post(*args, **kwargs):
        return mock_response

    return fake_post


def _mock_get_json(payload):
    mock_response = MagicMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json.return_value = payload

    async def fake_get(*args, **kwargs):
        return mock_response

    return fake_get


def _mock_get_dispatch(by_path):
    async def fake_get(self, url, *args, **kwargs):
        for path, payload in by_path.items():
            if path in str(url):
                mock_response = MagicMock()
                mock_response.raise_for_status = lambda: None
                mock_response.json.return_value = payload
                return mock_response
        raise AssertionError(f"GET inesperado: {url}")

    return fake_get


# ── POST /api/auth/github/device-code ────────────────────────────────────────

def test_device_code_not_configured(client):
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", ""):
        r = client.post("/api/auth/github/device-code")
    assert r.status_code == 503


def test_device_code_success_mocked_no_auth_needed(client):
    """A diferencia de accounts.py, este endpoint no requiere sesión previa."""
    payload = {
        "device_code": "devcode-login-1",
        "user_code": "WXYZ-9876",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json(payload)
    ):
        r = client.post("/api/auth/github/device-code")
    assert r.status_code == 200
    data = r.json()
    assert data["user_code"] == "WXYZ-9876"
    assert "ga_token" not in r.cookies


def test_device_code_ignores_platform_visibility_toggle(admin_client, client):
    """El toggle de Admin (oauth_github_enabled en /api/settings/platform)
    solo controla si se MUESTRA el botón en /login/ — nunca si el flujo de
    login en sí funciona. Apagarlo no debe bloquear este endpoint."""
    admin_client.put("/api/settings/platform", json={"oauth_github_enabled": False})
    payload = {
        "device_code": "devcode-login-toggle-off",
        "user_code": "AAAA-1111",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json(payload)
    ):
        r = client.post("/api/auth/github/device-code")
    assert r.status_code == 200
    assert r.json()["user_code"] == "AAAA-1111"


# ── POST /api/auth/github/device-token ───────────────────────────────────────

def test_device_token_missing_device_code(client):
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"):
        r = client.post("/api/auth/github/device-token", json={})
    assert r.status_code == 422


def test_device_token_pending_mocked(client):
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json({"error": "authorization_pending"})
    ):
        r = client.post("/api/auth/github/device-token", json={"device_code": "x"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["pending"] is True
    assert "ga_token" not in r.cookies


def test_device_token_creates_new_user_mocked(client):
    """Primera vez que esta identidad de GitHub inicia sesión: crea el
    usuario local y abre sesión (cookie ga_token)."""
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json({"access_token": "ghu_faketoken"})
    ), patch.object(
        httpx.AsyncClient,
        "get",
        new=_mock_get_json(
            {"id": 555111, "login": "octocat", "email": "octocat@example.com", "name": "The Octocat"}
        ),
    ):
        r = client.post("/api/auth/github/device-token", json={"device_code": "x"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["username"] == "octocat"
    assert "ga_token" in r.cookies


def test_device_token_reuses_existing_user_on_second_login(client):
    """Dos logins con la misma identidad de GitHub resuelven al mismo
    usuario — no se duplica en cada inicio de sesión."""
    mocks = (
        patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"),
        patch.object(httpx.AsyncClient, "post", new=_mock_post_json({"access_token": "ghu_faketoken"})),
        patch.object(
            httpx.AsyncClient,
            "get",
            new=_mock_get_json(
                {"id": 777222, "login": "reuseuser", "email": "reuse@example.com", "name": "Reuse User"}
            ),
        ),
    )
    with mocks[0], mocks[1], mocks[2]:
        r1 = client.post("/api/auth/github/device-token", json={"device_code": "x"})
        r2 = client.post("/api/auth/github/device-token", json={"device_code": "y"})
    assert r1.json()["username"] == r2.json()["username"] == "reuseuser"


def test_device_token_username_collision_gets_suffix(client):
    """Si el login de GitHub ya existe como username local (de otra cuenta),
    se añade un sufijo en vez de fallar o pisar la cuenta existente."""
    import asyncio

    from app.auth.auth import register_user

    asyncio.run(register_user("collideuser", "pass1234", email="collideuser@example.com"))

    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json({"access_token": "ghu_faketoken"})
    ), patch.object(
        httpx.AsyncClient,
        "get",
        new=_mock_get_json(
            {"id": 999888, "login": "collideuser", "email": "collideuser-gh@example.com", "name": "Collide"}
        ),
    ):
        r = client.post("/api/auth/github/device-token", json={"device_code": "x"})
    assert r.status_code == 200
    username = r.json()["username"]
    assert username != "collideuser"
    assert username.startswith("collideuser")


def test_device_token_missing_email_falls_back_to_user_emails(client):
    """Si el perfil de GitHub no expone email público, se pide /user/emails."""
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json({"access_token": "ghu_faketoken"})
    ), patch.object(
        httpx.AsyncClient,
        "get",
        new=_mock_get_dispatch(
            {
                "/user/emails": [
                    {"email": "primary@example.com", "primary": True},
                    {"email": "secondary@example.com", "primary": False},
                ],
                "/user": {"id": 424242, "login": "noemailuser", "email": None, "name": "No Email"},
            }
        ),
    ):
        r = client.post("/api/auth/github/device-token", json={"device_code": "x"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_device_token_requires_client_id_configured(client):
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", ""):
        r = client.post("/api/auth/github/device-token", json={"device_code": "x"})
    assert r.status_code == 503
