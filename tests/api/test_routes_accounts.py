"""Tests de rutas de accounts: GET, PUT, DELETE, test, sync."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx


def _setup_user(client, username="accuser"):
    """Registra un usuario y autentica el client."""
    from app.auth.auth import create_token, register_user
    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return username


# ── GET /api/accounts ─────────────────────────────────────────────────────────

def test_list_accounts_empty(client):
    """Sin cuentas vinculadas devuelve todos los providers con linked=False."""
    _setup_user(client, "acclist1")
    r = client.get("/api/accounts")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # Hay exactamente los providers definidos
    providers = {item["provider"] for item in data}
    assert providers == {"anthropic", "openai", "github", "ollama", "nvidia", "google"}
    for item in data:
        assert item.get("linked") is False


def test_list_accounts_requires_auth(client):
    """GET /api/accounts sin auth devuelve 401."""
    r = client.get("/api/accounts")
    assert r.status_code == 401


def test_list_accounts_shows_linked_after_put(client):
    """Después de vincular una cuenta, aparece en la lista sin api_key en claro."""
    _setup_user(client, "acclist2")
    r_put = client.put("/api/accounts/openai", json={"api_key": "sk-test-openai-123456"})
    assert r_put.status_code == 200

    r = client.get("/api/accounts")
    assert r.status_code == 200
    data = r.json()
    openai_entry = next((a for a in data if a["provider"] == "openai"), None)
    assert openai_entry is not None
    # La api_key no debe aparecer en claro
    assert "api_key" not in openai_entry
    # Pero sí la versión enmascarada
    assert "api_key_masked" in openai_entry


# ── PUT /api/accounts/{provider} ─────────────────────────────────────────────

def test_link_account_openai(client):
    """PUT /api/accounts/openai vincula la cuenta y devuelve api_key enmascarada."""
    _setup_user(client, "putacc1")
    r = client.put("/api/accounts/openai", json={"api_key": "sk-test-openai-123456"})
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "openai"
    assert "api_key" not in data
    assert "api_key_masked" in data
    assert data["api_key_masked"].startswith("sk-tes")


def test_link_account_anthropic(client):
    """PUT /api/accounts/anthropic vincula con api_key de Anthropic."""
    _setup_user(client, "putacc2")
    r = client.put("/api/accounts/anthropic", json={"api_key": "sk-ant-test-key-1234"})
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "anthropic"
    assert "api_key" not in data


def test_link_account_invalid_provider(client):
    """PUT con provider no soportado devuelve 400."""
    _setup_user(client, "putacc3")
    r = client.put("/api/accounts/unsupported_llm", json={"api_key": "key123"})
    assert r.status_code == 400


def test_link_account_missing_api_key(client):
    """PUT sin api_key (para provider que la requiere) devuelve 422."""
    _setup_user(client, "putacc4")
    r = client.put("/api/accounts/openai", json={})
    assert r.status_code == 422


def test_link_account_ollama_no_key_required(client):
    """PUT /api/accounts/ollama no requiere api_key."""
    _setup_user(client, "putacc5")
    r = client.put("/api/accounts/ollama", json={"host": "http://localhost:11434"})
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "ollama"


def test_link_account_requires_auth(client):
    """PUT sin auth devuelve 401."""
    r = client.put("/api/accounts/openai", json={"api_key": "sk-test"})
    assert r.status_code == 401


# ── DELETE /api/accounts/{provider} ──────────────────────────────────────────

def test_unlink_account(client):
    """Vincular y desconectar una cuenta devuelve ok=True."""
    _setup_user(client, "delacc1")
    client.put("/api/accounts/openai", json={"api_key": "sk-test-openai-123456"})
    r = client.delete("/api/accounts/openai")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unlink_account_not_linked(client):
    """Desconectar cuenta no vinculada devuelve 404."""
    _setup_user(client, "delacc2")
    r = client.delete("/api/accounts/openai")
    assert r.status_code == 404


def test_unlink_account_invalid_provider(client):
    """Desconectar provider no soportado devuelve 404 (no vinculado)."""
    _setup_user(client, "delacc3")
    r = client.delete("/api/accounts/unknown_provider")
    assert r.status_code == 404


def test_unlink_account_requires_auth(client):
    """DELETE sin auth devuelve 401."""
    r = client.delete("/api/accounts/openai")
    assert r.status_code == 401


# ── POST /api/accounts/{provider}/test ───────────────────────────────────────

def test_test_account_openai_mocked(client):
    """POST test con openai mockeado devuelve ok=True y lista de modelos."""
    _setup_user(client, "testacc1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]
    }
    mock_response.raise_for_status = lambda: None

    async def fake_get(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = client.post(
            "/api/accounts/openai/test",
            json={"api_key": "sk-test-openai-123456"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "models" in data
    assert "gpt-4o" in data["models"]


def test_test_account_anthropic_mocked(client):
    """POST test con anthropic mockeado devuelve modelos."""
    _setup_user(client, "testacc2")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"id": "claude-3-5-sonnet-20241022"}, {"id": "claude-3-haiku-20240307"}]
    }
    mock_response.raise_for_status = lambda: None

    async def fake_get(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = client.post(
            "/api/accounts/anthropic/test",
            json={"api_key": "sk-ant-test-key-1234"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "claude-3-5-sonnet-20241022" in data["models"]


def test_test_account_invalid_provider(client):
    """POST test con provider inválido devuelve 400."""
    _setup_user(client, "testacc3")
    r = client.post("/api/accounts/invalid_prov/test", json={"api_key": "key"})
    assert r.status_code == 400


def test_test_account_requires_auth(client):
    """POST test sin auth devuelve 401."""
    r = client.post("/api/accounts/openai/test", json={"api_key": "sk-test"})
    assert r.status_code == 401


# ── POST /api/accounts/{provider}/sync ───────────────────────────────────────

def test_sync_account_not_linked(client):
    """Sync sin cuenta vinculada devuelve 404."""
    _setup_user(client, "syncacc1")
    r = client.post("/api/accounts/openai/sync")
    assert r.status_code == 404


def test_sync_account_openai_mocked(client):
    """Sync con openai mockeado crea conexiones y devuelve resumen."""
    _setup_user(client, "syncacc2")
    # Primero vinculamos la cuenta
    client.put("/api/accounts/openai", json={"api_key": "sk-test-openai-123456"})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]
    }
    mock_response.raise_for_status = lambda: None

    async def fake_get(*args, **kwargs):
        return mock_response

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = client.post("/api/accounts/openai/sync")

    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "openai"
    assert "models" in data
    assert "api_key" not in data


def test_sync_account_invalid_provider(client):
    """Sync con provider inválido devuelve 400."""
    _setup_user(client, "syncacc3")
    r = client.post("/api/accounts/bad_provider/sync")
    assert r.status_code == 400


def test_sync_account_requires_auth(client):
    """POST sync sin auth devuelve 401."""
    r = client.post("/api/accounts/openai/sync")
    assert r.status_code == 401


# Necesario para los mocks con MagicMock sin import explícito
from unittest.mock import MagicMock  # noqa: E402
