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


def test_unlink_account_deletes_synced_connections(client):
    """Desvincular una cuenta borra también las conexiones que había
    sincronizado — dejarlas huérfanas (sin cuenta que las gestione) es más
    confuso que útil."""
    _setup_user(client, "delacc4")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(
        httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo")
    ):
        client.post(f"/api/accounts/{account_id}/sync")

    r = client.delete(f"/api/accounts/{account_id}")
    assert r.status_code == 200
    assert r.json()["connections_deleted"] == 2

    conns = client.get("/api/connections/raw").json()
    names = {c["name"] for c in conns}
    assert "OpenAI / gpt-4o" not in names
    assert "OpenAI / gpt-3.5-turbo" not in names


def test_unlink_account_does_not_delete_sibling_or_manual_connections(client):
    """Desvincular una cuenta solo borra SUS conexiones — ni las de una
    cuenta hermana del mismo proveedor ni una Connection creada a mano."""
    _setup_user(client, "delacc5")
    id1 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-one-123456"}).json()["id"]
    id2 = client.post("/api/accounts", json={"provider": "openai", "api_key": "sk-two-123456"}).json()["id"]
    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o")):
        client.post(f"/api/accounts/{id1}/sync")
        client.post(f"/api/accounts/{id2}/sync")
    manual = client.post(
        "/api/connections",
        json={"type": "openai", "name": "Manual conn", "api_key": "sk-manual", "model": "gpt-4o"},
    ).json()

    r = client.delete(f"/api/accounts/{id1}")
    assert r.status_code == 200
    assert r.json()["connections_deleted"] == 1

    remaining_ids = {c["id"] for c in client.get("/api/connections/raw").json()}
    assert any(c["_account_id"] == id2 for c in client.get("/api/connections/raw").json())
    assert manual["id"] in remaining_ids


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


def test_sync_account_deselecting_deletes_connection(client):
    """Desmarcar en una segunda sync un modelo que ya estaba sincronizado
    borra de verdad su Connection — la selección explícita manda."""
    _setup_user(client, "syncacc6")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(
        httpx.AsyncClient,
        "get",
        new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo"),
    ):
        client.post(
            f"/api/accounts/{account_id}/sync",
            json={"models": ["gpt-4o", "gpt-3.5-turbo"]},
        )
        r = client.post(
            f"/api/accounts/{account_id}/sync", json={"models": ["gpt-4o"]}
        )

    assert r.status_code == 200
    assert r.json()["sync_summary"]["connections_deleted"] == 1

    conns = client.get("/api/connections/raw").json()
    names = {c["name"] for c in conns}
    assert "OpenAI / gpt-4o" in names
    assert "OpenAI / gpt-3.5-turbo" not in names


def test_sync_account_without_body_does_not_delete(client):
    """Sin body (sincronizar "todo") no borra conexiones aunque el proveedor
    ya no reporte un modelo previamente sincronizado — solo la selección
    explícita del diálogo borra."""
    _setup_user(client, "syncacc7")
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(
        httpx.AsyncClient,
        "get",
        new=_mock_openai_models("gpt-4o", "gpt-3.5-turbo"),
    ):
        client.post(f"/api/accounts/{account_id}/sync")

    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o")):
        r = client.post(f"/api/accounts/{account_id}/sync")

    assert r.status_code == 200
    assert r.json()["sync_summary"]["connections_deleted"] == 0

    conns = client.get("/api/connections/raw").json()
    names = {c["name"] for c in conns}
    assert "OpenAI / gpt-4o" in names
    assert "OpenAI / gpt-3.5-turbo" in names


def test_sync_account_visible_in_connections_for_admin_user(admin_client):
    """Las conexiones creadas al sincronizar deben aparecer en
    GET /api/connections (lo que usa la pestaña APIs LLM) también para un
    usuario con rol admin cuyo username no es literalmente "admin" — owner_id
    tiene que ser el usuario real, no un bucket especial que connections.py
    no reconoce."""
    client = admin_client
    account_id = client.post(
        "/api/accounts", json={"provider": "openai", "api_key": "sk-test-openai-123456"}
    ).json()["id"]

    with patch.object(httpx.AsyncClient, "get", new=_mock_openai_models("gpt-4o")):
        r = client.post(f"/api/accounts/{account_id}/sync")

    assert r.status_code == 200
    assert r.json()["sync_summary"]["connections_created"] == 1

    conns = client.get("/api/connections").json()
    assert any(c.get("model") == "gpt-4o" for c in conns)


def test_sync_account_requires_auth(client):
    r = client.post("/api/accounts/some-id/sync")
    assert r.status_code == 401


# ── Proveedor iagentshub (url+usuario+contraseña, sync = hub-sync) ──────────

def test_create_account_iagentshub_missing_fields(client):
    """POST iagentshub sin url/username/api_key devuelve 422."""
    _setup_user(client, "hubacc1")
    r = client.post(
        "/api/accounts",
        json={"provider": "iagentshub", "url": "https://hub.example.com"},
    )
    assert r.status_code == 422


def test_create_account_iagentshub_success(client):
    _setup_user(client, "hubacc2")
    r = client.post(
        "/api/accounts",
        json={
            "provider": "iagentshub",
            "url": "https://hub.example.com",
            "username": "hubuser",
            "api_key": "hubpass",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "iagentshub"
    assert data["username"] == "hubuser"
    assert "api_key" not in data
    assert data["api_key_masked"]


def test_test_new_account_iagentshub_mocked(client):
    """POST /test con iagentshub prueba el login, sin lista de modelos."""
    _setup_user(client, "hubacc3")
    from app.connections.base import TestResult
    from app.connections.iagentshub import IAgentsHubProvider

    with patch.object(
        IAgentsHubProvider,
        "test",
        return_value=TestResult(True, "OK — conectado como hubuser"),
    ):
        r = client.post(
            "/api/accounts/test",
            json={
                "provider": "iagentshub",
                "url": "https://hub.example.com",
                "username": "hubuser",
                "api_key": "hubpass",
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["models"] == []


def test_test_new_account_iagentshub_failed_login_mocked(client):
    _setup_user(client, "hubacc4")
    from app.connections.base import TestResult
    from app.connections.iagentshub import IAgentsHubProvider

    with patch.object(
        IAgentsHubProvider,
        "test",
        return_value=TestResult(False, "Usuario o contraseña incorrectos"),
    ):
        r = client.post(
            "/api/accounts/test",
            json={
                "provider": "iagentshub",
                "url": "https://hub.example.com",
                "username": "hubuser",
                "api_key": "wrong",
            },
        )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_sync_account_iagentshub_creates_mirror_connection_mocked(client):
    """Sync de una cuenta iagentshub reutiliza hub-sync y deja una Connection
    espejo tipo iagentshub ligada a la cuenta vía _account_id."""
    _setup_user(client, "hubacc5")
    account_id = client.post(
        "/api/accounts",
        json={
            "provider": "iagentshub",
            "url": "https://hub.example.com",
            "username": "hubuser",
            "api_key": "hubpass",
        },
    ).json()["id"]

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=[])

    from unittest.mock import AsyncMock

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(return_value=mock_response)

    with patch("app.connections.iagentshub._login", return_value="fake-token"), patch(
        "httpx.AsyncClient", return_value=mock_http
    ):
        r = client.post(f"/api/accounts/{account_id}/sync")

    assert r.status_code == 200
    data = r.json()
    assert data["sync_summary"]["ok"] is True
    assert data["sync_summary"]["errors"] == []

    conns = client.get("/api/connections/raw").json()
    mirrors = [c for c in conns if c.get("type") == "iagentshub"]
    assert len(mirrors) == 1
    assert mirrors[0]["_account_id"] == account_id


def test_sync_account_iagentshub_login_failure_mocked(client):
    _setup_user(client, "hubacc6")
    account_id = client.post(
        "/api/accounts",
        json={
            "provider": "iagentshub",
            "url": "https://hub.example.com",
            "username": "hubuser",
            "api_key": "wrongpass",
        },
    ).json()["id"]

    with patch(
        "app.connections.iagentshub._login", side_effect=Exception("auth failed")
    ):
        r = client.post(f"/api/accounts/{account_id}/sync")
    assert r.status_code == 502


# ── GitHub OAuth Device Flow ──────────────────────────────────────────────────

def _mock_post_json(payload):
    mock_response = MagicMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json.return_value = payload

    async def fake_post(*args, **kwargs):
        return mock_response

    return fake_post


def test_github_device_code_not_configured(client):
    """Sin GITHUB_OAUTH_CLIENT_ID configurado, el endpoint devuelve 503."""
    _setup_user(client, "ghdf1")
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", ""):
        r = client.post("/api/accounts/github/device-code")
    assert r.status_code == 503


def test_github_device_code_success_mocked(client):
    _setup_user(client, "ghdf2")
    payload = {
        "device_code": "devcode123",
        "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900,
        "interval": 5,
    }
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json(payload)
    ):
        r = client.post("/api/accounts/github/device-code")
    assert r.status_code == 200
    data = r.json()
    assert data["user_code"] == "ABCD-1234"
    assert data["device_code"] == "devcode123"
    assert data["verification_uri"] == "https://github.com/login/device"


def test_github_device_code_requires_auth(client):
    r = client.post("/api/accounts/github/device-code")
    assert r.status_code == 401


def test_github_device_token_missing_device_code(client):
    _setup_user(client, "ghdf3")
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"):
        r = client.post("/api/accounts/github/device-token", json={})
    assert r.status_code == 422


def test_github_device_token_pending_mocked(client):
    _setup_user(client, "ghdf4")
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json({"error": "authorization_pending"})
    ):
        r = client.post(
            "/api/accounts/github/device-token", json={"device_code": "devcode123"}
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["pending"] is True


def test_github_device_token_success_mocked(client):
    _setup_user(client, "ghdf5")
    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"), patch.object(
        httpx.AsyncClient, "post", new=_mock_post_json({"access_token": "ghu_faketoken123"})
    ):
        r = client.post(
            "/api/accounts/github/device-token", json={"device_code": "devcode123"}
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["access_token"] == "ghu_faketoken123"


def test_github_device_token_requires_auth(client):
    r = client.post("/api/accounts/github/device-token", json={"device_code": "x"})
    assert r.status_code == 401
