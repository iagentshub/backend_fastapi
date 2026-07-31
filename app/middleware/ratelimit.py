"""Rate limiter simple basado en ventana deslizante, sin dependencias externas."""
import math
import time
from collections import OrderedDict, deque
from typing import Callable

from fastapi import Request

from app.config.session import RATE_MAX_IPS as _MAX_IPS
from app.errors import APIError
from app.utils.net import client_ip as _client_ip


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
        if calls <= 0 or window <= 0:
            raise ValueError("calls y window deben ser mayores que cero")
        self._calls = calls
        self._window = window
        self._key_func = key_func
        self._data: OrderedDict[str, deque[float]] = OrderedDict()

    async def __call__(self, request: Request) -> None:
        key = self._key_func(request)
        now = time.monotonic()
        events = self._data.setdefault(key, deque())
        self._data.move_to_end(key)

        cutoff = now - self._window
        while events and events[0] <= cutoff:
            events.popleft()

        if len(events) >= self._calls:
            retry_after = max(1, math.ceil(self._window - (now - events[0])))
            raise APIError(
                429,
                "rate_limit_exceeded",
                "Demasiadas solicitudes. Espera un momento.",
                extra={"retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        events.append(now)

        # LRU acotado: elimina primero las IP que llevan más tiempo sin usarse.
        while len(self._data) > _MAX_IPS:
            self._data.popitem(last=False)
