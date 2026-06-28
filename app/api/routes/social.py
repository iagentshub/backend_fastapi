"""Rutas del catálogo social: visibilidad pública, exploración y stars."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import app.config.data as _cfg
from app.api.routes.auth import require_auth
from app.storage.db import IS_PG, PH, close_db, open_db
from app.storage.storage import AgentStorage, SkillStorage

router = APIRouter(tags=["social"])

CATEGORIES = [
    "Coding", "Writing", "Research", "Data", "DevOps",
    "Support", "Education", "Productivity", "Marketing", "Finance", "Other",
]

_PUBLIC_VAL = True if IS_PG else 1


def _check_category(cat: str) -> None:
    if cat not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Categoría inválida. Opciones: {CATEGORIES}",
        )


def _upsert_social(
    cur: Any,
    resource_type: str,
    resource_id: str,
    owner: str,
    name: str,
    description: str,
    category: str,
    trial_missing_deps: str,
    tags: str = "[]",
    is_public: int = 0,
    labels: str = '["private"]',
) -> None:
    if IS_PG:
        cur.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (resource_type, resource_id, owner) DO UPDATE SET "
            "name=EXCLUDED.name, description=EXCLUDED.description, is_public=EXCLUDED.is_public, "
            "category=EXCLUDED.category, trial_missing_deps=EXCLUDED.trial_missing_deps, "
            "tags=EXCLUDED.tags, labels=EXCLUDED.labels, updated_at=now()",
            (resource_type, resource_id, owner, name, description, bool(is_public), category, trial_missing_deps, tags, labels),
        )
    else:
        cur.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(resource_type, resource_id, owner) DO UPDATE SET "
            "name=excluded.name, description=excluded.description, is_public=excluded.is_public, "
            "category=excluded.category, trial_missing_deps=excluded.trial_missing_deps, "
            "tags=excluded.tags, labels=excluded.labels, updated_at=excluded.updated_at",
            (resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels),
        )


class _AgentVisibilityBody(BaseModel):
    is_public: bool
    category: str
    trial_missing_deps: str = "warn"


class _SkillVisibilityBody(BaseModel):
    is_public: bool
    category: str


class _KnowledgeVisibilityBody(BaseModel):
    is_public: bool
    category: str


@router.put("/api/agents/{scope}/{agent_id}/visibility")
async def set_agent_visibility(
    scope: str,
    agent_id: str,
    body: _AgentVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    if body.trial_missing_deps not in ("warn", "silent"):
        raise HTTPException(status_code=422, detail="trial_missing_deps debe ser 'warn' o 'silent'")
    agents = AgentStorage(_cfg.AGENTS_DIR)
    agent = agents.get(agent_id, scope)
    if not agent:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    resource_labels = agent.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        if body.is_public:
            _upsert_social(
                cur, "agent", agent_id, username,
                agent.get("name", agent_id),
                agent.get("description", ""),
                body.category,
                body.trial_missing_deps,
                json.dumps(agent.get("tags") or []),
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            cur.execute(
                f"DELETE FROM resource_social "
                f"WHERE resource_type={PH} AND resource_id={PH} AND owner={PH}",
                ("agent", agent_id, username),
            )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


@router.put("/api/skills/{scope}/{skill_id}/visibility")
async def set_skill_visibility(
    scope: str,
    skill_id: str,
    body: _SkillVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    skills = SkillStorage(_cfg.SKILLS_DIR)
    skill = skills.get(scope, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    resource_labels = skill.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        if body.is_public:
            _upsert_social(
                cur, "skill", skill_id, username,
                skill.get("name", skill_id),
                skill.get("description", ""),
                body.category,
                "warn",
                json.dumps(skill.get("tags") or []),
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            cur.execute(
                f"DELETE FROM resource_social "
                f"WHERE resource_type={PH} AND resource_id={PH} AND owner={PH}",
                ("skill", skill_id, username),
            )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


@router.put("/api/knowledge/folders/{folder_id}/visibility")
async def set_knowledge_visibility(
    folder_id: str,
    body: _KnowledgeVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT name FROM knowledge_folders WHERE id={PH} AND owner_id={PH}",
            (folder_id, username),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Carpeta no encontrada")
        folder_name = row["name"]
        if body.is_public:
            _upsert_social(
                cur, "knowledge", folder_id, username,
                folder_name,
                "",
                body.category,
                "warn",
                "[]",
                1,
                '["private"]',
            )
        else:
            cur.execute(
                f"DELETE FROM resource_social "
                f"WHERE resource_type={PH} AND resource_id={PH} AND owner={PH}",
                ("knowledge", folder_id, username),
            )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


@router.get("/api/explore")
async def explore(
    type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    label: Optional[str] = None,
    limit: int = 40,
    offset: int = 0,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    limit = min(limit, 100)
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        conditions: List[str] = [f"is_public = {PH}"]
        params: List[Any] = [_PUBLIC_VAL]
        if type and type != "all":
            conditions.append(f"resource_type = {PH}")
            params.append(type)
        if category:
            conditions.append(f"category = {PH}")
            params.append(category)
        if q:
            conditions.append(f"(name LIKE {PH} OR description LIKE {PH})")
            params.extend([f"%{q}%", f"%{q}%"])
        if tag:
            conditions.append(f"tags LIKE {PH}")
            params.append(f'%"{tag}"%')
        if label:
            conditions.append(f"labels LIKE {PH}")
            params.append(f'%"{label}"%')
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        cur.execute(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, fork_of_user, fork_of_id, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC "
            f"LIMIT {PH} OFFSET {PH}",
            params,
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row["tags"] = json.loads(row.get("tags") or "[]")
            except (ValueError, TypeError):
                row["tags"] = []
            try:
                row["labels"] = json.loads(row.get("labels") or '["private"]')
            except (ValueError, TypeError):
                row["labels"] = ["private"]
            rows.append(row)
        return rows
    finally:
        close_db(conn)


@router.get("/api/social/me/resources")
async def my_resources(
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """All resource_social rows owned by the current user (public + private)."""
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        conditions: List[str] = [f"owner = {PH}"]
        params: List[Any] = [username]
        if type and type != "all":
            conditions.append(f"resource_type = {PH}")
            params.append(type)
        where = " AND ".join(conditions)
        cur.execute(
            f"SELECT resource_type, resource_id, owner, name, description, is_public, category, "
            f"stars_count, fork_of_user, fork_of_id, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY updated_at DESC",
            params,
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row["tags"] = json.loads(row.get("tags") or "[]")
            except (ValueError, TypeError):
                row["tags"] = []
            try:
                row["labels"] = json.loads(row.get("labels") or '["private"]')
            except (ValueError, TypeError):
                row["labels"] = ["private"]
            rows.append(row)
        return {"resources": rows}
    finally:
        close_db(conn)


@router.get("/api/users/{target_username}/resources")
async def user_resources(
    target_username: str,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        conditions: List[str] = [f"is_public = {PH}", f"owner = {PH}"]
        params: List[Any] = [_PUBLIC_VAL, target_username]
        if type and type != "all":
            conditions.append(f"resource_type = {PH}")
            params.append(type)
        where = " AND ".join(conditions)
        cur.execute(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, fork_of_user, fork_of_id, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC",
            params,
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row["tags"] = json.loads(row.get("tags") or "[]")
            except (ValueError, TypeError):
                row["tags"] = []
            try:
                row["labels"] = json.loads(row.get("labels") or '["private"]')
            except (ValueError, TypeError):
                row["labels"] = ["private"]
            rows.append(row)
        return rows
    finally:
        close_db(conn)


@router.post("/api/users/{target}/follow")
async def follow_user(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    if target == username:
        raise HTTPException(status_code=400, detail="No puedes seguirte a ti mismo")
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM users WHERE username = {PH}", (target,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        if IS_PG:
            cur.execute(
                "INSERT INTO user_follows (follower, following) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (username, target),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO user_follows (follower, following) VALUES (?, ?)",
                (username, target),
            )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


@router.delete("/api/users/{target}/follow")
async def unfollow_user(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM user_follows WHERE follower = {PH} AND following = {PH}",
            (username, target),
        )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


@router.get("/api/users/{target}/follow-status")
async def follow_status(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT 1 FROM user_follows WHERE follower = {PH} AND following = {PH}",
            (username, target),
        )
        is_following = cur.fetchone() is not None
        cur.execute(
            f"SELECT COUNT(*) FROM user_follows WHERE following = {PH}",
            (target,),
        )
        followers_count = cur.fetchone()[0]
        cur.execute(
            f"SELECT COUNT(*) FROM user_follows WHERE follower = {PH}",
            (target,),
        )
        following_count = cur.fetchone()[0]
    finally:
        close_db(conn)
    return {
        "following": is_following,
        "followers_count": followers_count,
        "following_count": following_count,
    }


@router.get("/api/feed")
async def get_feed(
    limit: int = 40,
    offset: int = 0,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    limit = min(limit, 100)
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        conditions: List[str] = [
            f"owner IN (SELECT following FROM user_follows WHERE follower = {PH})",
            f"is_public = {PH}",
        ]
        params: List[Any] = [username, _PUBLIC_VAL]
        if type and type != "all":
            conditions.append(f"resource_type = {PH}")
            params.append(type)
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        cur.execute(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, tags, labels, updated_at "
            f"FROM resource_social "
            f"WHERE {where} "
            f"ORDER BY updated_at DESC "
            f"LIMIT {PH} OFFSET {PH}",
            params,
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row["tags"] = json.loads(row.get("tags") or "[]")
            except (ValueError, TypeError):
                row["tags"] = []
            try:
                row["labels"] = json.loads(row.get("labels") or '["private"]')
            except (ValueError, TypeError):
                row["labels"] = ["private"]
            rows.append(row)
        return rows
    finally:
        close_db(conn)


@router.post("/api/{resource_type}/{resource_id}/star")
async def star_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        if IS_PG:
            cur.execute(
                "INSERT INTO resource_stars (username, resource_type, resource_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (username, resource_type, resource_id),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO resource_stars (username, resource_type, resource_id) "
                "VALUES (?, ?, ?)",
                (username, resource_type, resource_id),
            )
        cur.execute(
            f"UPDATE resource_social SET stars_count = ("
            f"SELECT COUNT(*) FROM resource_stars "
            f"WHERE resource_type={PH} AND resource_id={PH}"
            f") WHERE resource_type={PH} AND resource_id={PH}",
            (resource_type, resource_id, resource_type, resource_id),
        )
        conn.commit()
        cur.execute(
            f"SELECT COUNT(*) FROM resource_stars "
            f"WHERE resource_type={PH} AND resource_id={PH}",
            (resource_type, resource_id),
        )
        count = cur.fetchone()[0]
        return {"ok": True, "stars": count}
    finally:
        close_db(conn)


@router.post("/api/agents/{scope}/{source_id}/fork")
async def fork_agent(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    agents = AgentStorage(_cfg.AGENTS_DIR)
    source = agents.get(source_id, scope)
    if not source:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    if scope != "public":
        conn = open_db(_cfg.DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM resource_social "
                f"WHERE resource_type={PH} AND resource_id={PH} AND is_public={PH}",
                ("agent", source_id, _PUBLIC_VAL),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=403, detail="El agente no es público")
        finally:
            close_db(conn)

    source_owner = source.get("owner_id") or ""
    fork_payload = {
        k: v for k, v in source.items()
        if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
    }
    fork_payload["name"] = f"Fork of {source.get('name', source_id)}"

    try:
        result = agents.save(fork_payload, "private", owner_id=username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    new_id = result["id"]
    fork_name = result["name"]

    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        fork_tags = json.dumps(source.get("tags") or [])
        if IS_PG:
            cur.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (%s, %s, %s, %s, %s, FALSE, 'Other', 'warn', %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                ("agent", new_id, username, fork_name, source.get("description", ""),
                 source_owner, source_id, fork_tags),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
                ("agent", new_id, username, fork_name, source.get("description", ""),
                 source_owner, source_id, fork_tags),
            )
        conn.commit()
    finally:
        close_db(conn)

    return {"ok": True, "agent_id": new_id, "name": fork_name}


@router.post("/api/skills/{scope}/{source_id}/fork")
async def fork_skill(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = SkillStorage(_cfg.SKILLS_DIR)
    source = skills.get(scope, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Skill no encontrada")

    if scope != "public":
        conn = open_db(_cfg.DB_FILE)
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM resource_social "
                f"WHERE resource_type={PH} AND resource_id={PH} AND is_public={PH}",
                ("skill", source_id, _PUBLIC_VAL),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=403, detail="La skill no es pública")
        finally:
            close_db(conn)

    source_owner = source.get("owner_id") or ""
    fork_payload = {k: v for k, v in source.items() if k not in ("id", "scope", "owner_id")}
    fork_payload["name"] = f"Fork of {source.get('name', source_id)}"

    try:
        result = skills.save("private", fork_payload, owner_id=username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    new_id = result["id"]
    fork_name = result["name"]

    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        fork_tags = json.dumps(source.get("tags") or [])
        if IS_PG:
            cur.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (%s, %s, %s, %s, %s, FALSE, 'Other', 'warn', %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                ("skill", new_id, username, fork_name, source.get("description", ""),
                 source_owner, source_id, fork_tags),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
                ("skill", new_id, username, fork_name, source.get("description", ""),
                 source_owner, source_id, fork_tags),
            )
        conn.commit()
    finally:
        close_db(conn)

    return {"ok": True, "skill_id": new_id, "name": fork_name}


@router.delete("/api/{resource_type}/{resource_id}/star")
async def unstar_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    conn = open_db(_cfg.DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM resource_stars "
            f"WHERE username={PH} AND resource_type={PH} AND resource_id={PH}",
            (username, resource_type, resource_id),
        )
        cur.execute(
            f"UPDATE resource_social SET stars_count = ("
            f"SELECT COUNT(*) FROM resource_stars "
            f"WHERE resource_type={PH} AND resource_id={PH}"
            f") WHERE resource_type={PH} AND resource_id={PH}",
            (resource_type, resource_id, resource_type, resource_id),
        )
        conn.commit()
        cur.execute(
            f"SELECT COUNT(*) FROM resource_stars "
            f"WHERE resource_type={PH} AND resource_id={PH}",
            (resource_type, resource_id),
        )
        count = cur.fetchone()[0]
        return {"ok": True, "stars": count}
    finally:
        close_db(conn)
