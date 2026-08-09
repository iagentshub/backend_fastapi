"""GAIA Backend — Punto de entrada."""
from __future__ import annotations

import asyncio
import os

import uvicorn

from app.config import data as _cfg
from app.config.server import HOST, PORT, RELOAD, WORKERS
from app.storage.db import migrate_schema


def main() -> None:
    workers = WORKERS

    if workers > 1 and os.getenv("GAIA_GUEST_DEMO", "1") == "1":
        # Las sesiones de invitado viven en un dict de proceso
        # (app/storage/guest.py), no en la BD que sí comparten los workers. Sin
        # afinidad de sesión en el proxy, las peticiones del mismo invitado
        # caen en workers distintos: `get_session` crea una vacía y el agente
        # que acababa de crear desaparece. El tope MAX_SESSIONS también es por
        # proceso, así que el límite real es workers × MAX_SESSIONS.
        #
        # El fallo era silencioso y solo se veía en producción; al menos ahora
        # queda dicho en el arranque. La solución de fondo —persistir la sesión
        # de invitado en la BD— cambia su contrato de demo efímera y obliga a
        # contemplarla en el borrado RGPD: es una decisión de producto.
        from app.storage.guest import MAX_SESSIONS
        from app.utils import flog

        flog.warning(
            f"[guest] GAIA_WORKERS={workers} con sesiones de invitado en memoria: "
            "sin sticky sessions en el proxy el invitado perderá su trabajo entre "
            f"peticiones, y el tope real de sesiones es {workers * MAX_SESSIONS} "
            f"({MAX_SESSIONS} por worker), no {MAX_SESSIONS}."
        )

    if workers > 1:
        # Migrar el esquema una sola vez en el proceso maestro, antes de que
        # uvicorn lance los workers: cada worker es un proceso independiente
        # que ejecuta su propio lifespan, y sin esto competirían por crear
        # las mismas tablas/índices en paralelo contra una DB recién creada.
        asyncio.run(migrate_schema(_cfg.DB_FILE))
        os.environ["GAIA_SCHEMA_MIGRATED"] = "1"

    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        reload=RELOAD,
        workers=workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
