"""Rutas para cuentas de proveedor vinculadas."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, Request

from app.api.routes.auth import require_auth
from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.storage.accounts import AccountStorage, _mask
from app.storage.storage import AgentStorage, ConnectionStorage, SkillStorage
from app.utils import now_iso as _now
from app.utils.net import json_body

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_storage = AccountStorage(DB_FILE)
_conn_storage = ConnectionStorage(DB_FILE)
_device_flow_limiter = RateLimiter(calls=30, window=60)


async def _owner(user: str) -> str:
    """Las cuentas de proveedor, y las conexiones que generan al sincronizar,
    son siempre personales del usuario — igual que una Connection creada a
    mano con scope="personal" (ver `save_connection` en connections.py). No
    hay un bucket especial para admins: `connections.py` lista por
    `group_id`/`user`, nunca por un owner_id distinto del propio usuario."""
    return user


_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)

_PROVIDERS = ["anthropic", "openai", "github", "ollama", "nvidia", "google", "iagentshub"]
_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "github": "GitHub Copilot",
    "ollama": "Ollama",
    "nvidia": "NVIDIA NIM",
    "google": "Google Gemini",
    "iagentshub": "iAgents Hub",
}
_PROVIDER_TYPE_IDS: dict[str, str] = {
    "anthropic": "claude",
    "google": "gemini",
    "openai": "openai",
    "ollama": "ollama",
    "nvidia": "nvidia",
    "github": "github",
    "iagentshub": "iagentshub",
}

# iAgents Hub no encaja en el modelo "api_key -> lista de modelos -> una
# Connection por modelo": es una cuenta con url+usuario+contraseña cuyo
# "sync" trae agentes/skills/conocimiento/conexiones de otra instancia
# (reutiliza `_run_hub_sync` de connections.py), no una lista de LLMs.
_HUB_PROVIDER = "iagentshub"


async def _fetch_models(provider: str, api_key: str, host: str = "") -> List[str]:
    """Llama al proveedor y devuelve lista de model IDs."""
    try:
        if provider == "anthropic":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
            r.raise_for_status()
            data = r.json()
            return [m["id"] for m in data.get("data", [])]

        if provider == "openai":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
            return sorted(m["id"] for m in data.get("data", []))

        if provider == "github":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://models.inference.ai.azure.com/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            return [
                m.get("id") or m.get("name", "")
                for m in items
                if m.get("id") or m.get("name")
            ]

        if provider == "ollama":
            base = (host or "http://localhost:11434").rstrip("/")
            from app.config.security import assert_safe_url as _assu
            _assu(base)  # C3: prevenir SSRF via hosts almacenados en cuentas
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/api/tags", headers=headers)
            r.raise_for_status()
            data = r.json()
            return [m["name"] for m in data.get("models", [])]

        if provider == "nvidia":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
            return [m["id"] for m in data.get("data", [])]

        if provider == "google":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
            r.raise_for_status()
            data = r.json()
            return [m["name"].split("/")[-1] for m in data.get("models", [])]

    except httpx.HTTPStatusError as exc:
        raise APIError(
            exc.response.status_code, "upstream_error", str(exc)
        ) from exc
    except httpx.ConnectError:
        label = _PROVIDER_LABELS.get(provider, provider)
        raise APIError(
            502,
            "upstream_error",
            f"No se puede conectar con {label}. Comprueba que el servicio está activo y la URL es correcta.",
            extra={"provider": provider},
        ) from None
    except Exception as exc:
        raise APIError(502, "upstream_error", str(exc)) from exc

    return []


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
    Connections) y reutiliza `_run_hub_sync` para traer agentes/skills/
    conocimiento/conexiones — nada que ver con la selección de modelos que
    usan el resto de proveedores."""
    from app.api.routes.connections import _run_hub_sync

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
    }
    if mirror:
        conn_data["id"] = mirror["id"]
    saved_conn = await _conn_storage.save(conn_data, owner_id=owner)
    conn_data["id"] = saved_conn["id"]

    hub_result = await _run_hub_sync(conn_data["id"], conn_data, owner)

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
    request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Vincula una cuenta nueva. No hay límite de una por `provider`."""
    body = await json_body(request)
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
        raise APIError(422, "invalid_field", "api_key requerida", extra={"field": "api_key"})
    data: Dict[str, Any] = {"provider": provider, "api_key": api_key}
    if host:
        data["host"] = host
    if provider == _HUB_PROVIDER:
        data["url"] = url
        data["username"] = username
    if name:
        data["name"] = name
    saved = await _storage.save(data, await _owner(user))
    return _redact(saved)


@router.post("/test")
async def test_new_account(
    request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Prueba credenciales nuevas, antes de vincular la cuenta."""
    body = await json_body(request)
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
    from app.auth.github_oauth import request_device_code

    return await request_device_code(scope="read:user")


@router.post("/github/device-token")
async def github_device_token(
    request: Request,
    _: str = Depends(require_auth),
    _rl: None = Depends(_device_flow_limiter),
) -> Dict[str, Any]:
    """Sondea si el usuario ya autorizó el Device Flow iniciado con
    `/github/device-code`; devuelve el access_token en cuanto esté listo."""
    from app.auth.github_oauth import poll_device_token

    body = await json_body(request)
    device_code = str(body.get("device_code") or "").strip()
    if not device_code:
        raise APIError(
            422, "invalid_field", "device_code requerido", extra={"field": "device_code"}
        )
    return await poll_device_token(device_code)


@router.put("/{account_id}")
async def update_account(
    account_id: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    owner = await _owner(user)
    existing = await _storage.get(account_id, owner)
    if not existing:
        raise APIError(404, "not_found", "Cuenta no vinculada", extra={"resource": "account"})
    body = await json_body(request)
    api_key = str(body.get("api_key") or "").strip()
    host = str(body.get("host") or "").strip()
    url = str(body.get("url") or "").strip()
    username = str(body.get("username") or "").strip()
    name = str(body.get("name") or "").strip()
    data: Dict[str, Any] = {"id": account_id, "provider": existing["provider"]}
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
    saved = await _storage.save(data, owner)
    return _redact(saved)


@router.delete("/{account_id}")
async def unlink_account(
    account_id: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if not await _storage.delete(account_id, await _owner(user)):
        raise APIError(404, "not_found", "Cuenta no vinculada", extra={"resource": "account"})
    return {"ok": True}


@router.post("/{account_id}/test")
async def test_account(
    account_id: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Previsualiza modelos de una cuenta ya vinculada, usando sus
    credenciales guardadas (o las que se manden explícitamente en el body,
    para probar un cambio de api_key/host antes de guardarlo)."""
    account = await _storage.get(account_id, await _owner(user))
    if not account:
        raise APIError(404, "not_found", "Cuenta no vinculada", extra={"resource": "account"})
    try:
        body = await json_body(request)
    except Exception:
        body = {}
    if account["provider"] == _HUB_PROVIDER:
        url = str(body.get("url") or "").strip() or account.get("url", "")
        username = str(body.get("username") or "").strip() or account.get("username", "")
        api_key = str(body.get("api_key") or "").strip() or account.get("api_key", "")
        return await _test_hub_login(url, username, api_key)
    api_key = str(body.get("api_key") or "").strip() or account.get("api_key", "")
    host = str(body.get("host") or "").strip() or account.get("host", "")
    models = await _fetch_models(account["provider"], api_key, host)
    return {"ok": True, "models": models, "models_count": len(models)}


@router.post("/{account_id}/sync")
async def sync_account(
    account_id: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Sincroniza modelos de la cuenta.

    Body opcional `{"models": [...]}`: si se manda, solo se crean/actualizan
    conexiones para esos modelos (intersección con lo que el proveedor
    realmente reporta, nunca se confía ciegamente en el id que mande el
    cliente). Sin body (o sin la clave `models`), sincroniza todos los
    modelos encontrados.
    """
    owner = await _owner(user)
    account = await _storage.get(account_id, owner)
    if not account:
        raise APIError(404, "not_found", "Cuenta no vinculada", extra={"resource": "account"})

    provider = account["provider"]
    if provider == _HUB_PROVIDER:
        return await _sync_hub_account(account_id, account, owner)

    try:
        body = await json_body(request)
    except Exception:
        body = {}
    selected = body.get("models") if isinstance(body, dict) else None
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
    conn_ids: set = set()

    for model_id in models:
        conn_data: Dict[str, Any] = {
            "name": f"{label} / {model_id}",
            "type": type_id,
            "api_key": api_key,
            "model": model_id,
            "_account_id": account_id,
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

    # Conexiones ya existentes de ESTA cuenta que no salieron en esta pasada
    # (ej. quedaron desmarcadas): siguen contando para el resumen de impacto.
    for c in existing_conns:
        if c.get("_account_id") == account_id:
            conn_ids.add(c["id"])

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
