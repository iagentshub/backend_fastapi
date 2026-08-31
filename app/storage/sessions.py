"""SessionStorage — sesiones de navegador revocables.

El access token (`ga_token`) lleva el id de la sesión en su claim `sid` y no
autoriza nada por sí solo: `_identify` mira esta tabla en cada request. Eso es
lo que convierte «cerrar sesión» en algo real — antes se borraban las cookies y
el JWT seguía siendo válido hasta agotar su `exp`.

Del refresh token solo se guarda su SHA-256, igual que en
`personal_access_tokens` y por lo mismo: quien lea la tabla no puede renovar una
sesión con lo que encuentre. SHA-256 y no bcrypt porque son 256 bits de
`secrets`, no una contraseña adivinable, y el hash rápido permite resolverlo con
el índice UNIQUE en O(1).

Ver docs/adr/008-sesiones-revocables.md.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config.session import REFRESH_EXPIRE_HOURS
from app.sql import sql
from app.storage.db import open_db
from app.storage.tokens import parse_ts
from app.utils import now_iso as _now
from app.utils.generators import generate_id

# Prefijo visible del refresh token: lo hace reconocible en un log o en una
# captura y permite descartar de entrada lo que no lo sea, sin consultar la BD.
REFRESH_PREFIX = "iar_"

# Granularidad con la que se refresca `last_seen_at`.
# ponytail: un write por request autenticado sería peor que la consulta que ya
# hacemos para validar la sesión. 5 minutos basta para «última actividad» en la
# pantalla de sesiones; bajar si hiciera falta auditoría fina.
_LAST_SEEN_GRANULARITY = timedelta(minutes=5)

# Motivos de revocación. Se guardan para que la pantalla de sesiones y los logs
# puedan distinguir un logout de una expulsión por robo detectado.
REASON_LOGOUT = "logout"
REASON_LOGOUT_ALL = "logout_all"
REASON_MANUAL = "revoked_by_user"
REASON_PASSWORD = "password_changed"
REASON_ACCOUNT_DISABLED = "account_disabled"
REASON_ROLE_DOWNGRADED = "role_downgraded"
REASON_REFRESH_REUSE = "refresh_reuse"


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_refresh() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


def _expires_in(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _public(row: Any, current_id: Optional[str] = None) -> Dict[str, Any]:
    """Proyección segura de una fila: nunca incluye los hashes del refresh."""
    data = dict(row)
    return {
        "id": data["id"],
        "created_at": data.get("created_at"),
        "last_seen_at": data.get("last_seen_at"),
        "expires_at": data.get("expires_at"),
        "ip": data.get("ip"),
        "user_agent": data.get("user_agent"),
        "current": data["id"] == current_id,
    }


class RefreshReuse(Exception):
    """El refresh presentado ya había sido rotado: dos clientes lo tienen.

    No se distingue quién es el legítimo, así que la sesión entera cae. Es la
    respuesta estándar a la detección de reuso y el motivo por el que la
    rotación guarda `prev_refresh_hash`.
    """


class SessionStorage:
    async def open(
        self,
        user_id: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Abre una sesión. Devuelve (session_id, refresh_token_en_claro).

        El refresh en claro no se persiste y no se puede recuperar después.
        """
        session_id = generate_id(16)
        refresh = _new_refresh()
        now = _now()
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:insert_session"),
                    (
                        session_id,
                        user_id,
                        hash_refresh(refresh),
                        now,
                        now,
                        _expires_in(REFRESH_EXPIRE_HOURS),
                        ip,
                        (user_agent or "")[:255] or None,
                    ),
                )
        return session_id, refresh

    async def is_live(self, session_id: str) -> bool:
        """¿La sesión existe, no está revocada y no ha caducado?

        Se llama en cada request autenticado con cookie. Refresca `last_seen_at`
        como mucho una vez cada `_LAST_SEEN_GRANULARITY`.
        """
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/sessions:get_session"), (session_id,)
            )
            if row is None:
                return False
            data = dict(row)
            if data.get("revoked_at"):
                return False
            now = datetime.now(timezone.utc)
            expires_at = parse_ts(data.get("expires_at"))
            if expires_at and expires_at <= now:
                return False
            last_seen = parse_ts(data.get("last_seen_at"))
            if last_seen is None or now - last_seen >= _LAST_SEEN_GRANULARITY:
                async with conn.transaction():
                    await conn.execute(
                        sql("queries/sessions:touch_session"),
                        (now.isoformat(), session_id),
                    )
            return True

    async def rotate(self, refresh_token: str) -> Optional[Tuple[str, str, str]]:
        """Canjea un refresh por otro. Devuelve (session_id, user_id, refresh).

        None si el token no corresponde a ninguna sesión utilizable. Lanza
        `RefreshReuse` —tras revocar la sesión— si el token es uno ya rotado.

        La rotación mueve la caducidad hacia delante: una sesión en uso no
        expira a las `REFRESH_EXPIRE_HOURS` del login, sino de la última
        actividad. Sin eso, un access corto obligaría a volver a entrar cada
        pocas horas aunque el usuario no hubiera dejado de trabajar.
        """
        if not refresh_token.startswith(REFRESH_PREFIX):
            return None
        token_hash = hash_refresh(refresh_token)
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/sessions:session_by_refresh"), (token_hash,)
            )
            if row is None:
                reused = await conn.fetchone(
                    sql("queries/sessions:session_by_prev_refresh"), (token_hash,)
                )
                if reused is None:
                    return None
                await self.revoke(dict(reused)["id"], REASON_REFRESH_REUSE)
                raise RefreshReuse(dict(reused)["id"])

            data = dict(row)
            if data.get("revoked_at"):
                return None
            expires_at = parse_ts(data.get("expires_at"))
            if expires_at and expires_at <= datetime.now(timezone.utc):
                return None

            nuevo = _new_refresh()
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:rotate_refresh"),
                    (
                        hash_refresh(nuevo),
                        _now(),
                        _expires_in(REFRESH_EXPIRE_HOURS),
                        data["id"],
                    ),
                )
            return data["id"], data["user_id"], nuevo

    async def list_for_user(
        self, user_id: str, current_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        async with open_db() as conn:
            rows = await conn.fetchall(
                sql("queries/sessions:list_sessions_of_user"),
                (user_id, _now()),
            )
        return [_public(r, current_id) for r in rows]

    async def revoke(self, session_id: str, reason: str) -> bool:
        """Revoca una sesión. False si no existe o ya estaba revocada."""
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:revoke_session"),
                    (_now(), reason, session_id),
                )
            row = await conn.fetchone(
                sql("queries/sessions:get_session"), (session_id,)
            )
        return row is not None

    async def revoke_owned(self, session_id: str, user_id: str, reason: str) -> bool:
        """Revoca una sesión comprobando antes que es de ese usuario.

        El `user_id` evita que alguien cierre sesiones ajenas conociendo el id.
        `AsyncConn.execute()` no expone rowcount, así que se mira la fila antes.
        """
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/sessions:active_session_of_user"),
                (session_id, user_id),
            )
            if row is None:
                return False
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:revoke_session"),
                    (_now(), reason, session_id),
                )
            return True

    async def revoke_all(self, user_id: str, reason: str) -> None:
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:revoke_sessions_of_user"),
                    (_now(), reason, user_id),
                )

    async def revoke_others(self, user_id: str, keep_id: str, reason: str) -> None:
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:revoke_other_sessions_of_user"),
                    (_now(), reason, user_id, keep_id),
                )

    async def purge_expired(self) -> None:
        """Borra las sesiones caducadas.

        Oportunista, colgada del login: no hay planificador en el backend y una
        tarea de fondo para esto sería más maquinaria que problema. Una sesión
        caducada no autoriza nada aunque siga en la tabla — la purga es higiene
        de tamaño, no de seguridad.
        """
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/sessions:purge_expired_sessions"), (_now(),)
                )
