"""Passwords, JWT y hashing de tokens de un solo uso."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import bcrypt as _bcrypt
import jwt
from jwt import PyJWTError

import app.config.session as _session
from app.config.data import SETTINGS_FILE
from app.config.session import (
    JWT_ALGORITHM,
    JWT_AUDIENCE,
    JWT_EXPIRE_HOURS,
    JWT_ISSUER,
    JWT_SECRET_ENV,
    JWT_UNSAFE_SECRETS,
)

# ── Settings ───────────────────────────────────────────────────────────────────


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


# RFC 7518 §3.2: para HS256 la clave debe tener al menos el tamaño del hash,
# 32 bytes. Por debajo, la firma es más débil de lo que su nombre promete.
_MIN_SECRET_BYTES = 32

# El aviso se emite una vez por proceso: _secret() se llama en cada petición.
_secreto_corto_avisado = False


def _secret() -> str:
    global _secreto_corto_avisado
    env_val = os.environ.get(JWT_SECRET_ENV)
    secret = env_val or _load_settings().get("jwt_secret", "")
    if secret in JWT_UNSAFE_SECRETS:
        raise RuntimeError(
            f"JWT secret no configurado. "
            f"Define la variable de entorno {JWT_SECRET_ENV} o establece "
            f"'jwt_secret' en data/settings.json antes de arrancar."
        )
    if len(secret.encode("utf-8")) < _MIN_SECRET_BYTES and not _secreto_corto_avisado:
        # Avisa, no aborta: un secreto corto que ya está en uso firma todas las
        # sesiones vivas y cifra las API keys guardadas, así que fallar aquí
        # dejaría la instalación inarrancable en vez de mejorarla. python-jose
        # no decía nada de esto; PyJWT sí, y por eso se ve ahora.
        _secreto_corto_avisado = True
        from app.utils import flog

        flog.warning(
            f"[auth] {JWT_SECRET_ENV} tiene {len(secret.encode('utf-8'))} bytes; "
            f"RFC 7518 recomienda al menos {_MIN_SECRET_BYTES} para HS256. "
            "Genera uno más largo y rota cuando puedas."
        )
    return secret


# ── Token helpers ─────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    """SHA-256 hex digest — lo que se guarda en BD; el token raw va al usuario."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Password helpers ───────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    # BCRYPT_ROUNDS se lee del módulo, no por valor: la suite lo baja vía
    # GAIA_BCRYPT_ROUNDS y el default sigue siendo 12 (ver config/session.py).
    return _bcrypt.hashpw(
        plain.encode("utf-8"), _bcrypt.gensalt(rounds=_session.BCRYPT_ROUNDS)
    ).decode("utf-8")


async def hash_password_async(plain: str) -> str:
    """Calcula bcrypt sin bloquear el event loop de FastAPI."""
    return await asyncio.to_thread(hash_password, plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Hash malformado o vacío (usuario sin contraseña local: los de GitHub
        # tienen password_hash NULL). Es un "no coincide", no un error, y por eso
        # NO se registra: este camino se recorre en cada intento de login fallido
        # y logearlo convertiría un ataque de fuerza bruta en ruido en los logs.
        return False


async def verify_password_async(plain: str, hashed: str) -> bool:
    """Wrapper no-bloqueante — delega bcrypt al thread pool."""
    return await asyncio.to_thread(verify_password, plain, hashed)


# ── JWT ────────────────────────────────────────────────────────────────────────


def create_token(username: str, group_id: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "gid": group_id or username,  # group personal = username
        "iat": now,  # A2: issued-at para invalidación por cambio de contraseña
        "exp": expire,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def derive_csrf_token(ga_token: str) -> str:
    """Token anti-CSRF de una sesión: HMAC del JWT con el secreto de firma.

    Derivado en vez de aleatorio-y-guardado, por tres razones que se pagan
    solas: no hay estado que expirar (muere con el JWT del que sale), el
    servidor puede reemitir la cookie en cualquier respuesta sin consultar
    nada, y —lo que importa— resiste el *cookie tossing*.

    Ese último es el punto ciego del double-submit clásico: un subdominio
    comprometido puede sobreescribir la cookie del token Y mandar el mismo
    valor en la cabecera, y un servidor que se limite a comparar las dos lo da
    por bueno. Aquí se recalcula desde el `ga_token` de la víctima, así que un
    valor inyectado no cuadra.

    El token no es una credencial: quien lo tenga sin la cookie de sesión no
    puede hacer nada con él, y del HMAC no se vuelve al JWT.
    """
    digest = hmac.new(
        _secret().encode("utf-8"), ga_token.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def csrf_token_matches(ga_token: str, candidate: str) -> bool:
    """Comparación en tiempo constante del token recibido con el esperado."""
    if not ga_token or not candidate:
        return False
    return hmac.compare_digest(derive_csrf_token(ga_token), candidate)


def _claims(token: str) -> Optional[dict]:
    """Payload de un token verificado, o None si no es de fiar.

    Un único punto para verificar firma, expiración y procedencia; antes las
    cuatro funciones de abajo repetían el mismo `try/except` y cada una podía
    quedarse atrás al cambiar las reglas.

    ``iss``/``aud`` se validan solo cuando el token los trae. Se empezaron a
    emitir con la migración a PyJWT, así que exigirlos habría invalidado de
    golpe todas las sesiones abiertas. Pasadas ``JWT_EXPIRE_HOURS`` desde el
    despliegue no queda ningún token sin ellos y se pueden volver obligatorios
    (pasando ``issuer=``/``audience=`` a ``jwt.decode`` y quitando estas dos
    comprobaciones).
    """
    try:
        # verify_aud=False porque la comprobación se hace abajo, tolerando su
        # ausencia; PyJWT, si se le pasa `audience`, exige que el claim exista.
        data = jwt.decode(
            token,
            _secret(),
            algorithms=[JWT_ALGORITHM],
            options={"verify_aud": False},
        )
    except PyJWTError:
        return None
    emisor = data.get("iss")
    if emisor is not None and emisor != JWT_ISSUER:
        return None
    audiencia = data.get("aud")
    if audiencia is not None and audiencia != JWT_AUDIENCE:
        return None
    return data


def _iat_epoch(data: dict) -> Optional[float]:
    """`iat` como epoch float. PyJWT lo devuelve numérico; jose daba datetime."""
    iat = data.get("iat")
    if isinstance(iat, datetime):
        return iat.timestamp()
    return float(iat) if iat is not None else None


class TokenClaims(NamedTuple):
    """Lo que un token dice de quien lo presenta, ya verificado.

    Sustituye a las cuatro `decode_*` que devolvían tuplas de distinto tamaño
    (username; +iat; +group_id; los tres) y repetían el mismo cuerpo. Dos de
    ellas no tenían un solo llamador fuera de los tests.
    """

    username: str
    group_id: str
    iat: Optional[float]


def decode_claims(token: str) -> Optional[TokenClaims]:
    """Claims verificados del token, o None si no es de fiar.

    `group_id` cae al nombre de usuario cuando el token no lo trae: el group
    personal de cada usuario es él mismo.
    """
    data = _claims(token)
    if not data:
        return None
    username = data.get("sub")
    if not username:
        return None
    legacy_group_claim = "w" + "id"
    group_id = data.get("gid") or data.get(legacy_group_claim) or username
    return TokenClaims(username, group_id, _iat_epoch(data))


def decode_token(token: str) -> Optional[str]:
    """El nombre de usuario, o None si el token es inválido o ha expirado."""
    claims = decode_claims(token)
    return claims.username if claims else None
