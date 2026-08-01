"""TokenStorage — personal access tokens (PAT) para clientes no navegador.

El navegador usa la cookie JWT `ga_token`. Los clientes que no son un navegador
(la extensión de VS Code, scripts, CI) usan un PAT con `Authorization: Bearer`.

El token en claro se devuelve UNA sola vez, al crearlo. En BD solo vive su
SHA-256: si alguien lee la tabla, no puede autenticarse con lo que encuentre.

SHA-256 y no bcrypt a propósito: un PAT son 256 bits de entropía generados por
`secrets`, no una contraseña humana adivinable. No hay nada que forzar por
diccionario, y el hash rápido permite resolver el token con un índice en O(1)
en cada request — que es justo lo que bcrypt haría inviable.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.storage.db import open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id

# Prefijo visible del token. Permite reconocerlo de un vistazo y que los
# escáneres de secretos (GitHub, gitleaks) puedan detectar una fuga.
TOKEN_PREFIX = "iah_"

# Caracteres del token que se guardan en claro para poder identificar cada
# entrada en la lista sin revelar el secreto.
_VISIBLE = 12

# Valores admitidos en `expires_in_days`. None = sin caducidad.
VALID_EXPIRY_DAYS: frozenset[Optional[int]] = frozenset({30, 90, 180, None})
DEFAULT_EXPIRY_DAYS = 90

# Granularidad con la que se refresca `last_used_at`.
# ponytail: no escribimos en BD en cada request autenticado — sería un write por
# request, peor que la consulta que _get_user_auth_state() ya cachea para evitar.
# 5 minutos basta para "cuándo se usó por última vez"; bajar si hiciera falta
# auditoría fina.
_LAST_USED_GRANULARITY = timedelta(minutes=5)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """ISO-8601 → datetime aware en UTC. None si falta o está corrupto.

    Público: routes/auth.py lo reutiliza para contrastar credenciales contra
    password_changed_at, que se guarda en el mismo formato.
    """
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public(row: Any) -> Dict[str, Any]:
    """Proyección segura de una fila: nunca incluye token_hash."""
    data = dict(row)
    now = datetime.now(timezone.utc)
    expires_at = parse_ts(data.get("expires_at"))
    if data.get("revoked_at"):
        status = "revoked"
    elif expires_at and expires_at <= now:
        status = "expired"
    else:
        status = "active"
    return {
        "id": data["id"],
        "name": data["name"],
        "prefix": data["prefix"],
        "created_at": data["created_at"],
        "expires_at": data.get("expires_at"),
        "last_used_at": data.get("last_used_at"),
        "revoked_at": data.get("revoked_at"),
        "status": status,
    }


# ── Códigos de autorización de VS Code ────────────────────────────────────────
# El navegador (que ya tiene sesión) emite un código; la extensión lo canjea por
# un PAT. El código vive 60 s y solo sirve acompañado del `state` que la
# extensión generó al abrir el navegador: robar el código de la URI `vscode://`
# no basta para nada.
#
# En BD y no en un dict de proceso a propósito: uvicorn corre con GAIA_WORKERS
# workers (4 por defecto), así que el authorize y el exchange caen casi siempre
# en procesos distintos.

_CODE_TTL = timedelta(seconds=60)


async def create_auth_code(username: str, state: str) -> str:
    """Código de un solo uso para el flujo de login de la extensión."""
    code = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)

    async with open_db() as conn:
        async with conn.transaction():
            # Barrido perezoso: los caducados se van solos, sin cron.
            await conn.execute(
                "DELETE FROM vscode_auth_codes WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            await conn.execute(
                "INSERT INTO vscode_auth_codes (code_hash, username, state, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    hash_token(code),
                    username,
                    state,
                    (now + _CODE_TTL).isoformat(),
                ),
            )
    return code


async def consume_auth_code(code: str, state: str) -> Optional[str]:
    """Canjea el código. Devuelve el username, o None si no vale.

    Borra la fila pase lo que pase: un código presentado con el `state` que no
    toca es un intento de robo, y no merece un segundo intento.
    """
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT * FROM vscode_auth_codes WHERE code_hash = ?",
            (hash_token(code),),
        )
        if row is None:
            return None
        data = dict(row)

        # ponytail: SELECT + DELETE en transacción, como revoke(). Dos canjes
        # simultáneos del mismo código podrían colarse los dos y emitir dos PATs
        # al mismo usuario — inofensivo. Si hiciera falta atomicidad estricta:
        # DELETE ... RETURNING (SQLite ≥3.35 y PG lo soportan).
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM vscode_auth_codes WHERE code_hash = ?",
                (data["code_hash"],),
            )

        expires_at = parse_ts(data["expires_at"])
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            return None
        if not secrets.compare_digest(data["state"], state):
            return None

    return str(data["username"])


class TokenStorage:
    async def create(
        self,
        username: str,
        name: str,
        expires_in_days: Optional[int] = DEFAULT_EXPIRY_DAYS,
    ) -> Tuple[str, Dict[str, Any]]:
        """Crea un PAT. Devuelve (token_en_claro, metadatos_públicos).

        El token en claro no se persiste y no se puede recuperar después.
        """
        if expires_in_days not in VALID_EXPIRY_DAYS:
            raise ValueError("expires_in_days debe ser 30, 90, 180 o null")

        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = (
            (now + timedelta(days=expires_in_days)).isoformat()
            if expires_in_days is not None
            else None
        )
        row = {
            "id": generate_id(16),
            "username": username,
            "name": name,
            "token_hash": hash_token(token),
            "prefix": token[:_VISIBLE],
            "created_at": now.isoformat(),
            "expires_at": expires_at,
            "last_used_at": None,
            "revoked_at": None,
        }
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO personal_access_tokens "
                    "(id, username, name, token_hash, prefix, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["id"],
                        username,
                        name,
                        row["token_hash"],
                        row["prefix"],
                        row["created_at"],
                        expires_at,
                    ),
                )
        return token, _public(row)

    async def list_for_user(self, username: str) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                "SELECT * FROM personal_access_tokens WHERE username = ? "
                "ORDER BY created_at DESC",
                (username,),
            )
        return [_public(r) for r in rows]

    async def revoke(self, token_id: str, username: str) -> bool:
        """Revoca un token del usuario. Devuelve False si no existe, no es suyo
        o ya estaba revocado.

        El `username` en el WHERE evita que un usuario revoque tokens ajenos
        conociendo el id. AsyncConn.execute() no expone rowcount, así que
        comprobamos la fila antes de tocarla.
        """
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT id FROM personal_access_tokens "
                "WHERE id = ? AND username = ? AND revoked_at IS NULL",
                (token_id, username),
            )
            if row is None:
                return False
            async with conn.transaction():
                await conn.execute(
                    "UPDATE personal_access_tokens SET revoked_at = ? WHERE id = ?",
                    (_now(), token_id),
                )
            return True

    async def resolve(self, token: str) -> Optional[Dict[str, Any]]:
        """Token en claro → fila del PAT si es utilizable, si no None.

        Comprueba existencia, revocación y caducidad. NO comprueba el estado de
        la cuenta ni `password_changed_at`: de eso se encarga quien llama, que ya
        tiene esos datos cacheados.
        """
        if not token.startswith(TOKEN_PREFIX):
            return None

        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM personal_access_tokens WHERE token_hash = ?",
                (hash_token(token),),
            )
            if row is None:
                return None
            data = dict(row)

            if data.get("revoked_at"):
                return None

            now = datetime.now(timezone.utc)
            expires_at = parse_ts(data.get("expires_at"))
            if expires_at and expires_at <= now:
                return None

            last_used = parse_ts(data.get("last_used_at"))
            if last_used is None or now - last_used >= _LAST_USED_GRANULARITY:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE personal_access_tokens SET last_used_at = ? WHERE id = ?",
                        (now.isoformat(), data["id"]),
                    )

        return data
