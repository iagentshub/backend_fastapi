"""Tests de cobertura adicional para connections.py.

Cubre: hub-sync, import-models, sesiones guest, ownership personal, test-all con
filtro de ids, expansión Ollama, y ramas de error no cubiertas en otros tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

_CONN_OPENAI = {
    "type": "openai",
    "name": "Test OpenAI Missing",
    "api_key": "sk-test-key",
    "model": "gpt-4o",
}

_CONN_IAGENTSHUB = {
    "type": "iagentshub",
    "name": "Mi Hub Missing",
    "url": "https://hub.example.com",
    "username": "hubuser",
    "api_key": "hubpass",
}

_CONN_OLLAMA_BASE = {
    "type": "ollama",
    "name": "Mi Ollama Base",
    "host": "http://localhost:11434",
}


def _setup_user(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _create_conn(client, payload: dict) -> dict:
    r = client.post("/api/connections", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. Tipo inválido → 422 ────────────────────────────────────────────────────


def test_save_connection_invalid_type_returns_422(client):
    _setup_user(client, "invtype_u1")
    r = client.post(
        "/api/connections",
        json={"type": "provider_that_doesnt_exist", "name": "Bad"},
    )
    assert r.status_code == 422


# ── 2. Las conexiones nuevas siempre son personales ──────────────────────────


def test_save_connection_in_team_group_is_still_personal(client):
    username = _setup_user(client, "personal_u1")
    group = client.post("/api/groups", json={"name": "Equipo personal"}).json()

    from app.auth.auth import create_token, get_user_by_username

    client.cookies.set("ga_token", create_token(username, group_id=group["id"]))
    r = client.post("/api/connections", json={**_CONN_OPENAI, "scope": "group"})
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert "api_key" not in data
    assert data["owner_id"] == asyncio.run(get_user_by_username(username))["id"]


def test_edit_legacy_group_connection_preserves_owner_and_id(client):
    username = _setup_user(client, "legacy_group_conn_u1")
    group = client.post("/api/groups", json={"name": "Equipo legacy"}).json()

    from app.auth.auth import create_token
    from app.storage.connection_storage import ConnectionStorage

    legacy = asyncio.run(ConnectionStorage().save(_CONN_OPENAI, owner_id=group["id"]))
    client.cookies.set("ga_token", create_token(username, group_id=group["id"]))

    r = client.post(
        "/api/connections",
        json={**_CONN_OPENAI, "id": legacy["id"], "name": "Legacy editada"},
    )

    assert r.status_code == 200
    assert r.json()["id"] == legacy["id"]
    assert r.json()["owner_id"] == group["id"]
    assert r.json()["name"] == "Legacy editada"


# ── 3. Sesión guest — save / get / delete / test ─────────────────────────────


def test_guest_save_connection(client):
    """Un id fabricado por el cliente se ignora: el servidor genera el id."""
    client.post("/api/auth/guest")
    r = client.post("/api/connections", json={**_CONN_OPENAI, "id": "g-save-1"})
    assert r.status_code == 200
    data = r.json()
    assert data["id"] and data["id"] != "g-save-1"
    assert "api_key" not in data


def test_guest_get_connection_found(client):
    client.post("/api/auth/guest")
    r = client.post("/api/connections", json=_CONN_OPENAI)
    conn_id = r.json()["id"]
    r = client.get(f"/api/connections/{conn_id}")
    assert r.status_code == 200
    assert r.json()["id"] == conn_id


def test_guest_get_connection_not_found(client):
    client.post("/api/auth/guest")
    r = client.get("/api/connections/ghost-guest-xyz")
    assert r.status_code == 404


def test_guest_delete_connection_ok(client):
    client.post("/api/auth/guest")
    r = client.post("/api/connections", json=_CONN_OPENAI)
    conn_id = r.json()["id"]
    r = client.delete(f"/api/connections/{conn_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_guest_delete_connection_not_found(client):
    client.post("/api/auth/guest")
    r = client.delete("/api/connections/ghost-guest-del")
    assert r.status_code == 404


def test_guest_test_connection(client):
    """Guest puede testear una conexión guardada en su sesión efímera."""
    client.post("/api/auth/guest")
    r = client.post("/api/connections", json=_CONN_OPENAI)
    conn_id = r.json()["id"]

    mock_result = MagicMock()
    mock_result.ok = True
    mock_result.message = "OK"
    mock_result.detail = ""

    with patch("app.connections.openai.OpenAIProvider.test", return_value=mock_result):
        r = client.post(f"/api/connections/{conn_id}/test")

    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── 4. test-all con filtro de IDs ─────────────────────────────────────────────


def test_test_all_with_ids_filter(admin_client):
    """ids=[...] en test-all filtra qué conexiones se testean."""
    c1 = _create_conn(admin_client, _CONN_OPENAI)
    c2 = _create_conn(
        admin_client, {**_CONN_OPENAI, "name": "Conn2 Missing", "model": "gpt-3.5"}
    )

    mock_result = MagicMock()
    mock_result.ok = False
    mock_result.message = "fail"
    mock_result.detail = ""

    with patch("app.connections.openai.OpenAIProvider.test", return_value=mock_result):
        r = admin_client.post("/api/connections/test-all", json={"ids": [c1["id"]]})

    assert r.status_code == 200
    returned_ids = [item["id"] for item in r.json()]
    assert c1["id"] in returned_ids
    assert c2["id"] not in returned_ids


# ── 5. import_models ──────────────────────────────────────────────────────────


def test_import_models_not_found(admin_client):
    r = admin_client.post("/api/connections/doesnotexist123/import-models")
    assert r.status_code == 404


def test_import_models_unsupported_type_returns_400(admin_client):
    """iagentshub no tiene soporte de import de modelos."""
    created = _create_conn(admin_client, _CONN_IAGENTSHUB)
    r = admin_client.post(f"/api/connections/{created['id']}/import-models")
    assert r.status_code == 400


def test_import_models_no_models_returns_502(admin_client):
    created = _create_conn(admin_client, _CONN_OPENAI)
    with patch(
        "app.api.routes.connection_sync.fetch_provider_models",
        new_callable=AsyncMock,
        return_value=[],
    ):
        r = admin_client.post(f"/api/connections/{created['id']}/import-models")
    assert r.status_code == 502


def test_import_models_creates_connections(admin_client):
    created = _create_conn(admin_client, _CONN_OPENAI)
    models = ["gpt-4o", "gpt-4-turbo"]
    with patch(
        "app.api.routes.connection_sync.fetch_provider_models",
        new_callable=AsyncMock,
        return_value=models,
    ):
        r = admin_client.post(f"/api/connections/{created['id']}/import-models")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["created"] == 2
    assert set(data["models"]) == set(models)


def test_import_models_updates_on_second_call(admin_client):
    """Segunda llamada actualiza las conexiones importadas en lugar de duplicarlas."""
    created = _create_conn(admin_client, _CONN_OPENAI)
    conn_id = created["id"]
    with patch(
        "app.api.routes.connection_sync.fetch_provider_models",
        new_callable=AsyncMock,
        return_value=["gpt-4o"],
    ):
        admin_client.post(f"/api/connections/{conn_id}/import-models")
        r = admin_client.post(f"/api/connections/{conn_id}/import-models")
    assert r.status_code == 200
    data = r.json()
    assert data["updated"] == 1
    assert data["created"] == 0


# ── 6. hub_sync ───────────────────────────────────────────────────────────────


def test_hub_sync_connection_not_found(admin_client):
    r = admin_client.post("/api/connections/ghost-hub-id/hub-sync")
    assert r.status_code == 404


def test_hub_sync_wrong_type_returns_400(admin_client):
    """hub-sync solo funciona con conexiones de tipo iagentshub."""
    created = _create_conn(admin_client, _CONN_OPENAI)
    r = admin_client.post(f"/api/connections/{created['id']}/hub-sync")
    assert r.status_code == 400


def test_hub_sync_login_failure_returns_502(admin_client):
    created = _create_conn(admin_client, _CONN_IAGENTSHUB)
    with patch(
        "app.connections.iagentshub._login", side_effect=Exception("auth failed")
    ):
        r = admin_client.post(f"/api/connections/{created['id']}/hub-sync")
    assert r.status_code == 502


def test_hub_sync_success_empty_hub(admin_client):
    """Hub vacío (listas vacías) devuelve ok=True y errors=[]."""
    created = _create_conn(admin_client, _CONN_IAGENTSHUB)

    mock_get = AsyncMock(return_value=[])

    with (
        patch("app.connections.iagentshub._login", return_value="fake-token"),
        patch("app.services.hub_sync._get_remote_json", new=mock_get),
    ):
        r = admin_client.post(f"/api/connections/{created['id']}/hub-sync")

    assert r.status_code == 200
    data = r.json()
    assert data["errors"] == []
    assert data["ok"] is True
    assert data["agents"] == 0
    assert data["connections"] == 0


def test_hub_sync_reports_each_knowledge_save_failure(admin_client):
    created = _create_conn(admin_client, _CONN_IAGENTSHUB)

    async def mock_get(base_url, path, headers):
        if path.startswith("/api/knowledge?"):
            return [{"id": "doc-fallido", "title": "Documento"}]
        return []

    with (
        patch("app.connections.iagentshub._login", return_value="fake-token"),
        patch("app.services.hub_sync._get_remote_json", side_effect=mock_get),
        patch(
            "app.services.hub_sync._know_storage.save",
            new=AsyncMock(side_effect=RuntimeError("escritura fallida")),
        ),
    ):
        response = admin_client.post(f"/api/connections/{created['id']}/hub-sync")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["errors"] == ["conocimiento doc-fallido: escritura fallida"]


# ── 7. Expansión Ollama en list_connections ───────────────────────────────────


def test_list_connections_expands_ollama_base(client):
    """Conexión Ollama sin modelo genera entradas por modelo descubierto."""
    _setup_user(client, "ollama_exp_u1")
    client.post("/api/connections", json=_CONN_OLLAMA_BASE)

    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        return_value=["llama3:latest", "mistral:7b"],
    ):
        r = client.get("/api/connections")

    assert r.status_code == 200
    data = r.json()
    names = [c.get("name") for c in data]
    assert "llama3:latest" in names


def test_list_connections_ollama_no_models_returns_base(client):
    """Sin modelos disponibles devuelve la conexión base Ollama."""
    _setup_user(client, "ollama_nomod_u1")
    client.post("/api/connections", json=_CONN_OLLAMA_BASE)

    with patch(
        "app.connections.ollama.OllamaProvider.fetch_models",
        return_value=[],
    ):
        r = client.get("/api/connections")

    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
