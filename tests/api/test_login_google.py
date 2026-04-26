"""Tests del flujo Google OAuth (/api/auth/google)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _mock_httpx(tokens: dict, info: dict):
    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = tokens
    info_resp = MagicMock(status_code=200)
    info_resp.json.return_value = info
    return AsyncMock(return_value=token_resp), AsyncMock(return_value=info_resp)


def test_google_no_configurado_devuelve_503(client):
    """Sin variables de entorno de Google, el endpoint debe devolver 503."""
    with patch("app.auth.login_google._CLIENT_ID", ""), \
         patch("app.auth.login_google._CLIENT_SECRET", ""), \
         patch("app.auth.login_google._REDIRECT_URI", ""):
        r = client.get("/api/auth/google", follow_redirects=False)
    assert r.status_code == 503


def test_google_redirige_a_consent_screen(client):
    """Con credenciales configuradas debe redirigir a Google."""
    with patch("app.auth.login_google._CLIENT_ID", "fake-id"), \
         patch("app.auth.login_google._CLIENT_SECRET", "fake-secret"), \
         patch("app.auth.login_google._REDIRECT_URI", "http://localhost/callback"):
        r = client.get("/api/auth/google", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "accounts.google.com" in r.headers["location"]


def test_callback_sin_code_devuelve_400(client):
    r = client.get("/api/auth/google/callback")
    assert r.status_code == 400


def test_callback_con_state_invalido_devuelve_400(client):
    r = client.get("/api/auth/google/callback", params={"code": "abc", "state": "bad"})
    assert r.status_code == 400


def test_callback_completo_ok(client, patch_data_dir):
    """Simula el flujo completo: intercambio de código → perfil → JWT."""
    mock_tokens = {"access_token": "fake-access-token"}
    mock_info = {
        "sub": "google-sub-123",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
    }

    with patch("app.auth.login_google._CLIENT_ID", "fake-id"), \
         patch("app.auth.login_google._CLIENT_SECRET", "fake-secret"), \
         patch("app.auth.login_google._REDIRECT_URI", "http://localhost/callback"):

        r_init = client.get("/api/auth/google", follow_redirects=False)
        state_cookie = r_init.cookies.get("ga_oauth_state", "")
        mock_post, mock_get = _mock_httpx(mock_tokens, mock_info)

        with patch("httpx.AsyncClient.post", mock_post), \
             patch("httpx.AsyncClient.get", mock_get):
            r = client.get("/api/auth/google/callback", params={
                "code": "authcode123",
                "state": state_cookie,
            }, follow_redirects=False)

    assert r.status_code in (302, 307)
    assert "ga_token" in r.cookies


def test_callback_email_no_autorizado(client):
    """Un email fuera de la whitelist debe devolver 403."""
    mock_tokens = {"access_token": "fake-access-token"}
    mock_info = {
        "sub": "sub-blocked",
        "email": "blocked@other.com",
        "email_verified": True,
        "name": "Blocked",
    }

    with patch("app.auth.login_google._CLIENT_ID", "fake-id"), \
         patch("app.auth.login_google._CLIENT_SECRET", "fake-secret"), \
         patch("app.auth.login_google._REDIRECT_URI", "http://localhost/callback"), \
         patch("app.auth.login_google._ALLOWED_EMAILS", {"allowed@example.com"}):

        r_init = client.get("/api/auth/google", follow_redirects=False)
        state_cookie = r_init.cookies.get("ga_oauth_state", "")
        mock_post, mock_get = _mock_httpx(mock_tokens, mock_info)

        with patch("httpx.AsyncClient.post", mock_post), \
             patch("httpx.AsyncClient.get", mock_get):
            r = client.get("/api/auth/google/callback", params={
                "code": "code123",
                "state": state_cookie,
            }, follow_redirects=False)

    assert r.status_code == 403
