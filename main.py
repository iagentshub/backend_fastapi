"""GAIA Backend — Punto de entrada."""
from __future__ import annotations

import asyncio
import os
import uvicorn

from app.config import data as _cfg
from app.config.server import HOST, PORT, RELOAD
from app.storage.db import migrate_schema


def main() -> None:
    workers = 1 if RELOAD else int(os.getenv("GAIA_WORKERS", "4"))

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
