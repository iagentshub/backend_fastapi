"""Recuperación y cambio de contraseña.

Cambiar la contraseña revoca la sesión (`_touch_password_changed_at`): sin eso
un refresh robado sigue siendo válido después del cambio.
"""


from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.routes.auth._shared import _public_base_url
from app.api.routes.auth.dependencies import (
    require_auth,
)
from app.auth.auth import (
    consume_reset_token,
    create_password_reset_token,
    get_user_by_id,
    set_own_password,
    verify_password_async,
)
from app.config.session import (
    RATE_FORGOT_CALLS,
    RATE_FORGOT_WINDOW,
    RATE_RESET_CALLS,
    RATE_RESET_WINDOW,
)
from app.errors import APIError
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.services.email import send_reset_email
from app.utils.net import client_ip as _client_ip
from app.utils.validation import is_valid_email

router = APIRouter()


class EmailBody(BaseModel):
    email: str | None = Field(default=None, max_length=254)

class ResetPasswordBody(BaseModel):
    token: str | None = Field(default=None, max_length=512)
    password: str | None = Field(default=None, max_length=128)

class ChangePasswordBody(BaseModel):
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str | None = Field(default=None, max_length=128)

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
