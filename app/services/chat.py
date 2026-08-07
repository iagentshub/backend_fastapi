"""Servicio de chat — streaming SSE hacia los proveedores LLM."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
)

if TYPE_CHECKING:
    from app.models.agent import Agent
from urllib.parse import urlparse, urlunparse

from app.config.providers import (
    ANTHROPIC_API_VERSION,
    OPENAI_COMPAT_URLS,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
)
from app.config.security import PRIVATE_HOST_PREFIXES
from app.utils import flog

_NVIDIA_DEEPSEEK_V4_MODELS = {
    "deepseek-ai/deepseek-v4-pro",
    "deepseek-ai/deepseek-v4-flash",
}


def _openai_compat_chat_url(conn_type: str, configured_url: str = "") -> str:
    """Accept either a provider base URL or a full chat-completions endpoint."""
    default_url = OPENAI_COMPAT_URLS[conn_type]
    raw = configured_url.strip()
    if not raw:
        return default_url

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/")

    path = parsed.path.rstrip("/")
    host = parsed.netloc.casefold()
    default_path = urlparse(default_url).path

    # The hosted NVIDIA API exposes one canonical OpenAI-compatible route.
    # Connections sometimes store only the host, /v1, or /models after model
    # discovery; all of those must resolve to the inference endpoint.
    if conn_type == "nvidia" and host == "integrate.api.nvidia.com":
        path = default_path
    elif path.endswith("/chat/completions"):
        pass
    elif path.endswith("/models"):
        path = path[: -len("/models")] + "/chat/completions"
    elif conn_type == "gemini" and path.endswith("/v1beta"):
        path += "/openai/chat/completions"
    elif path.endswith("/v1") or path.endswith("/openai"):
        path += "/chat/completions"
    elif not path:
        path = default_path
    else:
        path += "/chat/completions"

    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


# Estos Protocol existen para no importar los storages de recurso desde aquí
# (app.storage.skill_storage y compañía; el import es circular). Son solo
# anotaciones: se les quitó @runtime_checkable
# porque ninguno se usaba nunca en un isinstance, y el decorador hacía creer
# que había una comprobación en tiempo de ejecución que no existe.
class _SkillStorage(Protocol):
    async def get(
        self, scope: str, skill_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...


class _ToolStorage(Protocol):
    async def get(
        self, scope: str, tool_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]: ...


class _KnowledgeStorage(Protocol):
    async def get(
        self, item_id: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...


class _PromptStorage(Protocol):
    async def get_any(
        self, prompt_id: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...

    async def find_by_alias(
        self, alias: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...


class _MemoryStorage(Protocol):
    # Declaraban get/save síncronos y el código los llama con await desde el
    # primer día: las implementaciones reales (MemoryStorage) son async.
    async def get(self, filename: str, owner_id: str = "admin") -> Optional[str]: ...
    async def save(
        self, filename: str, content: str, owner_id: str = "admin"
    ) -> Dict[str, Any]: ...


class _ChatStorage(Protocol):
    async def list_conversations(
        self, user_id: str, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]: ...
    async def get_messages(
        self, conversation_id: str, user_id: str, limit: int = 200
    ) -> List[Dict[str, Any]]: ...


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_tokens(
    out: "list[tuple[str, int, int]]",
    fn: Callable[..., "tuple[str, int, int]"],
    *args: Any,
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

    provider_task = asyncio.create_task(asyncio.to_thread(fn, *args, _on_token))
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
        yield _sse({"type": "token", "token": token})
        last_heartbeat = loop.time()
    out.append(await provider_task)


def _estimate_tokens(text: str) -> int:
    """Estimación rápida: ~4 chars por token (conservador)."""
    return max(1, len(text) // 4)


_HISTORY_TOKEN_BUDGET = 20_000


def _truncate_history(
    history: list,
    system_tokens: int,
    max_context: int = 60_000,
) -> list:
    """
    Descarta los mensajes más antiguos hasta que el total estimado de tokens
    (system + history) quepa en max_context. Siempre conserva al menos
    los 2 últimos mensajes para no romper el turno actual.
    """
    budget = max_context - system_tokens
    if budget <= 0:
        return history[-2:]  # extremo: solo el último turno

    total = sum(_estimate_tokens(str(m.get("content", ""))) for m in history)
    if total <= budget:
        return history  # ya cabe, nada que hacer

    # Eliminar desde el principio hasta que quepa
    trimmed = list(history)
    while len(trimmed) > 2 and total > budget:
        removed = trimmed.pop(0)
        total -= _estimate_tokens(str(removed.get("content", "")))
    return trimmed


def _do_openai_stream(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: Optional[int],
    on_token: Optional[Callable[[str], None]] = None,
) -> "tuple[str, int, int]":
    """Llama al endpoint OpenAI-compatible y devuelve (reply, tok_in, tok_out)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    full_reply = ""
    tok_in = tok_out = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if choices:
                token = choices[0].get("delta", {}).get("content") or ""
                full_reply += token
                if token and on_token is not None:
                    on_token(token)
            usage = obj.get("usage") or {}
            if usage:
                tok_in = usage.get("prompt_tokens", tok_in)
                tok_out = usage.get("completion_tokens", tok_out)
    return full_reply, tok_in, tok_out


def _do_openai_stream_with_dns_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: Optional[int],
    on_token: Optional[Callable[[str], None]] = None,
) -> "tuple[str, int, int]":
    """Retry transient connection/gateway failures before any token is emitted."""
    for attempt in range(3):
        emitted = False

        def _on_token(token: str) -> None:
            nonlocal emitted
            emitted = emitted or bool(token)
            if on_token is not None:
                on_token(token)

        try:
            return _do_openai_stream(url, headers, payload, timeout, _on_token)
        except urllib.error.HTTPError as exc:
            transient_gateway = exc.code in (502, 503, 504)
            if emitted or not transient_gateway or attempt == 2:
                raise
            exc.close()
            time.sleep(attempt + 1)
        except TimeoutError:
            if emitted or attempt == 2:
                raise
            time.sleep(attempt + 1)
        except urllib.error.URLError as exc:
            reason = exc.reason
            errno = getattr(reason, "errno", None)
            dns_failure = isinstance(reason, socket.gaierror) or errno in (
                -5,
                -2,
                11001,
            )
            timeout_failure = isinstance(reason, TimeoutError)
            if emitted or not (dns_failure or timeout_failure) or attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("No se pudo contactar con el proveedor")


def _do_claude_stream(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: Optional[int],
    on_token: Optional[Callable[[str], None]] = None,
) -> "tuple[str, int, int]":
    """Llama a la API de Anthropic y devuelve (reply, tok_in, tok_out).

    Pedía "stream": true y luego se guardaba los deltas para el final, así que
    Claude era el único proveedor donde el usuario miraba una pantalla quieta
    hasta que la respuesta estaba entera. El on_token es el mismo contrato que
    el del camino OpenAI-compat.
    """
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    full_reply = ""
    tok_in = tok_out = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            try:
                obj = json.loads(line[6:])
                ev_type = obj.get("type")
                if ev_type == "message_start":
                    usage = obj.get("message", {}).get("usage") or {}
                    tok_in = usage.get("input_tokens", 0)
                elif ev_type == "content_block_delta":
                    token = obj.get("delta", {}).get("text", "")
                    full_reply += token
                    if token and on_token is not None:
                        on_token(token)
                elif ev_type == "message_delta":
                    usage = obj.get("usage") or {}
                    tok_out = usage.get("output_tokens", tok_out)
            except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                flog.warning(f"[chat] Evento Anthropic inválido omitido: {exc}")
    return full_reply, tok_in, tok_out


def _do_ollama_call(
    host: str,
    payload: Dict[str, Any],
    timeout: Optional[int],
    api_key: str = "",
    # on_token va EL ÚLTIMO y no es casualidad: _stream_tokens llama a
    # fn(*args, _on_token), o sea que el callback entra como último posicional.
    # Si se cuela un parámetro nuevo detrás, el callback aterriza en él —y aquí
    # el de al lado es api_key, con lo que acabaría dentro de una cabecera
    # Authorization sin que nada fallara a la vista.
    on_token: Optional[Callable[[str], None]] = None,
) -> "tuple[str, int, int]":
    """Llama a Ollama y devuelve (reply, tok_in, tok_out).

    Era el último proveedor que devolvía la respuesta entera de una vez. Ollama
    con ``"stream": true`` responde **NDJSON** —un objeto JSON por línea, no
    SSE—, así que aquí no hay prefijo ``data: `` que quitar, a diferencia de los
    otros dos caminos.
    """
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=data,
        headers=headers,
        method="POST",
    )
    full_reply = ""
    tok_in = tok_out = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            token = (obj.get("message") or {}).get("content") or ""
            if token:
                full_reply += token
                if on_token is not None:
                    on_token(token)
            # El recuento solo viene en el último objeto, el que trae done:true.
            # Se lee en todos por si alguna versión lo adelanta.
            tok_in = obj.get("prompt_eval_count", tok_in)
            tok_out = obj.get("eval_count", tok_out)
    return full_reply, tok_in, tok_out


def _validate_ollama_host(host: str) -> None:
    """Rechaza hosts que apunten a rangos privados o de metadata de cloud (SSRF)."""
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Protocolo no permitido para Ollama: {parsed.scheme!r}")
    hostname = (parsed.hostname or "").lower()
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return  # loopback local — caso de uso legítimo para Ollama
    # Bloquear por prefijo de texto (cubre la mayoría de casos sin DNS)
    if any(hostname.startswith(p) for p in PRIVATE_HOST_PREFIXES):
        raise ValueError(f"Host Ollama no permitido: {hostname!r}")
    # Bloquear mediante ipaddress si es una IP literal (excluye loopback ya permitido arriba)
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_link_local:
            raise ValueError(f"Host Ollama no permitido: {hostname!r}")
    except ValueError as exc:
        if "no permitido" in str(exc):
            raise
        # No es una IP literal — es un hostname; se permite (el usuario lo configuró)


async def stream_chat(
    agent: "Dict[str, Any] | Agent",
    conn: Dict[str, Any],
    history: List[Dict[str, Any]],
    skill_storage: Optional[_SkillStorage],
    memory_storage: Optional[_MemoryStorage] = None,
    knowledge_storage: Optional[_KnowledgeStorage] = None,
    chat_storage: Optional[_ChatStorage] = None,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    *,
    prompt_storage: Optional[_PromptStorage] = None,
    tool_storage: Optional[_ToolStorage] = None,
    attached_knowledge: Optional[List[Dict[str, Any]]] = None,
) -> AsyncGenerator[str, None]:
    # asyncio estaba diferido aquí dentro. Es stdlib: no había ciclo que
    # romper, y _stream_tokens lo necesita a nivel de módulo.
    from app.models.agent import Agent

    if not isinstance(agent, Agent):
        agent = Agent.from_dict(agent)

    conn_type = str(conn.get("type") or "").lower()
    api_key = str(conn.get("api_key") or "")
    # Model: connection → agent → provider default
    model = str(
        conn.get("model") or agent.model or PROVIDER_DEFAULT_MODELS.get(conn_type) or ""
    )
    temperature = agent.temperature
    max_tokens = agent.max_tokens
    timeout = agent.timeout

    # System prompt + skills
    system = agent.system_prompt
    for sid in agent.skills:
        if skill_storage is None:
            break
        for scope in ("public", "private"):
            sk = await skill_storage.get(scope, sid)
            if sk:
                system += (
                    f"\n\n## Skill: {sk.get('name', sid)}\n{sk.get('content', '')}"
                )
                break

    # Tools injection — recurso estático vinculado al agente (agent.tools),
    # misma familia que Skill: el modelo conoce el contenido para poder
    # compartirlo con el usuario, nunca lo ejecuta (Fase 2, fuera de alcance).
    for tid in agent.tools:
        if tool_storage is None:
            break
        for scope in ("public", "private"):
            t = await tool_storage.get(scope, tid)
            if t:
                language = str(t.get("language") or "")
                name = t.get("name", tid)
                if language == "cpp":
                    system += (
                        f"\n\n## Tool: {name} (binario C++)\n"
                        f"{t.get('description', '') or 'Sin descripción.'}\n"
                        "Es un binario precompilado: no hay código fuente en texto "
                        "para mostrar en el chat. Indica al usuario que puede "
                        "descargarlo desde la tarjeta de esta tool en Conocimiento."
                    )
                else:
                    system += (
                        f"\n\n## Tool: {name} ({language})\n"
                        "No se ejecuta en el servidor: comparte este código con el "
                        "usuario, tal cual, para que lo ejecute en su propia "
                        "máquina. No digas que lo ejecutaste tú ni inventes su "
                        "resultado.\n\n"
                        f"```{language}\n{t.get('content', '')}\n```"
                    )
                break

    # Knowledge injection (URLs + documents attached to the agent)
    if knowledge_storage is not None and agent.knowledge:
        for kid in agent.knowledge:
            item = await knowledge_storage.get(kid)
            if item and item.get("content"):
                system += (
                    f"\n\n## Conocimiento: {item.get('title', kid)}\n{item['content']}"
                )

    # Knowledge adjuntado puntualmente a este mensaje desde el chat (vía "@" en
    # el composer) — ya viene resuelto y autorizado por el llamador (la ruta
    # valida ownership/permiso de grupo antes de construir esta lista).
    if attached_knowledge:
        for item in attached_knowledge:
            content = item.get("content")
            if content:
                system += (
                    f"\n\n## Conocimiento adjunto: {item.get('title', item.get('id', ''))}\n{content}"
                )

    # Memory injection
    if agent.use_memory and memory_storage is not None:
        mem_file = agent.memory_file or f"{agent.id}.md"
        mem_content = await memory_storage.get(mem_file)
        if mem_content and mem_content.strip():
            system += f"\n\n## Memoria del agente\n{mem_content}"

    # Prompt injection — "@alias" en el último mensaje del usuario referencia
    # ocultamente cualquier prompt accesible del usuario (propio o público),
    # no solo los vinculados al agente: se añade su contenido al system sin
    # modificar el texto visible del mensaje.
    if prompt_storage is not None and history and history[-1].get("role") == "user":
        import re

        mentions = {
            m.lower()
            for m in re.findall(
                r"@([a-z0-9][a-z0-9_-]{1,28}[a-z0-9])",
                str(history[-1].get("content", "")),
                re.IGNORECASE,
            )
        }
        for alias in mentions:
            p = await prompt_storage.find_by_alias(alias, owner_id=user_id)
            if p:
                system += f"\n\n## Prompt: {p.get('name', alias)}\n{p.get('content', '')}"

    # Recuerdo de conversaciones anteriores del mismo usuario con este agente
    if agent.use_memory and chat_storage is not None and user_id:
        try:
            convs = await chat_storage.list_conversations(user_id, agent.id)
        except Exception:
            convs = []
        past_lines: List[str] = []
        past_tokens = 0
        for c in convs:
            if c.get("id") == conversation_id:
                continue
            try:
                msgs = await chat_storage.get_messages(c["id"], user_id)
            except Exception:
                continue
            for m in msgs:
                label = "Usuario" if m.get("role") == "user" else "Agente"
                line = f"**{label}:** {m.get('content', '')}"
                past_tokens += _estimate_tokens(line)
                if past_tokens > _HISTORY_TOKEN_BUDGET:
                    break
                past_lines.append(line)
            if past_tokens > _HISTORY_TOKEN_BUDGET:
                break
        if past_lines:
            system += "\n\n## Conversaciones anteriores\n" + "\n\n".join(past_lines)

    # Truncar history si el contexto es demasiado largo
    _sys_tokens = _estimate_tokens(system)
    history = _truncate_history(list(history), _sys_tokens)

    try:
        if conn_type in OPENAI_COMPAT_URLS:
            url = _openai_compat_chat_url(
                conn_type,
                str(conn.get("url") or ""),
            )
            msgs: List[Dict[str, Any]] = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.extend(history)
            payload: Dict[str, Any] = {
                "model": model,
                "messages": msgs,
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if max_tokens:
                payload["max_tokens"] = int(max_tokens)
            elif conn_type == "nvidia" and model in _NVIDIA_DEEPSEEK_V4_MODELS:
                # El endpoint alojado permite respuestas muy largas. Un límite
                # conservador evita que las etapas de una orquestación agoten el
                # tiempo del gateway cuando el agente no define uno propio.
                payload["max_tokens"] = 2_048
            if conn_type == "nvidia" and model in _NVIDIA_DEEPSEEK_V4_MODELS:
                payload["chat_template_kwargs"] = {"thinking": False}
                if model == "deepseek-ai/deepseek-v4-flash":
                    payload["chat_template_kwargs"]["reasoning_effort"] = "none"
            if agent.effort_level:
                payload["reasoning_effort"] = agent.effort_level
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            # urllib lee el proveedor en un hilo. Reenviar cada delta mediante
            # una cola evita acumular la respuesta completa: con modelos lentos
            # el cliente recibe actividad y el proxy no corta por inactividad.
            out: "list[tuple[str, int, int]]" = []
            async for frame in _stream_tokens(
                out, _do_openai_stream_with_dns_retry, url, headers, payload, timeout
            ):
                yield frame
            reply, tok_in, tok_out = out[0]

        elif conn_type == "claude":
            url = (
                conn.get("url") or f"{PROVIDER_BASE_URLS['claude']}/messages"
            ).strip()
            if not url.endswith("/messages"):
                url = url.rstrip("/") + "/messages"
            payload = {
                "model": model,
                "messages": history,
                "temperature": temperature,
                "stream": True,
                "max_tokens": int(max_tokens) if max_tokens else 4096,
            }
            if system:
                payload["system"] = system
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            }
            out = []
            async for frame in _stream_tokens(
                out, _do_claude_stream, url, headers, payload, timeout
            ):
                yield frame
            reply, tok_in, tok_out = out[0]

        elif conn_type == "ollama":
            host = str(conn.get("host") or "http://localhost:11434").rstrip("/")
            _validate_ollama_host(host)
            msgs_ollama: List[Dict[str, Any]] = []
            if system:
                msgs_ollama.append({"role": "system", "content": system})
            msgs_ollama.extend(history)
            payload_o: Dict[str, Any] = {
                "model": model,
                "messages": msgs_ollama,
                "stream": True,
                "options": {"temperature": temperature},
            }
            ollama_api_key = str(conn.get("api_key") or "")
            out = []
            async for frame in _stream_tokens(
                out, _do_ollama_call, host, payload_o, timeout, ollama_api_key
            ):
                yield frame
            reply, tok_in, tok_out = out[0]

        else:
            yield _sse(
                {
                    "type": "error",
                    "message": f"Tipo de conexión '{conn_type}' no soportado",
                }
            )
            return

        yield _sse(
            {"type": "done", "reply": reply, "tokens": {"in": tok_in, "out": tok_out}}
        )

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body[:500]
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            error = parsed.get("error") or {}
            detail = (
                parsed.get("detail")
                or parsed.get("message")
                or (error.get("message") if isinstance(error, dict) else error)
                or detail
            )
        if exc.code == 404 and conn_type in OPENAI_COMPAT_URLS:
            detail = (
                f"No se encontró el endpoint de chat o el modelo '{model}'. "
                f"URL utilizada: {url}. Revisa que la conexión use una URL base "
                "OpenAI-compatible y que el modelo admita conversaciones."
            )
        yield _sse(
            {
                "type": "error",
                "message": f"El proveedor respondió HTTP {exc.code}: {detail}",
            }
        )
    except urllib.error.URLError as exc:
        yield _sse({"type": "error", "message": f"Error de conexión: {exc}"})
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
