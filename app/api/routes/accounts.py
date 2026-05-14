"""Rutas para cuentas de proveedor vinculadas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.routes.auth import require_auth
from app.auth.auth import get_user_role
from app.config.data import AGENTS_DIR, DB_FILE, SKILLS_DIR
from app.storage.accounts import AccountStorage
from app.storage.storage import AgentStorage, ConnectionStorage, SkillStorage

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_storage = AccountStorage(DB_FILE)
_conn_storage = ConnectionStorage(DB_FILE)


def _owner(user: str) -> str:
    return "admin" if get_user_role(user) == "admin" else user
_agent_storage = AgentStorage(AGENTS_DIR)
_skill_storage = SkillStorage(SKILLS_DIR)

_PROVIDERS = ["anthropic", "openai", "github", "ollama", "nvidia", "google"]
_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "github": "GitHub Copilot",
    "ollama": "Ollama",
    "nvidia": "NVIDIA NIM",
    "google": "Google Gemini",
}
_PROVIDER_TYPE_IDS: dict[str, str] = {
    "anthropic": "claude",
    "google":    "gemini",
    "openai":    "openai",
    "ollama":    "ollama",
    "nvidia":    "nvidia",
    "github":    "github",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            return [m.get("id") or m.get("name", "") for m in items if m.get("id") or m.get("name")]

        if provider == "ollama":
            base = (host or "http://localhost:11434").rstrip("/")
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/api/tags")
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
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc)) from exc
    except httpx.ConnectError:
        label = _PROVIDER_LABELS.get(provider, provider)
        raise HTTPException(status_code=502, detail=f"No se puede conectar con {label}. Comprueba que el servicio está activo y la URL es correcta.") from None
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return []


@router.get("")
async def list_accounts(user: str = Depends(require_auth)) -> List[Dict[str, Any]]:
    linked = {a["provider"]: a for a in _storage.list(_owner(user))}
    result = []
    for p in _PROVIDERS:
        if p in linked:
            result.append(linked[p])
        else:
            result.append({"provider": p, "linked": False})
    return result


@router.put("/{provider}")
async def link_account(
    provider: str, request: Request, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Proveedor no soportado: {provider}")
    body = await request.json()
    api_key = str(body.get("api_key") or "").strip()
    host = str(body.get("host") or "").strip()
    if not api_key and provider != "ollama":
        raise HTTPException(status_code=422, detail="api_key requerida")
    data: Dict[str, Any] = {"api_key": api_key}
    if host:
        data["host"] = host
    saved = _storage.save(provider, data, _owner(user))
    if saved.get("api_key"):
        saved["api_key_masked"] = saved["api_key"][:6] + "..." + saved["api_key"][-4:]
        del saved["api_key"]
    return saved


@router.delete("/{provider}")
async def unlink_account(
    provider: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if not _storage.delete(provider, _owner(user)):
        raise HTTPException(status_code=404, detail="Cuenta no vinculada")
    return {"ok": True}


@router.post("/{provider}/test")
async def test_account(
    provider: str, request: Request, _: str = Depends(require_auth)
) -> Dict[str, Any]:
    """Prueba las credenciales sin guardarlas."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Proveedor no soportado: {provider}")
    body = await request.json()
    api_key = str(body.get("api_key") or "").strip()
    host = str(body.get("host") or "").strip()
    models = await _fetch_models(provider, api_key, host)
    return {"ok": True, "models": models, "models_count": len(models)}


@router.post("/{provider}/sync")
async def sync_account(
    provider: str, user: str = Depends(require_auth)
) -> Dict[str, Any]:
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Proveedor no soportado: {provider}")
    account = _storage.get(provider, _owner(user))
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no vinculada")

    api_key = account.get("api_key", "")
    host = account.get("host", "")
    label = _PROVIDER_LABELS.get(provider, provider)

    # 1. Fetch models from provider
    models = await _fetch_models(provider, api_key, host)

    # 2. Create / update one connection per model
    type_id = _PROVIDER_TYPE_IDS.get(provider, provider)
    owner = _owner(user)
    existing_conns = _conn_storage.list(owner)
    existing_by_model: Dict[str, Any] = {
        c["model"]: c for c in existing_conns
        if c.get("type") == type_id and c.get("model")
    }
    connections_created = 0
    connections_updated = 0
    provider_conn_ids: set = set()

    for model_id in models:
        conn_data: Dict[str, Any] = {
            "name": f"{label} / {model_id}",
            "type": type_id,
            "api_key": api_key,
            "model": model_id,
        }
        if host:
            conn_data["host"] = host
        if model_id in existing_by_model:
            conn_data["id"] = existing_by_model[model_id]["id"]
            connections_updated += 1
        else:
            connections_created += 1
        saved_conn = _conn_storage.save(conn_data, owner_id=owner)
        provider_conn_ids.add(saved_conn["id"])

    # Include pre-existing connections of this provider that weren't in the model list
    for c in existing_conns:
        if c.get("type") == type_id:
            provider_conn_ids.add(c["id"])

    # 3. Find private agents linked to this provider's connections
    private_agents = _agent_storage.list(scope="private")
    agents_linked = []
    for summary in private_agents:
        if summary.get("connection_id") in provider_conn_ids:
            full = _agent_storage.get(summary["id"], scope="private") or {}
            routines = [r for r in (full.get("routines") or []) if isinstance(r, dict)]
            agents_linked.append({
                "id": summary["id"],
                "name": summary["name"],
                "routines_count": len(routines),
            })

    # 4. Count private skills
    private_skills_count = len(_skill_storage.list(scope="private"))

    # 5. Save updated account with summary
    summary_data = {
        "connections_created": connections_created,
        "connections_updated": connections_updated,
        "agents_count": len(agents_linked),
        "agents": agents_linked,
        "routines_count": sum(a["routines_count"] for a in agents_linked),
        "skills_private_count": private_skills_count,
    }
    account["models"] = models
    account["last_synced_at"] = _now()
    account["sync_summary"] = summary_data
    saved = _storage.save(provider, account, _owner(user))

    if saved.get("api_key"):
        saved["api_key_masked"] = saved["api_key"][:6] + "..." + saved["api_key"][-4:]
        del saved["api_key"]
    return saved
