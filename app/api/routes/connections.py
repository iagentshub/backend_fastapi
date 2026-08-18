"""Rutas de conexiones."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response

from app.api.routes.auth import (
    GroupContext,
    require_group,
    require_group_session,
)
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.config.security import assert_safe_url
from app.connections import get_provider
from app.errors import APIError
from app.models.llm_orchestration import orchestration_connection_id
from app.models.request_bodies import (
    ConnectionPayload,
)
from app.pagination.materialized import paginate_materialized
from app.services.connection_access import connection_access
from app.sql import sql
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import DB_ERRORS, IS_PG, open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.guest import get_session, is_guest
from app.storage.knowledge import KnowledgeStorage
from app.storage.llm_orchestrations import LLMOrchestrationStorage
from app.storage.skill_storage import SkillStorage
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.origin import assert_resource_writable, compute_origin_type

router = APIRouter(prefix="/api/connections", tags=["connections"])

_storage = ConnectionStorage()
_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)
_know_storage = KnowledgeStorage()
_llm_orchestration_storage = LLMOrchestrationStorage()
_shares = GroupShareStorage()
_groups = GroupStorage()


async def _owner(user: str, group_id: str) -> str | None:
    """None → admin ve todo; str → filtra por group."""
    return None if await get_user_role(user) == "admin" else group_id


async def _list_accessible(user: str, group_id: str) -> List[Dict[str, Any]]:
    """Lista conexiones del group activo + personales del usuario (en group de equipo).

    En group personal (group_id == user) devuelve solo las propias.
    En group de equipo incluye también las personales marcadas con _personal_key=True.
    """
    return await connection_access.list_accessible(user, group_id, include_shared=False)


async def _list_orchestration_connections(
    user: str, group_id: str, *, shared_only: bool = False
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    if not shared_only:
        owner_ids = [group_id] if group_id == user else [group_id, user]
        for owner_id in owner_ids:
            for item in await _llm_orchestration_storage.list(owner_id):
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                items.append(
                    connection_access.virtual_connection(
                        item, personal=owner_id == user and group_id != user
                    )
                )
    shared_ids = await _shares.get_group_shared_resource_ids(
        group_id, "llm_orchestration"
    )
    for item_id in shared_ids:
        if item_id in seen:
            continue
        item = await _llm_orchestration_storage.get_any(item_id)
        if item and await _groups.owner_is_active(str(item.get("owner_id") or "")):
            if item.get("owner_id") not in {user, group_id}:
                configured = await connection_access.get_accessible(
                    orchestration_connection_id(item_id), user, group_id
                )
                if not configured:
                    continue
            seen.add(item_id)
            items.append(
                connection_access.virtual_connection(item, shared=True, personal=False)
            )
    return items


async def _get_conn_any(
    conn_id: str, user: str, group_id: str
) -> Dict[str, Any] | None:
    """Obtiene una conexión buscando en el group activo, el personal, y por último
    entre las compartidas directamente con el group (referencia sin duplicar).

    Etiqueta el resultado con `owner_id` (y `_personal_key` en el caso del
    fallback personal), igual que hace `_list_accessible`, para que quien la
    reciba pueda distinguir "es mía" de "es del group/compartida" sin
    tener que volver a consultar — sin esto, un check de permiso posterior
    aplicado indiscriminadamente bloquea al propio dueño de su conexión
    personal en cuanto el group activo no es el suyo."""
    return await connection_access.get_accessible(conn_id, user, group_id)


async def _resolve_connections(
    user: str, group_id: str, include_shared: bool = True
) -> List[Dict[str, Any]]:
    """Devuelve la lista de conexiones visibles para el usuario según su rol.
    (incluye admin: la visibilidad global de admin se sirve vía /api/admin/*,
    devolver aquí todas las conexiones del sistema exponía las de otros
    usuarios sin marcarlas como ajenas)"""
    return await connection_access.list_accessible(
        user, group_id, include_shared=include_shared
    )


def _fetch_ollama_models(host: str, api_key: str = "") -> List[str]:
    """Llama a /api/tags y devuelve los nombres de modelos instalados."""
    from app.config.security import assert_safe_url
    from app.connections.ollama import OllamaProvider

    try:
        assert_safe_url(host)  # C3: prevenir SSRF via hosts de conexiones almacenadas
        data = OllamaProvider._fetch_tags(host, api_key)
    except OSError:
        alt = OllamaProvider._alt_host(host)
        if not alt:
            return []
        try:
            data = OllamaProvider._fetch_tags(alt, api_key)
        except (OSError, ValueError) as exc:
            flog.warning(f"[ollama] Sin catálogo de modelos en {alt}: {exc}")
            return []
    except ValueError as exc:
        # Dos cosas caen aquí y conviene no confundirlas al leer el log: el
        # ValueError de assert_safe_url (host bloqueado por SSRF) y el
        # JSONDecodeError de una respuesta que no es JSON. El OSError de red se
        # trata arriba, en la rama del host alternativo.
        # La lista vacía es indistinguible de "no hay modelos" en la UI, así que
        # el motivo real solo existe si se registra.
        flog.warning(f"[ollama] Catálogo no obtenido de {host}: {exc}")
        return []
    return [m["name"] for m in (data.get("models") or []) if m.get("name")]


async def _ollama_conns_to_models(
    ollama_conns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convierte todas las conexiones Ollama en una lista de entradas por modelo,
    sin duplicados. Las conexiones con modelo específico tienen prioridad sobre
    las generadas por expansión de la conexión base.
    """
    seen: set = set()
    result: List[Dict[str, Any]] = []

    for c in ollama_conns:
        model = (c.get("model") or "").strip()
        if not model:
            continue
        if model in seen:
            continue
        seen.add(model)
        clean = {k: v for k, v in c.items() if k != "api_key"}
        clean["name"] = model
        result.append(clean)

    base_conns = [c for c in ollama_conns if not (c.get("model") or "").strip()]
    if base_conns:
        base = base_conns[0]
        host = (base.get("host") or "http://localhost:11434").rstrip("/")
        api_key = str(base.get("api_key") or "")
        models = await asyncio.to_thread(_fetch_ollama_models, host, api_key)
        base_clean = {k: v for k, v in base.items() if k != "api_key"}
        if models:
            for model in models:
                if model in seen:
                    continue
                seen.add(model)
                result.append(
                    {
                        **base_clean,
                        "id": f"{base['id']}::{model}",
                        "name": model,
                        "model": model,
                    }
                )
        else:
            result.append(base_clean)

    return result


# IMPORTANTE: las rutas literales (/providers, /raw, /test-all) deben definirse
# ANTES que las rutas con parámetros (/{conn_id}) para que FastAPI las priorice.


@router.get("/raw")
async def list_connections_raw(
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    """Devuelve las conexiones tal como están en BD, sin expansión de modelos Ollama.
    Usado por el perfil para gestionar credenciales base."""
    user, group_id = ctx.user, ctx.group_id
    raw = await _resolve_connections(user, group_id)
    return [{k: v for k, v in c.items() if k != "api_key"} for c in raw]


@router.get("")
async def list_connections(
    requested_group_id: Optional[str] = Query(None, alias="group_id"),
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    response: Response = None,  # type: ignore[assignment]
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    user, active_group_id = ctx.user, ctx.group_id

    if requested_group_id is not None and not is_guest(user):
        role = await get_user_role(user)
        if role != "admin" and not await _groups.can_access(requested_group_id, user):
            raise APIError(403, "forbidden", "Sin acceso a este grupo")
        # Devuelve solo las conexiones compartidas con este grupo específico
        shared_ids = set(
            await _shares.get_group_shared_resource_ids(
                requested_group_id, "connection"
            )
        )
        raw: List[Dict[str, Any]] = []
        for rid in shared_ids:
            owner_id_row = await _storage.get_owner_id(rid)
            if not owner_id_row or not await _groups.owner_is_active(owner_id_row):
                continue
            c = await _storage.get(rid)
            if c:
                c["_shared"] = True
                c["_group_id"] = requested_group_id
                raw.append(c)
        raw.extend(
            await _list_orchestration_connections(
                user, requested_group_id, shared_only=True
            )
        )
    else:
        raw = await _resolve_connections(user, active_group_id)
        if not is_guest(user):
            raw.extend(await _list_orchestration_connections(user, active_group_id))

    if not include_inactive:
        raw = [c for c in raw if c.get("is_active", True)]

    for c in raw:
        if c.get("_shared") or c.get("owner_id") in (user, active_group_id):
            c["origin_type"] = compute_origin_type(c)

    non_ollama = [c for c in raw if c.get("type") != "ollama"]
    ollama_raw = [c for c in raw if c.get("type") == "ollama"]
    if (
        active_group_id != user
        and not is_guest(user)
        and await get_user_role(user) != "admin"
    ):
        # Las conexiones personales del usuario (incluidas aquí por
        # _list_accessible como cortesía al estar en un group de equipo)
        # no deben pasar por el permiso de RECURSO DE EQUIPO: son suyas,
        # punto — si no, un usuario sin membresía "real" en el group
        # activo (p. ej. justo tras cambiar de group) pierde el acceso a
        # sus propias conexiones personales.
        # Un solo SELECT sobre group_members para las dos listas: la fila del
        # miembro es la misma para todas las conexiones.
        permitido = await _groups.permission_checker(active_group_id, user)
        non_ollama = [
            connection
            for connection in non_ollama
            if connection.get("owner_id") == user
            or permitido("connections", connection["id"], "direct")
        ]
        ollama_raw = [
            connection
            for connection in ollama_raw
            if connection.get("owner_id") == user
            or permitido("connections", connection["id"], "direct")
        ]

    result: List[Dict[str, Any]] = [
        {k: v for k, v in c.items() if k != "api_key"} for c in non_ollama
    ]

    if ollama_raw:
        result.extend(await _ollama_conns_to_models(ollama_raw))

    result = paginate_materialized(
        result, limit=limit, offset=offset, response=response
    )
    return result


@router.post("")
async def save_connection(
    body: ConnectionPayload, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    payload = body.payload()
    # Las conexiones nuevas siempre pertenecen al usuario. Compartirlas con un
    # group es una acción posterior y opcional, igual que para el resto de
    # recursos privados. Se descarta `scope` por compatibilidad con clientes
    # antiguos que todavía puedan enviarlo.
    payload.pop("scope", None)
    if not get_provider(payload.get("type") or ""):
        raise APIError(
            422,
            "invalid_field",
            "Tipo de conexión no válido",
            extra={"field": "connection_type"},
        )

    # SSRF: `url` es el destino al que el chat hace POST más tarde. Se valida
    # aquí y también antes de cada stream, porque las conexiones que ya están
    # en la BD no vuelven a pasar por el alta. Ollama queda fuera a propósito:
    # usa `host` y un servidor en loopback es su caso de uso normal.
    conn_url = str(payload.get("url") or "").strip()
    if conn_url and (payload.get("type") or "").lower() != "ollama":
        try:
            assert_safe_url(conn_url)
        except ValueError as exc:
            raise APIError(422, "unsafe_url", str(exc), extra={"field": "url"}) from exc

    # Las conexiones son siempre privadas — se pueden compartir con un group completo
    labels = [lbl for lbl in (payload.get("labels") or []) if lbl != "public"]
    if "private" not in labels:
        labels = ["private"] + labels
    payload["labels"] = labels

    if is_guest(user):
        s = get_session(user)
        guest_id = payload.get("id")
        if guest_id and not any(c.get("id") == guest_id for c in s.connections):
            guest_id = None
        conn_id = guest_id or generate_id()
        conn: Dict[str, Any] = {
            **payload,
            "id": conn_id,
            "name": str(
                payload.get("name")
                or payload.get("label")
                or payload.get("type")
                or conn_id
            ).strip(),
            "resource_type": "connection",
            "scope": "private",
            "is_active": True,
        }
        s.connections = [c for c in s.connections if c.get("id") != conn["id"]]
        s.connections.append(conn)
        return {k: v for k, v in conn.items() if k != "api_key"}
    conn_id_in_payload = payload.get("id")
    owner = user
    existing = None
    if conn_id_in_payload:
        existing = await _storage.get(conn_id_in_payload, user)
        # Compatibilidad con conexiones antiguas creadas como propiedad del
        # group: al editarlas conservan su propietario en vez de duplicarse.
        if existing is None and group_id != user:
            existing = await _storage.get(conn_id_in_payload, group_id)
            if existing is not None:
                owner = group_id
        if existing:
            assert_resource_writable(existing, "connection")
    if conn_id_in_payload and not existing:
        # Un id entrante solo es válido para editar una fila existente;
        # en altas el id lo genera siempre el servidor.
        payload.pop("id", None)
    was_update = existing is not None
    conn = await _storage.save(payload, owner_id=owner)
    action = "actualizada" if was_update else "creada"
    flog.info(
        f"Conexión {action}: {conn['id']} {conn.get('name', conn.get('type', ''))!r}",
        username=user,
    )
    return {k: v for k, v in conn.items() if k != "api_key"}


@router.get("/tokens-daily")
async def get_tokens_daily(
    days: int = 14,
    ctx: GroupContext = Depends(require_group),
) -> List[Dict[str, Any]]:
    import datetime as _dt

    days = max(1, min(days, 90))
    cutoff = (_dt.date.today() - _dt.timedelta(days=days - 1)).isoformat()
    today = _dt.date.today().isoformat()
    group_id = ctx.group_id

    async with open_db() as conn:
        try:
            rows = await conn.fetchall(
                sql("queries/connections:tokens_per_day_of_owner"),
                (group_id, cutoff),
            )
            if not rows:
                if IS_PG:
                    await conn.execute(
                        sql("queries/connections:seed_token_daily_pg"),
                        (today, group_id),
                    )
                else:
                    await conn.execute(
                        sql("queries/connections:seed_token_daily_sqlite"),
                        (today, group_id),
                    )
                await conn.commit()
                rows = await conn.fetchall(
                    sql("queries/connections:tokens_per_day_of_owner"),
                    (group_id, cutoff),
                )
        except DB_ERRORS as exc:
            # El backfill de token_daily es oportunista: si falla, la gráfica
            # sale vacía pero la página carga. Sin registro, "mi consumo aparece
            # a cero" no tiene diagnóstico posible.
            flog.warning(f"[connections] Backfill de token_daily fallido: {exc}")
            rows = []

    return [{"day": r[0], "tokens": r[1]} for r in rows]


@router.get("/{conn_id}")
async def get_connection(
    conn_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        conn = next(
            (c for c in get_session(user).connections if c.get("id") == conn_id), None
        )
    else:
        role = await get_user_role(user)
        if role == "admin":
            conn = await _storage.get(conn_id, None)
        else:
            conn = await _get_conn_any(conn_id, user, group_id)
    if not conn:
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    if (
        group_id != user
        and not is_guest(user)
        and await get_user_role(user) != "admin"
        and conn.get("owner_id") != user
        and not await _groups.has_resource_permission(
            group_id, user, "connections", conn_id, "direct"
        )
    ):
        raise APIError(403, "forbidden", "Sin permiso para usar esta conexión")
    conn["origin_type"] = compute_origin_type(conn)
    return {k: v for k, v in conn.items() if k != "api_key"}


@router.delete("/{conn_id}")
async def delete_connection(
    conn_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        s = get_session(user)
        before = len(s.connections)
        s.connections = [c for c in s.connections if c.get("id") != conn_id]
        if len(s.connections) == before:
            raise APIError(
                404,
                "not_found",
                "Conexión no encontrada",
                extra={"resource": "connection"},
            )
        return {"ok": True}
    existing = await _get_conn_any(conn_id, user, group_id)
    if existing:
        assert_resource_writable(existing, "connection")
    owner_id = await _owner(user, group_id)
    deleted = await _storage.delete(conn_id, owner_id)
    if not deleted and group_id != user:
        deleted = await _storage.delete(conn_id, user)
    if not deleted:
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    flog.info(f"Conexión borrada: {conn_id}", username=user)
    return {"ok": True}


async def _set_connection_active(
    conn_id: str, active: bool, ctx: GroupContext
) -> Dict[str, Any]:
    user, group_id = ctx.user, ctx.group_id
    if is_guest(user):
        raise APIError(
            403, "forbidden", "Los invitados no pueden desactivar conexiones"
        )
    existing = await _get_conn_any(conn_id, user, group_id)
    if existing:
        assert_resource_writable(existing, "connection")
    owner_id = await _owner(user, group_id)
    ok = await _storage.set_active(conn_id, owner_id, active)
    if not ok and group_id != user:
        ok = await _storage.set_active(conn_id, user, active)
    if not ok:
        raise APIError(
            404, "not_found", "Conexión no encontrada", extra={"resource": "connection"}
        )
    estado = "activada" if active else "desactivada"
    flog.info(f"Conexión {estado}: {conn_id}", username=user)
    return {"ok": True, "is_active": active}


@router.post("/{conn_id}/activate")
async def activate_connection(
    conn_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_connection_active(conn_id, True, ctx)


@router.post("/{conn_id}/deactivate")
async def deactivate_connection(
    conn_id: str, ctx: GroupContext = Depends(require_group)
) -> Dict[str, Any]:
    return await _set_connection_active(conn_id, False, ctx)
