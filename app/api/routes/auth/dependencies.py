"""Modelo de autorización compartido por todo el backend.

Un único modelo de principal para todo el backend. Antes había dos guards
escritos a mano (require_auth sin distinguir invitado, require_admin encima)
y comprobaciones de invitado repartidas por endpoint; el que no se acordaba
de comprobarlo quedaba abierto al invitado. Ahora el rango es la política y
el default de cada dependency es explícito.

``require_auth``, ``require_group``, ``require_admin``, ``GroupContext`` etc.
son el contrato que importan ~30 archivos de todo el backend — no se mueven
de sitio en el sentido de que ``app.api.routes.auth`` sigue exponiéndolos
(ver ``__init__.py``), solo se relocaliza el archivo que los define.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import Cookie, Header

from app.auth.user_lookup import get_user_by_identity
from app.config.session import LOGIN_MAX_FAILS, LOGIN_WINDOW
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter
from app.storage.groups import GroupStorage as _GroupStorage
from app.storage.guest import is_guest
from app.storage.sessions import SessionStorage as _SessionStorage
from app.storage.tokens import TokenStorage as _TokenStorage
from app.storage.tokens import parse_ts as _parse_ts
from app.utils.net import client_ip as _client_ip

_groups = _GroupStorage()
_tokens = _TokenStorage()
_sessions = _SessionStorage()

# Compartido entre login.py, pat_tokens.py y vscode_oauth.py a propósito: los
# tres son superficie de fuerza bruta de credenciales y comparten el mismo
# presupuesto de intentos, no uno cada uno — si cada archivo tuviera su
# propia instancia, un atacante triplicaría su cupo repartiendo intentos
# entre /login, /tokens y /vscode/exchange.
_login_limiter = RateLimiter(
    calls=LOGIN_MAX_FAILS,
    window=LOGIN_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-login",
)

# ── Caché de estado de autenticación para require_auth ────────────────────────
# Evita una consulta a BD en cada request autenticado. TTL de 60 s: una cuenta
# suspendida queda bloqueada en ≤60 s desde la acción del admin, sin sacrificar
# el rendimiento en producción.
# A2: también cachea password_changed_at para invalidar tokens emitidos antes
#     de un cambio de contraseña, sin una consulta a BD por request.
_ACTIVE_CACHE_TTL = 60  # segundos
_ACTIVE_CACHE_MAX = 5_000  # entradas máximas antes de eviction
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

    from app.auth.passwords import decode_claims

    if ga_token:
        claims = decode_claims(ga_token)
        if not claims:
            raise APIError(401, "invalid_token", "Token inválido o expirado")
        await _assert_session_live(claims.session_id)
        return claims.username, claims.iat, claims.group_id

    raise APIError(401, "not_authenticated", "No autenticado")


async def _assert_session_live(session_id: Optional[str]) -> None:
    """La sesión del token sigue abierta, o 401.

    Una consulta por request autenticado con cookie, deliberadamente sin caché:
    cachear el estado revocado devolvería el retraso que esto viene a quitar —
    «he cerrado sesión» tiene que ser inmediato, y con varios workers una caché
    por proceso lo haría inmediato solo en el que atendió el logout.

    `session_id` a None son los tokens emitidos antes de que la tabla existiera.
    Se aceptan: están firmados y son auténticos, y rechazarlos habría echado de
    golpe a todos los usuarios con sesión abierta en el despliegue. La ventana
    se cierra sola —esos tokens caducan— y a partir de ahí esta rama se puede
    convertir en un 401 (`token_sin_sesion`).
    """
    if session_id is None:
        return
    if not await _sessions.is_live(session_id):
        raise APIError(
            401, "session_revoked", "Sesión cerrada o revocada. Vuelve a entrar."
        )


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
