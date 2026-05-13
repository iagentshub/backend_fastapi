"""Google OAuth 2.0 — Authorization Code Flow."""
from __future__ import annotations

import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import RedirectResponse

from app.auth.auth import create_token, get_or_create_oauth_user
from app.config.session import SECURE_COOKIES

_CLIENT_ID     = os.getenv("GAIA_GOOGLE_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("GAIA_GOOGLE_CLIENT_SECRET", "")
_REDIRECT_URI  = os.getenv("GAIA_GOOGLE_REDIRECT_URI", "")
_FRONTEND_URL  = os.getenv("GAIA_FRONTEND_URL", "http://localhost:8080")

# Whitelist opcional. Si ambas están vacías, cualquier cuenta Google puede entrar.
_ALLOWED_EMAILS  = {e.strip().lower() for e in os.getenv("GAIA_ALLOWED_EMAILS", "").split(",") if e.strip()}
_ALLOWED_DOMAINS = {d.strip().lower() for d in os.getenv("GAIA_ALLOWED_DOMAINS", "").split(",") if d.strip()}

_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_INFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"

router = APIRouter(prefix="/api/auth/google", tags=["auth-google"])


def google_enabled() -> bool:
    return bool(_CLIENT_ID and _CLIENT_SECRET and _REDIRECT_URI)


def _is_email_allowed(email: str) -> bool:
    if not _ALLOWED_EMAILS and not _ALLOWED_DOMAINS:
        return True
    if email in _ALLOWED_EMAILS:
        return True
    domain = email.split("@")[-1]
    return domain in _ALLOWED_DOMAINS


@router.get("")
async def google_login() -> RedirectResponse:
    """Inicia el flujo OAuth redirigiendo al consentimiento de Google."""
    if not google_enabled():
        raise HTTPException(status_code=503, detail="Google OAuth no configurado")
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "state": state,
    }
    redirect = RedirectResponse(url=f"{_AUTH_URL}?{urlencode(params)}")
    redirect.set_cookie("ga_oauth_state", state, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=600)
    return redirect


@router.get("/callback")
async def google_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    ga_oauth_state: str = Cookie(default=""),
) -> RedirectResponse:
    """Recibe el código de Google, valida el usuario y emite el JWT."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Código de autorización ausente")
    if not state or state != ga_oauth_state:
        raise HTTPException(status_code=400, detail="Estado OAuth inválido (posible CSRF)")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(_TOKEN_URL, data={
            "code": code,
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "redirect_uri": _REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Error al intercambiar el código con Google")
        tokens = token_resp.json()

        info_resp = await client.get(
            _INFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        if info_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Error al obtener perfil de Google")
        info = info_resp.json()

    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified"):
        raise HTTPException(status_code=400, detail="Email de Google no verificado")
    if not _is_email_allowed(email):
        raise HTTPException(status_code=403, detail="Email no autorizado para acceder a esta aplicación")

    username = get_or_create_oauth_user(
        provider="google",
        sub=info["sub"],
        email=email,
        name=info.get("name", email),
    )
    token = create_token(username)
    redirect = RedirectResponse(url=f"{_FRONTEND_URL}/agents/")
    redirect.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    redirect.delete_cookie("ga_oauth_state")
    return redirect
