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
from app.pagination.http import LINKED_HEADER, TOTAL_HEADER
from app.services.official_pack_service import OfficialPackService
from app.storage.agent_storage import AgentStorage
from app.storage.db import IS_PG, open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
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
_knowledge_packs = KnowledgePackStorage()
_workflows = WorkflowStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()
_official_packs = OfficialPackService()

# Relación entre el catálogo y lo que el usuario ya tiene enlazado.
# `all` es el valor por defecto porque es el comportamiento que la ruta tuvo
# siempre: quien no envíe el parámetro sigue viendo el catálogo entero. Es el
# cliente el que decide que descubrir significa "lo que todavía no tengo".
# Ver docs/adr/004-explorar-esconde-lo-que-ya-tienes.md
RELATION_MODES = ("all", "new", "linked")

# Una fila del catálogo ya la tengo si existe una copia mía apuntando a ella.
# Correlacionada a propósito, y con las cuatro columnas en el orden de
# `idx_rsoc_link_origin` (owner, linked_to_user, linked_to_id, resource_type):
# así el índice parcial la resuelve entera en vez de degradar a un rango por
# owner.
_LINKED_BY_REQUESTER = (
    "EXISTS (SELECT 1 FROM resource_social mine "
    "WHERE mine.owner = ? "
    "AND mine.linked_to_user = resource_social.owner "
    "AND mine.linked_to_id = resource_social.resource_id "
    "AND mine.resource_type = resource_social.resource_type)"
)


def _validate_relation(relation: Optional[str]) -> str:
    value = (relation or "all").strip().lower()
    if value not in RELATION_MODES:
        raise APIError(
            422,
            "invalid_field",
            "Modo de relación no soportado",
            extra={"field": "relation", "invalid": [relation]},
        )
    return value


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
    pack_mode: Optional[bool] = None,
    relation: Optional[str] = None,
    response: Response = None,  # type: ignore[assignment]
    # Abierto al invitado: solo devuelve filas is_public y el usuario únicamente
    # sirve para excluir lo propio. Es el catálogo público, y la superficie que
    # tiene sentido enseñar en el demo.
    username: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    relation_mode = _validate_relation(relation)
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
        if pack_mode is True:
            conditions.append(
                "NOT (resource_type='knowledge' AND EXISTS ("
                "SELECT 1 FROM knowledge_items ki "
                "WHERE ki.id=resource_social.resource_id AND ki.pack_id IS NOT NULL))"
            )
        elif pack_mode is False:
            conditions.append("resource_type != 'knowledge_pack'")
        if type and type != "all":
            if type == "knowledge" and pack_mode is True:
                conditions.append("resource_type IN ('knowledge','knowledge_pack')")
            else:
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
        # La relación se añade la última para poder rehacer el mismo WHERE con
        # la condición contraria sin recolocar marcadores.
        base_where = " AND ".join(conditions)
        base_params = list(params)
        if relation_mode == "new":
            conditions.append(f"NOT {_LINKED_BY_REQUESTER}")
            params.append(username)
        elif relation_mode == "linked":
            conditions.append(_LINKED_BY_REQUESTER)
            params.append(username)
        where = " AND ".join(conditions)
        # La página se recorta en SQL, así que el total requiere un COUNT con
        # las mismas condiciones antes de añadir limit/offset.
        if response is not None:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM resource_social WHERE {where}", tuple(params)
            )
            response.headers[TOTAL_HEADER] = str(total or 0)
            # Un catálogo vacío porque el usuario ya lo tiene todo no es lo
            # mismo que una búsqueda sin resultados, y el cliente no puede
            # distinguirlos sin preguntar otra vez. Solo se paga cuando no hay
            # nada que devolver.
            if relation_mode == "new" and not total and not offset:
                already = await conn.fetchval(
                    f"SELECT COUNT(*) FROM resource_social "
                    f"WHERE {base_where} AND {_LINKED_BY_REQUESTER}",
                    tuple([*base_params, username]),
                )
                response.headers[LINKED_HEADER] = str(already or 0)
        # El estado viaja en cada fila, no solo en el filtro: en `relation=all`
        # conviven las dos y la tarjeta necesita saber cuál está mirando. El
        # parámetro del SELECT va antes que los del WHERE porque la sustitución
        # de marcadores es posicional.
        page_params = [username, *params, limit, offset]
        raw = await conn.fetchall(
            f"SELECT resource_type, resource_id, owner, name, description, category, "
            f"stars_count, linked_to_user, linked_to_id, trial_missing_deps, tags, labels, verified, "
            f"{_LINKED_BY_REQUESTER} AS linked_by_me "
            f"FROM resource_social WHERE {where} "
            # Lo recién publicado debe ser descubrible aunque todavía no tenga
            # estrellas. El catálogo permite cargar páginas posteriores para
            # consultar también los recursos históricos más populares.
            #
            # La clave primaria cierra el orden: una sincronización de contenido
            # oficial publica cientos de filas con el mismo `updated_at`, y sin
            # desempate único el motor puede devolver la misma fila en dos
            # páginas y saltarse otra.
            f"ORDER BY updated_at DESC, stars_count DESC, "
            f"resource_type ASC, resource_id ASC, owner ASC "
            f"LIMIT ? OFFSET ?",
            tuple(page_params),
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
        # SQLite devuelve 0/1 y PostgreSQL un boolean: el cliente recibe siempre
        # un booleano JSON.
        row["linked_by_me"] = bool(row.get("linked_by_me"))
        rows.append(row)
    packs = await _knowledge.pack_locations(
        [
            str(row.get("resource_id") or "")
            for row in rows
            if row.get("resource_type") == "knowledge"
        ]
    )
    for row in rows:
        location = packs.get(str(row.get("resource_id") or "")) if packs else None
        if location is not None and row.get("resource_type") == "knowledge":
            row["pack_id"] = location["pack_id"]
            row["pack_relative_path"] = location["pack_relative_path"]
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
    relation: Optional[str] = None,
    username: str = Depends(require_session),
) -> List[PublicOfficialPack]:
    relation_mode = _validate_relation(relation)
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
        relation=relation_mode,
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

    elif resource_type == "knowledge_pack":
        pack = await _knowledge_packs.get(resource_id)
        if pack:
            base["file_count"] = pack.get("file_count", 0)
            base["size_bytes"] = pack.get("size_bytes", 0)
            base["items"] = pack.get("items", [])

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


def _append_pack_members_to_graph(
    *,
    pack_id: str,
    pack_node_id: str,
    members: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    seen: set[str],
) -> None:
    """Añade la jerarquía real del directorio: pack -> carpetas -> archivos."""
    for member in members:
        member_id = str(member.get("id") or "")
        if not member_id:
            continue
        relative_path = str(member.get("relative_path") or member_id)
        parts = [part for part in relative_path.split("/") if part]
        parent_id = pack_node_id
        directory_parts: List[str] = []
        for directory_name in parts[:-1]:
            directory_parts.append(directory_name)
            directory_path = "/".join(directory_parts)
            directory_id = f"knowledge_pack_directory:{pack_id}:{directory_path}"
            if directory_id not in seen:
                seen.add(directory_id)
                nodes.append(
                    {
                        "id": directory_id,
                        "label": directory_name,
                        "type": "knowledge_directory",
                        "description": directory_path,
                    }
                )
                edges.append(
                    {
                        "source_id": parent_id,
                        "target_id": directory_id,
                        "relation": "contains",
                    }
                )
            parent_id = directory_id
        member_node_id = f"knowledge:{member_id}"
        if member_node_id not in seen:
            seen.add(member_node_id)
            nodes.append(
                {
                    "id": member_node_id,
                    "label": parts[-1] if parts else relative_path,
                    "type": "knowledge",
                    "description": member.get("kind", ""),
                }
            )
            edges.append(
                {
                    "source_id": parent_id,
                    "target_id": member_node_id,
                    "relation": "contains",
                }
            )


async def _public_agent_graph_parts(
    agent: Dict[str, Any], source_node_id: str
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Recursos públicos que usa un agente, sin revelar dependencias privadas."""
    raw_selection = agent.get("public_dependencies")
    selected = (
        {str(value) for value in raw_selection if value}
        if raw_selection is not None
        else None
    )
    references = (
        ("skill", agent.get("skills") or []),
        ("knowledge_pack", agent.get("knowledge_packs") or []),
        ("knowledge", agent.get("knowledge") or []),
        ("prompt", agent.get("prompts") or []),
        ("tool", agent.get("tools") or []),
    )
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    seen: set[str] = set()
    async with open_db() as conn:
        for kind, resource_ids in references:
            for child_resource_id in resource_ids:
                child_id = str(child_resource_id or "")
                if not child_id:
                    continue
                if selected is not None and f"{kind}:{child_id}" not in selected:
                    continue
                row = await conn.fetchone(
                    "SELECT name, description FROM resource_social "
                    "WHERE resource_type=? AND resource_id=? AND is_public=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (kind, child_id, _PUBLIC_VAL),
                )
                if not row:
                    continue
                node_id = f"{kind}:{child_id}"
                parent_id = source_node_id
                if kind == "knowledge":
                    item = await _knowledge.get(child_id)
                    pack_id = str((item or {}).get("pack_id") or "")
                    if pack_id:
                        pack = await _knowledge_packs.get(pack_id, include_items=False)
                        pack_node_id = f"knowledge_pack:{pack_id}"
                        if pack and pack_node_id not in seen:
                            seen.add(pack_node_id)
                            nodes.append(
                                {
                                    "id": pack_node_id,
                                    "label": pack.get("name", pack_id),
                                    "type": "knowledge_pack",
                                    "description": "Selección parcial",
                                }
                            )
                            edges.append(
                                {
                                    "source_id": source_node_id,
                                    "target_id": pack_node_id,
                                    "relation": "uses_partial",
                                }
                            )
                        parent_id = pack_node_id
                        _append_pack_members_to_graph(
                            pack_id=pack_id,
                            pack_node_id=pack_node_id,
                            members=[item] if item else [],
                            nodes=nodes,
                            edges=edges,
                            seen=seen,
                        )
                        continue
                if node_id not in seen:
                    seen.add(node_id)
                    nodes.append(
                        {
                            "id": node_id,
                            "label": row["name"] or child_id,
                            "type": kind,
                            "description": row["description"] or "",
                        }
                    )
                edges.append(
                    {
                        "source_id": parent_id,
                        "target_id": node_id,
                        "relation": "uses",
                    }
                )
                if kind == "knowledge_pack":
                    pack = await _knowledge_packs.get(child_id)
                    _append_pack_members_to_graph(
                        pack_id=child_id,
                        pack_node_id=node_id,
                        members=(pack or {}).get("items") or [],
                        nodes=nodes,
                        edges=edges,
                        seen=seen,
                    )
    memory_file = str(agent.get("memory_file") or "").strip()
    if (
        agent.get("use_memory")
        and memory_file
        and (selected is None or f"memory:{memory_file}" in selected)
    ):
        memory_node_id = f"memory:{memory_file}"
        nodes.append(
            {
                "id": memory_node_id,
                "label": memory_file,
                "type": "memory",
                "description": "Memoria publicada con el agente",
            }
        )
        edges.append(
            {
                "source_id": source_node_id,
                "target_id": memory_node_id,
                "relation": "uses",
            }
        )
    return nodes, edges


@router.get("/api/explore/{resource_type}/{resource_id}/graph")
async def explore_resource_graph(
    resource_type: str,
    resource_id: str,
    _: str = Depends(require_session),
) -> Dict[str, Any]:
    """Grafo público de agentes y orquestaciones visibles en Explore."""
    if resource_type not in {"agent", "workflow", "knowledge_pack"}:
        raise APIError(
            422,
            "invalid_field",
            "Este tipo de recurso no dispone de grafo público",
            extra={"field": "resource_type"},
        )
    async with open_db() as conn:
        published = await conn.fetchone(
            "SELECT name, description FROM resource_social "
            "WHERE resource_type=? AND resource_id=? AND is_public=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (resource_type, resource_id, _PUBLIC_VAL),
        )
    if not published:
        raise APIError(
            404,
            "not_found",
            "Recurso no encontrado o no es público",
            extra={"resource": resource_type},
        )

    root_id = f"{resource_type}:{resource_id}"
    nodes: List[Dict[str, Any]] = [
        {
            "id": root_id,
            "label": published["name"] or resource_id,
            "type": resource_type,
            "description": published["description"] or "",
        }
    ]
    edges: List[Dict[str, Any]] = []

    if resource_type == "knowledge_pack":
        pack = await _knowledge_packs.get(resource_id)
        _append_pack_members_to_graph(
            pack_id=resource_id,
            pack_node_id=root_id,
            members=(pack or {}).get("items") or [],
            nodes=nodes,
            edges=edges,
            seen={root_id},
        )
    elif resource_type == "agent":
        agent = await _agents.get(resource_id)
        if agent:
            child_nodes, child_edges = await _public_agent_graph_parts(agent, root_id)
            nodes.extend(child_nodes)
            edges.extend(child_edges)
    else:
        workflow = await _workflows.get_any(resource_id)
        definition = (workflow or {}).get("definition") or {}
        raw_nodes = definition.get("nodes") or []
        raw_edges = definition.get("edges") or []
        step_ids: Dict[str, str] = {}
        incoming: set[str] = set()
        public_agents: Dict[str, Dict[str, Any]] = {}
        async with open_db() as conn:
            for raw_node in raw_nodes:
                agent_id = str(raw_node.get("agent_id") or "")
                if not agent_id or agent_id in public_agents:
                    continue
                row = await conn.fetchone(
                    "SELECT name, description FROM resource_social "
                    "WHERE resource_type='agent' AND resource_id=? AND is_public=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (agent_id, _PUBLIC_VAL),
                )
                if row:
                    public_agents[agent_id] = dict(row)

        for index, raw_node in enumerate(raw_nodes):
            raw_step_id = str(raw_node.get("id") or f"step-{index}")
            step_id = f"workflow_step:{resource_id}:{raw_step_id}"
            step_ids[raw_step_id] = step_id
            agent_id = str(raw_node.get("agent_id") or "")
            public_agent = public_agents.get(agent_id)
            label = (
                (public_agent or {}).get("name") or raw_node.get("label") or raw_step_id
            )
            nodes.append(
                {
                    "id": step_id,
                    "label": label,
                    "type": "evaluator"
                    if raw_node.get("kind") == "evaluator"
                    else "agent",
                    "description": (public_agent or {}).get("description") or "",
                }
            )
            if public_agent:
                agent = await _agents.get(agent_id)
                if agent:
                    child_nodes, child_edges = await _public_agent_graph_parts(
                        agent, step_id
                    )
                    known_node_ids = {node["id"] for node in nodes}
                    nodes.extend(
                        node for node in child_nodes if node["id"] not in known_node_ids
                    )
                    edges.extend(child_edges)

        for raw_edge in raw_edges:
            source = step_ids.get(str(raw_edge.get("source") or ""))
            target = step_ids.get(str(raw_edge.get("target") or ""))
            if not source or not target:
                continue
            incoming.add(target)
            edges.append(
                {
                    "source_id": source,
                    "target_id": target,
                    "relation": "flow",
                    "dashed": raw_edge.get("type") == "loop",
                }
            )
        for step_id in step_ids.values():
            if step_id not in incoming:
                edges.append(
                    {
                        "source_id": root_id,
                        "target_id": step_id,
                        "relation": "orchestrates",
                    }
                )

    return {"root_id": root_id, "nodes": nodes, "edges": edges}


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
            # Mismo desempate por clave primaria que el catálogo: el feed
            # también se recorre por páginas.
            f"ORDER BY updated_at DESC, "
            f"resource_type ASC, resource_id ASC, owner ASC "
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
