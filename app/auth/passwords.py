"""Passwords, JWT y hashing de tokens de un solo uso."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.config.data import SETTINGS_FILE
from app.config.session import (
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS,
    JWT_SECRET_ENV,
    JWT_UNSAFE_SECRETS,
)

# ── Settings ───────────────────────────────────────────────────────────────────


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _secret() -> str:
    env_val = os.environ.get(JWT_SECRET_ENV)
    secret = env_val or _load_settings().get("jwt_secret", "")
    if secret in JWT_UNSAFE_SECRETS:
        raise RuntimeError(
            f"JWT secret no configurado. "
            f"Define la variable de entorno {JWT_SECRET_ENV} o establece "
            f"'jwt_secret' en data/settings.json antes de arrancar."
        )
    return secret


# ── Token helpers ─────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    """SHA-256 hex digest — lo que se guarda en BD; el token raw va al usuario."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Password helpers ───────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


async def hash_password_async(plain: str) -> str:
    """Calcula bcrypt sin bloquear el event loop de FastAPI."""
    return await asyncio.to_thread(hash_password, plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
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
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """Return the username or None if the token is invalid/expired."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        return data.get("sub")
    except JWTError:
        return None


def decode_token_with_iat(token: str) -> tuple[Optional[str], Optional[float]]:
    """Return (username, iat_epoch) o (None, None) si el token es inválido.

    El campo ``iat`` (issued-at) se usa en ``require_auth`` para invalidar
    sesiones cuyo token fue emitido antes de un cambio de contraseña.
    """
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        iat = data.get("iat")
        # python-jose puede devolver iat como datetime o como int; normalizamos a float
        if isinstance(iat, datetime):
            iat = iat.timestamp()
        elif iat is not None:
            iat = float(iat)
        return data.get("sub"), iat
    except JWTError:
        return None, None


def decode_group_token(token: str) -> tuple[Optional[str], Optional[str]]:
    """Return (username, group_id). group_id defaults to username if not present."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        username = data.get("sub")
        legacy_group_claim = "w" + "id"
        group_id = data.get("gid") or data.get(legacy_group_claim) or username
        return username, group_id
    except JWTError:
        return None, None


def decode_group_token_full(
    token: str,
) -> tuple[Optional[str], Optional[str], Optional[float]]:
    """Return (username, group_id, iat_epoch).

    Versión extendida de ``decode_group_token`` que también extrae el campo
    ``iat`` (issued-at) necesario para invalidar sesiones tras cambio de
    contraseña (C1).
    """
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        username = data.get("sub")
        legacy_group_claim = "w" + "id"
        group_id = data.get("gid") or data.get(legacy_group_claim) or username
        iat = data.get("iat")
        if isinstance(iat, datetime):
            iat = iat.timestamp()
        elif iat is not None:
            iat = float(iat)
        return username, group_id, iat
    except JWTError:
        return None, None, None
