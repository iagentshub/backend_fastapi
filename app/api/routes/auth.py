"""Auth routes — shared dependencies and session endpoints."""

from __future__ import annotations

import os
import time
from typing import Any, Optional
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
    consume_reset_token,
    create_password_reset_token,
    create_token,
    decode_group_token_full,
    get_user_by_id,
    get_user_by_identity,
    get_user_by_login,
    get_user_by_username,
    get_user_role,
    register_user_email,
    set_own_password,
    verify_email_token,
    verify_password_async,
)
from app.auth.gdpr import cancel_user_deletion, get_owned_groups, schedule_user_deletion
from app.auth.oauth_github import get_or_create_github_user
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
from app.services.email import send_reset_email, send_verification_email
from app.storage.db import open_db
from app.storage.groups import GroupStorage as _GroupStorage
from app.storage.guest import is_guest
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
from app.utils import flog
from app.utils.net import client_ip as _client_ip
from app.utils.net import json_body
from app.utils.validation import is_valid_email, is_valid_username, normalize_username

_groups = _GroupStorage()
_tokens = _TokenStorage()


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
# Generoso a propósito: el cliente sondea /github/device-token cada ~5s
# (intervalo que marca GitHub) durante hasta 15 min — un límite ajustado
# como el de /login (pensado para fuerza bruta de contraseñas) cortaría un
# login legítimo a mitad de la espera.
_github_login_limiter = RateLimiter(calls=30, window=60, key_func=_client_ip)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Caché de estado de autenticación para require_auth ────────────────────────
# Evita una consulta a BD en cada request autenticado. TTL de 60 s: una cuenta
# suspendida queda bloqueada en ≤60 s desde la acción del admin, sin sacrificar
# el rendimiento en producción.
# A2: también cachea password_changed_at para invalidar tokens emitidos antes
#     de un cambio de contraseña, sin una consulta a BD por request.
_ACTIVE_CACHE_TTL = 60       # segundos
_ACTIVE_CACHE_MAX = 5_000    # entradas máximas antes de eviction
# {username: (is_active, password_changed_at, role, expires_at)}
# El rol viaja en la misma entrada: sale de la fila de usuario que ya leemos,
# así que require_role() no cuesta una consulta extra por request.
_active_cache: dict[str, tuple[bool, str | None, str, float]] = {}


async def _get_user_auth_state(username: str) -> tuple[bool, str | None, str]:
    """Devuelve (is_active, password_changed_at, role). Usa caché con TTL de 60 s.

    Los usuarios guest son siempre activos y no tienen password_changed_at.
    """
    if is_guest(username):
        return True, None, "guest"

    now = time.monotonic()
    cached = _active_cache.get(username)
    if cached and now < cached[3]:
        return cached[0], cached[1], cached[2]

    user = await get_user_by_identity(username)
    active = bool(user and user.get("is_active", 1))
    pwd_changed = user.get("password_changed_at") if user else None
    role = (user.get("role") or "standard") if user else "standard"

    # Eviction: si el dict supera el límite, eliminar la mitad de entradas expiradas
    # (o las más antiguas si no hay suficientes expiradas)
    if len(_active_cache) >= _ACTIVE_CACHE_MAX:
        expired = [k for k, (_, _, _, exp) in _active_cache.items() if now >= exp]
        if len(expired) >= _ACTIVE_CACHE_MAX // 2:
            for k in expired:
                del _active_cache[k]
        else:
            # Eliminar la mitad más antigua por orden de inserción
            for k in list(_active_cache)[: _ACTIVE_CACHE_MAX // 2]:
                del _active_cache[k]

    _active_cache[username] = (active, pwd_changed, role, now + _ACTIVE_CACHE_TTL)
    return active, pwd_changed, role


async def _is_user_active(username: str) -> bool:
    """Compatibilidad: devuelve True si la cuenta está activa."""
    active, _, _ = await _get_user_auth_state(username)
    return active


class GroupContext:
    """Contexto de request con usuario y group activo."""

    __slots__ = ("user", "group_id")

    def __init__(self, user: str, group_id: str) -> None:
        self.user = user
        self.group_id = group_id


def _bearer(authorization: Optional[str]) -> Optional[str]:
    """Extrae el token de una cabecera `Authorization: Bearer <token>`."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


async def _identify(
    ga_token: Optional[str], authorization: Optional[str]
) -> tuple[str, Optional[float], Optional[str]]:
    """Resuelve la credencial de la request → (username, issued_at, gid).

    Acepta dos credenciales, con la MISMA autoridad:
      - Cookie `ga_token` (JWT): la sesión del navegador. `gid` sale del claim.
      - `Authorization: Bearer iah_...` (PAT): clientes no navegador (extensión
        de VS Code, scripts). Un PAT no es un JWT y no lleva group dentro,
        así que devuelve gid=None y quien llama lo saca de X-iAgents-Group.

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
        username, gid, token_iat = decode_group_token_full(ga_token)
        if not username:
            raise APIError(401, "invalid_token", "Token inválido o expirado")
        return username, token_iat, gid

    raise APIError(401, "not_authenticated", "No autenticado")


async def _assert_account_ok(username: str, issued_at: Optional[float]) -> str:
    """Cuenta activa + credencial emitida tras el último cambio de contraseña.

    Cambiar la contraseña invalida las sesiones robadas — y también los PATs
    anteriores, que son credenciales de largo recorrido y por tanto el activo
    más valioso para un atacante que ya tuvo acceso.

    Los guests salen de _get_user_auth_state como (True, None, "guest") → no bloquean.

    Devuelve el rol del principal para que require_role() no vuelva a consultarlo.
    """
    is_active, password_changed_at, role = await _get_user_auth_state(username)
    if not is_active:
        raise APIError(403, "account_disabled", "Cuenta desactivada")
    if not password_changed_at or issued_at is None:
        return role
    changed = _parse_ts(password_changed_at)
    if changed is None:
        return role  # fecha malformada en BD → no bloquear
    if issued_at < changed.timestamp():
        raise APIError(
            401,
            "credential_expired_password_change",
            "Credencial expirada tras cambio de contraseña. Vuelve a autenticarte.",
        )
    return role


# ── Autorización por rol ──────────────────────────────────────────────────────
# Un único modelo de principal para todo el backend. Antes había dos guards
# escritos a mano (require_auth sin distinguir invitado, require_admin encima)
# y comprobaciones de invitado repartidas por endpoint; el que no se acordaba
# de comprobarlo quedaba abierto al invitado. Ahora el rango es la política y
# el default de cada dependency es explícito.
#
# Rol desconocido (p. ej. "gestor", que admin.py:497 acepta en BD) → rango de
# usuario registrado: pasa las puertas de "standard", no las de "admin". Es el
# comportamiento que ya tenían, porque nadie ramifica sobre esos roles.
_STANDARD_RANK = 1
_ROLE_RANK = {"guest": 0, "standard": _STANDARD_RANK, "admin": 2}


def _assert_min_role(role: str, minimum: str) -> None:
    if _ROLE_RANK.get(role, _STANDARD_RANK) < _ROLE_RANK[minimum]:
        if minimum == "admin":
            raise APIError(403, "forbidden", "Acceso restringido")
        raise APIError(
            403,
            "guest_forbidden",
            "Esta acción requiere una cuenta registrada.",
        )


async def _resolve_principal(
    ga_token: Optional[str], authorization: Optional[str]
) -> tuple[str, str, str, Optional[str]]:
    """Credencial → (user_id, legacy_personal_id, role, gid).

    El invitado no tiene fila en `users`, así que su identidad hace de ambos ids.
    """
    identity, issued_at, gid = await _identify(ga_token, authorization)
    role = await _assert_account_ok(identity, issued_at)
    if is_guest(identity):
        return identity, identity, role, gid
    user = await get_user_by_identity(identity)
    if not user:
        raise APIError(401, "invalid_token", "Token inválido o expirado")
    return user["id"], user["username"], role, gid


def require_role(minimum: str):
    """Construye la dependency que exige `minimum` como rol mínimo.

    `minimum="guest"` acepta a cualquiera con credencial válida, invitado
    incluido — es el permiso más laxo y hay que pedirlo explícitamente.
    """

    async def dependency(
        ga_token: Optional[str] = Cookie(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> str:
        user_id, _personal, role, _gid = await _resolve_principal(
            ga_token, authorization
        )
        _assert_min_role(role, minimum)
        return user_id

    return dependency


def require_group_role(minimum: str):
    """Igual que require_role, pero resuelve además el group activo.

    El group sale del claim `gid` del JWT (navegador) o de la cabecera
    `X-iAgents-Group` (Bearer). En ambos casos se valida igual: si el
    group no existe, está desactivado o el usuario ya no es miembro, se cae
    al espacio personal (group_id = user_id).
    """

    async def dependency(
        ga_token: Optional[str] = Cookie(default=None),
        authorization: Optional[str] = Header(default=None),
        x_iagents_group: Optional[str] = Header(default=None),
    ) -> GroupContext:
        user_id, personal_id, role, gid = await _resolve_principal(
            ga_token, authorization
        )
        _assert_min_role(role, minimum)
        return await _resolve_group(user_id, personal_id, gid, x_iagents_group)

    return dependency


async def _resolve_group(
    user_id: str,
    legacy_personal_id: str,
    gid: Optional[str],
    x_iagents_group: Optional[str],
) -> GroupContext:
    if gid is None:
        gid = x_iagents_group

    # Si el gid es distinto del espacio personal → validar group de equipo
    if gid and gid not in (user_id, legacy_personal_id):
        group = await _groups.get(gid)
        if (
            group
            and group.get("status", "active") == "active"
            and await _groups.is_member(gid, user_id)
        ):
            return GroupContext(user=user_id, group_id=gid)
        # Group desactivado, eliminado o no miembro → espacio personal
    return GroupContext(user=user_id, group_id=user_id)


# Dependencies concretas. El default exige cuenta registrada: un invitado que
# llegue a un endpoint que no lo contemple recibe 403 en vez de entrar.
#
# El allowlist no se decidió a ojo: un endpoint que contiene una rama
# `is_guest(...)` es consciente del invitado por diseño, y son exactamente los
# que trabajan sobre los cinco campos de GuestSession (connections, agents,
# skills, knowledge, memory) más el chat y GET /me. Esos 32 usan
# require_session / require_group_session. Los otros 106 —billing, settings,
# social, sharing, users, accounts, workflows, groups— nunca miraron si quien
# llamaba era invitado, que es precisamente lo que los hacía inseguros.
require_auth = require_role("standard")
require_group = require_group_role("standard")

# Explícitos: aceptan invitado por diseño. Son los endpoints del demo.
require_session = require_role("guest")
require_group_session = require_group_role("guest")

require_admin = require_role("admin")


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
    body = await json_body(request)
    username = normalize_username(str(body.get("username") or ""))
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    birth_date = str(body.get("birth_date") or "").strip() or None
    gender = str(body.get("gender") or "").strip() or None
    country = str(body.get("country") or "").strip() or None
    phone = str(body.get("phone") or "").strip() or None

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
        raise APIError(409, "already_exists", str(exc), extra={"resource": resource}) from exc

    if EMAIL_VERIFY_ENABLED and verify_token:
        base_url = _public_base_url(request)
        send_verification_email(email, verify_token, base_url)
        return {"ok": True, "email": email, "pending_verification": True}

    user = await get_user_by_username(username)
    if not user:
        raise APIError(500, "user_creation_failed", "No se pudo crear la sesión")
    token = create_token(user["id"])
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
    body = await json_body(request)
    identifier = str(body.get("identifier") or body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    # Extraer IP real del cliente
    _ip = _client_ip(request)

    if not identifier or not password:
        flog.warning(
            f"[login] FAIL identificador={identifier or '(vacío)'} razón=campos_vacíos", ip=_ip
        )
        raise APIError(400, "missing_credentials", "Usuario o email y contraseña requeridos")

    user = await get_user_by_login(identifier)
    if not user or not user.get("password_hash"):
        flog.warning(f"[login] FAIL identificador={identifier} razón=usuario_no_encontrado", ip=_ip)
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not await verify_password_async(password, user["password_hash"]):
        flog.warning(f"[login] FAIL identificador={identifier} razón=contraseña_incorrecta", ip=_ip)
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not user.get("is_active", 1):
        flog.warning(f"[login] FAIL identificador={identifier} razón=cuenta_desactivada", ip=_ip)
        raise APIError(403, "account_disabled", "Cuenta desactivada")
    if EMAIL_VERIFY_ENABLED and not user.get("is_verified", 1):
        flog.warning(f"[login] FAIL identificador={identifier} razón=pendiente_verificación", ip=_ip)
        raise APIError(
            403,
            "email_not_verified",
            "Cuenta pendiente de verificación. Revisa tu correo.",
        )

    token = create_token(user["id"])
    flog.ok(
        f"[login] OK identificador={identifier} usuario={user['username']}",
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
    user = await get_user_by_username(username)
    if not user:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    auth_token = create_token(user["id"])
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


@router.post("/github/device-code")
async def github_login_device_code(
    _rl: None = Depends(_github_login_limiter),
) -> dict[str, Any]:
    """Inicia sesión con GitHub (Device Flow) — sin sesión previa, a
    diferencia de `/api/accounts/github/device-code` (que vincula una cuenta
    proveedor para un usuario ya logueado)."""
    from app.auth.github_oauth import request_device_code

    return await request_device_code(scope="read:user user:email")


@router.post("/github/device-token")
async def github_login_device_token(
    request: Request,
    response: Response,
    _rl: None = Depends(_github_login_limiter),
) -> dict[str, Any]:
    """Sondea el Device Flow iniciado con `/github/device-code`. Si ya se
    autorizó, resuelve (o crea) el usuario local ligado a esa identidad de
    GitHub y abre sesión — mismo mecanismo de cookie que `/login`."""
    from app.auth.github_oauth import fetch_github_identity, poll_device_token

    body = await json_body(request)
    device_code = str(body.get("device_code") or "").strip()
    if not device_code:
        raise APIError(
            422, "invalid_field", "device_code requerido", extra={"field": "device_code"}
        )

    result = await poll_device_token(device_code)
    if not result.get("ok"):
        return result

    identity = await fetch_github_identity(result["access_token"])
    if not identity.get("id"):
        raise APIError(
            502, "github_identity_error", "No se pudo obtener la identidad de GitHub"
        )

    user = await get_or_create_github_user(
        identity["id"], identity["login"], identity["email"], identity["name"]
    )
    if not user.get("is_active", 1):
        raise APIError(403, "account_disabled", "Cuenta desactivada")

    token = create_token(user["id"])
    flog.ok(f"[login] OK vía GitHub usuario={user['username']}", ip=_client_ip(request))
    response.set_cookie(
        "ga_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=43200,
    )
    return {"ok": True, "pending": False, "username": user["username"]}


@router.post("/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie("ga_token")
    return {"ok": True}


@router.get("/me")
async def me(
    ctx: GroupContext = Depends(require_group_session),  # noqa: B008
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
    request: Request,
    _rl: None = Depends(_forgot_limiter),
) -> dict[str, Any]:
    body = await json_body(request)
    email = str(body.get("email") or "").strip().lower()
    if not email or not is_valid_email(email):
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
    body = await json_body(request)
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
    body = await json_body(request)
    current = str(body.get("current_password") or "")
    new_pw = str(body.get("new_password") or "").strip()
    if not current or not new_pw:
        raise APIError(400, "all_fields_required", "Completa todos los campos")
    if len(new_pw) < 8:  # N4: mínimo coherente con el registro (8 caracteres)
        raise APIError(400, "password_too_short", "La nueva contraseña debe tener al menos 8 caracteres")

    user = await get_user_by_id(username)
    if not user:
        raise APIError(404, "not_found", "Usuario no encontrado", extra={"resource": "user"})
    if not await verify_password_async(current, user.get("password_hash", "")):
        raise APIError(401, "current_password_incorrect", "Contraseña actual incorrecta")
    # ALTO-8 (.admin_pass) vive ahora dentro de set_own_password: era el único
    # de los tres caminos que cambian contraseña que lo limpiaba, y encima
    # dependía de GAIA_DATA_DIR —sin esa variable no borraba nada— y borraba el
    # fichero en vez de vaciarlo, lo que hacía que el siguiente arranque
    # regenerase la contraseña recién elegida.
    await set_own_password(username, new_pw)

    return {"ok": True}


# ── GDPR ──────────────────────────────────────────────────────────────────────


@router.get("/me/deletion-status")
async def get_deletion_status(username: str = Depends(require_auth)) -> dict[str, Any]:
    user = await get_user_by_id(username)
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
    owned = await get_owned_groups(username)
    if owned:
        raise APIError(
            409,
            "owned_groups_exist",
            "Transfiere o elimina tus grupos antes de borrar la cuenta",
            extra={"groups": owned},
        )
    await schedule_user_deletion(username)
    return {"ok": True, "message": "Cuenta programada para eliminación en 30 días"}


@router.post("/me/cancel-deletion")
async def cancel_account_deletion(request: Request) -> dict[str, Any]:
    body = await json_body(request)
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
    user = await get_user_by_id(username)
    safe_name = (user.get("username") if user else "account").replace(" ", "_")
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

    body = await json_body(request)
    bio = str(body.get("bio") or "").strip()[:500] or None
    raw_langs = body.get("languages") or []
    languages = json.dumps([lang for lang in raw_langs if lang in _ALLOWED_LANGUAGES])
    is_email_public = 1 if body.get("is_email_public") is True else 0
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
            "UPDATE users SET bio=?, languages=?, is_email_public=?, github=?, cv=? WHERE id=?",
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
            "UPDATE users SET avatar=? WHERE id=?",
            (encoded, username),
        )
        await conn.commit()
    user = await get_user_by_id(username)
    public_username = user["username"] if user else ""
    return {"ok": True, "avatar_url": f"/api/users/{public_username}/avatar"}

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

    body = await json_body(request)
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
    flog.info(
        f"PAT creado: {meta['id']} {name!r} ({meta['prefix']}…)", username=username
    )
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

    body = await json_body(request)
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

    body = await json_body(request)
    code = str(body.get("code") or "")
    state = str(body.get("state") or "")
    if not code or not state:
        raise APIError(400, "code_and_state_required", "code y state requeridos")

    user_id = await _consume_auth_code(code, state)
    if not user_id:
        raise APIError(
            400, "invalid_auth_code", "Código inválido, caducado o ya usado"
        )

    token, meta = await _tokens.create(user_id, "VS Code", DEFAULT_EXPIRY_DAYS)
    user = await get_user_by_id(user_id)
    username = user["username"] if user else ""
    flog.info(f"PAT creado desde VS Code ({meta['prefix']}…)", username=username)
    return {"token": token, "token_id": meta["id"], "username": username}
