"""Rutas de conexiones."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.routes.auth import (
    GroupContext,
    require_group_session,
)
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.connections import get_provider
from app.errors import APIError
from app.models.request_bodies import (
    ConnectionPayload,
)
from app.services.connection_access import connection_access
from app.sql import sql
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import DB_ERRORS, IS_PG, open_db
from app.storage.group_shares import GroupShareStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.llm_orchestrations import LLMOrchestrationStorage
from app.storage.skill_storage import SkillStorage
from app.utils import flog
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


# IMPORTANTE: las rutas literales (/providers, /raw, /test-all) deben definirse
# ANTES que las rutas con parámetros (/{conn_id}) para que FastAPI las priorice.


@router.get("/raw")
async def list_connections_raw(
    ctx: GroupContext = Depends(require_group_session),
) -> List[Dict[str, Any]]:
    """Devuelve las conexiones tal como están en BD, sin expansión de modelos Ollama.
    Usado por el perfil para gestionar credenciales base."""
    user, group_id = ctx.user, ctx.group_id
    raw = await _resolve_connections(user, group_id)
    return [{k: v for k, v in c.items() if k != "api_key"} for c in raw]


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
    provider = get_provider(payload.get("type") or "")
    if not provider:
        raise APIError(
            422,
            "invalid_field",
            "Tipo de conexión no válido",
            extra={"field": "connection_type"},
        )

    # La política vive en el proveedor y se repite al usar la conexión para
    # cubrir registros anteriores a esta validación de escritura.
    try:
        provider.validate_config(payload, purpose="save")
    except ValueError as exc:
        field = "host" if (payload.get("type") or "").lower() == "ollama" else "url"
        raise APIError(422, "unsafe_url", str(exc), extra={"field": field}) from exc

    # Las conexiones son siempre privadas — se pueden compartir con un group completo
    labels = [lbl for lbl in (payload.get("labels") or []) if lbl != "public"]
    if "private" not in labels:
        labels = ["private"] + labels
    payload["labels"] = labels

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
    ctx: GroupContext = Depends(require_group_session),
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
        and role != "admin"
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
    existing = await _get_conn_any(conn_id, user, group_id)
    if existing:
        assert_resource_writable(existing, "connection")
    owner_id = await _owner(user, group_id)
    deleted = (
        await _storage.delete_as_admin(conn_id)
        if owner_id is None
        else await _storage.delete(conn_id, owner_id)
    )
    if not deleted and owner_id is not None and group_id != user:
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
    conn_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_connection_active(conn_id, True, ctx)


@router.post("/{conn_id}/deactivate")
async def deactivate_connection(
    conn_id: str, ctx: GroupContext = Depends(require_group_session)
) -> Dict[str, Any]:
    return await _set_connection_active(conn_id, False, ctx)
