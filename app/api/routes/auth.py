"""Rutas de autenticación."""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.auth.auth import (
    authenticate,
    create_token,
    decode_token,
    delete_user,
    ensure_admin_password_hashed,
    get_user_role,
    list_users,
    register_user,
)
from app.config.data import SETTINGS_FILE

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Rate limiter en memoria ───────────────────────────────────────────────────
_rate_data: Dict[str, list] = defaultdict(list)
_LOGIN_WINDOW = 300       # 5 minutos
_LOGIN_MAX_FAILS = 5      # fallos antes de bloquear
_REGISTER_WINDOW = 3600   # 1 hora
_REGISTER_MAX = 5         # registros por hora por IP


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


def _check_login_rate(ip: str) -> None:
    now = time.monotonic()
    events = [t for t in _rate_data[f"login:{ip}"] if now - t < _LOGIN_WINDOW]
    _rate_data[f"login:{ip}"] = events
    if len(events) >= _LOGIN_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera unos minutos.")


def _record_login_fail(ip: str) -> None:
    _rate_data[f"login:{ip}"].append(time.monotonic())


def _check_register_rate(ip: str) -> None:
    now = time.monotonic()
    events = [t for t in _rate_data[f"reg:{ip}"] if now - t < _REGISTER_WINDOW]
    _rate_data[f"reg:{ip}"] = events
    if len(events) >= _REGISTER_MAX:
        raise HTTPException(status_code=429, detail="Demasiados registros desde esta dirección. Espera un rato.")


def _record_register(ip: str) -> None:
    _rate_data[f"reg:{ip}"].append(time.monotonic())


def require_auth(ga_token: Optional[str] = Cookie(default=None)) -> str:
    """Dependencia reutilizable: valida el token de sesión y devuelve el username."""
    if not ga_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    username = decode_token(ga_token)
    if not username:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    return username


def require_admin(username: str = Depends(require_auth)) -> str:
    if get_user_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    return username


@router.post("/login")
async def login(request: Request, response: Response) -> Dict[str, Any]:
    ip = _client_ip(request)
    _check_login_rate(ip)
    body = await request.json()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not authenticate(username, password):
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    token = create_token(username)
    response.set_cookie(
        key="ga_token", value=token, httponly=True,
        samesite="lax", max_age=43200,
    )
    return {"ok": True, "username": username}


@router.post("/register")
async def register(request: Request, response: Response) -> Dict[str, Any]:
    _check_register_rate(_client_ip(request))
    body = await request.json()
    username = str(body.get("username") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username y password son obligatorios")
    if not re.match(r"^[a-zA-Z0-9_\-]{3,32}$", username):
        raise HTTPException(status_code=400, detail="Nombre de usuario inválido (3-32 chars, letras/números/_/-)")
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="La contraseña es demasiado corta")
    try:
        register_user(username, password, email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    _record_register(_client_ip(request))
    token = create_token(username)
    response.set_cookie(
        key="ga_token", value=token, httponly=True,
        samesite="lax", max_age=43200,
    )
    return {"ok": True, "username": username}


@router.post("/logout")
async def logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie("ga_token")
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_auth)) -> Dict[str, Any]:
    return {"username": username, "role": get_user_role(username)}


@router.post("/change-password")
async def change_password(
    request: Request, username: str = Depends(require_auth)
) -> Dict[str, Any]:
    body = await request.json()
    current = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "").strip()
    if not current or not new_pw:
        raise HTTPException(status_code=400, detail="Completa todos los campos")
    if not authenticate(username, current):
        raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")
    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="La nueva contraseña es muy corta")
    settings: Dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    settings["admin_password_plain"] = new_pw
    settings.pop("admin_password_hash", None)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


# ── Admin ─────────────────────────────────────────────────────────────────────
# Estas rutas se montan en /api/admin (ver app.py)

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.get("/users")
async def admin_list_users(_: str = Depends(require_admin)) -> List[Dict[str, Any]]:
    return list_users()


@admin_router.delete("/users/{username}")
async def admin_delete_user(
    username: str, admin: str = Depends(require_admin)
) -> Dict[str, Any]:
    if username == admin:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propia cuenta")
    if not delete_user(username):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"ok": True}
