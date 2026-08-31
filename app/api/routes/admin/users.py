"""Gestión de usuarios desde el panel de admin: listar, editar, crear,
borrar e impersonar."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request, Response

from app.api.routes.admin._router import admin_router
from app.api.routes.auth import require_admin
from app.api.routes.auth._shared import _public_base_url
from app.auth.auth import (
    admin_set_password,
    admin_update_user,
    delete_user,
    get_user_by_username,
    hash_password_async,
)
from app.auth.sessions import open_session
from app.errors import APIError
from app.models.request_bodies import AdminUserCreateBody, AdminUserPatchBody
from app.services.email import send_account_status_email
from app.sql import sql
from app.storage.db import open_db
from app.utils import flog
from app.utils.generators import generate_id
from app.utils.validation import is_valid_email, is_valid_username, normalize_username


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
    if "role" in updates:
        flog.audit(
            "admin.user.role_changed",
            resource_type="user",
            resource_id=username,
            details={
                "from": target.get("role") if target else None,
                "to": updates["role"],
            },
            summary=f"Rol de {username} actualizado por un administrador",
            username=admin,
        )
    if "is_active" in updates:
        flog.audit(
            "admin.user.status_changed",
            resource_type="user",
            resource_id=username,
            details={
                "from": bool(target.get("is_active", 1)) if target else None,
                "to": bool(updates["is_active"]),
            },
            summary=f"Estado de {username} actualizado por un administrador",
            username=admin,
        )
    if new_pw:
        flog.audit(
            "admin.user.credential_rotated",
            resource_type="user",
            resource_id=username,
            details={"credential_rotated": True},
            summary=f"Credencial de {username} renovada por un administrador",
            username=admin,
        )
    return {"ok": True}


@admin_router.post("/users")
async def admin_create_user(
    body: AdminUserCreateBody,
    admin: str = Depends(require_admin),
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
            if await conn.fetchone(sql("queries/admin_users:email_exists"), (email,)):
                raise APIError(
                    409,
                    "already_exists",
                    "El email ya está registrado",
                    extra={"resource": "email"},
                )
            if await conn.fetchone(
                sql("queries/admin_users:username_exists"), (username,)
            ):
                raise APIError(
                    409,
                    "already_exists",
                    "El usuario ya existe",
                    extra={"resource": "user"},
                )
            await conn.execute(
                sql("queries/admin_users:insert_user"),
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

    flog.audit(
        "admin.user.created",
        resource_type="user",
        resource_id=username,
        details={"role": role},
        summary=f"Usuario {username} creado por un administrador",
        username=admin,
    )
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
    flog.audit(
        "admin.user.deleted",
        resource_type="user",
        resource_id=username,
        summary=f"Usuario {username} eliminado por un administrador",
        username=admin,
    )
    return {"ok": True}


@admin_router.post("/impersonate/{username}")
async def admin_impersonate(
    username: str,
    request: Request,
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

    # Sesión nueva para el usuario impersonado, en su group personal
    # (group_id=username por defecto). Es una sesión propia y revocable: sale
    # en la lista del impersonado y cerrarla corta la impersonación.
    await open_session(response, target_user["id"], request)

    flog.audit(
        "admin.impersonation.started",
        resource_type="user",
        resource_id=username,
        summary=f"{admin} inició una sesión de impersonación como {username}",
        username=admin,
    )
    return {"ok": True, "username": username}
