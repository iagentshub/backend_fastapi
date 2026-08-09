"""Rate limiter simple basado en ventana deslizante, sin dependencias externas."""

import math
import time
from collections import OrderedDict, deque
from typing import Callable

from fastapi import Request

from app.config.server import WORKERS as _WORKERS
from app.config.session import RATE_MAX_IPS as _MAX_IPS
from app.errors import APIError
from app.storage.db import open_db
from app.utils.net import client_ip as _client_ip

# Todos los limiters creados, para que los tests puedan vaciarlos sin llevar
# una lista a mano (la de conftest se había quedado corta en 4 de 13).
INSTANCES: list["RateLimiter"] = []

# Suelo del reparto entre workers: ningún límite puede quedar por debajo de dos
# intentos por proceso. Uno solo convierte cualquier equivocación en un 429.
_MIN_CALLS = 2


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
        *,
        shared: bool = False,
        name: str = "",
    ) -> None:
        if calls <= 0 or window <= 0:
            raise ValueError("calls y window deben ser mayores que cero")
        # El contador vive en memoria del proceso y uvicorn arranca WORKERS
        # procesos independientes, así que el límite efectivo era el declarado
        # multiplicado por WORKERS (con el default de 4: 5 intentos de login
        # se convertían en 20). Se reparte la cuota entre ellos.
        #
        # El reparto se redondea hacia ARRIBA, no hacia abajo: con calls=5 y 4
        # workers, `5 // 4 = 1` dejaba un solo intento por proceso, así que
        # quien se equivocaba una vez de contraseña y reintentaba sobre la misma
        # conexión keep-alive recibía un 429 con Retry-After: 300. Afectaba a
        # login, registro, alta de invitado y recuperación de contraseña, los
        # cuatro con límite 5. Pasarse un poco del límite declarado (8 en el
        # cluster en vez de 5) es preferible a bloquear al usuario legítimo.
        #
        # ponytail: sigue siendo por proceso y se pierde al reiniciar. Si el
        # límite tiene que ser EXACTO —para el login probablemente deba serlo—
        # el contador tiene que salir del proceso (Redis/BD), que es otra
        # conversación.
        if shared and not name:
            raise ValueError("Los limiters compartidos requieren un nombre estable")
        self._calls = calls if shared else max(_MIN_CALLS, math.ceil(calls / _WORKERS))
        self._window = window
        self._key_func = key_func
        self._shared = shared
        self._name = name
        self._data: OrderedDict[str, deque[float]] = OrderedDict()
        INSTANCES.append(self)

    async def __call__(self, request: Request) -> None:
        key = self._key_func(request)
        if self._shared:
            await self._consume_shared(key)
            return
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

    async def _consume_shared(self, key: str) -> None:
        """Consume una cuota fija en BD mediante un UPSERT atómico multiworker."""
        now = time.time()
        cutoff = now - self._window
        limiter_key = f"{self._name}:{key}"
        query = """
            INSERT INTO rate_limit_windows(limiter_key, window_start, request_count)
            VALUES (?, ?, 1)
            ON CONFLICT(limiter_key) DO UPDATE SET
                window_start = CASE
                    WHEN rate_limit_windows.window_start <= ? THEN excluded.window_start
                    ELSE rate_limit_windows.window_start
                END,
                request_count = CASE
                    WHEN rate_limit_windows.window_start <= ? THEN 1
                    ELSE rate_limit_windows.request_count + 1
                END
            RETURNING request_count, window_start
        """
        async with open_db() as conn:
            row = await conn.fetchone(query, (limiter_key, now, cutoff, cutoff))
            await conn.commit()
        count = int(row[0]) if row else self._calls + 1
        window_start = float(row[1]) if row else now
        if count > self._calls:
            retry_after = max(1, math.ceil(self._window - (now - window_start)))
            raise APIError(
                429,
                "rate_limit_exceeded",
                "Demasiadas solicitudes. Espera un momento.",
                extra={"retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
