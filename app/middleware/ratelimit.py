"""Rate limiter simple basado en ventana deslizante, sin dependencias externas."""
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )


class RateLimiter:
    """Dependencia FastAPI: limita N llamadas por ventana de tiempo por IP.

    Uso:
        _limiter = RateLimiter(calls=30, window=60)

        @router.post("/endpoint")
        async def handler(request: Request, _: None = Depends(_limiter)):
            ...
    """

    def __init__(
        self,
        calls: int,
        window: int,
        key_func: Callable[[Request], str] = _client_ip,
    ) -> None:
        self._calls = calls
        self._window = window
        self._key_func = key_func
        self._data: dict[str, list[float]] = defaultdict(list)

    async def __call__(self, request: Request) -> None:
        key = self._key_func(request)
        now = time.monotonic()
        events = [t for t in self._data[key] if now - t < self._window]
        self._data[key] = events
        if len(events) >= self._calls:
            raise HTTPException(
                status_code=429,
                detail="Demasiadas solicitudes. Espera un momento.",
            )
        self._data[key].append(now)
