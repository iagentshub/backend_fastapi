"""Tests de rutas de accounts: GET, POST, PUT, DELETE, test, sync.

Varias cuentas pueden compartir `provider` (cada una con su propio `id`) —
las rutas de mutación/lectura de una cuenta concreta van por id, no por
provider.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx


def _setup_user(client, username="accuser"):
    """Registra un usuario y autentica el client."""
    from app.auth.auth import create_token, register_user
    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return username


def _mock_openai_models(*model_ids):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": [{"id": m} for m in model_ids]}
    mock_response.raise_for_status = lambda: None

    async def fake_get(*args, **kwargs):
        return mock_response

    return fake_get


# ── GET /api/accounts ────────────────────────────────────────────────────────

def test_list_accounts_empty(client):
    """Sin cuentas vinculadas devuelve una lista vacía."""
    _setup_user(client, "acclist1")
    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert r.json() == []


def test_list_accounts_requires_auth(client):
    """GET /api/accounts sin auth devuelve 401."""
    r = client.get("/api/accounts")
    assert r.status_code == 401


def test_list_accounts_shows_linked_after_create(client):
    """Tras crear una cuenta, aparece en la lista sin api_key en claro."""
    _setup_user(client, "acclist2")
    r_create = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"})
    assert r_create.status_code == 200

    r = client.get("/api/accounts")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    entry = data[0]
    assert entry["provider"] == "openai"
    assert "id" in entry and entry["id"]
    assert "api_key" not in entry
    assert "api_key_masked" in entry


def test_list_accounts_allows_several_same_provider(client):
    """Dos cuentas OpenAI distintas conviven con ids propios."""
    _setup_user(client, "acclist3")
    r1 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-account-one-123456"})
    r2 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-account-two-123456"})
    id1, id2 = r1.json()["id"], r2.json()["id"]
    assert id1 != id2

    data = client.get("/api/accounts").json()
    provider_entries = [a for a in data if a["provider"] == "openai"]
    assert len(provider_entries) == 2
    assert {a["id"] for a in provider_entries} == {id1, id2}


# ── POST /api/accounts ───────────────────────────────────────────────────────

def test_create_account_openai(client):
    """POST /api/accounts crea la cuenta y devuelve api_key enmascarada + id."""
    _setup_user(client, "postacc1")
    r = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"})
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "openai"
    assert data["id"]
    assert "api_key" not in data
    assert data["api_key_masked"].startswith("sk-tes")


def test_create_account_anthropic(client):
    _setup_user(client, "postacc2")
    r = client.post("/api/accounts", json={"provider": "anthropic", "api_key": "sk-ant-test-key-1234"})
    assert r.status_code == 200
    assert r.json()["provider"] == "anthropic"


def test_create_account_invalid_provider(client):
    _setup_user(client, "postacc3")
    r = client.post("/api/accounts", json={"provider": "unsupported_llm", "api_key": "key123"})
    assert r.status_code == 400


def test_create_account_missing_api_key(client):
    _setup_user(client, "postacc4")
    r = client.post("/api/accounts", json={"provider": "openai"})
    assert r.status_code == 422


def test_create_account_ollama_no_key_required(client):
    _setup_user(client, "postacc5")
    r = client.post("/api/accounts", json={"provider": "ollama", "host": "http://localhost:11434"})
    assert r.status_code == 200
    assert r.json()["provider"] == "ollama"


def test_create_account_requires_auth(client):
    r = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-test"})
    assert r.status_code == 401


# ── PUT /api/accounts/{id} ───────────────────────────────────────────────────

def test_update_account(client):
    _setup_user(client, "putacc1")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-original-123456"}
    ).json()["id"]

    r = client.put(f"/api/accounts/{account_id}", json={"api_key": "sk-updated-123456"})
    assert r.status_code == 200
    assert r.json()["api_key_masked"].startswith("sk-upd")


def test_update_account_not_found(client):
    _setup_user(client, "putacc2")
    r = client.put("/api/accounts/does-not-exist", json={"api_key": "sk-test"})
    assert r.status_code == 404


def test_update_account_requires_auth(client):
    r = client.put("/api/accounts/some-id", json={"api_key": "sk-test"})
    assert r.status_code == 401


# ── DELETE /api/accounts/{id} ────────────────────────────────────────────────

def test_unlink_account(client):
    _setup_user(client, "delacc1")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]
    r = client.delete(f"/api/accounts/{account_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_unlink_account_not_found(client):
    _setup_user(client, "delacc2")
    r = client.delete("/api/accounts/does-not-exist")
    assert r.status_code == 404


def test_unlink_account_requires_auth(client):
    r = client.delete("/api/accounts/some-id")
    assert r.status_code == 401


def test_unlink_account_does_not_affect_sibling(client):
    """Borrar una de dos cuentas OpenAI deja la otra intacta."""
    _setup_user(client, "delacc3")
    id1 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-one-123456"}).json()["id"]
    id2 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-two-123456"}).json()["id"]
    client.delete(f"/api/accounts/{id1}")
    remaining = client.get("/api/accounts").json()
    assert {a["id"] for a in remaining} == {id2}


# ── POST /api/accounts/test (credenciales nuevas, sin guardar) ──────────────

def test_test_new_account_openai_mocked(client):
    _setup_user(client, "testnew1")
    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo")):
        r = client.post(
            "/api/accounts/test",
            json={"provider": "openai", "api_key": "sk-test-openai-123456"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "gpt-4o" in data["models"]


def test_test_new_account_invalid_provider(client):
    _setup_user(client, "testnew2")
    r = client.post("/api/accounts/test", json={"provider": "invalid_prov", "api_key": "key"})
    assert r.status_code == 400


def test_test_new_account_requires_auth(client):
    r = client.post("/api/accounts/test", json={"provider": "openai", "api_key": "sk-test"})
    assert r.status_code == 401


# ── POST /api/accounts/{id}/test (cuenta ya vinculada) ───────────────────────

def test_test_account_uses_stored_credentials(client):
    """POST test con body vacío usa la api_key ya guardada de la cuenta."""
    _setup_user(client, "testacc1")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo")):
        r = client.post(f"/api/accounts/{account_id}/test", json={})

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "gpt-4o" in data["models"]


def test_test_account_not_found(client):
    _setup_user(client, "testacc2")
    r = client.post("/api/accounts/does-not-exist/test", json={})
    assert r.status_code == 404


def test_test_account_requires_auth(client):
    r = client.post("/api/accounts/some-id/test", json={})
    assert r.status_code == 401


# ── POST /api/accounts/{id}/sync ─────────────────────────────────────────────

def test_sync_account_not_found(client):
    _setup_user(client, "syncacc1")
    r = client.post("/api/accounts/does-not-exist/sync")
    assert r.status_code == 404


def test_sync_account_openai_mocked(client):
    """Sync con openai mockeado crea conexiones y devuelve resumen."""
    _setup_user(client, "syncacc2")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo")):
        r = client.post(f"/api/accounts/{account_id}/sync")

    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "openai"
    assert set(data["models"]) == {"gpt-4o", "gpt-3.5-turbo"}
    assert "api_key" not in data


def test_sync_account_with_selected_models(client):
    """Sync con {"models": [...]} solo crea/actualiza esas conexiones, no el resto."""
    _setup_user(client, "syncacc4")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(
        httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo", "gpt-4o-mini")
    ):
        r = client.post(f"/api/accounts/{account_id}/sync", json={"models": ["gpt-4o"]})

    assert r.status_code == 200
    data = r.json()
    assert data["models"] == ["gpt-4o"]

    conns = client.get("/api/connections/raw").json()
    names = {c["name"] for c in conns}
    assert "OpenAI / gpt-4o" in names
    assert "OpenAI / gpt-3.5-turbo" not in names
    assert "OpenAI / gpt-4o-mini" not in names


def test_sync_two_accounts_same_provider_do_not_clash(client):
    """Sincronizar dos cuentas OpenAI con el mismo modelo crea dos conexiones
    distintas, una por cuenta — no se pisan entre sí."""
    _setup_user(client, "syncacc5")
    id1 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-acc-one-123456"}).json()["id"]
    id2 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-acc-two-123456"}).json()["id"]

    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o")):
        client.post(f"/api/accounts/{id1}/sync")
        client.post(f"/api/accounts/{id2}/sync")

    conns = client.get("/api/connections/raw").json()
    gpt4o_conns = [c for c in conns if c.get("model") == "gpt-4o"]
    assert len(gpt4o_conns) == 2
    assert {c["_account_id"] for c in gpt4o_conns} == {id1, id2}


def test_sync_account_requires_auth(client):
    r = client.post("/api/accounts/some-id/sync")
    assert r.status_code == 401
