"""GAIA Backend — Punto de entrada."""
from __future__ import annotations

import uvicorn

from app.config.server import HOST, PORT, RELOAD


def main() -> None:
    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        reload=RELOAD,
        log_level="info",
    )


if __name__ == "__main__":
    main()
