"""NotificationStorage — las filas de la campana, sin más.

Solo SQL a propósito: el correo que acompaña a cada aviso lo encola
`app.services.notifications`, que es quien orquesta los dos canales. Si el
almacenamiento importara el servicio de email se invertirían las capas y esta
capa dejaría de poder probarse sin SMTP.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.sql import sql
from app.storage.db import open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id

# Cuántas trae el desplegable. El cliente pide la lista entera en cada sondeo y
# de ahí saca también el contador, así que este número es el tamaño del payload
# que viaja cada 60 segundos por sesión abierta.
LIMITE_POR_DEFECTO = 50


async def insert_notification(
    *, user_id: str, kind: str, data: Dict[str, Any]
) -> str:
    """Guarda el aviso y devuelve su id."""
    notification_id = generate_id(16)
    async with open_db() as conn:
        await conn.execute(
            sql("queries/notifications:insert"),
            (
                notification_id,
                user_id,
                kind,
                json.dumps(data, ensure_ascii=False),
                _now(),
            ),
        )
        await conn.commit()
    return notification_id


async def list_notifications(
    user_id: str, limit: int = LIMITE_POR_DEFECTO
) -> List[Dict[str, Any]]:
    """Los avisos del usuario, del más reciente al más antiguo."""
    async with open_db() as conn:
        rows = await conn.fetchall(
            sql("queries/notifications:list_recent"), (user_id, limit)
        )
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            # El blob se decodifica aquí y no en la ruta: el cliente recibe un
            # objeto, no una cadena con JSON dentro. Una fila corrupta no puede
            # tumbar el listado entero, así que cae a diccionario vacío.
            "data": _decodificar(r["data"]),
            "read": r["read_at"] is not None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def count_unread(user_id: str) -> int:
    async with open_db() as conn:
        return int(
            await conn.fetchval(sql("queries/notifications:count_unread"), (user_id,))
            or 0
        )


async def mark_read(user_id: str, notification_id: str) -> bool:
    """Marca uno. False si no existe, no es suyo o ya estaba leído."""
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/notifications:mark_read"),
            (_now(), notification_id, user_id),
        )
        await conn.commit()
        return row is not None


async def mark_all_read(user_id: str) -> None:
    async with open_db() as conn:
        await conn.execute(
            sql("queries/notifications:mark_all_read"), (_now(), user_id)
        )
        await conn.commit()


async def purge_old(*, dias_leidas: int, dias_sin_leer: int) -> int:
    """Borra los avisos vencidos. Devuelve cuántos.

    Dos ventanas y no una: una notificación leída ya hizo su trabajo, pero una
    sin leer es lo único que le queda al usuario de que aquello pasó —la
    invitación que la originó desaparece de `group_invitations` en cuanto se
    acepta—. Barrerlas con el mismo plazo trataría igual dos cosas distintas.
    """
    async with open_db() as conn:
        leidas = await conn.fetchall(
            sql("queries/notifications:purge_read"), (_hace(dias_leidas),)
        )
        sin_leer = await conn.fetchall(
            sql("queries/notifications:purge_unread"), (_hace(dias_sin_leer),)
        )
        await conn.commit()
    return len(leidas) + len(sin_leer)


def _hace(dias: int) -> str:
    """Marca de tiempo ISO de hace `dias`, comparable con `created_at`."""
    from datetime import datetime, timedelta, timezone

    momento = datetime.now(timezone.utc) - timedelta(days=dias)
    return momento.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _decodificar(raw: Any) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
