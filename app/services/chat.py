"""Servicio de chat — streaming SSE hacia los proveedores LLM."""

from __future__ import annotations

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
    runtime_checkable,
)

if TYPE_CHECKING:
    from app.models.agent import Agent
from urllib.parse import urlparse

from app.config.providers import (
    ANTHROPIC_API_VERSION,
    OPENAI_COMPAT_URLS,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
)
from app.config.security import PRIVATE_HOST_PREFIXES


@runtime_checkable
class _SkillStorage(Protocol):
    def get(self, scope: str, skill_id: str) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class _KnowledgeStorage(Protocol):
    async def get(
        self, item_id: str, owner_id: Any = None
    ) -> Optional[Dict[str, Any]]: ...


@runtime_checkable
class _MemoryStorage(Protocol):
    def get(self, filename: str) -> Optional[str]: ...
    def save(self, filename: str, content: str) -> None: ...


@runtime_checkable
class _ChatStorage(Protocol):
    async def list_conversations(
        self, user_id: str, agent_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]: ...
    async def get_messages(
        self, conversation_id: str, user_id: str, limit: int = 200
    ) -> List[Dict[str, Any]]: ...


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    """Retry transient DNS failures before the provider has emitted any token."""
    for attempt in range(3):
        emitted = False

        def _on_token(token: str) -> None:
            nonlocal emitted
            emitted = emitted or bool(token)
            if on_token is not None:
                on_token(token)

        try:
            return _do_openai_stream(url, headers, payload, timeout, _on_token)
        except urllib.error.URLError as exc:
            reason = exc.reason
            errno = getattr(reason, "errno", None)
            dns_failure = isinstance(reason, socket.gaierror) or errno in (
                -5,
                -2,
                11001,
            )
            if emitted or not dns_failure or attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("No se pudo contactar con el proveedor")


def _do_claude_stream(
    url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: Optional[int]
) -> "tuple[str, int, int]":
    """Llama a la API de Anthropic y devuelve (reply, tok_in, tok_out)."""
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
                    full_reply += obj.get("delta", {}).get("text", "")
                elif ev_type == "message_delta":
                    usage = obj.get("usage") or {}
                    tok_out = usage.get("output_tokens", tok_out)
            except Exception:
                pass
    return full_reply, tok_in, tok_out


def _do_ollama_call(
    host: str, payload: Dict[str, Any], timeout: Optional[int]
) -> "tuple[str, int, int]":
    """Llama a Ollama y devuelve (reply, tok_in, tok_out)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    tok_in = body.get("prompt_eval_count", 0)
    tok_out = body.get("eval_count", 0)
    return body.get("message", {}).get("content") or "", tok_in, tok_out


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
) -> AsyncGenerator[str, None]:
    import asyncio
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

    # Knowledge injection (URLs + documents attached to the agent)
    if knowledge_storage is not None and agent.knowledge:
        for kid in agent.knowledge:
            item = await knowledge_storage.get(kid)
            if item and item.get("content"):
                system += (
                    f"\n\n## Conocimiento: {item.get('title', kid)}\n{item['content']}"
                )

    # Memory injection
    if agent.use_memory and memory_storage is not None:
        mem_file = agent.memory_file or f"{agent.id}.md"
        mem_content = await memory_storage.get(mem_file)
        if mem_content and mem_content.strip():
            system += f"\n\n## Memoria del agente\n{mem_content}"

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
            url = (conn.get("url") or OPENAI_COMPAT_URLS[conn_type]).rstrip("/")
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
            if agent.effort_level:
                payload["reasoning_effort"] = agent.effort_level
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            # urllib lee el proveedor en un hilo. Reenviar cada delta mediante
            # una cola evita acumular la respuesta completa: con modelos lentos
            # el cliente recibe actividad y el proxy no corta por inactividad.
            token_queue: asyncio.Queue[str] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def _on_token(token: str) -> None:
                loop.call_soon_threadsafe(token_queue.put_nowait, token)

            provider_task = asyncio.create_task(
                asyncio.to_thread(
                    _do_openai_stream_with_dns_retry,
                    url,
                    headers,
                    payload,
                    timeout,
                    _on_token,
                )
            )
            last_heartbeat = loop.time()
            while not provider_task.done() or not token_queue.empty():
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    # Algunos modelos de razonamiento tardan más de un minuto
                    # en producir el primer token. Mantener el SSE activo evita
                    # que nginx o el cliente confundan esa espera con un cuelgue.
                    if loop.time() - last_heartbeat >= 10:
                        yield ": keep-alive\n\n"
                        last_heartbeat = loop.time()
                    continue
                yield _sse({"type": "token", "token": token})
                last_heartbeat = loop.time()
            reply, tok_in, tok_out = await provider_task

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
            reply, tok_in, tok_out = await asyncio.to_thread(
                _do_claude_stream, url, headers, payload, timeout
            )

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
                "stream": False,
                "options": {"temperature": temperature},
            }
            reply, tok_in, tok_out = await asyncio.to_thread(
                _do_ollama_call, host, payload_o, timeout
            )

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
            error = parsed.get("error") or {}
            detail = (
                parsed.get("detail")
                or parsed.get("message")
                or (error.get("message") if isinstance(error, dict) else error)
                or detail
            )
        except Exception:
            pass
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
