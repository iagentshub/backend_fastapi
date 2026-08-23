"""Los callers activos de Ollama comparten la política del provider."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.connections.base import TestResult
from app.storage.connection_storage import ConnectionStorage


def _setup_user(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def test_save_connection_rejects_ollama_metadata_host(client):
    _setup_user(client, "ollama_save_ssrf")
    response = client.post(
        "/api/connections",
        json={
            "type": "ollama",
            "name": "Metadata",
            "host": "http://169.254.169.254",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsafe_url"


def test_guest_cannot_store_unsafe_ollama_host(client):
    client.post("/api/auth/guest")
    response = client.post(
        "/api/connections",
        json={
            "type": "ollama",
            "name": "Docker probe",
            "host": "http://172.18.0.2:5432",
        },
    )
    assert response.status_code == 422


def test_allowed_local_ollama_reaches_provider_test(client):
    _setup_user(client, "ollama_local_test")
    created = client.post(
        "/api/connections",
        json={
            "type": "ollama",
            "name": "Local",
            "host": "http://localhost:11434",
        },
    ).json()
    with patch(
        "app.connections.ollama.OllamaProvider.test",
        return_value=TestResult(True, "OK"),
    ) as provider_test:
        response = client.post(f"/api/connections/{created['id']}/test")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    provider_test.assert_called_once()


def test_save_connection_accepts_official_ollama_cloud_url(client):
    _setup_user(client, "ollama_official_save")
    response = client.post(
        "/api/connections",
        json={
            "type": "ollama",
            "name": "Ollama Cloud",
            "host": "https://ollama.com",
            "api_key": "ollama-cloud-key",
        },
    )
    assert response.status_code == 200
    assert response.json()["host"] == "https://ollama.com"


def test_legacy_unsafe_ollama_is_blocked_before_individual_test(client):
    _setup_user(client, "ollama_legacy_one")
    created = client.post(
        "/api/connections",
        json={"type": "ollama", "name": "Legacy unsafe"},
    ).json()
    connection = asyncio.run(
        ConnectionStorage().save(
            {
                "id": created["id"],
                "type": "ollama",
                "name": "Legacy unsafe",
                "host": "http://169.254.169.254",
            },
            owner_id=created["owner_id"],
        )
    )
    with patch("app.connections.ollama.OllamaProvider.test") as provider_test:
        response = client.post(f"/api/connections/{connection['id']}/test")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["message"] == "Destino no permitido"
    provider_test.assert_not_called()


def test_legacy_unsafe_ollama_has_no_latency_in_test_all(client):
    _setup_user(client, "ollama_legacy_all")
    created = client.post(
        "/api/connections",
        json={"type": "ollama", "name": "Legacy unsafe"},
    ).json()
    connection = asyncio.run(
        ConnectionStorage().save(
            {
                "id": created["id"],
                "type": "ollama",
                "name": "Legacy unsafe",
                "host": "http://10.0.0.8:11434",
            },
            owner_id=created["owner_id"],
        )
    )
    with patch("app.connections.ollama.OllamaProvider.test") as provider_test:
        response = client.post(
            "/api/connections/test-all", json={"ids": [connection["id"]]}
        )
    assert response.status_code == 200
    result = response.json()
    assert len(result) == 1
    assert result[0]["id"] == connection["id"]
    assert result[0]["ok"] is False
    assert result[0]["message"] == "Destino no permitido"
    assert result[0]["latency_ms"] is None
    provider_test.assert_not_called()


def test_ollama_catalog_rejects_unsafe_host_before_fetch(client):
    _setup_user(client, "ollama_catalog_ssrf")
    with patch("app.connections.ollama.OllamaProvider.fetch_models") as fetch:
        response = client.post(
            "/api/connections/ollama-models",
            json={"host": "http://127.0.0.1:5432"},
        )
    assert response.status_code == 200
    assert response.json()["models"] == []
    assert "error" in response.json()
    fetch.assert_not_called()


def test_link_ollama_account_rejects_unsafe_host(client):
    _setup_user(client, "ollama_account_ssrf")
    response = client.post(
        "/api/accounts",
        json={
            "provider": "ollama",
            "name": "Unsafe account",
            "host": "http://localhost:5432",
        },
    )
    assert response.status_code == 422


def test_update_ollama_account_rejects_unsafe_host(client):
    _setup_user(client, "ollama_account_update_ssrf")
    created = client.post(
        "/api/accounts",
        json={
            "provider": "ollama",
            "name": "Local",
            "host": "http://localhost:11434",
        },
    ).json()
    response = client.put(
        f"/api/accounts/{created['id']}",
        json={"host": "http://169.254.169.254"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsafe_url"
