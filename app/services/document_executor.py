"""Executor aislado para la extracción de texto de documentos.

`extract_document` es trabajo síncrono y caro: pypdf sobre un PDF de miles de
páginas, o tesseract sobre una imagen grande. Las cuatro rutas que lo llaman
usaban `asyncio.to_thread`, que es lo correcto para no bloquear el event loop
pero deja el trabajo en el **executor por defecto de asyncio** — el mismo donde
corre `hash_password_async`. Con `min(32, cpu + 4)` huecos, unas cuantas subidas
simultáneas de documentos grandes lo llenan y los logins se paran detrás, sin
que nada falle ni se registre: solo se espera. Es el mismo cuello que llevó a
sacar el transporte LLM a `LLMExecutor`; la extracción se quedó donde estaba.

A diferencia de `LLMExecutor`, aquí sí hay cola: una subida que espera su turno
es un comportamiento razonable, mientras que rechazarla con un 503 por
capacidad cambiaría el contrato de la API. Lo que no puede es esperar en el
mismo sitio que bcrypt.

Ver docs/adr/013-la-extraccion-no-pierde-texto-en-silencio.md
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

_DEFAULT_MAX_WORKERS = 4


def _configured_max_workers() -> int:
    try:
        return max(1, int(os.getenv("GAIA_DOCUMENT_MAX_THREADS", str(_DEFAULT_MAX_WORKERS))))
    except ValueError:
        return _DEFAULT_MAX_WORKERS


_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=_configured_max_workers(),
                thread_name_prefix="iagents-doc",
            )
        return _pool


async def run_document_blocking(fn: Callable[..., _T], *args: Any) -> _T:
    """Ejecuta trabajo de extracción fuera del executor por defecto."""
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _get_pool(), lambda: context.run(fn, *args)
    )


def shutdown_document_executor() -> None:
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        # Sin esperar: una extracción en curso termina sola y su hilo es
        # demonio del pool; bloquear el apagado detrás de un PDF patológico es
        # exactamente lo que este módulo evita en el resto del proceso.
        pool.shutdown(wait=False)
