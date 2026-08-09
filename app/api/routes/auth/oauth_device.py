"""Login con GitHub vía OAuth Device Flow — sin sesión previa.

Distinto de `/api/accounts/github/device-code`, que vincula una cuenta
proveedor para un usuario ya logueado.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.oauth_github import get_or_create_github_user
from app.auth.passwords import create_token
from app.config.session import JWT_MAX_AGE_SECONDS, SECURE_COOKIES
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.models.request_bodies import DeviceCodeBody
from app.utils import flog
from app.utils.net import client_ip as _client_ip

router = APIRouter()

# Generoso a propósito: el cliente sondea /github/device-token cada ~5s
# (intervalo que marca GitHub) durante hasta 15 min — un límite ajustado
# como el de /login (pensado para fuerza bruta de contraseñas) cortaría un
# login legítimo a mitad de la espera.
_github_login_limiter = RateLimiter(
    calls=30,
    window=60,
    key_func=_client_ip,
    shared=True,
    name="auth-github-device",
)


@router.post("/github/device-code")
async def github_login_device_code(
    _rl: None = Depends(_github_login_limiter),
) -> dict[str, Any]:
    """Inicia sesión con GitHub (Device Flow) — sin sesión previa, a
    diferencia de `/api/accounts/github/device-code` (que vincula una cuenta
    proveedor para un usuario ya logueado)."""
    from app.auth.github_oauth import request_device_code

    return await request_device_code(scope="read:user user:email")


@router.post("/github/device-token")
async def github_login_device_token(
    request: Request,
    body: DeviceCodeBody,
    response: Response,
    _rl: None = Depends(_github_login_limiter),
) -> dict[str, Any]:
    """Sondea el Device Flow iniciado con `/github/device-code`. Si ya se
    autorizó, resuelve (o crea) el usuario local ligado a esa identidad de
    GitHub y abre sesión — mismo mecanismo de cookie que `/login`."""
    from app.auth.github_oauth import fetch_github_identity, poll_device_token

    body = body.payload()
    device_code = str(body.get("device_code") or "").strip()
    if not device_code:
        raise APIError(
            422,
            "invalid_field",
            "device_code requerido",
            extra={"field": "device_code"},
        )

    result = await poll_device_token(device_code)
    if not result.get("ok"):
        return result

    identity = await fetch_github_identity(result["access_token"])
    if not identity.get("id"):
        raise APIError(
            502, "github_identity_error", "No se pudo obtener la identidad de GitHub"
        )

    user = await get_or_create_github_user(
        identity["id"], identity["login"], identity["email"], identity["name"]
    )
    if not user.get("is_active", 1):
        raise APIError(403, "account_disabled", "Cuenta desactivada")

    token = create_token(user["id"])
    flog.ok(f"[login] OK vía GitHub usuario={user['username']}", ip=_client_ip(request))
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=JWT_MAX_AGE_SECONDS,
    )
    return {"ok": True, "pending": False, "username": user["username"]}
