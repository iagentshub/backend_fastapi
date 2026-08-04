"""Tests de prompts: GET, POST, DELETE /api/prompts."""

from __future__ import annotations

import asyncio

_PROMPT_PAYLOAD = {
    "name": "Test Prompt",
    "description": "Un prompt de prueba.",
    "content": "Actúa como un experto en X.",
    "alias": "test-prompt",
}


def test_list_prompts_empty(admin_client):
    r = admin_client.get("/api/prompts")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_save_private_prompt(admin_client):
    r = admin_client.post("/api/prompts/private", json=_PROMPT_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Prompt"
    assert "id" in data
    assert data["alias"] == "test-prompt"


def test_save_public_prompt(admin_client):
    r = admin_client.post("/api/prompts/public", json=_PROMPT_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["scope"] == "public"
    assert data["labels"] == ["public"]
    assert data["owner_id"]


def test_save_prompt_requires_valid_alias(admin_client):
    r = admin_client.post(
        "/api/prompts/private",
        json={**_PROMPT_PAYLOAD, "alias": "a"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "alias"


def test_save_prompt_normalizes_alias_case(admin_client):
    r = admin_client.post(
        "/api/prompts/private",
        json={**_PROMPT_PAYLOAD, "alias": "Test-Prompt"},
    )
    assert r.status_code == 200
    assert r.json()["alias"] == "test-prompt"


def test_save_duplicate_alias_returns_409(admin_client):
    r1 = admin_client.post("/api/prompts/private", json=_PROMPT_PAYLOAD)
    assert r1.status_code == 200
    r2 = admin_client.post(
        "/api/prompts/private",
        json={**_PROMPT_PAYLOAD, "name": "Otro nombre"},
    )
    assert r2.status_code == 409
    body = r2.json()
    assert body["detail"]["code"] == "already_exists"
    assert body["detail"]["resource"] == "prompt"


def test_save_prompt_rejects_labels_outside_catalog(admin_client):
    r = admin_client.post(
        "/api/prompts/private",
        json={**_PROMPT_PAYLOAD, "labels": ["inventada"]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "labels"


def test_save_prompt_ignores_client_id(admin_client):
    """Un id fabricado por el cliente se ignora en el alta: lo genera el servidor."""
    r = admin_client.post(
        "/api/prompts/private", json={**_PROMPT_PAYLOAD, "id": "mi-prompt"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] and data["id"] != "mi-prompt"


def test_update_prompt_keeps_existing_id(admin_client):
    created = admin_client.post("/api/prompts/private", json=_PROMPT_PAYLOAD).json()
    r = admin_client.post(
        "/api/prompts/private",
        json={**_PROMPT_PAYLOAD, "id": created["id"], "name": "Editado"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == created["id"]
    assert data["name"] == "Editado"


def test_get_private_prompt(admin_client):
    created = admin_client.post("/api/prompts/private", json=_PROMPT_PAYLOAD).json()
    r = admin_client.get(f"/api/prompts/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_prompt_not_found(admin_client):
    r = admin_client.get("/api/prompts/private/nonexistent-prompt")
    assert r.status_code == 404


def test_delete_private_prompt(admin_client):
    created = admin_client.post("/api/prompts/private", json=_PROMPT_PAYLOAD).json()
    r = admin_client.delete(f"/api/prompts/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_owned_public_prompt(admin_client):
    created = admin_client.post("/api/prompts/public", json=_PROMPT_PAYLOAD).json()
    r = admin_client.delete(f"/api/prompts/public/{created['id']}")
    assert r.status_code == 200


def test_other_user_cannot_edit_or_delete_public_prompt(admin_client):
    from app.auth.auth import create_token, register_user

    created = admin_client.post("/api/prompts/public", json=_PROMPT_PAYLOAD).json()
    asyncio.run(
        register_user("promptother", "pass1234", email="promptother@example.com")
    )
    admin_client.cookies.set("ga_token", create_token("promptother"))

    edited = admin_client.post(
        "/api/prompts/public",
        json={**_PROMPT_PAYLOAD, "id": created["id"], "name": "Secuestrado"},
    )
    deleted = admin_client.delete(f"/api/prompts/public/{created['id']}")
    assert edited.status_code == 403
    assert deleted.status_code == 403


def test_activate_deactivate_prompt(admin_client):
    created = admin_client.post("/api/prompts/private", json=_PROMPT_PAYLOAD).json()
    r = admin_client.post(f"/api/prompts/{created['id']}/deactivate")
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    r = admin_client.post(f"/api/prompts/{created['id']}/activate")
    assert r.status_code == 200
    assert r.json()["is_active"] is True


def test_prompts_requires_auth(client):
    r = client.get("/api/prompts")
    assert r.status_code == 401
