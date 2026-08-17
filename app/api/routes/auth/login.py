"""Registro, login, sesión, recuperación de contraseña y perfil social."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.routes.auth.dependencies import (
    _groups,
    _login_limiter,
    require_auth,
    require_group_session,
)
from app.auth.auth import (
    consume_reset_token,
    create_password_reset_token,
    get_user_by_id,
    get_user_by_username,
    get_user_role,
    register_user_email,
    set_own_password,
    verify_email_token,
    verify_password_async,
)
from app.auth.cookies import clear_session_cookies, set_session_cookies
from app.auth.passwords import create_token
from app.config.content_languages import CONTENT_LANGUAGE_SET
from app.config.session import (
    EMAIL_VERIFY_ENABLED,
    RATE_FORGOT_CALLS,
    RATE_FORGOT_WINDOW,
    RATE_GUEST_CALLS,
    RATE_GUEST_WINDOW,
    RATE_RESET_CALLS,
    RATE_RESET_WINDOW,
    REGISTER_MAX,
    REGISTER_WINDOW,
    REGISTRATION_MODE,
)
from app.errors import APIError
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.services.email import send_reset_email, send_verification_email
from app.sql import sql
from app.storage.db import open_db
from app.utils import flog
from app.utils.net import client_ip as _client_ip
from app.utils.validation import is_valid_email, is_valid_username, normalize_username

router = APIRouter()


class RegisterBody(BaseModel):
    username: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=128)
    birth_date: str | None = None
    gender: str | None = None
    country: str | None = None
    phone: str | None = None


class LoginBody(BaseModel):
    identifier: str | None = Field(default=None, max_length=254)
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=128)


class EmailBody(BaseModel):
    email: str | None = Field(default=None, max_length=254)


class ResetPasswordBody(BaseModel):
    token: str | None = Field(default=None, max_length=512)
    password: str | None = Field(default=None, max_length=128)


class ChangePasswordBody(BaseModel):
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str | None = Field(default=None, max_length=128)


class ProfileBody(BaseModel):
    bio: str | None = Field(default=None, max_length=500)
    languages: list[str] = Field(default_factory=list, max_length=50)
    is_email_public: bool = False
    github: str | None = Field(default=None, max_length=100)
    cv: str | None = Field(default=None, max_length=20_000)


def _public_base_url(request: Request) -> str:
    """URL base canónica para construir enlaces en emails.

    Usa GAIA_FRONTEND_URL si está configurada (evita Host Header Injection).
    En desarrollo usa un origen local fijo; nunca confía en la cabecera Host.
    """
    del request  # La firma sigue siendo cómoda para los handlers FastAPI.
    configured = os.getenv("GAIA_FRONTEND_URL", "").rstrip("/")
    if configured:
        return configured
    return "http://localhost:8007"


_register_limiter = RateLimiter(
    calls=REGISTER_MAX,
    window=REGISTER_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-register",
)
_forgot_limiter = RateLimiter(
    calls=RATE_FORGOT_CALLS,
    window=RATE_FORGOT_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-forgot",
)
_reset_limiter = RateLimiter(
    calls=RATE_RESET_CALLS,
    window=RATE_RESET_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-reset",
)
_guest_limiter = RateLimiter(
    calls=RATE_GUEST_CALLS,
    window=RATE_GUEST_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-guest",
)


@router.post("/register")
async def register(
    body: RegisterBody, request: Request, response: Response
) -> dict[str, Any]:
    if REGISTRATION_MODE == "closed":
        raise APIError(403, "registration_disabled", "El registro está desactivado.")
    if REGISTRATION_MODE == "invite":
        raise APIError(
            403,
            "registration_invite_only",
            "El registro requiere invitación de un administrador.",
        )
    await _register_limiter(request)
    username = normalize_username(body.username or "")
    email = (body.email or "").strip().lower()
    password = body.password or ""
    birth_date = (body.birth_date or "").strip() or None
    gender = (body.gender or "").strip() or None
    country = (body.country or "").strip() or None
    phone = (body.phone or "").strip() or None

    if not is_valid_username(username):
        raise APIError(
            400,
            "invalid_field",
            "El usuario debe tener entre 5 y 32 caracteres: a-z, 0-9, punto, guion o guion bajo",
            extra={"field": "username"},
        )
    if not email or not is_valid_email(email):
        raise APIError(400, "invalid_field", "Email inválido", extra={"field": "email"})
    # username, email y password se validan; estos cuatro iban a la BD tal cual.
    # El registro puede estar abierto, así que el único tope era el cuerpo
    # máximo de la petición: 2 MB de "país" por cuenta creada.
    for campo, valor in (
        ("birth_date", birth_date),
        ("gender", gender),
        ("country", country),
        ("phone", phone),
    ):
        if valor and len(valor) > 120:
            raise APIError(
                400,
                "invalid_field",
                f"Valor demasiado largo: {campo}",
                extra={"field": campo},
            )
    if len(password) < 8:
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )

    try:
        username, verify_token = await register_user_email(
            username,
            email,
            password,
            birth_date=birth_date,
            gender=gender,
            country=country,
            phone=phone,
        )
    except ValueError as exc:
        resource = "username" if "usuario" in str(exc).lower() else "email"
        raise APIError(
            409, "already_exists", str(exc), extra={"resource": resource}
        ) from exc

    if EMAIL_VERIFY_ENABLED and verify_token:
        base_url = _public_base_url(request)
        # El idioma se resuelve AQUÍ: get_locale() es un ContextVar y el
        # envío se encola en un ThreadPoolExecutor donde ya no existe.
        send_verification_email(email, verify_token, base_url, lang=get_locale())
        return {"ok": True, "email": email, "pending_verification": True}

    user = await get_user_by_username(username)
    if not user:
        raise APIError(500, "user_creation_failed", "No se pudo crear la sesión")
    token = create_token(user["id"])
    set_session_cookies(response, token)
    return {"ok": True, "email": email, "pending_verification": False}


@router.post("/login")
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    _rl: None = Depends(_login_limiter),
) -> dict[str, Any]:
    identifier = (body.identifier or body.email or "").strip().lower()
    password = body.password or ""

    # Extraer IP real del cliente
    _ip = _client_ip(request)

    if not identifier or not password:
        flog.warning("[login] FAIL razón=campos_vacíos", ip=_ip)
        raise APIError(
            400, "missing_credentials", "Usuario o email y contraseña requeridos"
        )

    from app.auth.auth import get_user_by_login

    user = await get_user_by_login(identifier)
    if not user or not user.get("password_hash"):
        flog.warning("[login] FAIL razón=usuario_no_encontrado", ip=_ip)
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not await verify_password_async(password, user["password_hash"]):
        flog.warning("[login] FAIL razón=contraseña_incorrecta", ip=_ip)
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not user.get("is_active", 1):
        flog.warning("[login] FAIL razón=cuenta_desactivada", ip=_ip)
        raise APIError(403, "account_disabled", "Cuenta desactivada")
    if EMAIL_VERIFY_ENABLED and not user.get("is_verified", 1):
        flog.warning("[login] FAIL razón=pendiente_verificación", ip=_ip)
        raise APIError(
            403,
            "email_not_verified",
            "Cuenta pendiente de verificación. Revisa tu correo.",
        )

    token = create_token(user["id"])
    flog.ok(
        f"[login] OK usuario={user['username']}",
        ip=_ip,
        username=user["username"],
    )
    set_session_cookies(response, token)
    return {"ok": True, "username": user["username"]}


@router.get("/verify")
async def verify_email(token: str, response: Response) -> dict[str, Any]:
    username = await verify_email_token(token)
    if not username:
        raise APIError(
            400,
            "invalid_verification_link",
            "Enlace de verificación inválido o expirado",
        )
    user = await get_user_by_username(username)
    if not user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    auth_token = create_token(user["id"])
    set_session_cookies(response, auth_token)
    return {"ok": True, "username": username}


@router.post("/guest")
async def guest_login(
    response: Response,
    _rl: None = Depends(_guest_limiter),
) -> dict[str, Any]:
    from app.storage.guest import new_guest_id

    guest_id = new_guest_id()
    token = create_token(guest_id)
    set_session_cookies(response, token)
    return {"ok": True, "username": guest_id}


@router.post("/logout")
async def logout(response: Response) -> dict[str, Any]:
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/me")
async def me(
    ctx=Depends(require_group_session),  # noqa: B008
) -> dict[str, Any]:
    from app.config.session import WEBMAIL_URL
    from app.storage.guest import is_guest

    user_id = ctx.user
    group_id = ctx.group_id

    role = await get_user_role(user_id)
    group_name: str | None = None
    if is_guest(user_id):
        auth_method = "guest"
        user_row: dict[str, Any] = {}
        username = user_id
    else:
        user_row = await get_user_by_id(user_id) or {}
        username = user_row.get("username", "")
        auth_method = user_row.get("provider") or "internal"
        if group_id != user_id:
            group = await _groups.get(group_id)
            group_name = group["name"] if group else group_id
        else:
            group_name = user_row.get("display_name") or username

    payload: dict[str, Any] = {
        "id": user_id,
        "username": username,
        "role": role,
        "auth_method": auth_method,
        "group_id": group_id,
        "group_personal": group_id == user_id,
    }
    if user_row:
        payload["email"] = user_row.get("email")
        payload["is_email_public"] = bool(user_row.get("is_email_public", 0))
    if role == "admin" and WEBMAIL_URL:
        payload["webmail_url"] = WEBMAIL_URL
    if group_name is not None:
        payload["group_name"] = group_name
    return payload


@router.post("/forgot-password")
async def forgot_password(
    body: EmailBody,
    request: Request,
    _rl: None = Depends(_forgot_limiter),
) -> dict[str, Any]:
    email = (body.email or "").strip().lower()
    if not email or not is_valid_email(email):
        raise APIError(400, "invalid_field", "Email inválido", extra={"field": "email"})
    token = await create_password_reset_token(email)
    if token:
        base_url = _public_base_url(request)
        send_reset_email(email, token, base_url, lang=get_locale())
    # Respuesta siempre igual para no revelar si el email existe
    return {"ok": True}


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordBody,
    _rl: None = Depends(_reset_limiter),
) -> dict[str, Any]:
    token = (body.token or "").strip()
    new_password = (body.password or "").strip()
    if not token or not new_password:
        raise APIError(
            400, "token_and_password_required", "Token y contraseña requeridos"
        )
    if len(new_password) < 8:
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if not await consume_reset_token(token, new_password):
        raise APIError(400, "invalid_reset_link", "Enlace inválido o expirado")
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordBody, username: str = Depends(require_auth)
) -> dict[str, Any]:
    current = body.current_password or ""
    new_pw = (body.new_password or "").strip()
    if not current or not new_pw:
        raise APIError(400, "all_fields_required", "Completa todos los campos")
    if len(new_pw) < 8:  # N4: mínimo coherente con el registro (8 caracteres)
        raise APIError(
            400,
            "password_too_short",
            "La nueva contraseña debe tener al menos 8 caracteres",
        )

    user = await get_user_by_id(username)
    if not user:
        raise APIError(
            404, "not_found", "Usuario no encontrado", extra={"resource": "user"}
        )
    if not await verify_password_async(current, user.get("password_hash", "")):
        raise APIError(
            401, "current_password_incorrect", "Contraseña actual incorrecta"
        )
    # ALTO-8 (.admin_pass) vive ahora dentro de set_own_password: era el único
    # de los tres caminos que cambian contraseña que lo limpiaba, y encima
    # dependía de GAIA_DATA_DIR —sin esa variable no borraba nada— y borraba el
    # fichero en vez de vaciarlo, lo que hacía que el siguiente arranque
    # regenerase la contraseña recién elegida.
    await set_own_password(username, new_pw)

    return {"ok": True}


# ── Social profile ────────────────────────────────────────────────────────────

_ALLOWED_LANGUAGES = CONTENT_LANGUAGE_SET
_MAX_AVATAR_BYTES = 10 * 1024 * 1024  # 10 MB (la compresión real ocurre en el cliente)
_ALLOWED_AVATAR_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@router.put("/me/profile")
async def update_profile(
    body: ProfileBody,
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    import json

    bio = (body.bio or "").strip() or None
    raw_langs = body.languages
    languages = json.dumps([lang for lang in raw_langs if lang in _ALLOWED_LANGUAGES])
    is_email_public = 1 if body.is_email_public else 0
    # N3: solo permitir URLs https:// para el campo github (bloquear javascript: y otros)
    _github_raw = (body.github or "").strip()
    if _github_raw and not _github_raw.startswith("https://"):
        raise APIError(
            422,
            "invalid_field",
            "El campo github debe ser una URL https://",
            extra={"field": "github"},
        )
    github = _github_raw or None
    cv = (body.cv or "").strip() or None

    async with open_db() as conn:
        await conn.execute(
            sql("queries/login:update_profile"),
            (bio, languages, is_email_public, github, cv, username),
        )
        await conn.commit()
    return {"ok": True}


@router.post("/me/avatar")
async def upload_avatar(
    request: Request,
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    import base64
    from pathlib import Path as _Path

    from fastapi import UploadFile
    from fastapi.datastructures import FormData
    from starlette.datastructures import UploadFile as _StarletteUploadFile

    try:
        form: FormData = await request.form()
        raw_field = form.get("avatar")
        # request.form() construye starlette.datastructures.UploadFile, no
        # fastapi.UploadFile (subclase usada solo vía inyección de FastAPI) —
        # hay que comprobar contra la clase base real que devuelve el parser.
        if not isinstance(raw_field, _StarletteUploadFile):
            raise APIError(400, "avatar_field_required", "Campo 'avatar' requerido")
        file: UploadFile = raw_field

        ext = _Path(file.filename or "").suffix.lower()
        if ext not in _ALLOWED_AVATAR_EXT:
            raise APIError(
                400,
                "avatar_format_not_allowed",
                "Formato no permitido. Usa jpg, png o webp.",
            )

        data = await file.read()
        if len(data) > _MAX_AVATAR_BYTES:
            raise APIError(400, "avatar_too_large", "El avatar no puede superar 10 MB.")
        from app.utils.images import detect_avatar_mime

        if detect_avatar_mime(data) is None:
            raise APIError(
                400,
                "avatar_format_not_allowed",
                "El contenido no es una imagen JPG, PNG o WebP válida.",
            )

        encoded = base64.b64encode(data).decode("ascii")
        async with open_db() as conn:
            await conn.execute(
                sql("queries/login:update_avatar"),
                (encoded, username),
            )
            await conn.commit()
        user = await get_user_by_id(username)
    except APIError:
        raise
    except Exception as exc:
        flog.error(
            f"Fallo subiendo avatar para {username}: {exc}",
            exc_info=True,
        )
        raise APIError(500, "internal_error", "Error interno del servidor.") from exc

    public_username = user["username"] if user else ""
    return {"ok": True, "avatar_url": f"/api/users/{public_username}/avatar"}
