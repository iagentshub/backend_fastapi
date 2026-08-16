"""GitHub OAuth Device Flow — helpers compartidos entre el login de la app
(`app/api/routes/auth.py`) y la vinculación de una cuenta de proveedor
(`app/api/routes/accounts.py`). Requiere una GitHub OAuth App propia (Device
Flow habilitado) configurada vía `GITHUB_OAUTH_CLIENT_ID`.

Este módulo solo habla con GitHub: pide el código, sondea la autorización y
devuelve la identidad. Convertir esa identidad en una fila de `users` es de
``app.auth.github_user_link``, y son dos pasos distintos a propósito — el de
`accounts.py` vincula una cuenta de proveedor sin crear ningún usuario.
"""
from __future__ import annotations

from typing import Any, Dict

import httpx

from app.errors import APIError
from app.utils import flog


def _require_client_id() -> str:
    # Import perezoso: así `patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", ...)`
    # en los tests surte efecto — un `from ... import` a nivel de módulo
    # capturaría el valor una sola vez al cargar este módulo, antes de que
    # cualquier test pueda parchearlo.
    from app.config.providers import GITHUB_OAUTH_CLIENT_ID

    if not GITHUB_OAUTH_CLIENT_ID:
        raise APIError(
            503,
            "oauth_not_configured",
            "El login con GitHub no está configurado en este servidor",
        )
    return GITHUB_OAUTH_CLIENT_ID


async def request_device_code(scope: str = "read:user") -> Dict[str, Any]:
    client_id = _require_client_id()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://github.com/login/device/code",
            data={"client_id": client_id, "scope": scope},
            headers={"Accept": "application/json"},
        )
    r.raise_for_status()
    data = r.json()
    return {
        "device_code": data.get("device_code", ""),
        "user_code": data.get("user_code", ""),
        "verification_uri": data.get("verification_uri", ""),
        "expires_in": data.get("expires_in", 900),
        "interval": data.get("interval", 5),
    }


async def poll_device_token(device_code: str) -> Dict[str, Any]:
    """Sondea si el Device Flow ya fue autorizado. No lanza en pending/error —
    el resultado siempre trae `ok` para que el caller decida qué hacer."""
    client_id = _require_client_id()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
    r.raise_for_status()
    data = r.json()
    error = data.get("error")
    if error in ("authorization_pending", "slow_down"):
        return {"ok": False, "pending": True, "slow_down": error == "slow_down"}
    if error:
        return {"ok": False, "pending": False, "error": error}
    return {"ok": True, "pending": False, "access_token": data.get("access_token", "")}


async def fetch_github_identity(access_token: str) -> Dict[str, Any]:
    """Identidad de GitHub asociada al access_token: id estable, login,
    email (pide `/user/emails` si el perfil no tiene uno público — requiere
    haber pedido el scope `user:email`) y nombre para mostrar."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://api.github.com/user", headers=headers)
    r.raise_for_status()
    data = r.json()

    email = data.get("email")
    if not email:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r2 = await client.get("https://api.github.com/user/emails", headers=headers)
            r2.raise_for_status()
            emails = r2.json()
            primary = next((e for e in emails if e.get("primary")), None)
            email = (primary or (emails[0] if emails else {})).get("email")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            # Sin `user:email` en los scopes GitHub devuelve 403 aquí, y sin
            # correo el alta cae al `@users.noreply.github.com` de
            # github_user_link. Es aceptable, pero "todos mis usuarios de GitHub
            # tienen correo noreply" necesita este rastro para diagnosticarse.
            flog.debug(f"[github] Sin correo del perfil: {exc}")
            email = None

    return {
        "id": str(data.get("id", "")),
        "login": data.get("login", "") or "",
        "email": (email or "").strip().lower(),
        "name": data.get("name") or data.get("login", ""),
    }
