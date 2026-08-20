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

    # Aquí había un aviso: con varios workers el invitado perdía su trabajo
    # entre peticiones y el tope real era workers × MAX_SESSIONS. Ya no aplica
    # — la sesión de invitado es un usuario en la BD, que los workers comparten.
    # Ver docs/adr/012-el-invitado-es-un-usuario-efimero.md.
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
