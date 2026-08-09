"""Tests extendidos de conexiones: paginación, raw, get por ID, update, test, test-all, ollama-models."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

_CONN_OPENAI = {
    "type": "openai",
    "label": "Test OpenAI",
    "name": "Mi OpenAI",
    "api_key": "sk-test-key-openai",
    "model": "gpt-4o",
}

_CONN_ANTHROPIC = {
    "type": "claude",
    "label": "Test Claude",
    "name": "Mi Claude",
    "api_key": "sk-ant-test-key",
    "model": "claude-3-5-sonnet-20241022",
}


def _setup_user(client, username="connuser"):
    """Registra un usuario y autentica el client."""
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return username


def _create_conn(client, payload=None):
    """Crea una conexión y devuelve su JSON."""
    p = payload or _CONN_OPENAI
    r = client.post("/api/connections", json=p)
    assert r.status_code == 200, r.text
    return r.json()


# ── GET /api/connections (paginación) ─────────────────────────────────────────


def test_list_connections_pagination_limit(admin_client):
    """limit=1 devuelve solo 1 elemento."""
    _create_conn(admin_client, _CONN_OPENAI)
    _create_conn(admin_client, _CONN_ANTHROPIC)
    r = admin_client.get("/api/connections?limit=1")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1


def test_list_connections_pagination_offset(admin_client):
    """offset desplaza la lista de resultados."""
    _create_conn(admin_client, _CONN_OPENAI)
    _create_conn(admin_client, _CONN_ANTHROPIC)
    r_all = admin_client.get("/api/connections")
    assert r_all.status_code == 200
    total = len(r_all.json())

    r_off = admin_client.get("/api/connections?offset=1")
    assert r_off.status_code == 200
    assert len(r_off.json()) == total - 1


def test_list_connections_zero_limit_returns_all(admin_client):
    """limit=0 (valor por defecto) devuelve todos."""
    _create_conn(admin_client, _CONN_OPENAI)
    _create_conn(admin_client, _CONN_ANTHROPIC)
    r = admin_client.get("/api/connections?limit=0")
    assert r.status_code == 200
    assert len(r.json()) >= 2


# ── GET /api/connections/raw ──────────────────────────────────────────────────


def test_list_connections_raw_returns_list(admin_client):
    """/raw devuelve lista sin api_key."""
    _create_conn(admin_client, _CONN_OPENAI)
    r = admin_client.get("/api/connections/raw")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for item in data:
        assert "api_key" not in item


def test_list_connections_raw_requires_auth(client):
    """/raw requiere autenticación."""
    r = client.get("/api/connections/raw")
    assert r.status_code == 401


def test_list_connections_raw_empty_initially(client):
    """Sin auth group no llega — con auth vacío devuelve lista vacía."""
    _setup_user(client, "rawuser")
    r = client.get("/api/connections/raw")
    assert r.status_code == 200
    assert r.json() == []


# ── GET /api/connections/{id} ─────────────────────────────────────────────────


def test_get_connection_by_id(admin_client):
    """Obtener conexión por ID devuelve los datos correctos."""
    created = _create_conn(admin_client, _CONN_OPENAI)
    conn_id = created["id"]
    r = admin_client.get(f"/api/connections/{conn_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == conn_id
    assert data["type"] == "openai"


def test_get_connection_not_found(admin_client):
    """ID inexistente devuelve 404."""
    r = admin_client.get("/api/connections/nonexistent-id-xyz")
    assert r.status_code == 404


def test_get_connection_requires_auth(client):
    """GET /{id} sin auth devuelve 401."""
    r = client.get("/api/connections/some-id")
    assert r.status_code == 401


# ── PUT /api/connections/{id} (update via POST) ────────────────────────────────
# El backend usa POST /api/connections para crear/actualizar (upsert con id existente).


def test_update_connection_via_post(admin_client):
    """Actualizar una conexión enviando el id en el payload."""
    created = _create_conn(admin_client, _CONN_OPENAI)
    conn_id = created["id"]
    updated_payload = {
        **_CONN_OPENAI,
        "id": conn_id,
        "name": "OpenAI Actualizado",
        "model": "gpt-4-turbo",
    }
    r = admin_client.post("/api/connections", json=updated_payload)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "OpenAI Actualizado"
    assert data["model"] == "gpt-4-turbo"

    # Verificar que el GET devuelve el dato actualizado
    r2 = admin_client.get(f"/api/connections/{conn_id}")
    assert r2.status_code == 200
    assert r2.json()["name"] == "OpenAI Actualizado"


# ── POST /api/connections/{id}/test ──────────────────────────────────────────


def test_test_connection_ok_false_when_invalid_key(admin_client):
    """El test de conexión devuelve ok=False con credenciales falsas (sin red real)."""
    created = _create_conn(admin_client, _CONN_OPENAI)
    conn_id = created["id"]

    mock_result = MagicMock()
    mock_result.ok = False
    mock_result.message = "Invalid API key"
    mock_result.detail = ""

    with patch("app.connections.openai.OpenAIProvider.test", return_value=mock_result):
        r = admin_client.post(f"/api/connections/{conn_id}/test")

    assert r.status_code == 200
    data = r.json()
    assert "ok" in data
    assert data["ok"] is False


def test_test_connection_not_found(admin_client):
    """Test de conexión con ID inexistente devuelve 404."""
    r = admin_client.post("/api/connections/ghost-id-xyz/test")
    assert r.status_code == 404


def test_test_connection_requires_auth(client):
    """POST /{id}/test sin auth devuelve 401."""
    r = client.post("/api/connections/some-id/test")
    assert r.status_code == 401


# ── POST /api/connections/test-all ───────────────────────────────────────────


def test_test_all_connections_returns_list(admin_client):
    """test-all con conexión falsa devuelve lista con ok=False (sin red real)."""
    _create_conn(admin_client, _CONN_OPENAI)

    mock_result = MagicMock()
    mock_result.ok = False
    mock_result.message = "Connection refused"
    mock_result.detail = ""

    with patch("app.connections.openai.OpenAIProvider.test", return_value=mock_result):
        r = admin_client.post("/api/connections/test-all", json={})

    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    for item in data:
        assert "id" in item
        assert "ok" in item


def test_test_all_connections_empty_group(client):
    """test-all sin conexiones devuelve lista vacía."""
    _setup_user(client, "testalluser")
    r = client.post("/api/connections/test-all", json={})
    assert r.status_code == 200
    assert r.json() == []


def test_test_all_connections_requires_auth(client):
    """POST /test-all sin auth devuelve 401."""
    r = client.post("/api/connections/test-all", json={})
    assert r.status_code == 401


# ── POST /api/connections/ollama-models ──────────────────────────────────────


def test_ollama_models_returns_error_when_unreachable(client):
    """Cuando Ollama no es accesible, devuelve models=[] y un campo error."""
    _setup_user(client, "ollamauser")
    with patch(
        "app.connections.ollama.OllamaProvider._fetch_tags",
        side_effect=OSError("Connection refused"),
    ):
        r = client.post(
            "/api/connections/ollama-models",
            json={"host": "http://localhost:11434"},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["models"] == []
    assert "error" in data


def test_ollama_models_returns_models_when_available(client):
    """Cuando Ollama responde, devuelve la lista de modelos."""
    _setup_user(client, "ollamauser2")
    fake_tags = {"models": [{"name": "llama3:latest"}, {"name": "mistral:7b"}]}
    # assert_safe_url bloquea localhost (anti-SSRF); en tests lo deshabilitamos
    # para poder ejercer el path feliz sin necesitar un servidor Ollama real.
    with (
        patch("app.api.routes.connection_catalog.assert_safe_url"),
        patch(
            "app.connections.ollama.OllamaProvider._fetch_tags", return_value=fake_tags
        ),
    ):
        r = client.post(
            "/api/connections/ollama-models",
            json={"host": "http://localhost:11434"},
        )
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    assert "llama3:latest" in data["models"]
    assert "mistral:7b" in data["models"]


def test_ollama_models_requires_auth(client):
    """POST /ollama-models sin auth devuelve 401."""
    r = client.post("/api/connections/ollama-models", json={})
    assert r.status_code == 401
