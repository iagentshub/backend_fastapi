"""Auth routes — shared dependencies and session endpoints."""
from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response

from app.auth.auth import (
    create_token,
    decode_token,
    delete_user,
    get_user_by_email,
    get_user_by_username,
    get_user_role,
    hash_password,
    list_users,
    register_user_email,
    verify_password,
)
from app.config.session import REGISTER_MAX, REGISTER_WINDOW, REGISTRATION_MODE, SECURE_COOKIES

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
    """Dependency: validates session token and returns username."""
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
    if REGISTRATION_MODE == "closed":
        raise HTTPException(status_code=403, detail="El registro está desactivado.")
    if REGISTRATION_MODE == "invite":
        raise HTTPException(status_code=403, detail="El registro requiere invitación de un administrador.")
    _check_register_rate(_client_ip(request))
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    birth_date = str(body.get("birth_date") or "").strip() or None
    gender = str(body.get("gender") or "").strip() or None
    country = str(body.get("country") or "").strip() or None
    phone = str(body.get("phone") or "").strip() or None

    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Email inválido")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres")

    try:
        username = register_user_email(
            email,
            password,
            birth_date=birth_date,
            gender=gender,
            country=country,
            phone=phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    _record_register(_client_ip(request))
    token = create_token(username)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    return {"ok": True, "email": email}


@router.post("/login")
async def login(request: Request, response: Response) -> Dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email y contraseña requeridos")
    user = get_user_by_email(email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user.get("is_active", 1):
        raise HTTPException(status_code=403, detail="Cuenta desactivada")
    token = create_token(user["username"])
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
    return {"ok": True, "username": user["username"]}


@router.post("/guest")
async def guest_login(response: Response) -> Dict[str, Any]:
    from app.storage.guest import new_guest_id
    guest_id = new_guest_id()
    token = create_token(guest_id)
    response.set_cookie("ga_token", token, httponly=True, samesite="lax", secure=SECURE_COOKIES, max_age=43200)
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
        user = get_user_by_username(username) or {}
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

    user = get_user_by_username(username)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not verify_password(current, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")

    from app.storage.db import PH, close_db, open_db
    from app.config.data import DB_FILE

    conn = open_db(DB_FILE)
    try:
        conn.cursor().execute(
            f"UPDATE users SET password_hash = {PH} WHERE username = {PH}",
            (hash_password(new_pw), username),
        )
        conn.commit()
    finally:
        close_db(conn)
    return {"ok": True}


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
