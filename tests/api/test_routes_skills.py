"""Tests de skills: GET, POST, DELETE /api/skills."""
from __future__ import annotations


_SKILL_PAYLOAD = {
    "name": "Test Skill",
    "description": "Una skill de prueba.",
    "content": "Este es el contenido de la skill.",
    "tags": ["test"],
}


def test_list_skills_empty(admin_client):
    r = admin_client.get("/api/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_save_private_skill(admin_client):
    r = admin_client.post("/api/skills/private", json=_SKILL_PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Skill"
    assert "id" in data


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


def test_delete_public_skill_forbidden(admin_client):
    """No se pueden eliminar skills públicas."""
    r = admin_client.delete("/api/skills/public/some-public-skill")
    assert r.status_code in (403, 404)


def test_skills_requires_auth(client):
    r = client.get("/api/skills")
    assert r.status_code == 401
