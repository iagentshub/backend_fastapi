"""Tests de soporte de etiquetas (tags) en agentes, skills y explore."""
from __future__ import annotations


def _login(client, username="tagstest", password="pass1234"):
    from app.auth.auth import create_token, register_user
    register_user(username, password, email=f"{username}@example.com")
    client.cookies.set("ga_token", create_token(username))
    return username


def test_agent_tags_persisted_and_returned(client):
    """Guardar un agente con tags y verificar que aparecen en GET."""
    _login(client, "taguser1")
    r = client.post("/api/agents", json={
        "name": "Tagged Agent",
        "description": "agente con etiquetas",
        "tags": ["python", "bot", "nlp"],
    })
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.get(f"/api/agents/{agent_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert set(data.get("tags", [])) == {"python", "bot", "nlp"}


def test_agent_tags_go_to_resource_social(client):
    """Marcar un agente con tags como público y verificar que tags van a resource_social."""
    _login(client, "taguser2")
    r = client.post("/api/agents", json={
        "name": "Public Tagged Agent",
        "description": "agente público con etiquetas",
        "tags": ["python", "automation"],
        "labels": ["public"],
    })
    assert r.status_code == 200
    agent_id = r.json()["id"]

    r2 = client.put(f"/api/agents/private/{agent_id}/visibility", json={
        "is_public": True,
        "category": "Coding",
        "trial_missing_deps": "warn",
    })
    assert r2.status_code == 200

    from app.config.data import DB_FILE
    from app.storage.db import PH, close_db, open_db
    import json as _json
    conn = open_db(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT tags FROM resource_social WHERE resource_id = {PH}",
            (agent_id,),
        )
        row = cur.fetchone()
    finally:
        close_db(conn)

    assert row is not None
    stored_tags = _json.loads(row["tags"])
    assert set(stored_tags) == {"python", "automation"}


def test_explore_filter_by_tag(client):
    """Filtrar explore por ?tag=python — solo devuelve recursos con esa tag."""
    _login(client, "taguser3")

    # Agente con tag python
    r = client.post("/api/agents", json={
        "name": "Python Agent",
        "description": "agente python",
        "tags": ["python"],
        "labels": ["public"],
    })
    assert r.status_code == 200
    python_id = r.json()["id"]
    client.put(f"/api/agents/private/{python_id}/visibility", json={
        "is_public": True,
        "category": "Coding",
        "trial_missing_deps": "warn",
    })

    # Agente SIN tag python
    r2 = client.post("/api/agents", json={
        "name": "Java Agent",
        "description": "agente java",
        "tags": ["java"],
        "labels": ["public"],
    })
    assert r2.status_code == 200
    java_id = r2.json()["id"]
    client.put(f"/api/agents/private/{java_id}/visibility", json={
        "is_public": True,
        "category": "Coding",
        "trial_missing_deps": "warn",
    })

    r3 = client.get("/api/explore", params={"tag": "python"})
    assert r3.status_code == 200
    results = r3.json()
    result_ids = [x["resource_id"] for x in results]
    assert python_id in result_ids
    assert java_id not in result_ids

    # Verificar que tags se deserializa como lista
    python_entry = next(x for x in results if x["resource_id"] == python_id)
    assert isinstance(python_entry["tags"], list)
    assert "python" in python_entry["tags"]


def test_skill_tags_persisted(client):
    """Guardar una skill con tags y verificar que se persisten."""
    _login(client, "taguser4")
    r = client.post("/api/skills/private", json={
        "name": "Tagged Skill",
        "description": "skill con etiquetas",
        "content": "# instrucciones de la skill",
        "tags": ["nlp", "text", "ml"],
    })
    assert r.status_code == 200
    skill_id = r.json()["id"]

    r2 = client.get(f"/api/skills/private/{skill_id}")
    assert r2.status_code == 200
    data = r2.json()
    assert set(data.get("tags", [])) == {"nlp", "text", "ml"}
