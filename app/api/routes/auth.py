"""Rutas de autenticación — dependencias compartidas y endpoints de sesión."""
from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.auth.auth import (
    decode_token,
    delete_user,
    get_user_role,
    hash_password,
    list_users,
    register_user,
    verify_password,
    _load_users,
    _save_users,
)
from app.config.session import REGISTER_MAX, REGISTER_WINDOW

router = APIRouter(prefix="/api/auth", tags=["auth"])

_rate_data: Dict[str, list] = defaultdict(list)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


def _check_register_rate(ip: str) -> None:
    now = time.monotonic()
    events = [t for t in _rate_data[f"reg:{ip}"] if now - t < REGISTER_WINDOW]
    _rate_data[f"reg:{ip}"] = events
    if len(events) >= REGISTER_MAX:
        raise HTTPException(status_code=429, detail="Demasiados registros desde esta dirección. Espera un rato.")


def _record_register(ip: str) -> None:
    _rate_data[f"reg:{ip}"].append(time.monotonic())


def require_auth(ga_token: Optional[str] = Cookie(default=None)) -> str:
    """Dependencia: valida el token de sesión y devuelve el username."""
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
    from app.auth.auth import create_token
    token = create_token(username)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", max_age=43200)
    return {"ok": True, "username": username}


@router.post("/guest")
async def guest_login(response: Response) -> Dict[str, Any]:
    from app.auth.auth import create_token
    from app.storage.guest import new_guest_id
    guest_id = new_guest_id()
    token = create_token(guest_id)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", max_age=43200)
    return {"ok": True, "username": guest_id}


@router.post("/logout")
async def logout(response: Response) -> Dict[str, Any]:
    response.delete_cookie("ga_token")
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_auth)) -> Dict[str, Any]:
    from app.storage.guest import is_guest
    role = get_user_role(username)
    if is_guest(username):
        auth_method = "guest"
    else:
        users = _load_users()
        user = next((u for u in users if u.get("username") == username), {})
        auth_method = user.get("provider") or "internal"
    return {"username": username, "role": role, "auth_method": auth_method}


@router.post("/change-password")
async def change_password(
    request: Request, username: str = Depends(require_auth)
) -> Dict[str, Any]:
    body = await request.json()
    current = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "").strip()
    if not current or not new_pw:
        raise HTTPException(status_code=400, detail="Completa todos los campos")
    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="La nueva contraseña es muy corta")

    users = _load_users()
    for user in users:
        if user.get("username") == username:
            if not verify_password(current, user.get("password_hash", "")):
                raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")
            user["password_hash"] = hash_password(new_pw)
            _save_users(users)
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Usuario no encontrado")


# ── Admin ─────────────────────────────────────────────────────────────────────

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
