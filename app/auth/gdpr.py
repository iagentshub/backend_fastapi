"""Borrado de cuenta bajo RGPD: programación, cancelación y purga dura."""

from __future__ import annotations

import asyncio as _asyncio
import json as _json
import secrets
import shutil as _shutil
from datetime import datetime, timedelta, timezone

from app.auth.passwords import _hash_token
from app.auth.user_lookup import get_user_by_identity
from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.services.email import send_deletion_scheduled_email
from app.sql import sql
from app.storage.db import open_db
from app.utils import flog
from app.utils.generators import generate_date
from app.utils.validation import normalize_username


async def get_owned_groups(username: str) -> list:
    """Return groups where the user is owner (created_by)."""
    user = await get_user_by_identity(username)
    user_id = user["id"] if user else username
    async with open_db() as conn:
        rows = await conn.fetchall(
            sql("queries/gdpr:groups_created_by"),
            (user_id,),
        )
        return [dict(r) for r in rows]


async def schedule_user_deletion(username: str, lang: str = "es") -> str:
    """Schedule account deletion 30 days from now. Returns cancellation token (raw).

    `lang` lo resuelve el handler con get_locale(): aquí ya no serviría, porque
    el envío se encola en un ThreadPoolExecutor sin el ContextVar.
    """
    token = secrets.token_urlsafe(32)
    deletion_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    async with open_db() as conn:
        await conn.execute(
            sql("queries/gdpr:request_deletion"),
            (deletion_at, _hash_token(token), username, normalize_username(username)),
        )
        await conn.commit()
    user = await get_user_by_identity(username)
    if user:
        send_deletion_scheduled_email(user["email"], token, deletion_at, lang=lang)
    flog.info(f"[gdpr] Borrado programado para {username} el {deletion_at}")
    return token


async def cancel_user_deletion(token: str) -> bool:
    """Cancel a scheduled deletion via token. Returns True if found and cancelled."""
    async with open_db() as conn:
        if not await conn.fetchone(
            sql("queries/gdpr:deletion_token_exists"), (_hash_token(token),)
        ):
            return False
        await conn.execute(
            sql("queries/gdpr:cancel_deletion"),
            (_hash_token(token),),
        )
        await conn.commit()
        return True


def _purge_user_files(username: str) -> None:
    """Borra ficheros del usuario del filesystem. Síncrono — llamar desde asyncio.to_thread."""
    for base_dir in (AGENTS_DIR, SKILLS_DIR):
        for scope_dir in (base_dir / "private", base_dir / "public"):
            if not scope_dir.exists():
                continue
            for item_dir in scope_dir.iterdir():
                cfg = item_dir / "config.json"
                if not cfg.exists():
                    continue
                try:
                    if _json.loads(cfg.read_text()).get("owner_id") == username:
                        _shutil.rmtree(item_dir, ignore_errors=True)
                except Exception as exc:  # noqa: BLE001
                    # Esto es un borrado por GDPR: un config.json ilegible dejaba
                    # atrás el agente de un usuario que pidió que se le borrara,
                    # y sin rastro de que había pasado. Se sigue con el resto de
                    # directorios, pero queda constancia de cuál se ha quedado.
                    flog.error(f"[gdpr] No se pudo purgar {item_dir}: {exc}")


async def purge_user_data(username: str) -> None:
    """Hard-delete all user data from DB (cascade) and filesystem."""
    user = await get_user_by_identity(username)
    if not user:
        return
    user_id = user["id"]
    public_username = user["username"]

    try:
        async with open_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql("queries/gdpr:delete_messages"),
                    (user_id,),
                )
                await conn.execute(
                    sql("queries/gdpr:delete_conversations"), (user_id,)
                )
                await conn.execute(sql("queries/gdpr:delete_agents"), (user_id,))
                await conn.execute(sql("queries/gdpr:delete_skills"), (user_id,))
                await conn.execute(
                    sql("queries/gdpr:delete_knowledge_items"), (user_id,)
                )
                await conn.execute(
                    sql("queries/gdpr:delete_connections"), (user_id,)
                )
                await conn.execute(
                    sql("queries/gdpr:delete_orchestration_bindings"),
                    (user_id, user_id),
                )
                await conn.execute(
                    sql("queries/gdpr:delete_orchestrations"), (user_id,)
                )
                await conn.execute(sql("queries/gdpr:delete_workflows"), (user_id,))
                await conn.execute(sql("queries/gdpr:delete_social"), (user_id,))
                await conn.execute(sql("queries/gdpr:delete_stars"), (user_id,))
                await conn.execute(
                    sql("queries/gdpr:delete_follows"),
                    (user_id, user_id),
                )
                await conn.execute(
                    sql("queries/gdpr:delete_token_daily"), (user_id,)
                )
                await conn.execute(
                    sql("queries/gdpr:delete_accounts"), (user_id,)
                )
                await conn.execute(
                    sql("queries/gdpr:delete_group_shares"),
                    (user_id,),
                )
                await conn.execute(
                    sql("queries/gdpr:delete_group_invitations"), (user_id,)
                )
                await conn.execute(
                    sql("queries/gdpr:delete_group_members"), (user_id,)
                )
                await conn.execute(
                    sql("queries/gdpr:delete_groups"), (user_id,)
                )
                await conn.execute(sql("queries/gdpr:delete_user"), (user_id,))
        flog.ok(f"[gdpr] BD purgada para {public_username}")
    except Exception as exc:
        flog.error(f"[gdpr] Error purgando BD de {username}: {exc}")
        raise

    await _asyncio.to_thread(_purge_user_files, user_id)
    flog.ok(f"[gdpr] Purga completa de {public_username}")


async def purge_expired_deletions() -> int:
    """Hard-delete accounts whose 30-day grace period has passed. Returns count."""
    now = generate_date()
    async with open_db() as conn:
        rows = await conn.fetchall(
            sql("queries/gdpr:pending_deletions"),
            (now,),
        )
    usernames = [r[0] for r in rows]

    for username in usernames:
        try:
            await purge_user_data(username)
        except Exception as exc:  # noqa: BLE001
            # Purga por lotes: que un usuario falle no puede dejar sin borrar
            # a los demás que también lo pidieron.
            flog.error(f"[gdpr] No se pudo purgar {username}: {exc}")

    return len(usernames)
