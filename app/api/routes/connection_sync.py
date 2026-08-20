"""Sincronización con hubs e importación de modelos remotos."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.api.routes.auth import GroupContext, require_group_session
from app.auth.auth import get_user_role
from app.config.session import RATE_IP_FACTOR
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.services.connection_access import connection_access
from app.services.credentials import assert_readable
from app.services.hub_sync import run_hub_sync
from app.services.provider_models import fetch_provider_models
from app.storage.connection_storage import ConnectionStorage

router = APIRouter(prefix="/api/connections", tags=["connection-sync"])

_storage = ConnectionStorage()
# N2: limitar hub-sync para evitar amplificación de peticiones HTTP externas.
# 20 syncs/min por cuenta es un límite útil en producción sin romper tests.
_hub_sync_limiter = RateLimiter(
    calls=20,
    window=60,
    key_func=principal_key,
    shared=True,
    name="hub-sync",
    ip_calls=20 * RATE_IP_FACTOR,
)


async def _owner(user: str, group_id: str) -> str | None:
    return None if await get_user_role(user) == "admin" else group_id


async def _get_conn_any(
    conn_id: str, user: str, group_id: str
) -> Dict[str, Any] | None:
    return await connection_access.get_accessible(conn_id, user, group_id)


@router.post("/{conn_id}/hub-sync")
async def hub_sync(
    conn_id: str,
    ctx: GroupContext = Depends(require_group_session),
    _rl: None = Depends(_hub_sync_limiter),  # N2: prevenir amplificación HTTP
) -> Dict[str, Any]:
    """Sincroniza agentes, skills, conocimiento y conexiones desde un hub remoto."""
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
    if conn.get("type") != "iagentshub":
        raise APIError(
            400,
            "hub_sync_invalid_type",
            "Solo disponible para conexiones de tipo iAgents Hub",
        )
    return await run_hub_sync(conn_id, conn, group_id)


@router.post("/{conn_id}/import-models")
async def import_models(
    conn_id: str,
    ctx: GroupContext = Depends(require_group_session),
) -> Dict[str, Any]:
    """Descubre modelos de la conexión-credencial y crea una conexión por modelo."""
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

    conn_type = conn.get("type", "")

    _PROVIDER_TYPE_TO_ACCOUNT: Dict[str, str] = {
        "claude": "anthropic",
        "gemini": "google",
        "openai": "openai",
        "ollama": "ollama",
        "nvidia": "nvidia",
        "qwen": "qwen",
        "grok": "grok",
    }
    account_key = _PROVIDER_TYPE_TO_ACCOUNT.get(conn_type)
    if not account_key:
        raise APIError(
            400,
            "import_models_unsupported",
            f"Import de modelos no soportado para '{conn_type}'",
            extra={"type": conn_type},
        )

    assert_readable(conn)
    api_key = conn.get("api_key", "")
    host = conn.get("host", "") or conn.get("url", "")
    models = await fetch_provider_models(account_key, api_key, host)
    if not models:
        raise APIError(
            502, "no_models_found", "No se encontraron modelos en este proveedor"
        )

    owner_id = await _owner(user, group_id)
    owner = conn.get("owner_id") or (owner_id or group_id)

    existing = await _storage.list(owner)
    existing_by_model = {
        c["model"]: c
        for c in existing
        if c.get("type") == conn_type and c.get("_source_conn") == conn_id
    }
    created = 0
    updated = 0
    for model_id in models:
        data: Dict[str, Any] = {
            "name": f"{conn.get('name', conn_type)} / {model_id}",
            "type": conn_type,
            "api_key": api_key,
            "model": model_id,
            "_imported": True,
            "_source_conn": conn_id,
        }
        if conn.get("host"):
            data["host"] = conn["host"]
        if conn.get("url"):
            data["url"] = conn["url"]
        if model_id in existing_by_model:
            data["id"] = existing_by_model[model_id]["id"]
            updated += 1
        else:
            created += 1
        await _storage.save(data, owner_id=owner)
    return {"ok": True, "created": created, "updated": updated, "models": models}
