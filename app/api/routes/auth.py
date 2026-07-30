"""Auth routes — shared dependencies and session endpoints."""

from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    Query,
    Request,
    Response,
)
from fastapi.responses import RedirectResponse

from app.auth.auth import (
    cancel_user_deletion,
    consume_reset_token,
    create_password_reset_token,
    create_token,
    decode_workspace_token_full,
    get_owned_workspaces,
    get_user_by_email,
    get_user_by_username,
    get_user_role,
    register_user_email,
    schedule_user_deletion,
    send_reset_email,
    send_verification_email,
    set_own_password,
    verify_email_token,
    verify_password_async,
)
from app.config.data import DB_FILE as _DB_FILE
from app.config.session import (
    EMAIL_VERIFY_ENABLED,
    LOGIN_MAX_FAILS,
    LOGIN_WINDOW,
    RATE_FORGOT_CALLS,
    RATE_FORGOT_WINDOW,
    RATE_GUEST_CALLS,
    RATE_GUEST_WINDOW,
    RATE_RESET_CALLS,
    RATE_RESET_WINDOW,
    REGISTER_MAX,
    REGISTER_WINDOW,
    REGISTRATION_MODE,
    SECURE_COOKIES,
)
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.storage.db import open_db
from app.storage.tokens import (
    DEFAULT_EXPIRY_DAYS,
    VALID_EXPIRY_DAYS,
)
from app.storage.tokens import (
    TokenStorage as _TokenStorage,
)
from app.storage.tokens import (
    consume_auth_code as _consume_auth_code,
)
from app.storage.tokens import (
    create_auth_code as _create_auth_code,
)
from app.storage.tokens import (
    parse_ts as _parse_ts,
)
from app.storage.workspaces import WorkspaceStorage as _WorkspaceStorage
from app.utils import flog
from app.utils.net import client_ip as _client_ip

_workspaces = _WorkspaceStorage(_DB_FILE)
_tokens = _TokenStorage()

# Regex estricta de email (RFC 5321): bloquea payloads XSS como x'><script>@a.com
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _public_base_url(request: Request) -> str:
    """URL base canónica para construir enlaces en emails.

    Usa GAIA_FRONTEND_URL si está configurada (evita Host Header Injection).
    Solo recurre a request.base_url en entornos de desarrollo sin esa variable.
    """
    configured = os.getenv("GAIA_FRONTEND_URL", "").rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")
_register_limiter = RateLimiter(
    calls=REGISTER_MAX, window=REGISTER_WINDOW, key_func=_client_ip
)
_login_limiter = RateLimiter(
    calls=LOGIN_MAX_FAILS, window=LOGIN_WINDOW, key_func=_client_ip
)
_forgot_limiter = RateLimiter(
    calls=RATE_FORGOT_CALLS, window=RATE_FORGOT_WINDOW, key_func=_client_ip
)
_reset_limiter = RateLimiter(
    calls=RATE_RESET_CALLS, window=RATE_RESET_WINDOW, key_func=_client_ip
)
_guest_limiter = RateLimiter(
    calls=RATE_GUEST_CALLS, window=RATE_GUEST_WINDOW, key_func=_client_ip
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Caché de estado de autenticación para require_auth ────────────────────────
# Evita una consulta a BD en cada request autenticado. TTL de 60 s: una cuenta
# suspendida queda bloqueada en ≤60 s desde la acción del admin, sin sacrificar
# el rendimiento en producción.
# A2: también cachea password_changed_at para invalidar tokens emitidos antes
#     de un cambio de contraseña, sin una consulta a BD por request.
_ACTIVE_CACHE_TTL = 60       # segundos
_ACTIVE_CACHE_MAX = 5_000    # entradas máximas antes de eviction
# {username: (is_active, password_changed_at, expires_at)}
_active_cache: dict[str, tuple[bool, str | None, float]] = {}


async def _get_user_auth_state(username: str) -> tuple[bool, str | None]:
    """Devuelve (is_active, password_changed_at). Usa caché con TTL de 60 s.

    Los usuarios guest son siempre activos y no tienen password_changed_at.
    """
    from app.storage.guest import is_guest as _is_guest_fn
    if _is_guest_fn(username):
        return True, None

    now = time.monotonic()
    cached = _active_cache.get(username)
    if cached and now < cached[2]:
        return cached[0], cached[1]

    user = await get_user_by_username(username)
    active = bool(user and user.get("is_active", 1))
    pwd_changed = user.get("password_changed_at") if user else None

    # Eviction: si el dict supera el límite, eliminar la mitad de entradas expiradas
    # (o las más antiguas si no hay suficientes expiradas)
    if len(_active_cache) >= _ACTIVE_CACHE_MAX:
        expired = [k for k, (_, _, exp) in _active_cache.items() if now >= exp]
        if len(expired) >= _ACTIVE_CACHE_MAX // 2:
            for k in expired:
                del _active_cache[k]
        else:
            # Eliminar la mitad más antigua por orden de inserción
            for k in list(_active_cache)[: _ACTIVE_CACHE_MAX // 2]:
                del _active_cache[k]

    _active_cache[username] = (active, pwd_changed, now + _ACTIVE_CACHE_TTL)
    return active, pwd_changed


async def _is_user_active(username: str) -> bool:
    """Compatibilidad: devuelve True si la cuenta está activa."""
    active, _ = await _get_user_auth_state(username)
    return active


class WorkspaceContext:
    """Contexto de request con usuario y workspace activo."""

    __slots__ = ("user", "workspace_id")

    def __init__(self, user: str, workspace_id: str) -> None:
        self.user = user
        self.workspace_id = workspace_id


def _bearer(authorization: str | None) -> str | None:
    """Extrae el token de una cabecera `Authorization: Bearer <token>`."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def _identify(
    ga_token: str | None, authorization: str | None
) -> tuple[str, float | None, str | None]:
    """Resuelve la credencial de la request → (username, issued_at, wid).

    Acepta dos credenciales, con la MISMA autoridad:
      - Cookie `ga_token` (JWT): la sesión del navegador. `wid` sale del claim.
      - `Authorization: Bearer iah_...` (PAT): clientes no navegador (extensión
        de VS Code, scripts). Un PAT no es un JWT y no lleva workspace dentro,
        así que devuelve wid=None y quien llama lo saca de X-iAgents-Workspace.

    `issued_at` unifica el `iat` del JWT y el `created_at` del PAT: ambos se
    contrastan igual contra password_changed_at.

    El Bearer tiene prioridad: si un cliente lo envía explícitamente, es la
    credencial que quiere usar — caer a una cookie de sesión sería sorprendente.
    """
    token = _bearer(authorization)
    if token:
        pat = await _tokens.resolve(token)
        if not pat:
            raise APIError(401, "invalid_pat", "Token inválido, revocado o caducado")
        created = _parse_ts(pat.get("created_at"))
        return pat["username"], created.timestamp() if created else None, None

    if ga_token:
        username, wid, token_iat = decode_workspace_token_full(ga_token)
        if not username:
            raise APIError(401, "invalid_token", "Token inválido o expirado")
        return username, token_iat, wid

    raise APIError(401, "not_authenticated", "No autenticado")


async def _assert_account_ok(username: str, issued_at: float | None) -> None:
    """Cuenta activa + credencial emitida tras el último cambio de contraseña.

    Cambiar la contraseña invalida las sesiones robadas — y también los PATs
    anteriores, que son credenciales de largo recorrido y por tanto el activo
    más valioso para un atacante que ya tuvo acceso.

    Los guests salen de _get_user_auth_state como (True, None) → no bloquean.
    """
    is_active, password_changed_at = await _get_user_auth_state(username)
    if not is_active:
        raise APIError(403, "account_disabled", "Cuenta desactivada")
    if not password_changed_at or issued_at is None:
        return
    changed = _parse_ts(password_changed_at)
    if changed is None:
        return  # fecha malformada en BD → no bloquear
    if issued_at < changed.timestamp():
        raise APIError(
            401,
            "credential_expired_password_change",
            "Credencial expirada tras cambio de contraseña. Vuelve a autenticarte.",
        )


async def require_auth(
    ga_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    """Dependency: valida la credencial (cookie o Bearer) y la cuenta."""
    username, issued_at, _ = await _identify(ga_token, authorization)
    await _assert_account_ok(username, issued_at)
    return username


async def require_workspace(
    ga_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    x_iagents_workspace: str | None = Header(default=None),
) -> WorkspaceContext:
    """Dependency: valida la credencial y resuelve el workspace activo.

    El workspace sale del claim `wid` del JWT (navegador) o de la cabecera
    `X-iAgents-Workspace` (Bearer). En ambos casos se valida igual: si el
    workspace no existe, está desactivado o el usuario ya no es miembro, se cae
    al espacio personal (workspace_id = username).
    """
    username, issued_at, wid = await _identify(ga_token, authorization)
    await _assert_account_ok(username, issued_at)

    if wid is None:
        wid = x_iagents_workspace

    # Si el wid es distinto del username → validar workspace de equipo
    if wid and wid != username:
        ws = await _workspaces.get(wid)
        if (
            ws
            and ws.get("status", "active") == "active"
            and await _workspaces.is_member(wid, username)
        ):
            return WorkspaceContext(user=username, workspace_id=wid)
        # Workspace desactivado, eliminado o no miembro → espacio personal
    return WorkspaceContext(user=username, workspace_id=username)


async def require_admin(username: str = Depends(require_auth)) -> str:
    if await get_user_role(username) != "admin":
        raise APIError(403, "forbidden", "Acceso restringido")
    return username


@router.post("/register")
async def register(request: Request, response: Response) -> dict[str, Any]:
    if REGISTRATION_MODE == "closed":
        raise APIError(403, "registration_disabled", "El registro está desactivado.")
    if REGISTRATION_MODE == "invite":
        raise APIError(
            403,
            "registration_invite_only",
            "El registro requiere invitación de un administrador.",
        )
    await _register_limiter(request)
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    birth_date = str(body.get("birth_date") or "").strip() or None
    gender = str(body.get("gender") or "").strip() or None
    country = str(body.get("country") or "").strip() or None
    phone = str(body.get("phone") or "").strip() or None

    if not email or not _EMAIL_RE.match(email):
        raise APIError(400, "invalid_field", "Email inválido", extra={"field": "email"})
    if len(password) < 8:
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )

    try:
        username, verify_token = await register_user_email(
            email,
            password,
            birth_date=birth_date,
            gender=gender,
            country=country,
            phone=phone,
        )
    except ValueError as exc:
        raise APIError(
            409, "already_exists", str(exc), extra={"resource": "email"}
        ) from exc

    if EMAIL_VERIFY_ENABLED and verify_token:
        base_url = _public_base_url(request)
        send_verification_email(email, verify_token, base_url)
        return {"ok": True, "email": email, "pending_verification": True}

    token = create_token(username)
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "email": email, "pending_verification": False}


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    _rl: None = Depends(_login_limiter),
) -> dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    # Extraer IP real del cliente
    _ip = _client_ip(request)

    if not email or not password:
        flog.warning(
            f"[login] FAIL email={email or '(vacío)'} razón=campos_vacíos", ip=_ip
        )
        raise APIError(400, "missing_credentials", "Email y contraseña requeridos")

    user = await get_user_by_email(email)
    if not user or not user.get("password_hash"):
        flog.warning(f"[login] FAIL email={email} razón=usuario_no_encontrado", ip=_ip)
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not await verify_password_async(password, user["password_hash"]):
        flog.warning(f"[login] FAIL email={email} razón=contraseña_incorrecta", ip=_ip)
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not user.get("is_active", 1):
        flog.warning(f"[login] FAIL email={email} razón=cuenta_desactivada", ip=_ip)
        raise APIError(403, "account_disabled", "Cuenta desactivada")
    if EMAIL_VERIFY_ENABLED and not user.get("is_verified", 1):
        flog.warning(f"[login] FAIL email={email} razón=pendiente_verificación", ip=_ip)
        raise APIError(
            403,
            "email_not_verified",
            "Cuenta pendiente de verificación. Revisa tu correo.",
        )

    token = create_token(user["username"])
    flog.ok(
        f"[login] OK email={email} usuario={user['username']}",
        ip=_ip,
        username=user["username"],
    )
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": user["username"]}


@router.get("/verify")
async def verify_email(token: str, response: Response) -> dict[str, Any]:
    username = await verify_email_token(token)
    if not username:
        raise APIError(
            400, "invalid_verification_link", "Enlace de verificación inválido o expirado"
        )
    auth_token = create_token(username)
    response.set_cookie(
        "ga_token",
        auth_token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": username}


@router.post("/guest")
async def guest_login(
    response: Response,
    _rl: None = Depends(_guest_limiter),
) -> dict[str, Any]:
    from app.storage.guest import new_guest_id

    guest_id = new_guest_id()
    token = create_token(guest_id)
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "username": guest_id}


@router.post("/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie("ga_token")
    return {"ok": True}


@router.get("/me")
async def me(
    ctx: WorkspaceContext = Depends(require_workspace),  # noqa: B008
) -> dict[str, Any]:
    from app.config.session import WEBMAIL_URL
    from app.storage.guest import is_guest

    username = ctx.user
    workspace_id = ctx.workspace_id

    role = await get_user_role(username)
    ws_name: str | None = None
    if is_guest(username):
        auth_method = "guest"
        user_row: dict[str, Any] = {}
    else:
        user_row = await get_user_by_username(username) or {}
        auth_method = user_row.get("provider") or "internal"
        if workspace_id != username:
            ws = await _workspaces.get(workspace_id)
            ws_name = ws["name"] if ws else workspace_id
        else:
            ws_name = user_row.get("display_name") or username

    payload: dict[str, Any] = {
        "username": username,
        "role": role,
        "auth_method": auth_method,
        "workspace_id": workspace_id,
        "workspace_personal": workspace_id == username,
    }
    if role == "admin" and WEBMAIL_URL:
        payload["webmail_url"] = WEBMAIL_URL
    if ws_name is not None:
        payload["workspace_name"] = ws_name
    return payload


@router.post("/forgot-password")
async def forgot_password(
    request: Request,
    _rl: None = Depends(_forgot_limiter),
) -> dict[str, Any]:
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise APIError(400, "invalid_field", "Email inválido", extra={"field": "email"})
    token = await create_password_reset_token(email)
    if token:
        base_url = _public_base_url(request)
        send_reset_email(email, token, base_url)
    # Respuesta siempre igual para no revelar si el email existe
    return {"ok": True}


@router.post("/reset-password")
async def reset_password(
    request: Request,
    _rl: None = Depends(_reset_limiter),
) -> dict[str, Any]:
    body = await request.json()
    token = str(body.get("token") or "").strip()
    new_password = str(body.get("password") or "").strip()
    if not token or not new_password:
        raise APIError(400, "token_and_password_required", "Token y contraseña requeridos")
    if len(new_password) < 8:
        raise APIError(
            400, "password_too_short", "La contraseña debe tener al menos 8 caracteres"
        )
    if not await consume_reset_token(token, new_password):
        raise APIError(400, "invalid_reset_link", "Enlace inválido o expirado")
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    request: Request, username: str = Depends(require_auth)
) -> dict[str, Any]:
    body = await request.json()
    current = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "").strip()
    if not current or not new_pw:
        raise APIError(400, "all_fields_required", "Completa todos los campos")
    if len(new_pw) < 8:  # N4: mínimo coherente con el registro (8 caracteres)
        raise APIError(400, "password_too_short", "La nueva contraseña debe tener al menos 8 caracteres")

    user = await get_user_by_username(username)
    if not user:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if not await verify_password_async(current, user.get("password_hash", "")):
        raise APIError(401, "current_password_incorrect", "Contraseña actual incorrecta")
    await set_own_password(username, new_pw)

    # ALTO-8: al cambiar la contraseña del admin, borrar .admin_pass del disco
    # para que la contraseña temporal no persista indefinidamente.
    if user.get("role") == "admin":
        import contextlib
        import pathlib

        _data_dir = os.getenv("GAIA_DATA_DIR", "").strip()
        if _data_dir:
            with contextlib.suppress(OSError):
                pathlib.Path(_data_dir, ".admin_pass").unlink(missing_ok=True)

    return {"ok": True}


# ── GDPR ──────────────────────────────────────────────────────────────────────


@router.get("/me/deletion-status")
async def get_deletion_status(username: str = Depends(require_auth)) -> dict[str, Any]:
    user = await get_user_by_username(username)
    if not user:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    return {
        "scheduled": user.get("deletion_requested_at") is not None,
        "deletion_date": user.get("deletion_requested_at"),
    }


@router.post("/me/request-deletion")
async def request_account_deletion(
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    owned = await get_owned_workspaces(username)
    if owned:
        raise APIError(
            409,
            "owned_workspaces_exist",
            "Transfiere o elimina tus workspaces antes de borrar la cuenta",
            extra={"workspaces": owned},
        )
    await schedule_user_deletion(username)
    return {"ok": True, "message": "Cuenta programada para eliminación en 30 días"}


@router.post("/me/cancel-deletion")
async def cancel_account_deletion(request: Request) -> dict[str, Any]:
    body = await request.json()
    token = str(body.get("token", "")).strip()
    if not token or not await cancel_user_deletion(token):
        raise APIError(400, "invalid_deletion_token", "Token inválido o expirado")
    return {"ok": True}


@router.get("/me/export")
async def export_my_data(username: str = Depends(require_auth)):
    from datetime import datetime, timezone

    from fastapi.responses import StreamingResponse

    from app.services.gdpr import export_user_data

    buf = await export_user_data(username)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_name = username.split("@")[0].replace(" ", "_")
    filename = f"export_{safe_name}_{date_str}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Social profile ────────────────────────────────────────────────────────────

_ALLOWED_LANGUAGES = {"es", "en", "fr", "de", "pt", "it", "zh", "ja", "ar"}
_MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB
_ALLOWED_AVATAR_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@router.put("/me/profile")
async def update_profile(
    request: Request,
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    import json

    body = await request.json()
    bio = str(body.get("bio") or "").strip()[:500] or None
    raw_langs = body.get("languages") or []
    languages = json.dumps([lang for lang in raw_langs if lang in _ALLOWED_LANGUAGES])
    email_public = str(body.get("email_public") or "").strip()[:200] or None
    # N3: solo permitir URLs https:// para el campo github (bloquear javascript: y otros)
    _github_raw = str(body.get("github") or "").strip()[:100]
    if _github_raw and not _github_raw.startswith("https://"):
        raise APIError(
            422, "invalid_field", "El campo github debe ser una URL https://",
            extra={"field": "github"},
        )
    github = _github_raw or None
    cv = str(body.get("cv") or "").strip()[:20000] or None

    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET bio=?, languages=?, email_public=?, github=?, cv=? WHERE username=?",
            (bio, languages, email_public, github, cv, username),
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

    form: FormData = await request.form()
    file: UploadFile = form.get("avatar")  # type: ignore[assignment]
    if not file:
        raise APIError(400, "avatar_field_required", "Campo 'avatar' requerido")

    ext = _Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_AVATAR_EXT:
        raise APIError(
            400, "avatar_format_not_allowed", "Formato no permitido. Usa jpg, png o webp."
        )

    data = await file.read()
    if len(data) > _MAX_AVATAR_BYTES:
        raise APIError(400, "avatar_too_large", "El avatar no puede superar 2 MB.")

    encoded = base64.b64encode(data).decode("ascii")
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET avatar=? WHERE username=?",
            (encoded, username),
        )
        await conn.commit()
    return {"ok": True, "avatar_url": f"/api/users/{username}/avatar"}

# ── Personal access tokens ────────────────────────────────────────────────────
# Credencial para clientes que no son un navegador (extensión de VS Code,
# scripts, CI). Se gestionan desde el perfil, con la sesión web ya iniciada.


@router.get("/tokens")
async def list_tokens(username: str = Depends(require_auth)) -> list[dict[str, Any]]:
    """Metadatos de los PATs del usuario. El secreto no se devuelve nunca."""
    return await _tokens.list_for_user(username)


@router.post("/tokens")
async def create_pat(
    request: Request, username: str = Depends(require_auth)
) -> dict[str, Any]:
    """Crea un PAT. El token en claro viaja en esta respuesta y en ninguna más."""
    from app.storage.guest import is_guest as _is_guest

    if _is_guest(username):
        raise APIError(
            403,
            "guest_cannot_create_tokens",
            "Las sesiones de invitado no pueden crear tokens.",
        )
    await _login_limiter(request)

    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 100:
        raise APIError(
            400, "token_name_required", "Nombre requerido (máximo 100 caracteres)"
        )

    # Ausente → 90 días. Presente y null → sin caducidad. Son casos distintos.
    expires = body.get("expires_in_days", DEFAULT_EXPIRY_DAYS)
    if expires is not None:
        try:
            expires = int(expires)
        except (TypeError, ValueError) as exc:
            raise APIError(
                400, "invalid_field", "expires_in_days inválido",
                extra={"field": "expires_in_days"},
            ) from exc
    if expires not in VALID_EXPIRY_DAYS:
        raise APIError(
            400, "invalid_field", "expires_in_days debe ser 30, 90, 180 o null",
            extra={"field": "expires_in_days"},
        )

    token, meta = await _tokens.create(username, name, expires)
    flog.info(f"PAT creado: {name!r} ({meta['prefix']}…)", username=username)
    return {**meta, "token": token}


@router.delete("/tokens/{token_id}")
async def revoke_pat(
    token_id: str, username: str = Depends(require_auth)
) -> dict[str, Any]:
    """Revoca un PAT. Irreversible: deja de autenticar de inmediato."""
    if not await _tokens.revoke(token_id, username):
        raise APIError(404, "not_found", "Token no encontrado", extra={"resource": "token"})
    flog.info(f"PAT revocado: {token_id}", username=username)
    return {"ok": True}


# ── Login de la extensión de VS Code ──────────────────────────────────────────
# La extensión abre el navegador, que ya sabe quién eres (cookie `ga_token`), y
# este te devuelve a VS Code con un código de un solo uso. La extensión lo canjea
# por un PAT. Ni el token en claro ni la cookie salen nunca por la URI vscode://.

# Editores que pueden recibir el callback. Sin lista blanca, /vscode/start sería
# un redirector abierto: cualquiera podría mandar a un usuario logueado a un
# esquema arbitrario con sus parámetros.
_VSCODE_SCHEMES = frozenset(
    {"vscode", "vscode-insiders", "vscodium", "cursor", "windsurf"}
)
_VSCODE_AUTHORITY = "iagentshub.iagentshub"


def _check_callback(callback: str) -> None:
    parsed = urlsplit(callback)
    if parsed.scheme not in _VSCODE_SCHEMES or parsed.netloc != _VSCODE_AUTHORITY:
        raise APIError(400, "callback_not_allowed", "Callback no permitido")


@router.get("/vscode/start")
async def vscode_start(
    request: Request,
    state: str = Query(..., min_length=8, max_length=128),
    callback: str = Query(..., max_length=512),
) -> RedirectResponse:
    """Puente extensión → web. Manda al usuario a la pantalla de autorización.

    Existe porque la extensión solo conoce la URL de la API, que en desarrollo no
    es la misma que la de la web. Aquí el backend, que sí sabe dónde vive el
    frontend (GAIA_FRONTEND_URL), resuelve esa diferencia.
    """
    _check_callback(callback)
    query = urlencode({"state": state, "callback": callback})
    return RedirectResponse(
        f"{_public_base_url(request)}/vscode-auth/?{query}", status_code=302
    )


@router.post("/vscode/authorize")
async def vscode_authorize(
    request: Request, username: str = Depends(require_auth)
) -> dict[str, Any]:
    """Emite el código. Exige la sesión del navegador: es el consentimiento."""
    from app.storage.guest import is_guest as _is_guest

    if _is_guest(username):
        raise APIError(
            403,
            "guest_cannot_connect_vscode",
            "Las sesiones de invitado no pueden conectar VS Code.",
        )

    body = await request.json()
    state = str(body.get("state") or "")
    if not 8 <= len(state) <= 128:
        raise APIError(400, "invalid_field", "state inválido", extra={"field": "state"})

    return {"code": await _create_auth_code(username, state)}


@router.post("/vscode/exchange")
async def vscode_exchange(request: Request) -> dict[str, Any]:
    """Código + state → PAT. Sin cookie: quien llama aquí es la extensión.

    El PAT se crea aquí y no al autorizar, para que el token en claro exista solo
    en esta respuesta y no tenga que dormir en ninguna tabla esperando el canje.
    """
    await _login_limiter(request)

    body = await request.json()
    code = str(body.get("code") or "")
    state = str(body.get("state") or "")
    if not code or not state:
        raise APIError(400, "code_and_state_required", "code y state requeridos")

    username = await _consume_auth_code(code, state)
    if not username:
        raise APIError(
            400, "invalid_auth_code", "Código inválido, caducado o ya usado"
        )

    token, meta = await _tokens.create(username, "VS Code", DEFAULT_EXPIRY_DAYS)
    flog.info(f"PAT creado desde VS Code ({meta['prefix']}…)", username=username)
    return {"token": token, "token_id": meta["id"], "username": username}
