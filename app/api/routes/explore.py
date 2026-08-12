"""Explore / discovery routes: catálogo público, vista previa, perfil social
(recursos propios, de otro usuario), follow y feed.

Extraído de social.py (ver admin.py para el motivo completo del split de
auth.py; social.py tenía el mismo problema de tamaño/responsabilidades
mezcladas: visibilidad, exploración, follow y link/fork todo junto).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response

import app.config.data as _cfg
from app.api.pagination import TOTAL_HEADER
from app.api.routes.auth import require_auth, require_session
from app.api.routes.social import _PUBLIC_VAL, _social_limiter
from app.config.content_languages import (
    CONTENT_LANGUAGE_SET,
    language_codes_from_labels,
)
from app.errors import APIError
from app.models.official_source import (
    LinkOfficialPackRequest,
    LinkOfficialPackResult,
    PublicOfficialPack,
    PublicOfficialPackDetail,
)
from app.services.official_pack_service import OfficialPackService
from app.storage.agent_storage import AgentStorage
from app.storage.db import IS_PG, open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage

router = APIRouter(tags=["explore"])

# Singletons de módulo, como en agents.py y connections.py. Construir un storage
# dentro del handler no cuesta por el objeto, sino por lo que arrastra: el flag
# de migración era de instancia, así que cada uno recién creado reejecutaba
# _ensure_migrated() —y su SELECT COUNT(*)— en la primera operación.
# explore_preview llegaba a construir seis en una sola petición.
_agents = AgentStorage(_cfg.AGENTS_DIR)
_skills = SkillStorage(_cfg.SKILLS_DIR)
_prompts = PromptStorage()
_tools = ToolStorage()
_knowledge = KnowledgeStorage()
_workflows = WorkflowStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()
_official_packs = OfficialPackService()


async def _add_owner_usernames(rows: List[Dict[str, Any]]) -> None:
    """Attach the public username while keeping the internal owner id intact."""
    owner_ids = {str(row.get("owner") or "") for row in rows} - {"", "__public__"}
    if not owner_ids:
        return
    placeholders = ",".join("?" for _ in owner_ids)
    async with open_db() as conn:
        users = await conn.fetchall(
            f"SELECT id, username FROM users WHERE id IN ({placeholders})",
            tuple(owner_ids),
        )
    usernames = {row["id"]: row["username"] for row in users}
    for row in rows:
        row["owner_username"] = usernames.get(str(row.get("owner") or ""))


@router.get("/api/explore")
async def explore(
    type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    label: Optional[List[str]] = Query(None),
    language: Optional[List[str]] = Query(None),
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_official: bool = True,
    response: Response = None,  # type: ignore[assignment]
    # Abierto al invitado: solo devuelve filas is_public y el usuario únicamente
    # sirve para excluir lo propio. Es el catálogo público, y la superficie que
    # tiene sentido enseñar en el demo.
    username: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    async with open_db() as conn:
        # Lo propio no se enseña en el catálogo… salvo el contenido oficial:
        # vive en la cuenta del admin que sincroniza la fuente, pero es del
        # hub, no suyo. Sin esta excepción el admin era el único usuario que
        # no veía en Explorar lo que acababa de traer.
        conditions: List[str] = [
            "is_public = ?",
            "(owner != ? OR labels LIKE '%\"official\"%')",
        ]
        params: List[Any] = [_PUBLIC_VAL, username]
        if not include_official:
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM resource_source_links source_link "
                "WHERE source_link.resource_type=resource_social.resource_type "
                "AND source_link.resource_id=resource_social.resource_id "
                "AND source_link.resource_owner_id=resource_social.owner)"
            )
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
        if language:
            normalized_languages = [
                str(value).strip().lower() for value in language if str(value).strip()
            ]
            invalid_languages = [
                value
                for value in normalized_languages
                if value not in CONTENT_LANGUAGE_SET
            ]
            if invalid_languages:
                raise APIError(
                    422,
                    "invalid_field",
                    "Idioma de contenido no soportado",
                    extra={"field": "language", "invalid": invalid_languages},
                )
            if normalized_languages:
                conditions.append(
                    "("
                    + " OR ".join(["labels LIKE ?"] * len(normalized_languages))
                    + ")"
                )
                params.extend(f'%"lang_{value}"%' for value in normalized_languages)
        if label:
            # Coincide con cualquiera de las etiquetas seleccionadas (OR)
            conditions.append("(" + " OR ".join(["labels LIKE ?"] * len(label)) + ")")
            params.extend(f'%"{label_value}"%' for label_value in label)
        where = " AND ".join(conditions)
        # El total va en cabecera (ver app/api/pagination.py). Aquí la página se
        # recorta en SQL, así que len(rows) no sirve: hace falta su propio
        # COUNT con las MISMAS condiciones, antes de añadir limit/offset.
        if response is not None:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM resource_social WHERE {where}", tuple(params)
            )
            response.headers[TOTAL_HEADER] = str(total or 0)
        params.extend([limit, offset])
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
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
        row["languages"] = language_codes_from_labels(row["labels"])
        rows.append(row)
    await _add_owner_usernames(rows)
    return rows


@router.get("/api/explore/official-packs", response_model=List[PublicOfficialPack])
async def explore_official_packs(
    type: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    label: Optional[List[str]] = Query(None),
    language: Optional[List[str]] = Query(None),
    username: str = Depends(require_session),
) -> List[PublicOfficialPack]:
    normalized_languages = [
        str(value).strip().lower() for value in (language or []) if str(value).strip()
    ]
    invalid_languages = [
        value for value in normalized_languages if value not in CONTENT_LANGUAGE_SET
    ]
    if invalid_languages:
        raise APIError(
            422,
            "invalid_field",
            "Idioma de contenido no soportado",
            extra={"field": "language", "invalid": invalid_languages},
        )
    return await _official_packs.list_packs(
        username,
        resource_type=type or "all",
        category=category or "",
        query=q or "",
        tag=tag or "",
        labels=label,
        languages=normalized_languages,
    )


@router.get(
    "/api/explore/official-packs/{source_id}",
    response_model=PublicOfficialPackDetail,
)
async def explore_official_pack_detail(
    source_id: str,
    username: str = Depends(require_session),
) -> PublicOfficialPackDetail:
    detail = await _official_packs.detail(username, source_id)
    if detail is None:
        raise APIError(
            404,
            "not_found",
            "Pack oficial no encontrado o sin recursos publicos",
            extra={"resource": "official_pack"},
        )
    return detail


@router.get("/api/explore/official-packs/{source_id}/graph")
async def explore_official_pack_graph(
    source_id: str,
    username: str = Depends(require_session),
) -> Dict[str, Any]:
    graph = await _official_packs.graph(username, source_id)
    if graph is None:
        raise APIError(
            404,
            "not_found",
            "Pack oficial no encontrado o sin recursos publicos",
            extra={"resource": "official_pack"},
        )
    return graph


@router.post(
    "/api/explore/official-packs/{source_id}/link",
    response_model=LinkOfficialPackResult,
)
async def link_official_pack(
    source_id: str,
    body: LinkOfficialPackRequest,
    username: str = Depends(require_auth),
) -> LinkOfficialPackResult:
    try:
        result = await _official_packs.link(username, source_id, body)
    except PermissionError as exc:
        raise APIError(
            400,
            "already_owner",
            "Ya eres el propietario de los recursos de este pack",
            extra={"resource": "official_pack"},
        ) from exc
    except ValueError as exc:
        code = str(exc)
        if code == "official_pack_stale":
            raise APIError(
                409,
                "conflict",
                "El repositorio ha cambiado; vuelve a abrir el pack",
                extra={"resource": "official_pack"},
            ) from exc
        raise APIError(
            422,
            "invalid_field",
            "La seleccion del pack oficial no es valida",
            extra={"field": "component_keys"},
        ) from exc
    if result is None:
        raise APIError(
            404,
            "not_found",
            "Pack oficial no encontrado o sin recursos publicos",
            extra={"resource": "official_pack"},
        )
    return result


@router.get("/api/explore/{resource_type}/{resource_id}/preview")
async def explore_preview(
    resource_type: str,
    resource_id: str,
    username: str = Depends(require_session),  # ver explore(): catálogo público
) -> Dict[str, Any]:
    """Rich preview data for a single public resource.

    Lo oficial no necesita rama propia: es una fila de resource_social como
    cualquier otra, marcada con la label ``official``.
    """

    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT name, description, owner, category, labels "
            "FROM resource_social WHERE resource_type=? AND resource_id=? AND is_public=?",
            (resource_type, resource_id, _PUBLIC_VAL),
        )

    if not row:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado o no es público",
            extra={"resource": resource_type},
        )

    base: Dict[str, Any] = dict(row)
    try:
        base["labels"] = json.loads(base.get("labels") or '["private"]')
    except (ValueError, TypeError):
        base["labels"] = ["private"]
    base["resource_type"] = resource_type
    base["resource_id"] = resource_id
    await _add_owner_usernames([base])

    if resource_type == "agent":
        agent = await _agents.get(resource_id)
        if agent:
            skill_names = []
            for sid in agent.get("skills", []):
                sk = await _skills.get_any(sid)
                if not sk:
                    continue
                # No revelar nombres de skills privadas ajenas en la vista
                # previa pública, aunque el agente que las usa sí sea público.
                if sk.get("scope") != "public" and not await _shares.is_accessible(
                    _groups,
                    resource_type="skill",
                    resource_id=sid,
                    owner_id=sk.get("owner_id"),
                    requester=username,
                ):
                    continue
                skill_names.append(sk.get("name", sid))
            knowledge_titles = []
            for kid in agent.get("knowledge", []):
                item = await _knowledge.get(kid)
                if not item:
                    continue
                if not await _shares.is_accessible(
                    _groups,
                    resource_type="knowledge",
                    resource_id=kid,
                    owner_id=item.get("owner_id"),
                    requester=username,
                ):
                    continue
                knowledge_titles.append(item.get("title", kid))
            prompt_names = []
            for pid in agent.get("prompts", []):
                pr = await _prompts.get_any(pid)
                if not pr:
                    continue
                # No revelar nombres de prompts privados ajenos en la vista
                # previa pública, aunque el agente que los usa sí sea público.
                if pr.get("scope") != "public" and not await _shares.is_accessible(
                    _groups,
                    resource_type="prompt",
                    resource_id=pid,
                    owner_id=pr.get("owner_id"),
                    requester=username,
                ):
                    continue
                prompt_names.append(pr.get("name", pid))
            tool_names = []
            for tid in agent.get("tools", []):
                tl = await _tools.get_any(tid)
                if not tl:
                    continue
                # No revelar nombres de tools privadas ajenas en la vista
                # previa pública, aunque el agente que las usa sí sea público.
                if tl.get("scope") != "public" and not await _shares.is_accessible(
                    _groups,
                    resource_type="tool",
                    resource_id=tid,
                    owner_id=tl.get("owner_id"),
                    requester=username,
                ):
                    continue
                tool_names.append(tl.get("name", tid))
            base["system_prompt"] = (agent.get("system_prompt") or "")[:600]
            base["skills"] = skill_names
            base["knowledge"] = knowledge_titles
            base["prompts"] = prompt_names
            base["tools"] = tool_names
            base["use_memory"] = agent.get("use_memory", False)
            base["temperature"] = agent.get("temperature", 0.7)
            base["agent_type"] = agent.get("agent_type", "")

    elif resource_type == "skill":
        sk = await _skills.get_any(resource_id)
        if sk:
            base["body"] = (sk.get("body") or "")[:3000]
            base["parameters"] = sk.get("parameters", [])
            base["icon"] = sk.get("icon", "")

    elif resource_type == "prompt":
        pr = await _prompts.get_any(resource_id)
        if pr:
            base["content"] = (pr.get("content") or "")[:3000]
            base["alias"] = pr.get("alias", "")
            base["icon"] = pr.get("icon", "")

    elif resource_type == "tool":
        tl = await _tools.get_any(resource_id)
        if tl:
            base["language"] = tl.get("language", "")
            base["binary_filename"] = tl.get("binary_filename")
            base["binary_size"] = tl.get("binary_size")
            base["icon"] = tl.get("icon", "")
            if tl.get("language") != "cpp":
                base["content"] = (tl.get("content") or "")[:3000]

    elif resource_type == "knowledge":
        item = await _knowledge.get(resource_id)
        if item:
            base["content"] = (item.get("content") or "")[:2000]
            base["type"] = item.get("type", "")
            base["source"] = item.get("source", "")
            base["char_count"] = item.get("char_count", 0)

    elif resource_type == "workflow":
        workflow = await _workflows.get_any(resource_id)
        if workflow:
            nodes = workflow.get("definition", {}).get("nodes", [])
            agent_names = []
            for node in nodes:
                agent = await _agents.get(str(node.get("agent_id") or ""))
                agent_names.append(
                    (agent.get("name") if agent else None)
                    or node.get("label")
                    or node.get("agent_id")
                )
            base["steps"] = len(nodes)
            base["agent_names"] = agent_names

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
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified "
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

    await _add_owner_usernames(rows)

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
        target_id = await conn.fetchval(
            "SELECT id FROM users WHERE username = ?", (target_username,)
        )
        if not target_id:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        # Las publicaciones nuevas usan el id interno; las creadas antes de la
        # migracion de identidad conservan el username en owner. El perfil
        # publico debe mostrar ambas sin exigir republicar los recursos.
        conditions: List[str] = ["is_public = ?", "owner IN (?, ?)"]
        params: List[Any] = [_PUBLIC_VAL, target_id, target_username]
        if type and type != "all":
            conditions.append("resource_type = ?")
            params.append(type)
        where = " AND ".join(conditions)
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels "
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
    await _add_owner_usernames(rows)
    return rows


@router.post("/api/users/{target}/follow")
async def follow_user(
    target: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:
    async with open_db() as conn:
        row = await conn.fetchone("SELECT id FROM users WHERE username = ?", (target,))
        if not row:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        target_id = row["id"]
        if target_id == username:
            raise APIError(400, "cannot_follow_self", "No puedes seguirte a ti mismo")
        if IS_PG:
            await conn.execute(
                "INSERT INTO user_follows (follower, following) VALUES (?, ?) "
                "ON CONFLICT DO NOTHING",
                (username, target_id),
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO user_follows (follower, following) VALUES (?, ?)",
                (username, target_id),
            )
        await conn.commit()
    return {"ok": True}


@router.delete("/api/users/{target}/follow")
async def unfollow_user(
    target: str,
    username: str = Depends(require_auth),
    _rl: None = Depends(_social_limiter),
) -> Dict[str, Any]:

    async with open_db() as conn:
        target_id = await conn.fetchval(
            "SELECT id FROM users WHERE username = ?", (target,)
        )
        if not target_id:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        await conn.execute(
            "DELETE FROM user_follows WHERE follower = ? AND following = ?",
            (username, target_id),
        )
        await conn.commit()
    return {"ok": True}


@router.get("/api/users/{target}/follow-status")
async def follow_status(
    target: str,
    username: str = Depends(require_auth),
) -> Dict[str, Any]:

    async with open_db() as conn:
        target_id = await conn.fetchval(
            "SELECT id FROM users WHERE username = ?", (target,)
        )
        if not target_id:
            raise APIError(
                404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
            )
        is_following_row = await conn.fetchone(
            "SELECT 1 FROM user_follows WHERE follower = ? AND following = ?",
            (username, target_id),
        )
        followers_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE following = ?",
            (target_id,),
        )
        following_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_follows WHERE follower = ?",
            (target_id,),
        )

    return {
        "following": is_following_row is not None,
        "followers_count": followers_count or 0,
        "following_count": following_count or 0,
    }


@router.get("/api/feed")
async def get_feed(
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    type: Optional[str] = None,
    username: str = Depends(require_auth),
) -> List[Dict[str, Any]]:
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
        if response is not None:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM resource_social WHERE {where}", tuple(params)
            )
            response.headers[TOTAL_HEADER] = str(total or 0)
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
    await _add_owner_usernames(rows)
    return rows
