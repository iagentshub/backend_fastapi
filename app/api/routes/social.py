"""Rutas del catálogo social: visibilidad pública, exploración y stars."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import app.config.data as _cfg
from app.api.routes.auth import WorkspaceContext, require_auth, require_workspace
from app.services.chat import stream_chat
from app.storage.db import IS_PG, open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.storage import AgentStorage, ConnectionStorage, MemoryStorage, SkillStorage

router = APIRouter(tags=["social"])


async def _assert_public(resource_type: str, source_id: str) -> None:
    """Fork/link solo están disponibles para contenido público del marketplace."""
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT 1 FROM resource_social WHERE resource_type=? AND resource_id=? AND is_public=?",
            (resource_type, source_id, _PUBLIC_VAL),
        )
    if not row:
        raise HTTPException(status_code=403, detail="No tienes acceso a este recurso")


_inherit_skills_store = SkillStorage(_cfg.SKILLS_DIR)
_inherit_knowledge_store = KnowledgeStorage(_cfg.DB_FILE)
_inherit_memory_store = MemoryStorage(_cfg.MEMORY_DIR)


async def _inherit_resource_ids(
    ids: List[str], resource_type: str, target_owner_id: str
) -> List[str]:
    """Al forkear/enlazar un agente, sus skills/conocimiento privados se clonan junto
    con él (heredados) para que sigan siendo accesibles desde el nuevo dueño. Los
    públicos, o los que ya pertenecen al destino, se referencian tal cual (sin clonar)."""
    new_ids: List[str] = []
    for rid in ids:
        if resource_type == "skill":
            item = await _inherit_skills_store.get_any(rid)
        else:
            item = await _inherit_knowledge_store.get(rid)
        if not item:
            continue
        if item.get("owner_id") == target_owner_id or item.get("scope") == "public":
            new_ids.append(rid)
            continue
        if resource_type == "skill":
            # id propio (no derivado del nombre) para no colisionar con una skill
            # homónima de otro owner — GET /api/skills/{scope}/{id} no filtra por
            # owner_id, así que dos ids iguales de dueños distintos son ambiguos.
            clone = {k: v for k, v in item.items() if k not in ("id", "scope", "owner_id")}
            clone["id"] = uuid4().hex[:12]
            saved = await _inherit_skills_store.save("private", clone, owner_id=target_owner_id)
        else:
            saved = await _inherit_knowledge_store.save(
                type=item.get("type", "url"),
                title=item.get("title", rid),
                source=item.get("source", ""),
                content=item.get("content", ""),
                owner_id=target_owner_id,
            )
        new_ids.append(saved["id"])
    return new_ids


async def _inherit_agent_memory(
    source_agent: Dict[str, Any],
    source_owner: str,
    new_agent_id: str,
    target_owner_id: str,
) -> None:
    """Copia el contenido de memoria del agente original al nuevo, si usa memoria."""
    if not source_agent.get("use_memory"):
        return
    mem_name = source_agent.get("memory_file") or f"{source_agent.get('id')}.md"
    content = await _inherit_memory_store.get(mem_name, source_owner)
    if content:
        await _inherit_memory_store.save(f"{new_agent_id}.md", content, owner_id=target_owner_id)

CATEGORIES = [
    "Coding",
    "Writing",
    "Research",
    "Data",
    "DevOps",
    "Support",
    "Education",
    "Productivity",
    "Marketing",
    "Finance",
    "Other",
]

_PUBLIC_VAL = True if IS_PG else 1


def _check_category(cat: str) -> None:
    if cat not in CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Categoría inválida. Opciones: {CATEGORIES}",
        )


async def _upsert_social(
    conn: Any,
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
        await conn.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now()) "
            "ON CONFLICT (resource_type, resource_id, owner) DO UPDATE SET "
            "name=EXCLUDED.name, description=EXCLUDED.description, is_public=EXCLUDED.is_public, "
            "category=EXCLUDED.category, trial_missing_deps=EXCLUDED.trial_missing_deps, "
            "tags=EXCLUDED.tags, labels=EXCLUDED.labels, updated_at=now()",
            (
                resource_type,
                resource_id,
                owner,
                name,
                description,
                bool(is_public),
                category,
                trial_missing_deps,
                tags,
                labels,
            ),
        )
    else:
        await conn.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name, description, is_public, category, trial_missing_deps, tags, labels, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(resource_type, resource_id, owner) DO UPDATE SET "
            "name=excluded.name, description=excluded.description, is_public=excluded.is_public, "
            "category=excluded.category, trial_missing_deps=excluded.trial_missing_deps, "
            "tags=excluded.tags, labels=excluded.labels, updated_at=excluded.updated_at",
            (
                resource_type,
                resource_id,
                owner,
                name,
                description,
                is_public,
                category,
                trial_missing_deps,
                tags,
                labels,
            ),
        )


class _AgentTryBody(BaseModel):
    connection_id: str
    message: str


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
        raise HTTPException(
            status_code=422, detail="trial_missing_deps debe ser 'warn' o 'silent'"
        )
    agents = AgentStorage(_cfg.AGENTS_DIR)
    agent = await agents.get(agent_id, scope)
    if not agent:
        raise HTTPException(status_code=404, detail="Agente no encontrado")
    resource_labels = agent.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _upsert_social(
                conn,
                "agent",
                agent_id,
                username,
                agent.get("name", agent_id),
                agent.get("description", ""),
                body.category,
                body.trial_missing_deps,
                json.dumps(agent.get("tags") or []),
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("agent", agent_id, username),
            )
        await conn.commit()
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
    skill = await skills.get(scope, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill no encontrada")
    resource_labels = skill.get("labels") or ["private"]
    is_public_val = 1 if "public" in resource_labels else 0

    async with open_db() as conn:
        if body.is_public:
            await _upsert_social(
                conn,
                "skill",
                skill_id,
                username,
                skill.get("name", skill_id),
                skill.get("description", ""),
                body.category,
                "warn",
                json.dumps(skill.get("tags") or []),
                is_public_val,
                json.dumps(resource_labels),
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("skill", skill_id, username),
            )
        await conn.commit()
    return {"ok": True}


@router.put("/api/knowledge/folders/{folder_id}/visibility")
async def set_knowledge_visibility(
    folder_id: str,
    body: _KnowledgeVisibilityBody,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    _check_category(body.category)

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT name FROM knowledge_folders WHERE id=? AND owner_id=?",
            (folder_id, username),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Carpeta no encontrada")
        folder_name = row["name"]
        if body.is_public:
            await _upsert_social(
                conn,
                "knowledge",
                folder_id,
                username,
                folder_name,
                "",
                body.category,
                "warn",
                "[]",
                1,
                '["private"]',
            )
        else:
            await conn.execute(
                "DELETE FROM resource_social "
                "WHERE resource_type=? AND resource_id=? AND owner=?",
                ("knowledge", folder_id, username),
            )
        await conn.commit()
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

    async with open_db() as conn:
        conditions: List[str] = ["is_public = ?"]
        params: List[Any] = [_PUBLIC_VAL]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if q:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if label:
            conditions.append("labels LIKE ?")
            params.append(f'%"{label}"%')
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, fork_of_user, fork_of_id, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC "
            f"LIMIT ? OFFSET ?",
            tuple(params),
        )

    rows = []
    for r in raw:
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


@router.get("/api/explore/{resource_type}/{resource_id}/preview")
async def explore_preview(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Rich preview data for a single public resource."""

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT name, description, owner, category, labels "
            "FROM resource_social WHERE resource_type=? AND resource_id=? AND is_public=?",
            (resource_type, resource_id, _PUBLIC_VAL),
        )

    if not row:
        raise HTTPException(status_code=404, detail="Resource not found or not public")

    base: Dict[str, Any] = dict(row)
    try:
        base["labels"] = json.loads(base.get("labels") or '["private"]')
    except (ValueError, TypeError):
        base["labels"] = ["private"]
    base["resource_type"] = resource_type
    base["resource_id"] = resource_id

    if resource_type == "agent":
        agents = AgentStorage(_cfg.AGENTS_DIR)
        agent = await agents.get(resource_id)
        if agent:
            skills_storage = SkillStorage(_cfg.SKILLS_DIR)
            skill_names = []
            for sid in agent.get("skills", []):
                sk = await skills_storage.get_any(sid)
                if sk:
                    skill_names.append(sk.get("name", sid))
            knowledge_storage = KnowledgeStorage(_cfg.DB_FILE)
            knowledge_titles = []
            for kid in agent.get("knowledge", []):
                item = await knowledge_storage.get(kid)
                if item:
                    knowledge_titles.append(item.get("title", kid))
            base["system_prompt"] = (agent.get("system_prompt") or "")[:600]
            base["skills"] = skill_names
            base["knowledge"] = knowledge_titles
            base["use_memory"] = agent.get("use_memory", False)
            base["temperature"] = agent.get("temperature", 0.7)
            base["agent_type"] = agent.get("agent_type", "")

    elif resource_type == "skill":
        skills_storage = SkillStorage(_cfg.SKILLS_DIR)
        sk = await skills_storage.get_any(resource_id)
        if sk:
            base["body"] = (sk.get("body") or "")[:3000]
            base["parameters"] = sk.get("parameters", [])
            base["icon"] = sk.get("icon", "")

    elif resource_type == "knowledge":
        knowledge_storage = KnowledgeStorage(_cfg.DB_FILE)
        item = await knowledge_storage.get(resource_id)
        if item:
            base["content"] = (item.get("content") or "")[:2000]
            base["type"] = item.get("type", "")
            base["source"] = item.get("source", "")
            base["char_count"] = item.get("char_count", 0)

    return base


@router.get("/api/social/me/resources")
async def my_resources(
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    """All resource_social rows owned by the current user."""

    async with open_db() as conn:
        conditions: List[str] = ["owner = ?"]
        params: List[Any] = [username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, is_public, category, "
            f"stars_count, fork_of_user, fork_of_id, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY updated_at DESC",
            tuple(params),
        )

    rows = []
    for r in raw:
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

    # Annotate linked rows with linked_broken flag
    linked_ids = [r["linked_to_id"] for r in rows if r.get("linked_to_id")]
    if linked_ids:
        placeholders = ",".join("?" * len(linked_ids))
        async with open_db() as conn2:
            pub_rows = await conn2.fetchall(
                f"SELECT resource_id FROM resource_social WHERE resource_id IN ({placeholders}) AND is_public = ?",
                tuple(linked_ids) + (_PUBLIC_VAL,),
            )
        still_public = {r["resource_id"] for r in pub_rows}
        for row in rows:
            if row.get("linked_to_id"):
                linked_owner = row.get("linked_to_user") or ""
                still_accessible = linked_owner == username
                row["linked_broken"] = (
                    row["linked_to_id"] not in still_public and not still_accessible
                )

    return {"resources": rows}


@router.get("/api/users/{target_username}/resources")
async def user_resources(
    target_username: str,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:

    async with open_db() as conn:
        conditions: List[str] = ["is_public = ?", "owner = ?"]
        params: List[Any] = [_PUBLIC_VAL, target_username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, fork_of_user, fork_of_id, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
            f"FROM resource_social WHERE {where} "
            f"ORDER BY stars_count DESC, updated_at DESC",
            tuple(params),
        )

    rows = []
    for r in raw:
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


@router.post("/api/users/{target}/follow")
async def follow_user(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    if target == username:
        raise HTTPException(status_code=400, detail="No puedes seguirte a ti mismo")

    async with open_db() as conn:
        row = await conn.fetchone("SELECT 1 FROM users WHERE username = ?", (target,))
        if not row:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        if IS_PG:
            await conn.execute(
                "INSERT INTO user_follows (follower, following) VALUES (?, ?) "
                "ON CONFLICT DO NOTHING",
                (username, target),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO user_follows (follower, following) VALUES (?, ?)",
                (username, target),
            )
        await conn.commit()
    return {"ok": True}


@router.delete("/api/users/{target}/follow")
async def unfollow_user(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        await conn.execute(
            "DELETE FROM user_follows WHERE follower = ? AND following = ?",
            (username, target),
        )
        await conn.commit()
    return {"ok": True}


@router.get("/api/users/{target}/follow-status")
async def follow_status(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        is_following_row = await conn.fetchone(
            "SELECT 1 FROM user_follows WHERE follower = ? AND following = ?",
            (username, target),
        )
        followers_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE following = ?",
            (target,),
        )
        following_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE follower = ?",
            (target,),
        )

    return {
        "following": is_following_row is not None,
        "followers_count": followers_count or 0,
        "following_count": following_count or 0,
    }


@router.get("/api/feed")
async def get_feed(
    limit: int = 40,
    offset: int = 0,
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
    limit = min(limit, 100)

    async with open_db() as conn:
        conditions: List[str] = [
            "owner IN (SELECT following FROM user_follows WHERE follower = ?)",
            "is_public = ?",
        ]
        params: List[Any] = [username, _PUBLIC_VAL]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        params.extend([limit, offset])
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, tags, labels, updated_at "
            f"FROM resource_social "
            f"WHERE {where} "
            f"ORDER BY updated_at DESC "
            f"LIMIT ? OFFSET ?",
            tuple(params),
        )

    rows = []
    for r in raw:
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


@router.post("/api/{resource_type}/{resource_id}/star")
async def star_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_stars (username, resource_type, resource_id) "
                "VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (username, resource_type, resource_id),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_stars (username, resource_type, resource_id) "
                "VALUES (?, ?, ?)",
                (username, resource_type, resource_id),
            )
        await conn.execute(
            "UPDATE resource_social SET stars_count = ("
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?"
            ") WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}


@router.post("/api/knowledge/{source_id}/fork")
async def fork_knowledge(
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    knowledge = KnowledgeStorage(_cfg.DB_FILE)
    source = await knowledge.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge no encontrado")

    source_owner = source.get("owner_id") or ""
    await _assert_public("knowledge", source_id)

    new_title = source.get("title", source_id)
    result = await knowledge.save(
        type=source.get("type", "url"),
        title=new_title,
        source=source.get("source", ""),
        content=source.get("content", ""),
        owner_id=username,
    )
    new_id = result["id"]

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "knowledge",
                    new_id,
                    username,
                    new_title,
                    source.get("source", ""),
                    source_owner,
                    source_id,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?)",
                (
                    "knowledge",
                    new_id,
                    username,
                    new_title,
                    source.get("source", ""),
                    source_owner,
                    source_id,
                ),
            )
        await conn.commit()
    return {"ok": True, "knowledge_id": new_id, "name": new_title}


@router.post("/api/knowledge/{source_id}/link")
async def link_knowledge(
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    knowledge = KnowledgeStorage(_cfg.DB_FILE)
    source = await knowledge.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge no encontrado")

    source_owner = source.get("owner_id") or ""
    await _assert_public("knowledge", source_id)

    link_title = source.get("title", source_id)
    result = await knowledge.save(
        type=source.get("type", "url"),
        title=link_title,
        source=source.get("source", ""),
        content=source.get("content", ""),
        owner_id=username,
    )
    new_id = result["id"]

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "knowledge",
                    new_id,
                    username,
                    link_title,
                    source.get("source", ""),
                    source_owner,
                    source_id,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?)",
                (
                    "knowledge",
                    new_id,
                    username,
                    link_title,
                    source.get("source", ""),
                    source_owner,
                    source_id,
                ),
            )
        await conn.commit()
    return {"ok": True, "knowledge_id": new_id, "name": link_title}


@router.post("/api/agents/{scope}/{source_id}/fork")
async def fork_agent(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    agents = AgentStorage(_cfg.AGENTS_DIR)
    source = await agents.get(source_id, scope)
    if not source:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public("agent", source_id)

    fork_payload = {
        k: v
        for k, v in source.items()
        if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
    }
    # id propio (no derivado del nombre): al no renombrar la copia, un id basado en
    # el nombre colisionaría con el original (misma slug) y una lectura por id sin
    # filtro de owner (GET /api/agents/{id}) devolvería cualquiera de los dos.
    fork_payload["id"] = uuid4().hex[:12]
    if username != source_owner:
        # Skills/conocimiento privados y memoria se heredan junto con el agente
        fork_payload["skills"] = await _inherit_resource_ids(
            fork_payload.get("skills") or [], "skill", username
        )
        fork_payload["knowledge"] = await _inherit_resource_ids(
            fork_payload.get("knowledge") or [], "knowledge", username
        )
        fork_payload["memory_file"] = None

    try:
        result = await agents.save(fork_payload, "private", owner_id=username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if username != source_owner:
        await _inherit_agent_memory(source, source_owner, result["id"], username)

    fork_labels = list(result.get("labels") or ["private"])
    for ol in ("fork", "linked"):
        if ol in fork_labels:
            fork_labels.remove(ol)
    fork_labels.append("fork")
    result = await agents.save(
        {**result, "labels": fork_labels}, "private", owner_id=username
    )

    new_id = result["id"]
    fork_name = result["name"]
    fork_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "agent",
                    new_id,
                    username,
                    fork_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    fork_tags,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
                (
                    "agent",
                    new_id,
                    username,
                    fork_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    fork_tags,
                ),
            )
        await conn.commit()
    return {"ok": True, "agent_id": new_id, "name": fork_name}


@router.post("/api/skills/{scope}/{source_id}/fork")
async def fork_skill(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = SkillStorage(_cfg.SKILLS_DIR)
    source = await skills.get(scope, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Skill no encontrada")

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public("skill", source_id)

    fork_payload = {
        k: v for k, v in source.items() if k not in ("id", "scope", "owner_id")
    }
    # id propio (no derivado del nombre) — ver comentario equivalente en fork_agent.
    fork_payload["id"] = uuid4().hex[:12]

    try:
        result = await skills.save("private", fork_payload, owner_id=username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    fork_labels = list(result.get("labels") or ["private"])
    for ol in ("fork", "linked"):
        if ol in fork_labels:
            fork_labels.remove(ol)
    fork_labels.append("fork")
    result = await skills.save(
        "private", {**result, "labels": fork_labels}, owner_id=username
    )

    new_id = result["id"]
    fork_name = result["name"]
    fork_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "skill",
                    new_id,
                    username,
                    fork_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    fork_tags,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, fork_of_user, fork_of_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
                (
                    "skill",
                    new_id,
                    username,
                    fork_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    fork_tags,
                ),
            )
        await conn.commit()
    return {"ok": True, "skill_id": new_id, "name": fork_name}


@router.post("/api/agents/{scope}/{source_id}/link")
async def link_agent(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    agents = AgentStorage(_cfg.AGENTS_DIR)
    source = await agents.get(source_id, scope)
    if not source:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public("agent", source_id)

    link_payload = {
        k: v
        for k, v in source.items()
        if k not in ("id", "scope", "owner_id", "created_at", "updated_at")
    }
    # id propio (no derivado del nombre) — ver comentario equivalente en fork_agent.
    link_payload["id"] = uuid4().hex[:12]
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked"):
        if ol in link_labels:
            link_labels.remove(ol)
    link_labels.append("linked")
    link_payload["labels"] = link_labels
    if username != source_owner:
        # Skills/conocimiento privados y memoria se heredan junto con el agente
        link_payload["skills"] = await _inherit_resource_ids(
            link_payload.get("skills") or [], "skill", username
        )
        link_payload["knowledge"] = await _inherit_resource_ids(
            link_payload.get("knowledge") or [], "knowledge", username
        )
        link_payload["memory_file"] = None

    try:
        result = await agents.save(link_payload, "private", owner_id=username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if username != source_owner:
        await _inherit_agent_memory(source, source_owner, result["id"], username)

    new_id = result["id"]
    link_name = result["name"]
    link_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "agent",
                    new_id,
                    username,
                    link_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    link_tags,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
                (
                    "agent",
                    new_id,
                    username,
                    link_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    link_tags,
                ),
            )
        await conn.commit()
    return {"ok": True, "agent_id": new_id, "name": link_name}


@router.post("/api/skills/{scope}/{source_id}/link")
async def link_skill(
    scope: str,
    source_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = SkillStorage(_cfg.SKILLS_DIR)
    source = await skills.get(scope, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Skill no encontrada")

    source_owner = source.get("owner_id") or ""
    if scope != "public":
        await _assert_public("skill", source_id)

    link_payload = {
        k: v for k, v in source.items() if k not in ("id", "scope", "owner_id")
    }
    # id propio (no derivado del nombre) — ver comentario equivalente en fork_agent.
    link_payload["id"] = uuid4().hex[:12]
    link_labels = list(link_payload.get("labels") or ["private"])
    for ol in ("fork", "linked"):
        if ol in link_labels:
            link_labels.remove(ol)
    link_labels.append("linked")
    link_payload["labels"] = link_labels

    try:
        result = await skills.save("private", link_payload, owner_id=username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    new_id = result["id"]
    link_name = result["name"]
    link_tags = json.dumps(source.get("tags") or [])

    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                "INSERT INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, FALSE, 'Other', 'warn', ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    "skill",
                    new_id,
                    username,
                    link_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    link_tags,
                ),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO resource_social "
                "(resource_type, resource_id, owner, name, description, is_public, category, "
                "trial_missing_deps, linked_to_user, linked_to_id, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, 'Other', 'warn', ?, ?, ?)",
                (
                    "skill",
                    new_id,
                    username,
                    link_name,
                    source.get("description", ""),
                    source_owner,
                    source_id,
                    link_tags,
                ),
            )
        await conn.commit()
    return {"ok": True, "skill_id": new_id, "name": link_name}


@router.post("/api/agents/private/{agent_id}/sync")
async def sync_linked_agent(
    agent_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    agents = AgentStorage(_cfg.AGENTS_DIR)
    local = await agents.get(agent_id, "private")
    if not local or local.get("owner_id") != username:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("agent", agent_id),
        )

    if not row or not row[0]:
        raise HTTPException(
            status_code=400, detail="El agente no tiene enlace a un original"
        )

    original_id = row[0]
    original = await agents.get(original_id)
    if not original:
        raise HTTPException(
            status_code=404, detail="El agente original ya no existe",
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("agent", original_id)
    except HTTPException:
        raise HTTPException(
            status_code=403, detail="El agente original ya no es accesible",
        )

    sync_fields = {
        k: v
        for k, v in original.items()
        if k not in ("id", "scope", "owner_id", "created_at", "name")
    }
    updated = {**local, **sync_fields}
    await agents.save(updated, "private", owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/skills/private/{skill_id}/sync")
async def sync_linked_skill(
    skill_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    skills = SkillStorage(_cfg.SKILLS_DIR)
    local = await skills.get("private", skill_id)
    if not local or local.get("owner_id") != username:
        raise HTTPException(status_code=404, detail="Skill no encontrada")

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_id, linked_to_user FROM resource_social "
            "WHERE resource_type=? AND resource_id=?",
            ("skill", skill_id),
        )

    if not row or not row[0]:
        raise HTTPException(
            status_code=400, detail="La skill no tiene enlace a un original"
        )

    original_id = row[0]
    original = await skills.get_any(original_id)
    if not original:
        raise HTTPException(
            status_code=404, detail="La skill original ya no existe",
        )
    try:
        if original.get("scope") != "public":
            await _assert_public("skill", original_id)
    except HTTPException:
        raise HTTPException(
            status_code=403, detail="La skill original ya no es accesible",
        )

    sync_fields = {
        k: v
        for k, v in original.items()
        if k not in ("id", "scope", "owner_id", "name")
    }
    updated = {**local, **sync_fields}
    await skills.save("private", updated, owner_id=local.get("owner_id"))

    return {"ok": True, "synced_from": original_id}


@router.post("/api/agents/{scope}/{agent_id}/try")
async def try_agent(
    scope: str,
    agent_id: str,
    body: _AgentTryBody,
    ctx: WorkspaceContext = Depends(require_workspace),
) -> Dict[str, Any]:
    """Prueba un agente público usando la connection propia del caller, sin guardar historial."""

    # Step 1: Validate the agent is public in resource_social
    async with open_db() as db:
        row = await db.fetchone(
            "SELECT trial_missing_deps FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND is_public=?",
            ("agent", agent_id, _PUBLIC_VAL),
        )
    if not row:
        raise HTTPException(
            status_code=404, detail="Agente no encontrado o no es público"
        )

    trial_missing_deps: str = row["trial_missing_deps"] or "warn"

    # Step 2: Get agent config from DB storage
    agents = AgentStorage(_cfg.AGENTS_DIR)
    agent_data = await agents.get(agent_id, scope)
    if not agent_data:
        raise HTTPException(status_code=404, detail="Agente no encontrado")

    # Step 3: Resolve caller's connection (workspace first, then personal fallback)
    conn_storage = ConnectionStorage(_cfg.DB_FILE)
    conn_data = await conn_storage.get(body.connection_id, ctx.workspace_id)
    if conn_data is None and ctx.workspace_id != ctx.user:
        conn_data = await conn_storage.get(body.connection_id, ctx.user)
    if conn_data is None:
        raise HTTPException(status_code=400, detail="Connection no encontrada")

    # Step 4: Filter skills based on trial_missing_deps policy
    skills_storage = SkillStorage(_cfg.SKILLS_DIR)
    warnings: List[str] = []
    agent_skills: List[str] = list(agent_data.get("skills") or [])

    accessible: List[str] = []
    for skill_id in agent_skills:
        if await skills_storage.get("public", skill_id):
            accessible.append(skill_id)
            continue
        priv = await skills_storage.get("private", skill_id)
        if priv and priv.get("owner_id") == ctx.workspace_id:
            accessible.append(skill_id)
            continue
        if trial_missing_deps == "warn":
            warnings.append(skill_id)
    agent_data = {**agent_data, "skills": accessible}

    # Step 5: Stream chat and collect reply (no history saved)
    reply_parts: List[str] = []
    async for chunk in stream_chat(
        agent_data,
        conn_data,
        [{"role": "user", "content": body.message}],
        skills_storage,
        None,
        None,
    ):
        if chunk.startswith("data:"):
            try:
                ev = json.loads(chunk[5:].strip())
                if ev.get("type") == "chunk":
                    reply_parts.append(ev.get("content", ""))
            except Exception:
                pass

    return {"reply": "".join(reply_parts), "warnings": warnings}


@router.delete("/api/{resource_type}/{resource_id}/star")
async def unstar_resource(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        await conn.execute(
            "DELETE FROM resource_stars "
            "WHERE username=? AND resource_type=? AND resource_id=?",
            (username, resource_type, resource_id),
        )
        await conn.execute(
            "UPDATE resource_social SET stars_count = ("
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?"
            ") WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id, resource_type, resource_id),
        )
        await conn.commit()
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM resource_stars "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        )
    return {"ok": True, "stars": count or 0}


@router.post("/api/agents/private/{agent_id}/link/convert-to-fork")
async def convert_agent_link_to_fork(
    agent_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_user, linked_to_id FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND owner=? AND linked_to_id IS NOT NULL",
            ("agent", agent_id, username),
        )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Agente no encontrado o no tiene enlace activo",
        )

    prev_linked_to_user = row[0]
    prev_linked_to_id = row[1]

    async with open_db() as conn:
        await conn.execute(
            "UPDATE resource_social SET "
            "linked_to_user=NULL, linked_to_id=NULL, "
            "fork_of_user=?, fork_of_id=? "
            "WHERE resource_type=? AND resource_id=? AND owner=?",
            (prev_linked_to_user, prev_linked_to_id, "agent", agent_id, username),
        )
        await conn.commit()

    agents = AgentStorage(_cfg.AGENTS_DIR)
    agent_data = await agents.get(agent_id, "private")
    if agent_data:
        updated = dict(agent_data)
        updated.pop("linked_to_user", None)
        updated.pop("linked_to_id", None)
        labels: List[str] = list(updated.get("labels") or ["private"])
        if "linked" in labels:
            labels.remove("linked")
        if "fork" not in labels:
            labels.append("fork")
        updated["labels"] = labels
        await agents.save(updated, "private", owner_id=updated.get("owner_id"))

    return {"ok": True, "agent_id": agent_id}


@router.post("/api/skills/private/{skill_id}/link/convert-to-fork")
async def convert_skill_link_to_fork(
    skill_id: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT linked_to_user, linked_to_id FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND owner=? AND linked_to_id IS NOT NULL",
            ("skill", skill_id, username),
        )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Skill no encontrada o no tiene enlace activo",
        )

    prev_linked_to_user = row[0]
    prev_linked_to_id = row[1]

    async with open_db() as conn:
        await conn.execute(
            "UPDATE resource_social SET "
            "linked_to_user=NULL, linked_to_id=NULL, "
            "fork_of_user=?, fork_of_id=? "
            "WHERE resource_type=? AND resource_id=? AND owner=?",
            (prev_linked_to_user, prev_linked_to_id, "skill", skill_id, username),
        )
        await conn.commit()

    skills = SkillStorage(_cfg.SKILLS_DIR)
    skill_data = await skills.get("private", skill_id)
    if skill_data:
        updated = dict(skill_data)
        updated.pop("linked_to_user", None)
        updated.pop("linked_to_id", None)
        labels_s: List[str] = list(updated.get("labels") or ["private"])
        if "linked" in labels_s:
            labels_s.remove("linked")
        if "fork" not in labels_s:
            labels_s.append("fork")
        updated["labels"] = labels_s
        await skills.save("private", updated, owner_id=updated.get("owner_id"))

    return {"ok": True, "skill_id": skill_id}
