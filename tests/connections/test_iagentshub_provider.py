"""Tests del provider IAgentsHub — _login() e IAgentsHubProvider.test()."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from app.connections.iagentshub import IAgentsHubProvider, _login

# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_urlopen_response(data: dict, status: int = 200):
    """Simula la respuesta de app.connections.iagentshub.safe_urlopen."""
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _http_error(code: int, body: bytes = b'{"error": "bad"}') -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(
        url="https://hub.example.com/api/auth/login",
        code=code,
        msg="Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,    # type: ignore[arg-type]
    )
    err.read = lambda: body
    return err


# ── _login() ──────────────────────────────────────────────────────────────────

def test_login_raises_when_no_token_in_cookie():
    """_login() debe lanzar ValueError si la respuesta no devuelve ga_token."""
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = b""
    mock_resp.headers.get_all.return_value = []

    with patch("app.connections.iagentshub.safe_urlopen", return_value=mock_resp):
        with pytest.raises(ValueError, match="token de sesión"):
            _login("https://hub.example.com", "alice", "secret")


# ── IAgentsHubProvider.test() — validación de campos ──────────────────────────

def test_test_missing_url():
    result = IAgentsHubProvider.test({"url": "", "username": "alice", "api_key": "pass"})
    assert result.ok is False
    assert "URL" in result.message


def test_test_url_only_spaces():
    result = IAgentsHubProvider.test({"url": "   ", "username": "alice", "api_key": "pass"})
    assert result.ok is False
    assert "URL" in result.message


def test_test_missing_username():
    result = IAgentsHubProvider.test(
        {"url": "https://hub.example.com", "username": "", "api_key": "pass"}
    )
    assert result.ok is False
    assert "usuario" in result.message.lower()


def test_test_missing_password():
    result = IAgentsHubProvider.test(
        {"url": "https://hub.example.com", "username": "alice", "api_key": ""}
    )
    assert result.ok is False
    assert "contraseña" in result.message.lower()


# ── IAgentsHubProvider.test() — caminos HTTP ──────────────────────────────────

def test_test_success():
    """Login correcto + /api/auth/me devuelve 200 con username."""
    me_resp = _mock_urlopen_response({"username": "alice"})

    with patch("app.connections.iagentshub._login", return_value="tok-abc"):
        with patch("app.connections.iagentshub.safe_urlopen", return_value=me_resp):
            result = IAgentsHubProvider.test(
                {"url": "https://hub.example.com", "username": "alice", "api_key": "pw"}
            )

    assert result.ok is True
    assert "alice" in result.message


def test_test_success_no_username_in_response():
    """Si el JSON de /me no trae username, usa el del config."""
    me_resp = _mock_urlopen_response({})

    with patch("app.connections.iagentshub._login", return_value="tok-abc"):
        with patch("app.connections.iagentshub.safe_urlopen", return_value=me_resp):
            result = IAgentsHubProvider.test(
                {"url": "https://hub.example.com", "username": "bob", "api_key": "pw"}
            )

    assert result.ok is True
    assert "bob" in result.message


def test_test_http_401():
    """HTTPError 401 → credenciales incorrectas."""
    with patch("app.connections.iagentshub._login", side_effect=_http_error(401)):
        result = IAgentsHubProvider.test(
            {"url": "https://hub.example.com", "username": "alice", "api_key": "bad"}
        )
    assert result.ok is False
    assert "contraseña" in result.message.lower() or "incorrecto" in result.message.lower()


def test_test_http_other_error():
    """HTTPError distinto de 401 incluye el código en el mensaje."""
    with patch("app.connections.iagentshub._login", side_effect=_http_error(503)):
        result = IAgentsHubProvider.test(
            {"url": "https://hub.example.com", "username": "alice", "api_key": "pw"}
        )
    assert result.ok is False
    assert "503" in result.message


def test_test_value_error_from_login():
    """ValueError lanzado por _login (sin token) se reporta correctamente."""
    with patch("app.connections.iagentshub._login", side_effect=ValueError("sin token")):
        result = IAgentsHubProvider.test(
            {"url": "https://hub.example.com", "username": "alice", "api_key": "pw"}
        )
    assert result.ok is False
    assert "sin token" in result.message


def test_test_generic_connection_error():
    """Excepción genérica (timeout, DNS) → error de conexión."""
    with patch("app.connections.iagentshub._login", side_effect=OSError("connection refused")):
        result = IAgentsHubProvider.test(
            {"url": "https://hub.example.com", "username": "alice", "api_key": "pw"}
        )
    assert result.ok is False
    assert "connection refused" in result.detail


# ── Metadatos del provider ─────────────────────────────────────────────────────

def test_provider_type_id():
    assert IAgentsHubProvider.type_id == "iagentshub"


def test_provider_fields_declared():
    keys = [f.key for f in IAgentsHubProvider.fields]
    assert "url" in keys
    assert "username" in keys
    assert "api_key" in keys
