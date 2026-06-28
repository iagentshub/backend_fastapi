"""Tests del sistema de etiquetas semánticas (labels)."""
from __future__ import annotations

import json


# ── helpers ───────────────────────────────────────────────────────────────────

def _login(client, username="labelsuser", password="pass1234"):
    from app.auth.auth import create_token, register_user
    register_user(username, password, email=f"{username}@example.com")
    client.cookies.set("ga_token", create_token(username))
    return username


def _create_agent(client, name="Label Agent", labels=None):
    payload = {"name": name, "description": "agente de prueba labels"}
    if labels is not None:
        payload["labels"] = labels
    r = client.post("/api/agents", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _create_skill(client, name="Label Skill", labels=None):
    payload = {
        "name": name,
        "description": "skill de prueba labels",
        "content": "# instrucciones de prueba",
    }
    if labels is not None:
        payload["labels"] = labels
    r = client.post("/api/skills/private", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _get_social_row(agent_id, owner):
    from app.config.data import DB_FILE
    from app.storage.db import PH, close_db, open_db
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT is_public, labels FROM resource_social "
            f"WHERE resource_type={PH} AND resource_id={PH} AND owner={PH}",
            ("agent", agent_id, owner),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"is_public": row["is_public"], "labels": json.loads(row["labels"] or '["private"]')}
    finally:
        close_db(conn)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_agent_labels_stored_and_returned(client):
    """Test 1: Crear agente con labels compuestas → GET devuelve esas labels."""
    _login(client, "labels_t1")
    agent = _create_agent(client, "Multi Label Agent", labels=["public", "production"])

    assert "labels" in agent, "save() debe devolver labels"
    assert "public" in agent["labels"]
    assert "production" in agent["labels"]

    # GET /api/agents devuelve las labels
    r = client.get("/api/agents")
    assert r.status_code == 200
    found = next((a for a in r.json() if a["id"] == agent["id"]), None)
    assert found is not None
    assert "public" in found["labels"]
    assert "production" in found["labels"]


def test_agent_public_label_sets_is_public_1(client):
    """Test 2: labels=["public"] + PUT visibility → resource_social.is_public = 1."""
    user = _login(client, "labels_t2")
    agent = _create_agent(client, "Public Label Agent", labels=["public"])
    agent_id = agent["id"]

    r = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={"is_public": True, "category": "Coding", "trial_missing_deps": "warn"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    row = _get_social_row(agent_id, user)
    assert row is not None, "Debe existir fila en resource_social"
    assert row["is_public"] in (1, True), f"is_public debe ser 1, got {row['is_public']}"
    assert "public" in row["labels"]


def test_agent_private_label_sets_is_public_0(client):
    """Test 3: labels=["private"] + PUT visibility → resource_social.is_public = 0."""
    user = _login(client, "labels_t3")
    agent = _create_agent(client, "Private Label Agent", labels=["private"])
    agent_id = agent["id"]

    r = client.put(
        f"/api/agents/private/{agent_id}/visibility",
        json={"is_public": True, "category": "Data", "trial_missing_deps": "warn"},
    )
    assert r.status_code == 200

    row = _get_social_row(agent_id, user)
    assert row is not None, "Debe existir fila en resource_social"
    assert row["is_public"] in (0, False), f"is_public debe ser 0, got {row['is_public']}"
    assert "private" in row["labels"]


def test_list_agents_filtered_by_label(client):
    """Test 4: GET /api/agents?label=favorite devuelve solo agentes con esa label."""
    _login(client, "labels_t4")
    fav = _create_agent(client, "Favorite Agent", labels=["private", "favorite"])
    _create_agent(client, "Normal Agent", labels=["private"])

    r = client.get("/api/agents?label=favorite")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()]
    assert fav["id"] in ids, "El agente con label 'favorite' debe aparecer"

    # Agente sin la label no debe aparecer
    r_all = client.get("/api/agents")
    assert len(r_all.json()) >= 2, "Sin filtro debe haber al menos 2 agentes"

    r_fav = client.get("/api/agents?label=favorite")
    for agent in r_fav.json():
        assert "favorite" in (agent.get("labels") or []), (
            f"Agente {agent['id']} devuelto pero no tiene label 'favorite'"
        )


def test_skill_labels_stored_and_returned(client):
    """Test 5: Skill con labels=["development"] → GET devuelve esa label."""
    _login(client, "labels_t5")
    skill = _create_skill(client, "Dev Skill", labels=["development"])

    assert "labels" in skill, "save() debe devolver labels en la skill"
    assert "development" in skill["labels"]

    r = client.get("/api/skills")
    assert r.status_code == 200
    found = next((s for s in r.json() if s.get("id") == skill["id"]), None)
    assert found is not None, "La skill debe aparecer en GET /api/skills"
    assert "development" in (found.get("labels") or [])


def test_agent_default_label_is_private(client):
    """Test extra: agente sin labels explícitas usa 'private' por defecto."""
    _login(client, "labels_t6")
    agent = _create_agent(client, "Default Label Agent")

    assert "labels" in agent
    assert agent["labels"] == ["private"]
