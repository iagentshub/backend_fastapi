"""A qué navegadores y dispositivos empujar los avisos de un usuario.

Solo SQL, como `notifications`: quién cifra y quién manda es cosa de
`app.services.push`.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.sql import sql
from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id


async def subscribe(
    *,
    user_id: str,
    endpoint: str,
    p256dh: str = "",
    auth: str = "",
    kind: str = "webpush",
    user_agent: str = "",
) -> None:
    """Da de alta el destino, o refresca el que ya existía.

    El navegador devuelve el mismo `endpoint` cada vez que la app se
    resuscribe, que es en cada arranque. Sin el upsert, una semana de uso
    normal dejaría decenas de filas idénticas y el usuario recibiría el mismo
    aviso una vez por cada una.
    """
    # Los dos identificadores van enteros y literales, no compuestos con un
    # f-string: `tests/storage/test_sql_en_ficheros.py` busca las secciones por
    # su forma en el código y una construida al vuelo la da por huérfana.
    parametros = (
        generate_id(16),
        user_id,
        kind,
        endpoint,
        p256dh,
        auth,
        user_agent[:300],
        _now(),
    )
    async with open_db() as conn:
        if IS_PG:
            await conn.execute(
                sql("queries/push_subscriptions:upsert_pg"), parametros
            )
        else:
            await conn.execute(
                sql("queries/push_subscriptions:upsert_sqlite"), parametros
            )
        await conn.commit()


async def list_for_user(user_id: str) -> List[Dict[str, Any]]:
    async with open_db() as conn:
        rows = await conn.fetchall(
            sql("queries/push_subscriptions:list_for_user"), (user_id,)
        )
    return [dict(r) for r in rows]


async def unsubscribe(endpoint: str) -> None:
    """Baja explícita, o retirada porque el servicio push la dio por muerta."""
    async with open_db() as conn:
        await conn.execute(
            sql("queries/push_subscriptions:delete_by_endpoint"), (endpoint,)
        )
        await conn.commit()


async def touch(subscription_id: str) -> None:
    async with open_db() as conn:
        await conn.execute(
            sql("queries/push_subscriptions:touch"), (_now(), subscription_id)
        )
        await conn.commit()


async def count_for_user(user_id: str) -> int:
    async with open_db() as conn:
        return int(
            await conn.fetchval(
                sql("queries/push_subscriptions:count_for_user"), (user_id,)
            )
            or 0
        )
