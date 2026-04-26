"""Login local de administrador — credencial via variable de entorno GAIA_ADMIN_PASSWORD."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Dict, List

import bcrypt as _bcrypt
from fastapi import APIRouter, HTTPException, Request, Response

from app.auth.auth import create_token
from app.config.session import LOGIN_MAX_FAILS, LOGIN_WINDOW

_ENV_VAR        = "GAIA_ADMIN_PASSWORD"
_ADMIN_USERNAME = "admin"

_admin_hash: str | None = None
_rate_data: Dict[str, List[float]] = defaultdict(list)

router = APIRouter(prefix="/api/auth/local", tags=["auth-local"])


def init_local_admin() -> None:
    """Hashea GAIA_ADMIN_PASSWORD al arrancar. Llamar una sola vez en startup."""
    global _admin_hash
    plain = os.environ.get(_ENV_VAR, "").strip()
    if plain:
        _admin_hash = _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt(rounds=12)).decode()


def local_admin_enabled() -> bool:
    return _admin_hash is not None


def _check_rate(ip: str) -> None:
    now = time.monotonic()
    events = [t for t in _rate_data[ip] if now - t < LOGIN_WINDOW]
    _rate_data[ip] = events
    if len(events) >= LOGIN_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera unos minutos.")


def _record_fail(ip: str) -> None:
    _rate_data[ip].append(time.monotonic())


def _authenticate(password: str) -> bool:
    if not _admin_hash:
        return False
    try:
        return _bcrypt.checkpw(password.encode(), _admin_hash.encode())
    except Exception:
        return False


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


@router.post("")
async def local_login(request: Request, response: Response) -> dict:
    """Login de emergencia para el administrador. Requiere GAIA_ADMIN_PASSWORD configurado."""
    if not local_admin_enabled():
        raise HTTPException(
            status_code=503,
            detail="Login local no disponible. Define la variable de entorno GAIA_ADMIN_PASSWORD.",
        )
    ip = _client_ip(request)
    _check_rate(ip)
    body = await request.json()
    password = str(body.get("password") or "")
    if not _authenticate(password):
        _record_fail(ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = create_token(_ADMIN_USERNAME)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", max_age=43200)
    return {"ok": True, "username": _ADMIN_USERNAME}
