"""Rutas para cuentas de proveedor vinculadas."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.routes.auth import require_auth
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.config.session import RATE_IP_FACTOR
from app.connections import account_providers, get_account_provider
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.models.request_bodies import AccountBody, AccountSyncBody, DeviceCodeBody
from app.services.credentials import assert_readable
from app.services.provider_models import fetch_provider_models as _fetch_models
from app.storage.accounts import AccountStorage, _mask
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.skill_storage import SkillStorage
from app.utils import now_iso as _now

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_storage = AccountStorage()
_conn_storage = ConnectionStorage()
# El sondeo del device flow lo hace un usuario ya autenticado que vincula su
# cuenta de GitHub; su cupo no puede depender de cuántos compañeros comparten
# la salida a internet de la oficina.
_device_flow_limiter = RateLimiter(
    calls=30,
    window=60,
    key_func=principal_key,
    shared=True,
    name="accounts-device-flow",
    ip_calls=30 * RATE_IP_FACTOR,
)


async def _owner(user: str) -> str:
    """Las cuentas de proveedor, y las conexiones que generan al sincronizar,
    son siempre personales del usuario — igual que una Connection creada a
    mano con scope="personal" (ver `save_connection` en connections.py). No
    hay un bucket especial para admins: `connections.py` lista por
    `group_id`/`user`, nunca por un owner_id distinto del propio usuario."""
    return user


_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)

_ACCOUNT_PROVIDERS = account_providers()
_PROVIDERS = list(_ACCOUNT_PROVIDERS)
_PROVIDER_LABELS = {
    account_type: implementation.label
    for account_type, implementation in _ACCOUNT_PROVIDERS.items()
}
_PROVIDER_TYPE_IDS = {
    account_type: implementation.type_id
    for account_type, implementation in _ACCOUNT_PROVIDERS.items()
}

# iAgents Hub no encaja en el modelo "api_key -> lista de modelos -> una
# Connection por modelo": es una cuenta con url+usuario+contraseña cuyo
# "sync" trae agentes/skills/conocimiento/conexiones de otra instancia
# (reutiliza el servicio `run_hub_sync`), no una lista de LLMs.
_HUB_PROVIDER = "iagentshub"


async def _test_hub_login(url: str, username: str, password: str) -> Dict[str, Any]:
    """Prueba el login contra otra instancia de iAgents Hub (sin modelos)."""
    from app.connections.iagentshub import IAgentsHubProvider

    result = await asyncio.to_thread(
        IAgentsHubProvider.test,
        {"url": url, "username": username, "api_key": password},
    )
    return {
        "ok": result.ok,
        "message": result.message,
        "models": [],
        "models_count": 0,
    }


async def _sync_hub_account(
    account_id: str, account: Dict[str, Any], owner: str
) -> Dict[str, Any]:
    """Sincroniza una cuenta de proveedor `iagentshub`: crea/actualiza una
    Connection-espejo tipo `iagentshub` (misma que usaría el usuario desde
    Connections) y reutiliza `run_hub_sync` para traer agentes/skills/
    conocimiento/conexiones — nada que ver con la selección de modelos que
    usan el resto de proveedores."""
    from app.services.hub_sync import run_hub_sync

    label = account.get("name") or _PROVIDER_LABELS[_HUB_PROVIDER]
    existing_conns = await _conn_storage.list(owner)
    mirror = next(
        (
            c
            for c in existing_conns
            if c.get("_account_id") == account_id and c.get("type") == _HUB_PROVIDER
        ),
        None,
    )
    conn_data: Dict[str, Any] = {
        "name": label,
        "type": _HUB_PROVIDER,
        "url": account.get("url", ""),
        "username": account.get("username", ""),
        "api_key": account.get("api_key", ""),
        "_account_id": account_id,
        "provider_account_id": account_id,
    }
    if mirror:
        conn_data["id"] = mirror["id"]
    saved_conn = await _conn_storage.save(conn_data, owner_id=owner)
    conn_data["id"] = saved_conn["id"]

    hub_result = await run_hub_sync(conn_data["id"], conn_data, owner)

    account["id"] = account_id
    account["last_synced_at"] = _now()
    account["sync_summary"] = hub_result
    saved = await _storage.save(account, owner)
    return _redact(saved)


def _redact(saved: Dict[str, Any]) -> Dict[str, Any]:
    if saved.get("api_key"):
        saved["api_key_masked"] = _mask(saved["api_key"])
        del saved["api_key"]
    return saved


# IMPORTANTE: la ruta literal /test debe definirse ANTES que /{account_id}...
# para que FastAPI la priorice (mismo criterio que en connections.py).


@router.get("")
async def list_accounts(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    """Cuentas vinculadas del usuario. Pueden existir varias del mismo
    `provider` (ej. dos API keys distintas de OpenAI), cada una con su
    propio `id`."""
    return [_redact(a) for a in await _storage.list(await _owner(user))]


@router.post("")
async def link_account(
    body: AccountBody, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Vincula una cuenta nueva. No hay límite de una por `provider`."""
    body = body.payload()
    provider = str(body.get("provider") or "").strip()
    if provider not in _PROVIDERS:
        raise APIError(
            400,
            "unsupported_provider",
            f"Proveedor no soportado: {provider}",
            extra={"provider": provider},
        )
    api_key = str(body.get("api_key") or "").strip()
    host = str(body.get("host") or "").strip()
    url = str(body.get("url") or "").strip()
    username = str(body.get("username") or "").strip()
    name = str(body.get("name") or "").strip()
    if provider == _HUB_PROVIDER:
        if not url or not username or not api_key:
            raise APIError(
                422,
                "invalid_field",
                "url, username y api_key (contraseña) son obligatorios",
                extra={"field": "url"},
            )
    elif not api_key and provider != "ollama":
        raise APIError(
            422, "invalid_field", "api_key requerida", extra={"field": "api_key"}
        )
    data: Dict[str, Any] = {"provider": provider, "api_key": api_key}
    if host:
        data["host"] = host
    if provider == _HUB_PROVIDER:
        data["url"] = url
        data["username"] = username
    if name:
        data["name"] = name
    implementation = get_account_provider(provider)
    if implementation is not None:
        try:
            implementation.validate_config(data, purpose="save")
        except ValueError as exc:
            field = "host" if provider == "ollama" else "url"
            raise APIError(
                422, "unsafe_url", str(exc), extra={"field": field}
            ) from exc
    saved = await _storage.save(data, await _owner(user))
    return _redact(saved)


@router.post("/test")
async def test_new_account(
    body: AccountBody, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Prueba credenciales nuevas, antes de vincular la cuenta."""
    body = body.payload()
    provider = str(body.get("provider") or "").strip()
    if provider not in _PROVIDERS:
        raise APIError(
            400,
            "unsupported_provider",
            f"Proveedor no soportado: {provider}",
            extra={"provider": provider},
        )
    api_key = str(body.get("api_key") or "").strip()
    host = str(body.get("host") or "").strip()
    if provider == _HUB_PROVIDER:
        url = str(body.get("url") or "").strip()
        username = str(body.get("username") or "").strip()
        return await _test_hub_login(url, username, api_key)
    models = await _fetch_models(provider, api_key, host)
    return {"ok": True, "models": models, "models_count": len(models)}


@router.post("/github/device-code")
async def github_device_code(
    _: str = Depends(require_auth), _rl: None = Depends(_device_flow_limiter)
) -> Dict[str, Any]:
    """Inicia el GitHub OAuth Device Flow para vincular una cuenta proveedor
    (usuario ya logueado en iAgentsHub): en vez de pegar un Personal Access
    Token a mano, visita `verification_uri`, introduce `user_code` y autoriza
    — el cliente sondea `/github/device-token` hasta que esté listo. Para el
    login de la app (sin sesión previa) ver `app/api/routes/auth.py`."""
    from app.auth.github_device_flow import request_device_code

    return await request_device_code(scope="read:user")


@router.post("/github/device-token")
async def github_device_token(
    body: DeviceCodeBody,
    _: str = Depends(require_auth),
    _rl: None = Depends(_device_flow_limiter),
) -> Dict[str, Any]:
    """Sondea si el usuario ya autorizó el Device Flow iniciado con
    `/github/device-code`; devuelve el access_token en cuanto esté listo."""
    from app.auth.github_device_flow import poll_device_token

    body = body.payload()
    device_code = str(body.get("device_code") or "").strip()
    if not device_code:
        raise APIError(
            422,
            "invalid_field",
            "device_code requerido",
            extra={"field": "device_code"},
        )
    return await poll_device_token(device_code)


@router.put("/{account_id}")
async def update_account(
    account_id: str, body: AccountBody, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    owner = await _owner(user)
    existing = await _storage.get(account_id, owner)
    if not existing:
        raise APIError(
            404, "not_found", "Cuenta no vinculada", extra={"resource": "account"}
        )
    body = body.payload()
    api_key = str(body.get("api_key") or "").strip()
    host = str(body.get("host") or "").strip()
    url = str(body.get("url") or "").strip()
    username = str(body.get("username") or "").strip()
    name = str(body.get("name") or "").strip()
    # La edición es parcial: conservar host/url/nombre y validar la configuración
    # efectiva completa, no solo los campos presentes en esta petición.
    data: Dict[str, Any] = dict(existing)
    data.update({"id": account_id, "provider": existing["provider"]})
    if api_key:
        data["api_key"] = api_key
    if host:
        data["host"] = host
    if url:
        data["url"] = url
    if username:
        data["username"] = username
    if name:
        data["name"] = name
    implementation = get_account_provider(existing["provider"])
    if implementation is not None:
        try:
            implementation.validate_config(data, purpose="save")
        except ValueError as exc:
            field = "host" if existing["provider"] == "ollama" else "url"
            raise APIError(
                422, "unsafe_url", str(exc), extra={"field": field}
            ) from exc
    saved = await _storage.save(data, owner)
    return _redact(saved)


@router.delete("/{account_id}")
async def unlink_account(
    account_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    owner = await _owner(user)
    if not await _storage.delete(account_id, owner):
        raise APIError(
            404, "not_found", "Cuenta no vinculada", extra={"resource": "account"}
        )
    # Desvincular borra también las conexiones que esa cuenta había
    # sincronizado — dejarlas huérfanas (sin cuenta que las gestione, pero
    # con credenciales vigentes) es más confuso que útil.
    connections_deleted = 0
    for conn in await _conn_storage.list(owner):
        if conn.get("_account_id") == account_id:
            if await _conn_storage.delete(conn["id"], owner_id=owner):
                connections_deleted += 1
    return {"ok": True, "connections_deleted": connections_deleted}


@router.post("/{account_id}/test")
async def test_account(
    account_id: str,
    body: AccountBody | None = None,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Previsualiza modelos de una cuenta ya vinculada, usando sus
    credenciales guardadas (o las que se manden explícitamente en el body,
    para probar un cambio de api_key/host antes de guardarlo)."""
    account = await _storage.get(account_id, await _owner(user))
    if not account:
        raise APIError(
            404, "not_found", "Cuenta no vinculada", extra={"resource": "account"}
        )
    payload = body.payload() if body else {}
    if account["provider"] == _HUB_PROVIDER:
        url = str(payload.get("url") or "").strip() or account.get("url", "")
        username = str(payload.get("username") or "").strip() or account.get(
            "username", ""
        )
        api_key = str(payload.get("api_key") or "").strip() or account.get(
            "api_key", ""
        )
        if not api_key:
            assert_readable(account)
        return await _test_hub_login(url, username, api_key)
    api_key = str(payload.get("api_key") or "").strip() or account.get("api_key", "")
    # Sin clave nueva en el body, la prueba usaría la guardada: si no se pudo
    # descifrar, el fallo es local y se dice, no se manda vacía al proveedor.
    if not api_key:
        assert_readable(account)
    host = str(payload.get("host") or "").strip() or account.get("host", "")
    models = await _fetch_models(account["provider"], api_key, host)
    return {"ok": True, "models": models, "models_count": len(models)}


@router.post("/{account_id}/sync")
async def sync_account(
    account_id: str,
    body: AccountSyncBody | None = None,
    user: str = Depends(require_auth),
) -> Dict[str, Any]:
    """Sincroniza modelos de la cuenta.

    Body opcional `{"models": [...]}`: si se manda, la selección es la
    verdad — se crean/actualizan conexiones para esos modelos (intersección
    con lo que el proveedor realmente reporta, nunca se confía ciegamente en
    el id que mande el cliente) y se BORRAN las conexiones de esta cuenta
    para modelos que ya estaban sincronizados y ahora no están en la lista
    (el usuario los desmarcó). Sin body (o sin la clave `models`), sincroniza
    todos los modelos encontrados y no borra nada — es un "traer todo", no
    una selección explícita.
    """
    owner = await _owner(user)
    account = await _storage.get(account_id, owner)
    if not account:
        raise APIError(
            404, "not_found", "Cuenta no vinculada", extra={"resource": "account"}
        )

    assert_readable(account)

    provider = account["provider"]
    if provider == _HUB_PROVIDER:
        return await _sync_hub_account(account_id, account, owner)

    payload = body.payload() if body else {}
    selected = payload.get("models")
    if not isinstance(selected, list):
        selected = None

    api_key = account.get("api_key", "")
    host = account.get("host", "")
    label = account.get("name") or _PROVIDER_LABELS.get(provider, provider)

    # 1. Fetch models from provider
    models = await _fetch_models(provider, api_key, host)
    if selected is not None:
        selected_set = set(selected)
        models = [m for m in models if m in selected_set]

    # 2. Create / update one connection per model — ligadas a ESTA cuenta
    # (_account_id) para no pisar las de otra cuenta del mismo provider si
    # el usuario tiene varias vinculadas a la vez.
    type_id = _PROVIDER_TYPE_IDS.get(provider, provider)

    existing_conns = await _conn_storage.list(owner)
    existing_by_model: Dict[str, Any] = {
        c["model"]: c
        for c in existing_conns
        if c.get("type") == type_id
        and c.get("model")
        and c.get("_account_id") == account_id
    }
    connections_created = 0
    connections_updated = 0
    connections_deleted = 0
    conn_ids: set = set()

    for model_id in models:
        conn_data: Dict[str, Any] = {
            "name": f"{label} / {model_id}",
            "type": type_id,
            "api_key": api_key,
            "model": model_id,
            "_account_id": account_id,
            "provider_account_id": account_id,
        }
        if host:
            conn_data["host"] = host
        if model_id in existing_by_model:
            conn_data["id"] = existing_by_model[model_id]["id"]
            connections_updated += 1
        else:
            connections_created += 1
        saved_conn = await _conn_storage.save(conn_data, owner_id=owner)
        conn_ids.add(saved_conn["id"])

    # Conexiones ya existentes de ESTA cuenta que no salieron en esta pasada:
    # con selección explícita (`selected`) el usuario las desmarcó a
    # propósito, se borran de verdad — no un "traer todo" donde el modelo
    # simplemente ya no aparece en el catálogo del proveedor.
    models_set = set(models)
    for model_id, existing_conn in existing_by_model.items():
        if model_id in models_set:
            continue
        if selected is not None:
            if await _conn_storage.delete(existing_conn["id"], owner_id=owner):
                connections_deleted += 1
        else:
            conn_ids.add(existing_conn["id"])

    account_conn_ids = conn_ids

    # 3. Find private agents linked to this account's connections (DB-backed)
    private_agents = await _agent_storage.list(scope="private")
    agents_linked = []
    for summary in private_agents:
        if summary.get("connection_id") in account_conn_ids:
            full = await _agent_storage.get(summary["id"], scope="private") or {}
            routines = [r for r in (full.get("routines") or []) if isinstance(r, dict)]
            agents_linked.append(
                {
                    "id": summary["id"],
                    "name": summary["name"],
                    "routines_count": len(routines),
                }
            )

    # 4. Count private skills (DB-backed)
    private_skills_count = len(await _skill_storage.list(scope="private"))

    # 5. Save updated account with summary
    summary_data = {
        "connections_created": connections_created,
        "connections_updated": connections_updated,
        "connections_deleted": connections_deleted,
        "agents_count": len(agents_linked),
        "agents": agents_linked,
        "routines_count": sum(a["routines_count"] for a in agents_linked),
        "skills_private_count": private_skills_count,
    }
    account["id"] = account_id
    account["models"] = models
    account["last_synced_at"] = _now()
    account["sync_summary"] = summary_data
    saved = await _storage.save(account, owner)
    return _redact(saved)
