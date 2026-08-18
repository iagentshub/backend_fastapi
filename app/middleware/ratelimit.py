"""Rate limiter simple basado en ventana deslizante, sin dependencias externas."""

import hashlib
import math
import time
from collections import OrderedDict, deque
from typing import Callable

from fastapi import Request

from app.config.server import WORKERS as _WORKERS
from app.config.session import RATE_MAX_IPS as _MAX_IPS
from app.errors import APIError
from app.sql import sql
from app.storage.db import open_db
from app.utils.net import client_ip as _client_ip

# Todos los limiters creados, para que los tests puedan vaciarlos sin llevar
# una lista a mano (la de conftest se había quedado corta en 4 de 13).
INSTANCES: list["RateLimiter"] = []

# Suelo del reparto entre workers: ningún límite puede quedar por debajo de dos
# intentos por proceso. Uno solo convierte cualquier equivocación en un 429.
_MIN_CALLS = 2


def principal_key(request: Request) -> str:
    """Clave de cuota por principal: quien hace la petición, no desde dónde.

    La IP es una clave mala para un endpoint autenticado en las dos
    direcciones: detrás de un NAT corporativo todos los empleados comparten
    cupo, y un atacante con IPs rotativas no encuentra techo. Cuando la
    petición trae credencial, la cuenta es la unidad que gasta recursos —el
    chat llama al LLM, el test de conexión sale a un tercero—, así que es la
    que tiene que pagar la cuota.

    Se resuelve **sin tocar la base de datos**: esta función corre antes que la
    dependency de autorización y en la ruta caliente del chat.

      - Bearer (PAT): resolverlo a usuario cuesta una consulta, y su hash es
        igual de estable como clave. Se trunca porque solo hace de identidad.
      - Cookie `ga_token`: el JWT ya viene firmado; verificarlo es un HMAC.
      - Sin credencial legible cae a la IP, que es lo que había.

    No autoriza nada: un token caducado o falso cae a la rama de IP y la
    dependency de auth lo rechaza después con su 401.
    """
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    token = value.strip()
    if scheme.lower() == "bearer" and token:
        return "pat:" + hashlib.sha256(token.encode()).hexdigest()[:32]

    cookie = request.cookies.get("ga_token")
    if cookie:
        from app.auth.passwords import decode_claims

        claims = decode_claims(cookie)
        if claims:
            return "user:" + claims.username

    return "ip:" + _client_ip(request)


class RateLimiter:
    """Dependencia FastAPI: limita N llamadas por ventana de tiempo por clave.

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
        ip_calls: int | None = None,
    ) -> None:
        if calls <= 0 or window <= 0:
            raise ValueError("calls y window deben ser mayores que cero")
        # Sin `shared`, la cuota se reparte entre los WORKERS procesos y se
        # redondea hacia ARRIBA: pasarse un poco del límite declarado es
        # preferible a dejar un solo intento por proceso y bloquear al usuario
        # legítimo. Con `shared` el contador vive en la BD y el límite
        # declarado es el del clúster, sin reparto ni pérdida al reiniciar.
        # Ver docs/adr/009-cuota-compartida-y-por-principal.md
        if shared and not name:
            raise ValueError("Los limiters compartidos requieren un nombre estable")
        if ip_calls is not None and ip_calls <= 0:
            raise ValueError("ip_calls debe ser mayor que cero")
        self._calls = self._share(calls, shared)
        self._window = window
        self._key_func = key_func
        self._shared = shared
        self._name = name
        # Segunda ventana, más laxa y siempre por IP. Solo tiene sentido con
        # una clave primaria por principal: sin ella, quien abre cuentas
        # desechables se lleva un cupo entero por cuenta. Ver principal_key().
        self._ip_calls = self._share(ip_calls, shared) if ip_calls else None
        self._data: OrderedDict[str, deque[float]] = OrderedDict()
        INSTANCES.append(self)

    @staticmethod
    def _share(calls: int, shared: bool) -> int:
        return calls if shared else max(_MIN_CALLS, math.ceil(calls / _WORKERS))

    async def __call__(self, request: Request) -> None:
        await self._consume(self._key_func(request), self._calls)
        if self._ip_calls:
            # Prefijo propio: si la clave primaria ya cayó a la IP (petición sin
            # credencial), compartir fila haría que una sola petición gastara
            # dos de la misma cuota.
            await self._consume(f"ipwide:{_client_ip(request)}", self._ip_calls)

    async def _consume(self, key: str, calls: int) -> None:
        if self._shared:
            await self._consume_shared(key, calls)
        else:
            self._consume_local(key, calls)

    def _consume_local(self, key: str, calls: int) -> None:
        """Ventana deslizante en memoria del proceso.

        ponytail: el contador es por proceso y se pierde al reiniciar. Es la
        opción para limiters sin nombre estable; los que protegen algo caro
        van con shared=True.
        """
        now = time.monotonic()
        events = self._data.setdefault(key, deque())
        self._data.move_to_end(key)

        cutoff = now - self._window
        while events and events[0] <= cutoff:
            events.popleft()

        if len(events) >= calls:
            self._reject(math.ceil(self._window - (now - events[0])))
        events.append(now)

        # LRU acotado: elimina primero las claves que llevan más tiempo sin usarse.
        while len(self._data) > _MAX_IPS:
            self._data.popitem(last=False)

    async def _consume_shared(self, key: str, calls: int) -> None:
        """Consume una cuota fija en BD mediante un UPSERT atómico multiworker."""
        now = time.time()
        cutoff = now - self._window
        limiter_key = f"{self._name}:{key}"
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/ratelimit:consume_window"),
                (limiter_key, now, cutoff, cutoff),
            )
            await conn.commit()
        count = int(row[0]) if row else calls + 1
        window_start = float(row[1]) if row else now
        if count > calls:
            self._reject(math.ceil(self._window - (now - window_start)))

    def _reject(self, retry_after: float) -> None:
        seconds = max(1, int(retry_after))
        raise APIError(
            429,
            "rate_limit_exceeded",
            "Demasiadas solicitudes. Espera un momento.",
            extra={"retry_after": seconds},
            headers={"Retry-After": str(seconds)},
        )


async def purge_expired_windows() -> int:
    """Borra de `rate_limit_windows` las ventanas ya vencidas.

    La tabla crece con una fila por (limiter, principal) y nada la vacía: el
    UPSERT reinicia la ventana de quien vuelve, pero quien no vuelve deja su
    fila para siempre. En una instalación con tráfico eso es una fila por IP y
    por usuario de cada limiter compartido, indefinidamente.

    El horizonte sale de las ventanas realmente registradas en este proceso, no
    de una constante: un limiter con `window=3600` (recuperación de contraseña)
    perdería su cuota si se purgara con el corte de 60 s de los demás.
    """
    horizon = max((li._window for li in INSTANCES if li._shared), default=0)
    if not horizon:
        return 0
    cutoff = time.time() - horizon
    async with open_db() as conn:
        deleted = await conn.fetchval(
            sql("queries/ratelimit:count_expired"), (cutoff,)
        ) or 0
        if deleted:
            await conn.execute(sql("queries/ratelimit:purge_expired"), (cutoff,))
            await conn.commit()
    return int(deleted)
