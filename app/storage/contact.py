"""ContactStorage — peticiones del formulario de contacto de la web pública.

Escribe lo que manda un visitante sin sesión, así que aquí no hay propietario
al que filtrar: quien lee es el admin y lee todo. Lo único que se guarda del
remitente además de lo que escribe es su IP, que es lo que permite atar un
envío abusivo al cupo que lo dejó pasar.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.sql import sql
from app.storage.db import open_db
from app.utils import now_iso as _now


async def save_contact_request(
    *,
    kind: str,
    name: str,
    email: str,
    message: str,
    ip: Optional[str] = None,
) -> None:
    """Guarda la petición. El aviso por correo va aparte y puede fallar."""
    async with open_db() as conn:
        await conn.execute(
            sql("queries/contact:insert_request"),
            (_now(), kind, name, email, message, ip),
        )
        await conn.commit()


async def list_contact_requests(limit: int = 100) -> List[Dict[str, Any]]:
    """Las últimas peticiones, de la más reciente a la más antigua."""
    async with open_db() as conn:
        rows = await conn.fetchall(sql("queries/contact:list_recent"), (limit,))
    return [
        {
            "id": r["id"],
            "created_at": r["created_at"],
            "type": r["kind"],
            "name": r["name"],
            "email": r["email"],
            "message": r["message"],
            "ip": r["ip"] or None,
        }
        for r in rows
    ]
