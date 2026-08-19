"""Comprobación de versión (GHCR/GitHub) y control de Watchtower."""

from __future__ import annotations

import os
import re

import httpx
from fastapi import BackgroundTasks, Depends
from pydantic import BaseModel

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.errors import APIError
from app.utils import flog


def _version_re(variant: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(variant)}-(\d{{14}})$")


async def _latest_ghcr_version(image: str, variant: str) -> str | None:
    """Versión (tag `<variant>-YYYYMMDDHHMMSS`) más reciente publicada en
    GHCR para `image`, restringida a la familia indicada.

    El prefijo evita mezclar tags históricos o ajenos con las versiones React
    soportadas actualmente.
    """
    prefix = "ghcr.io/"
    if not image.startswith(prefix):
        raise APIError(
            500,
            "invalid_field",
            "IMAGE_REPOSITORY debe apuntar a ghcr.io",
            extra={"field": "IMAGE_REPOSITORY"},
        )
    repository = image.removeprefix(prefix).strip("/")
    pattern = _version_re(variant)
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.get(
            "https://ghcr.io/token",
            params={
                "service": "ghcr.io",
                "scope": f"repository:{repository}:pull",
            },
        )
        token_response.raise_for_status()
        token = str(token_response.json().get("token") or "")
        if not token:
            raise httpx.HTTPError("GHCR no devolvió un token de lectura")
        tags: list[str] = []
        tags_url: str | None = f"https://ghcr.io/v2/{repository}/tags/list?n=1000"
        for _ in range(10):
            if tags_url is None:
                break
            tags_response = await client.get(
                tags_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            tags_response.raise_for_status()
            tags.extend(tags_response.json().get("tags") or [])
            link = tags_response.headers.get("link", "")
            next_link = re.search(r'<([^>]+)>;\s*rel="next"', link)
            tags_url = next_link.group(1) if next_link else None
            if tags_url and tags_url.startswith("/"):
                tags_url = f"https://ghcr.io{tags_url}"
    versions = [
        match.group(1) for tag in tags if (match := pattern.match(str(tag))) is not None
    ]
    return max(versions) if versions else None


async def _latest_github_commit_sha(repo: str, branch: str = "main") -> str | None:
    """SHA completo del último commit en `branch` de `repo` (API pública de
    GitHub, sin autenticación). None si la consulta falla por cualquier motivo
    — a diferencia de GHCR, esta comprobación es solo informativa
    (distingue si lo desactualizado es el backend o el frontend) y nunca debe
    tumbar el resto de /check-update.
    """
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url, headers={"Accept": "application/vnd.github+json"}
            )
            resp.raise_for_status()
            return resp.json().get("sha")
    except httpx.HTTPError:
        return None


@admin_router.get("/check-update")
async def admin_check_update(_: str = Depends(require_admin)) -> dict:
    """Compara la versión de la imagen en ejecución (GAIA_VERSION) contra la
    más reciente publicada en GHCR. Solo informa — no aplica el update
    (eso lo hace Watchtower, o `docker compose pull && up -d` manual).

    GAIA_VERSION solo dice "cuándo" se construyó la imagen, no "qué" código de
    backend_fastapi, frontend_react ni app_flutter lleva dentro — así que
    además se compara el commit horneado de cada uno de los tres repos
    (BACKEND_COMMIT/FRONTEND_COMMIT/APP_COMMIT) contra el HEAD de main en
    GitHub, para saber cuál está desactualizado.
    """
    current_version = os.environ.get("GAIA_VERSION", "dev")
    if current_version == "dev":
        return {
            "checked": False,
            "reason": "no_version",
            "current_version": current_version,
        }

    image_repository = os.environ.get("IMAGE_REPOSITORY", "ghcr.io/iagentshub/app")
    image_variant = os.environ.get("IMAGE_VARIANT", "react")
    try:
        latest_version = await _latest_ghcr_version(image_repository, image_variant)
    except httpx.HTTPError as exc:
        raise APIError(502, "check_update_failed", "No se pudo consultar GHCR") from exc

    backend_commit = os.environ.get("BACKEND_COMMIT", "dev")
    frontend_commit = os.environ.get("FRONTEND_COMMIT", "dev")
    app_commit = os.environ.get("APP_COMMIT", "dev")
    frontend_repo = "iagentshub/frontend_react"
    app_repo = "iagentshub/app_flutter"
    backend_latest, frontend_latest, app_latest = None, None, None
    if backend_commit != "dev":
        backend_latest = await _latest_github_commit_sha("iagentshub/backend_fastapi")
    if frontend_commit != "dev":
        frontend_latest = await _latest_github_commit_sha(frontend_repo)
    if app_commit != "dev":
        app_latest = await _latest_github_commit_sha(app_repo)

    commits = {
        "backend_commit": backend_commit,
        "backend_commit_latest": backend_latest[:7] if backend_latest else None,
        "backend_up_to_date": (
            backend_latest.startswith(backend_commit) if backend_latest else None
        ),
        "frontend_commit": frontend_commit,
        "frontend_commit_latest": frontend_latest[:7] if frontend_latest else None,
        "frontend_up_to_date": (
            frontend_latest.startswith(frontend_commit) if frontend_latest else None
        ),
        "app_commit": app_commit,
        "app_commit_latest": app_latest[:7] if app_latest else None,
        "app_up_to_date": (app_latest.startswith(app_commit) if app_latest else None),
    }

    if latest_version is None:
        return {
            "checked": False,
            "reason": "no_remote_versions",
            "current_version": current_version,
            **commits,
        }

    return {
        "checked": True,
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": latest_version > current_version,
        **commits,
    }


class AutoUpdateUpdate(BaseModel):
    enabled: bool


@admin_router.put("/auto-update")
async def admin_set_auto_update(
    body: AutoUpdateUpdate, _: str = Depends(require_admin)
) -> dict:
    """Arranca/para el contenedor "watchtower" a través de docker-proxy (ver
    docker-compose.hub.yml) y solo si la operación se aplica de verdad
    persiste la preferencia — así el valor guardado nunca miente sobre el
    estado real del contenedor."""
    proxy_url = os.environ.get("DOCKER_PROXY_URL", "")
    if not proxy_url:
        raise APIError(
            409,
            "auto_update_proxy_unavailable",
            "Esta instalación no tiene el proxy de Docker configurado. "
            "Actualiza docker-compose.hub.yml (docker compose pull && "
            "docker compose up -d) para poder controlar la "
            "auto-actualización desde aquí.",
        )
    container = os.environ.get("WATCHTOWER_CONTAINER_NAME", "watchtower")
    action = "start" if body.enabled else "stop"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{proxy_url}/containers/{container}/{action}")
    except httpx.HTTPError as exc:
        raise APIError(
            502,
            "auto_update_apply_failed",
            "No se pudo contactar con el proxy de Docker",
        ) from exc

    # 204 = aplicado; 304 = ya estaba en ese estado (idempotente, también ok).
    if resp.status_code not in (204, 304):
        raise APIError(
            502,
            "auto_update_apply_failed",
            f"Docker rechazó la operación (HTTP {resp.status_code})",
        )

    from app.services.platform_settings import (
        _read_platform_cfg,
        _write_platform_cfg,
    )

    cfg = _read_platform_cfg()
    cfg["auto_update_enabled"] = body.enabled
    _write_platform_cfg(cfg)
    return {"auto_update_enabled": body.enabled}


async def _trigger_watchtower_update(token: str) -> None:
    """Si Watchtower encuentra una imagen nueva, sustituye ESTE MISMO
    contenedor a mitad de la petición — por eso se dispara en segundo plano
    en vez de esperar su respuesta desde el endpoint: perder la conexión aquí
    es la señal esperada de que se está aplicando, no un fallo real."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            await client.post(
                "http://watchtower:8080/v1/update",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        flog.debug(f"[admin] Watchtower cerró o rechazó la conexión: {exc}")


@admin_router.post("/update-now")
async def admin_update_now(
    background_tasks: BackgroundTasks, _: str = Depends(require_admin)
) -> dict:
    """Fuerza ahora mismo el ciclo de comprobación+actualización que
    Watchtower ejecuta cada WATCHTOWER_INTERVAL segundos, vía su propia API
    HTTP (WATCHTOWER_HTTP_API_UPDATE en docker-compose.hub.yml) — sin ampliar
    los permisos de docker-proxy, deliberadamente restringidos a start/stop
    (ver ese fichero para el razonamiento).
    """
    token = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "")
    if not token:
        raise APIError(
            409,
            "update_now_unavailable",
            "Esta instalación no tiene la API HTTP de Watchtower activada. "
            "Actualiza docker-compose.hub.yml y vuelve a desplegar (docker "
            "compose pull && docker compose up -d) para poder disparar "
            "actualizaciones desde aquí.",
        )
    background_tasks.add_task(_trigger_watchtower_update, token)
    return {"triggered": True}
