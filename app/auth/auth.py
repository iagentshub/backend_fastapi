"""Auth: JWT + password hashing (un solo usuario admin)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.config.data import SETTINGS_FILE
from app.config.jwt import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET_ENV, JWT_UNSAFE_SECRETS

_USERS_PATH = SETTINGS_FILE.parent / "users.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_settings(settings: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_users() -> List[dict]:
    if _USERS_PATH.exists():
        return json.loads(_USERS_PATH.read_text(encoding="utf-8"))
    return []


def _save_users(users: List[dict]) -> None:
    _USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USERS_PATH.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_admin_password_hashed() -> None:
    """Si admin_password_plain existe sin hash, lo hashea y elimina el plain."""
    settings = _load_settings()
    plain = settings.get("admin_password_plain")
    if plain and not settings.get("admin_password_hash"):
        settings["admin_password_hash"] = hash_password(plain)
        del settings["admin_password_plain"]
        _save_settings(settings)


def _secret() -> str:
    """Lee el secreto JWT desde la variable de entorno o desde settings.json.

    Lanza RuntimeError si el secreto no está configurado o es el valor por defecto
    inseguro, para evitar firmar JWTs con una clave predecible.
    """
    env_val = os.environ.get(JWT_SECRET_ENV)
    secret = env_val or _load_settings().get("jwt_secret", "")
    if secret in JWT_UNSAFE_SECRETS:
        raise RuntimeError(
            f"JWT secret no configurado. "
            f"Define la variable de entorno {JWT_SECRET_ENV} o establece "
            f"'jwt_secret' en data/settings.json antes de arrancar."
        )
    return secret


def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def authenticate(username: str, password: str) -> bool:
    settings = _load_settings()
    admin_user = settings.get("admin_username", "admin")
    if username == admin_user:
        stored_hash = settings.get("admin_password_hash", "")
        if not stored_hash:
            return password == settings.get("admin_password_plain", "admin")
        return verify_password(password, stored_hash)
    for user in _load_users():
        if user.get("username") == username:
            return verify_password(password, user.get("password_hash", ""))
    return False


def register_user(username: str, password: str, email: str = "") -> None:
    """Crea un nuevo usuario. Lanza ValueError si el nombre o email ya existe."""
    settings = _load_settings()
    if username == settings.get("admin_username", "admin"):
        raise ValueError("Nombre de usuario no disponible")
    users = _load_users()
    if any(u.get("username") == username for u in users):
        raise ValueError("El nombre de usuario ya está en uso")
    if email and any(u.get("email") == email for u in users):
        raise ValueError("El correo electrónico ya está registrado")
    users.append({
        "username": username,
        "email": email,
        "password_hash": hash_password(password),
        "role": "standard",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_users(users)


def get_user_role(username: str) -> str:
    settings = _load_settings()
    if username == settings.get("admin_username", "admin"):
        return "admin"
    for user in _load_users():
        if user.get("username") == username:
            return user.get("role", "standard")
    return "standard"


def list_users() -> list:
    return [
        {k: v for k, v in u.items() if k != "password_hash"}
        for u in _load_users()
    ]


def delete_user(username: str) -> bool:
    users = _load_users()
    new = [u for u in users if u.get("username") != username]
    if len(new) == len(users):
        return False
    _save_users(new)
    return True


def create_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    """Devuelve el username o None si el token es inválido/expirado."""
    try:
        data = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        return data.get("sub")
    except JWTError:
        return None
