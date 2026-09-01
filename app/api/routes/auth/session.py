"""Registro, login, sesión y revocación.

Una sesión es tres cookies y una fila revocable: quien abre sesión pasa por
`app.auth.sessions.open_session`, que escribe esa fila. Ver
`docs/adr/008-sesiones-revocables.md`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.routes.auth._shared import _public_base_url
from app.api.routes.auth.dependencies import (
    _login_limiter,
    require_session,
)
from app.auth.auth import (
    get_user_by_username,
    register_user_email,
    verify_email_token,
    verify_password_async,
)
from app.auth.cookies import clear_session_cookies, set_session_cookies
from app.auth.gdpr import purge_user_data
from app.auth.passwords import DUMMY_PASSWORD_HASH, create_token, decode_claims
from app.auth.sessions import open_session
from app.config.session import (
    RATE_GUEST_CALLS,
    RATE_GUEST_WINDOW,
    RATE_REFRESH_CALLS,
    RATE_REFRESH_WINDOW,
    REGISTER_MAX,
    REGISTER_WINDOW,
)
from app.errors import APIError
from app.middleware.locale import get_locale
from app.middleware.ratelimit import RateLimiter
from app.models.legal import LegalAcceptancePayload
from app.services.email import send_verification_email
from app.services.legal_consent import (
    legal_contract,
    public_legal_contract,
    validate_acceptance,
)
from app.services.platform_settings import (
    email_verify_enabled,
    registration_mode,
)
from app.storage.guest import is_guest
from app.storage.sessions import (
    REASON_LOGOUT,
    REASON_LOGOUT_ALL,
    REASON_MANUAL,
    RefreshReuse,
    SessionStorage,
)
from app.utils import flog
from app.utils.net import client_ip as _client_ip
from app.utils.validation import is_valid_email, is_valid_username, normalize_username

router = APIRouter()


_sessions = SessionStorage()


class RegisterBody(BaseModel):
    username: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=128)
    birth_date: str | None = None
    gender: str | None = None
    country: str | None = None
    phone: str | None = None
    legal_acceptance: LegalAcceptancePayload | None = None


class LoginBody(BaseModel):
    identifier: str | None = Field(default=None, max_length=254)
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=128)


_register_limiter = RateLimiter(
    calls=REGISTER_MAX,
    window=REGISTER_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-register",
)

_guest_limiter = RateLimiter(
    calls=RATE_GUEST_CALLS,
    window=RATE_GUEST_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-guest",
)

# El canje del refresh es superficie de fuerza bruta como el login: quien tenga
# un refresh robado y caducado, o quiera adivinar uno, lo intenta aquí. El cupo
# es más alto que el de login porque un cliente legítimo renueva de verdad —una
# vez cada ACCESS_EXPIRE_MINUTES, y varias pestañas pueden coincidir.
_refresh_limiter = RateLimiter(
    calls=RATE_REFRESH_CALLS,
    window=RATE_REFRESH_WINDOW,
    key_func=_client_ip,
    shared=True,
    name="auth-refresh",
)


@router.post("/register")
async def register(
    body: RegisterBody, request: Request, response: Response
) -> dict[str, Any]:
    # Se resuelve en cada petición, no al importar: el admin cambia el modo
    # desde el panel y hasta ahora eso no llegaba aquí (ver registration_mode).
    modo = registration_mode()
    if modo == "closed":
        raise APIError(403, "registration_disabled", "El registro está desactivado.")
    if modo == "invite":
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
    contract = legal_contract()
    legal_documents = None
    if contract["required"] and body.legal_acceptance is None:
        raise APIError(
            428,
            "legal_acceptance_required",
            "Debes aceptar la versión vigente de los términos y la privacidad.",
            extra={"legal": public_legal_contract()},
        )
    if body.legal_acceptance is not None:
        legal_documents = validate_acceptance(body.legal_acceptance)

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

    # Una sola lectura para toda la petición: quien genera el token y quien
    # decide si sale el correo tienen que estar de acuerdo.
    verificar = email_verify_enabled()
    try:
        username, verify_token = await register_user_email(
            username,
            email,
            password,
            birth_date=birth_date,
            gender=gender,
            country=country,
            phone=phone,
            verify_email=verificar,
            legal_acceptances=legal_documents,
        )
    except ValueError as exc:
        resource = "username" if "usuario" in str(exc).lower() else "email"
        raise APIError(
            409, "already_exists", str(exc), extra={"resource": resource}
        ) from exc

    if verificar and verify_token:
        base_url = _public_base_url(request)
        # El idioma se resuelve AQUÍ: get_locale() es un ContextVar y el
        # envío se encola en un ThreadPoolExecutor donde ya no existe.
        send_verification_email(email, verify_token, base_url, lang=get_locale())
        return {"ok": True, "email": email, "pending_verification": True}

    user = await get_user_by_username(username)
    if not user:
        raise APIError(500, "user_creation_failed", "No se pudo crear la sesión")
    await open_session(response, user["id"], request)
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
        flog.audit(
            "auth.login.rejected",
            outcome="DENIED",
            details={"reason": "missing_credentials"},
            summary="[login] Rechazado: faltan credenciales",
            ip=_ip,
            username="-",
        )
        raise APIError(
            400, "missing_credentials", "Usuario o email y contraseña requeridos"
        )

    from app.auth.auth import get_user_by_login

    user = await get_user_by_login(identifier)
    if not user or not user.get("password_hash"):
        # No salir antes de bcrypt: esa diferencia temporal revelaría si el
        # identificador existe y si la cuenta tiene contraseña local u OAuth.
        await verify_password_async(password, DUMMY_PASSWORD_HASH)
        flog.audit(
            "auth.login.rejected",
            outcome="DENIED",
            details={"reason": "invalid_credentials"},
            summary="[login] Rechazado: credenciales incorrectas",
            ip=_ip,
            username="-",
        )
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    if not await verify_password_async(password, user["password_hash"]):
        flog.audit(
            "auth.login.rejected",
            resource_type="user",
            resource_id=user["username"],
            outcome="DENIED",
            details={"reason": "invalid_credentials"},
            summary="[login] Rechazado: credenciales incorrectas",
            ip=_ip,
            username="-",
        )
        raise APIError(401, "invalid_credentials", "Credenciales incorrectas")
    # Estas comprobaciones van deliberadamente después de bcrypt: solo quien
    # conoce la contraseña puede descubrir el estado interno de la cuenta.
    if not user.get("is_active", 1):
        flog.audit(
            "auth.login.rejected",
            resource_type="user",
            resource_id=user["username"],
            outcome="DENIED",
            details={"reason": "account_disabled"},
            summary="[login] Rechazado: cuenta desactivada",
            ip=_ip,
            username="-",
        )
        raise APIError(403, "account_disabled", "Cuenta desactivada")
    if email_verify_enabled() and not user.get("is_verified", 1):
        flog.audit(
            "auth.login.rejected",
            resource_type="user",
            resource_id=user["username"],
            outcome="DENIED",
            details={"reason": "email_not_verified"},
            summary="[login] Rechazado: cuenta pendiente de verificación",
            ip=_ip,
            username="-",
        )
        raise APIError(
            403,
            "email_not_verified",
            "Cuenta pendiente de verificación. Revisa tu correo.",
        )

    await _sessions.purge_expired()
    await open_session(response, user["id"], request)
    flog.audit(
        "auth.login.succeeded",
        resource_type="user",
        resource_id=user["username"],
        summary=f"[login] Sesión iniciada por {user['username']}",
        ip=_ip,
        username=user["username"],
    )
    return {"ok": True, "username": user["username"]}


@router.get("/verify")
async def verify_email(
    token: str, request: Request, response: Response
) -> dict[str, Any]:
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
    await open_session(response, user["id"], request)
    return {"ok": True, "username": username}


async def _cerrar_invitado_previo(ga_token: str | None) -> None:
    """Un invitado por navegador: el alta cierra y borra el anterior.

    Sin esto, pulsar dos veces «entrar como invitado» —recargar, dudar, volver
    a pulsar— deja tantos invitados como pulsaciones, y ninguno se purga: el
    barrido se lleva a los que no tienen **sesión viva**, y esos la tienen, solo
    que ya no la usa nadie. Medido: tres pulsaciones, tres filas, cero
    purgadas; el cupo se consume hasta que caduquen sus sesiones y la demo
    responde 503 estando casi vacía.

    Solo actúa sobre invitados: un usuario registrado que pulse el botón no
    pierde su sesión ni, mucho menos, su cuenta.
    """
    if not ga_token:
        return
    claims = decode_claims(ga_token, allow_expired=True)
    if not claims or not is_guest(claims.username):
        return
    if claims.session_id:
        await _sessions.revoke(claims.session_id, REASON_LOGOUT)
    await purge_user_data(claims.username)


@router.post("/guest")
async def guest_login(
    request: Request,
    response: Response,
    ga_token: str | None = Cookie(default=None),
    _rl: None = Depends(_guest_limiter),
) -> dict[str, Any]:
    from app.storage.guest import create_guest_user

    await _cerrar_invitado_previo(ga_token)
    guest_id = await create_guest_user()
    await open_session(response, guest_id, request)
    # El alta es la única petición del invitado que el log no puede atribuirle
    # solo: `_username_for_log` lee la cookie de la petición, y aquí la cookie
    # se emite en la respuesta, así que esa línea sale como anónima. Sin esta,
    # el registro tiene la IP pero no dice qué invitado nació de ella — y el
    # invitado se borra al salir, con lo que el log es lo único que queda.
    flog.ok(
        f"[guest] alta usuario={guest_id}",
        ip=_client_ip(request),
        username=guest_id,
    )
    return {"ok": True, "username": guest_id}


@router.post("/logout")
async def logout(
    response: Response,
    ga_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Cierra la sesión de verdad: revoca la fila, luego borra las cookies.

    Sin `require_auth` a propósito. Cerrar sesión tiene que funcionar también
    cuando el access ya ha caducado, que es justo cuando el usuario más lo
    intenta; exigir una credencial viva dejaría la fila abierta y el refresh
    utilizable. Quien no traiga cookie no revoca nada y se le contesta igual.
    """
    if ga_token:
        claims = decode_claims(ga_token, allow_expired=True)
        if claims and claims.session_id:
            await _sessions.revoke(claims.session_id, REASON_LOGOUT)
        # El invitado no sobrevive a su sesión: cerrarla borra su usuario y con
        # él todo lo que creó, con la misma rutina que el borrado RGPD. Va
        # después de revocar para que una purga que falle no deje la sesión
        # abierta, y antes de las cookies para que el cliente no se quede con
        # una credencial que ya no identifica a nadie.
        if claims and is_guest(claims.username):
            await purge_user_data(claims.username)
    clear_session_cookies(response)
    return {"ok": True}


@router.post("/refresh")
async def refresh_session(
    request: Request,
    response: Response,
    ga_token: str | None = Cookie(default=None),
    ga_refresh: str | None = Cookie(default=None),
    _rl: None = Depends(_refresh_limiter),
) -> dict[str, Any]:
    """Canjea el refresh por un access nuevo, rotando el refresh.

    El grupo activo se recupera del access caducado —lo único que se le pide, y
    `_resolve_group` revalida después la pertenencia—; sin eso, cada renovación
    devolvería al usuario a su espacio personal a media sesión.
    """
    if not ga_refresh:
        clear_session_cookies(response)
        raise APIError(401, "not_authenticated", "No autenticado")

    try:
        renovada = await _sessions.rotate(ga_refresh)
    except RefreshReuse:
        # Dos clientes con el mismo refresh: uno de los dos lo robó y no hay
        # forma de saber cuál. La sesión ya ha caído dentro de rotate().
        flog.warning(
            "[auth] refresh reutilizado: sesión revocada", ip=_client_ip(request)
        )
        clear_session_cookies(response)
        raise APIError(
            401, "session_revoked", "Sesión cerrada o revocada. Vuelve a entrar."
        ) from None

    if not renovada:
        clear_session_cookies(response)
        raise APIError(401, "invalid_token", "Token inválido o expirado")

    session_id, user_id, nuevo_refresh = renovada
    group_id: str | None = None
    if ga_token:
        claims = decode_claims(ga_token, allow_expired=True)
        if claims and claims.username == user_id:
            group_id = claims.group_id
    token = create_token(user_id, group_id=group_id, session_id=session_id)
    set_session_cookies(response, token, refresh=nuevo_refresh)
    return {"ok": True}


@router.get("/sessions")
async def list_sessions(
    user_id: str = Depends(require_session),
    ga_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Sesiones abiertas del usuario, con la actual marcada."""
    claims = decode_claims(ga_token) if ga_token else None
    actual = claims.session_id if claims else None
    return {"sessions": await _sessions.list_for_user(user_id, current_id=actual)}


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: str,
    user_id: str = Depends(require_session),
) -> dict[str, Any]:
    """Cierra una sesión concreta del usuario, incluida la propia."""
    if not await _sessions.revoke_owned(session_id, user_id, REASON_MANUAL):
        raise APIError(
            404, "not_found", "Sesión no encontrada", extra={"resource": "session"}
        )
    return {"ok": True}


@router.delete("/sessions")
async def revoke_other_sessions(
    response: Response,
    user_id: str = Depends(require_session),
    ga_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Cierra las demás sesiones del usuario y conserva la actual.

    Conservarla es lo que hace la acción usable: si cerrase también la propia,
    quien sospecha de un acceso ajeno tendría que volver a entrar justo cuando
    está intentando echar al otro.
    """
    claims = decode_claims(ga_token) if ga_token else None
    actual = claims.session_id if claims else None
    if actual:
        await _sessions.revoke_others(user_id, actual, REASON_LOGOUT_ALL)
    else:
        await _sessions.revoke_all(user_id, REASON_LOGOUT_ALL)
        clear_session_cookies(response)
    return {"ok": True}
