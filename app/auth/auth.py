"""Auth: JWT, password hashing y gestión de usuarios."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.config.data import SETTINGS_FILE
from app.config.session import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET_ENV, JWT_UNSAFE_SECRETS

_USERS_PATH = SETTINGS_FILE.parent / "users.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _load_users() -> List[dict]:
    if _USERS_PATH.exists():
        return json.loads(_USERS_PATH.read_text(encoding="utf-8"))
    return []


def _save_users(users: List[dict]) -> None:
    _USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USERS_PATH.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")


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


def hash_password(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def register_user(username: str, password: str, email: str = "") -> None:
    """Crea un nuevo usuario local. Lanza ValueError si el nombre o email ya existe."""
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


def get_or_create_oauth_user(provider: str, sub: str, email: str, name: str) -> str:
    """Busca un usuario OAuth por (provider, sub). Si no existe lo crea. Devuelve el username."""
    users = _load_users()
    for u in users:
        if u.get("provider") == provider and u.get("provider_sub") == sub:
            return u["username"]
    # Puede que ya exista el email por otro motivo — vincular OAuth
    for u in users:
        if u.get("email") == email:
            u["provider"] = provider
            u["provider_sub"] = sub
            _save_users(users)
            return u["username"]
    # Usuario nuevo
    username = email
    users.append({
        "username": username,
        "email": email,
        "display_name": name,
        "provider": provider,
        "provider_sub": sub,
        "role": "standard",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_users(users)
    return username


def get_user_role(username: str) -> str:
    if username == "guest":
        return "guest"
    for user in _load_users():
        if user.get("username") == username:
            return user.get("role", "standard")
    return "standard"


def list_users() -> list:
    return [
        {k: v for k, v in u.items() if k not in ("password_hash", "provider_sub")}
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
