"""Gestión de usuarios desde el panel de admin: listar, editar, crear,
borrar e impersonar."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request, Response

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.api.routes.auth.login import _public_base_url
from app.auth.auth import (
    admin_set_password,
    admin_update_user,
    create_token,
    delete_user,
    get_user_by_username,
    hash_password_async,
    list_users,
)
from app.config.session import JWT_MAX_AGE_SECONDS, SECURE_COOKIES
from app.errors import APIError
from app.models.request_bodies import AdminUserCreateBody, AdminUserPatchBody
from app.services.email import send_account_status_email
from app.storage.db import open_db
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.validation import is_valid_email, is_valid_username, normalize_username


@admin_router.get("/users")
async def admin_list_users(
    q: str | None = None,
    role: str | None = None,
    active: str | None = None,
    verified: str | None = None,
    _: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    users = await list_users()
    async with open_db() as conn:
        token_rows = await conn.fetchall(
            "SELECT owner_id, COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM connections GROUP BY owner_id"
        )
    token_map = {r[0]: {"tokens_in": r[1], "tokens_out": r[2]} for r in token_rows}
    for u in users:
        tokens = token_map.get(u.get("id"), {"tokens_in": 0, "tokens_out": 0})
        u["tokens_in"] = tokens["tokens_in"]
        u["tokens_out"] = tokens["tokens_out"]
    if q:
        q_low = q.lower()
        users = [
            u
            for u in users
            if q_low in (u.get("username") or "").lower()
            or q_low in (u.get("email") or "").lower()
        ]
    if role:
        users = [u for u in users if u.get("role") == role]
    if active is not None:
        want = active.lower() in ("1", "true", "yes")
        users = [u for u in users if bool(u.get("is_active", 1)) == want]
    if verified is not None:
        want = verified.lower() in ("1", "true", "yes")
        users = [u for u in users if bool(u.get("is_verified", 1)) == want]
    return users


@admin_router.patch("/users/{username}")
async def admin_patch_user(
    username: str,
    request: Request,
    body: AdminUserPatchBody,
    admin: str = Depends(require_admin),
) -> dict[str, Any]:
    target = await get_user_by_username(username)
    if target and target["id"] == admin:
        raise APIError(
            400, "cannot_modify_own_account", "No puedes modificar tu propia cuenta"
        )
    body = body.payload()
    updates: dict[str, Any] = {}
    if "is_active" in body:
        updates["is_active"] = 1 if body["is_active"] else 0
    if "role" in body:
        if body["role"] not in ("admin", "gestor", "standard"):
            raise APIError(
                400, "invalid_field", "Rol inválido", extra={"field": "role"}
            )
        updates["role"] = body["role"]
    new_pw = str(body.get("password") or "").strip()
    if new_pw and len(new_pw) < 8:  # N4: mínimo coherente con el registro
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if not updates and not new_pw:
        raise APIError(400, "no_changes", "Sin cambios")
    if updates and not await admin_update_user(username, **updates):
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    if new_pw and not await admin_set_password(username, new_pw):
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    if "is_active" in updates:
        user = await get_user_by_username(username)
        email = user.get("email") if user else None
        if email:
            base_url = _public_base_url(request)
            # A propósito SIN get_locale(): este correo lo dispara un admin, y
            # el idioma que toca es el del destinatario, no el de quien pulsa el
            # botón. La fila de `users` no guarda hoy un idioma de interfaz, así
            # que se queda en el default hasta que exista ese campo.
            send_account_status_email(email, bool(updates["is_active"]), base_url)
    return {"ok": True}


@admin_router.post("/users")
async def admin_create_user(
    body: AdminUserCreateBody,
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """Crea un usuario directamente desde el panel de admin.

    El usuario queda verificado y activo. No se envía email de verificación.
    """
    from datetime import datetime
    from datetime import timezone as _tz

    body = body.payload()
    username = normalize_username(str(body.get("username") or ""))
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "").strip()
    role = str(body.get("role") or "standard").strip()
    display_name = str(body.get("display_name") or "").strip()

    if not is_valid_username(username):
        raise APIError(
            400,
            "invalid_field",
            "El usuario debe tener entre 5 y 32 caracteres: a-z, 0-9, punto, guion o guion bajo",
            extra={"field": "username"},
        )
    if not email:
        raise APIError(400, "email_required", "El email es obligatorio")
    if not is_valid_email(email):
        raise APIError(
            400, "invalid_field", "Email no válido", extra={"field": "email"}
        )
    if not password:
        raise APIError(400, "password_required", "La contraseña es obligatoria")
    if len(password) < 8:  # N4: mínimo coherente con el registro
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if role not in ("standard", "admin"):
        raise APIError(
            422,
            "invalid_field",
            "role debe ser 'standard' o 'admin'",
            extra={"field": "role"},
        )

    now = datetime.now(_tz.utc).isoformat()
    password_hash = await hash_password_async(password)
    try:
        async with open_db() as conn, conn.transaction():
            if await conn.fetchone("SELECT 1 FROM users WHERE email = ?", (email,)):
                raise APIError(
                    409,
                    "already_exists",
                    "El email ya está registrado",
                    extra={"resource": "email"},
                )
            if await conn.fetchone(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ):
                raise APIError(
                    409,
                    "already_exists",
                    "El usuario ya existe",
                    extra={"resource": "user"},
                )
            await conn.execute(
                "INSERT INTO users "
                "(id, username, email, password_hash, display_name, role, "
                "is_active, is_verified, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generate_id(32),
                    username,
                    email,
                    password_hash,
                    display_name or None,
                    role,
                    1,
                    1,  # verificado — el admin crea la cuenta directamente
                    now,
                ),
            )
    except APIError:
        raise
    except Exception as exc:
        raise APIError(500, "internal_error", "Error interno del servidor.") from exc

    flog.ok(f"Admin creó usuario: {email} (rol={role})")
    return {"ok": True, "username": username, "email": email, "role": role}


@admin_router.delete("/users/{username}")
async def admin_delete_user(
    username: str, admin: str = Depends(require_admin)
) -> dict[str, Any]:
    target = await get_user_by_username(username)
    if target and target["id"] == admin:
        raise APIError(
            400, "cannot_delete_own_account", "No puedes eliminar tu propia cuenta"
        )
    if not await delete_user(username):
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    return {"ok": True}


@admin_router.post("/impersonate/{username}")
async def admin_impersonate(
    username: str,
    response: Response,
    admin: str = Depends(require_admin),
) -> dict[str, Any]:
    target_user = await get_user_by_username(username)
    if not target_user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    if target_user["id"] == admin:
        raise APIError(400, "already_own_user", "Ya eres este usuario")

    # Verificar que la cuenta del usuario objetivo esté activa
    if not target_user.get("is_active", 1):
        raise APIError(
            400,
            "cannot_impersonate_disabled",
            "No se puede impersonar una cuenta desactivada",
        )

    # N3: registrar la impersonación para auditoría de seguridad
    flog.warning(f"[admin] IMPERSONACIÓN: admin={admin!r} → usuario={username!r}")

    # Crear token para el group personal del usuario impersonado
    # (group_id=username por defecto)
    token = create_token(target_user["id"])

    # Establecer la cookie del nuevo token
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=JWT_MAX_AGE_SECONDS,
    )

    flog.ok(f"[admin] Token de impersonación creado exitosamente para {username!r}")
    return {"ok": True, "username": username}
