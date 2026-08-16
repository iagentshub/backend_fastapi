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
        # El invitado pierde su trabajo entre peticiones y el tope real es
        # workers × MAX_SESSIONS. Aviso, no arreglo.
        # Ver docs/adr/001-estado-en-memoria-con-multiples-workers.md
        from app.storage.guest import MAX_SESSIONS
        from app.utils import flog

        flog.warning(
            f"[guest] GAIA_WORKERS={workers} con sesiones de invitado en memoria: "
            "sin sticky sessions en el proxy el invitado perderá su trabajo entre "
            f"peticiones, y el tope real de sesiones es {workers * MAX_SESSIONS} "
            f"({MAX_SESSIONS} por worker), no {MAX_SESSIONS}."
        )

    if workers > 1:
        # Una sola vez en el proceso maestro: si no, los workers competirían por
        # crear las mismas tablas/índices contra una DB recién creada.
        # Ver docs/adr/001-estado-en-memoria-con-multiples-workers.md
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
