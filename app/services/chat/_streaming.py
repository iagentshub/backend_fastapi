"""El canal por el que sale cada token, y el recorte del historial.

`_stream_tokens` es el único sitio por el que puede pasar un proveedor que
emite en streaming: hace el trabajo bloqueante en un hilo y mantiene el
`: keep-alive` cada 10 s para que nginx y el cliente no lean un primer token
lento como un cuelgue. Claude y Ollama usaron `asyncio.to_thread` directamente
durante un tiempo y el usuario veía la pantalla quieta hasta que llegaba la
respuesta entera.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Dict,
)

if TYPE_CHECKING:
    pass

from app.connections.cancellation import ProviderCancellation
from app.services.llm_executor import (
    LLMLease,
    run_llm_blocking,
)


@dataclass
class ChatStreamState:
    """Resultado observable aunque el cliente cierre el SSE antes de ``done``."""

    reply: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_usage: bool = False
    interrupted: bool = False
    completed: bool = False
    started: bool = False
    connection_id: str = ""

    def start(self, *, tokens_in: int, connection_id: str = "") -> None:
        self.started = True
        self.tokens_in = max(0, tokens_in)
        self.estimated_usage = True
        if connection_id:
            self.connection_id = connection_id

    def append_token(self, token: str) -> None:
        self.reply += token
        self.tokens_out = _estimate_tokens(self.reply)
        self.estimated_usage = True

    def finish(self, reply: str, tokens_in: int, tokens_out: int) -> None:
        self.reply = reply
        self.tokens_in = max(0, tokens_in)
        self.tokens_out = max(0, tokens_out)
        self.estimated_usage = False
        self.completed = True

    def interrupt(self) -> None:
        if self.started and not self.completed:
            self.interrupted = True


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_tokens(
    out: "list[tuple[str, int, int]]",
    fn: Callable[..., "tuple[str, int, int]"],
    *args: Any,
    llm_lease: LLMLease | None = None,
    stream_state: ChatStreamState | None = None,
) -> AsyncGenerator[str, None]:
    """Corre ``fn`` (bloqueante, urllib) en un hilo y va emitiendo su SSE.

    ``fn`` recibe ``*args`` más un ``on_token`` al final. El resultado
    ``(reply, tok_in, tok_out)`` se deja en ``out`` porque un generador
    asíncrono no puede devolver valor: el llamador lee ``out[0]`` al terminar
    de iterar.

    Estaba escrito a mano dentro de la rama OpenAI-compat. Se saca aquí porque
    la de Claude necesita exactamente lo mismo y copiar treinta líneas de cola,
    hilo y heartbeat es como se acaba arreglando el bug en una sola de las dos.
    """
    token_queue: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _on_token(token: str) -> None:
        loop.call_soon_threadsafe(token_queue.put_nowait, token)

    cancellation = ProviderCancellation()
    provider_task = asyncio.create_task(
        run_llm_blocking(fn, *args, _on_token, cancellation, lease=llm_lease)
    )
    completed = False
    interrupted = False
    try:
        last_heartbeat = loop.time()
        while not provider_task.done() or not token_queue.empty():
            try:
                token = await asyncio.wait_for(token_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                # Algunos modelos de razonamiento tardan más de un minuto en
                # producir el primer token. Mantener el SSE activo evita que nginx
                # o el cliente confundan esa espera con un cuelgue.
                if loop.time() - last_heartbeat >= 10:
                    yield ": keep-alive\n\n"
                    last_heartbeat = loop.time()
                continue
            if stream_state is not None:
                stream_state.append_token(token)
            yield _sse({"type": "token", "token": token})
            last_heartbeat = loop.time()
        result = await provider_task
        out.append(result)
        if stream_state is not None:
            stream_state.finish(*result)
        completed = True
    except (asyncio.CancelledError, GeneratorExit):
        interrupted = True
        raise
    finally:
        if not completed:
            if interrupted and stream_state is not None:
                stream_state.interrupt()
            cancellation.cancel()
            # Cerrar la respuesta despierta normalmente al hilo de urllib. La
            # espera se protege para que una segunda cancelación ASGI no vuelva
            # a dejar la tarea huérfana; el slot se libera en invoke().
            if not provider_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(provider_task), timeout=1.0)
                except asyncio.CancelledError:
                    provider_task.cancel()
                except asyncio.TimeoutError:
                    provider_task.cancel()
                except (OSError, ValueError):
                    # El cierre deliberado del socket puede despertar urllib
                    # con cualquiera de estos errores; el estado parcial ya
                    # quedó capturado antes de cerrar el transporte.
                    if stream_state is not None:
                        stream_state.interrupt()


def _estimate_tokens(text: str) -> int:
    """Estimación rápida: ~4 chars por token (conservador)."""
    return max(1, len(text) // 4)


_HISTORY_TOKEN_BUDGET = 20_000

_CONTEXT_TOKEN_BUDGET = 60_000


def _truncate_history(
    history: list,
    system_tokens: int,
    max_context: int = 60_000,
) -> list:
    """
    Descarta los mensajes más antiguos hasta que el total estimado de tokens
    (system + history) quepa en max_context. Conserva primero los mensajes
    más recientes; el llamador reserva espacio para el turno actual.
    """
    budget = max_context - system_tokens
    if budget <= 0:
        return []

    total = sum(_estimate_tokens(str(m.get("content", ""))) for m in history)
    if total <= budget:
        return history  # ya cabe, nada que hacer

    # Eliminar desde el principio hasta que quepa
    trimmed = list(history)
    while len(trimmed) > 1 and total > budget:
        removed = trimmed.pop(0)
        total -= _estimate_tokens(str(removed.get("content", "")))
    if trimmed and total > budget:
        newest = dict(trimmed[-1])
        newest["content"] = str(newest.get("content", ""))[: max(0, budget * 4)]
        trimmed = [newest]
    return trimmed
