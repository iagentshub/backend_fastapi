"""Tests de skills: GET, POST, DELETE /api/skills."""

from __future__ import annotations

import asyncio

_SKILL_PAYLOAD = {
    "name": "Test Skill",
    "description": "Una skill de prueba.",
    "content": "Este es el contenido de la skill.",
}


def test_list_skills_empty(admin_client):
    r = admin_client.get("/api/v2/skills")
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


def test_save_private_skill(admin_client):
    r = admin_client.post("/api/skills/private", json=_SKILL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Skill"
    assert "id" in data
    assert "tags" not in data


def test_save_public_skill(admin_client):
    r = admin_client.post("/api/skills/public", json=_SKILL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["scope"] == "public"
    assert data["labels"] == ["public", "community"]
    assert data["owner_id"]


def test_save_skill_rejects_free_tags(admin_client):
    r = admin_client.post(
        "/api/skills/private",
        json={**_SKILL_PAYLOAD, "tags": ["inventada"]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "tags"


def test_save_skill_rejects_unknown_category(admin_client):
    r = admin_client.post(
        "/api/skills/private",
        json={**_SKILL_PAYLOAD, "category": "inventada"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "category"


def test_save_skill_rejects_labels_outside_catalog(admin_client):
    r = admin_client.post(
        "/api/skills/private",
        json={**_SKILL_PAYLOAD, "labels": ["inventada"]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "labels"


def test_save_skill_ignores_client_id(admin_client):
    """Un id fabricado por el cliente se ignora en el alta: lo genera el servidor."""
    r = admin_client.post(
        "/api/skills/private", json={**_SKILL_PAYLOAD, "id": "mi-skill"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] and data["id"] != "mi-skill"


def test_update_skill_keeps_existing_id(admin_client):
    created = admin_client.post("/api/skills/private", json=_SKILL_PAYLOAD).json()
    r = admin_client.post(
        "/api/skills/private",
        json={**_SKILL_PAYLOAD, "id": created["id"], "name": "Editada"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == created["id"]
    assert data["name"] == "Editada"


def test_get_private_skill(admin_client):
    created = admin_client.post("/api/skills/private", json=_SKILL_PAYLOAD).json()
    r = admin_client.get(f"/api/skills/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_skill_not_found(admin_client):
    r = admin_client.get("/api/skills/private/nonexistent-skill")
    assert r.status_code == 404


def test_delete_private_skill(admin_client):
    created = admin_client.post("/api/skills/private", json=_SKILL_PAYLOAD).json()
    r = admin_client.delete(f"/api/skills/private/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_owned_public_skill(admin_client):
    created = admin_client.post("/api/skills/public", json=_SKILL_PAYLOAD).json()
    r = admin_client.delete(f"/api/skills/public/{created['id']}")
    assert r.status_code == 200


def test_other_user_cannot_edit_or_delete_public_skill(admin_client):
    from app.auth.auth import create_token, register_user

    created = admin_client.post("/api/skills/public", json=_SKILL_PAYLOAD).json()
    asyncio.run(register_user("skillother", "pass1234", email="skillother@example.com"))
    admin_client.cookies.set("ga_token", create_token("skillother"))

    edited = admin_client.post(
        "/api/skills/public",
        json={**_SKILL_PAYLOAD, "id": created["id"], "name": "Secuestrada"},
    )
    deleted = admin_client.delete(f"/api/skills/public/{created['id']}")
    assert edited.status_code == 403
    assert deleted.status_code == 403


def test_skills_requires_auth(client):
    r = client.get("/api/v2/skills")
    assert r.status_code == 401
