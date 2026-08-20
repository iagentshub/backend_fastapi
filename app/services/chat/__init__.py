"""Servicio de chat — streaming SSE hacia los proveedores LLM.

Partido en paquete porque el módulo único llegó a 934 líneas. `stream_chat` es
la mitad y no se parte sin refactor de verdad: monta el contexto (skills,
prompts, conocimiento, memoria, historial), elige proveedor y emite. Lo que sí
sale es todo lo demás.

    _protocols.py  los almacenes, como Protocol, para no importar en círculo.
    _streaming.py  `_stream_tokens`, el keep-alive y el recorte de historial.
    providers.py   la llamada a cada proveedor y la puerta anti-SSRF.

Los tests parchean `safe_urlopen` y `time.sleep` en `providers`, y
`run_llm_blocking` en `_streaming`: son los módulos donde se resuelven esos
nombres, no este.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
)

if TYPE_CHECKING:
    from app.models.agent import Agent

from app.config.providers import (
    ANTHROPIC_API_VERSION,
    OPENAI_COMPAT_URLS,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
)
from app.services.chat._protocols import (
    _ChatStorage,
    _KnowledgeStorage,
    _MemoryStorage,
    _PromptStorage,
    _SkillStorage,
    _ToolStorage,
)
from app.services.chat._streaming import (
    _CONTEXT_TOKEN_BUDGET,
    _HISTORY_TOKEN_BUDGET,
    ChatStreamState,
    _estimate_tokens,
    _sse,
    _stream_tokens,
    _truncate_history,
)
from app.services.chat.providers import (
    _NVIDIA_DEEPSEEK_V4_MODELS,
    UnsafeProviderURL,
    _assert_provider_url,
    _detalle_publico,
    _do_claude_stream,
    _do_ollama_call,
    _do_openai_stream_with_dns_retry,
    _openai_compat_chat_url,
    _validate_ollama_host,
)
from app.services.llm_executor import LLMCapacityError, LLMLease
from app.storage.crypto import UNREADABLE_FLAG
from app.storage.db import DB_ERRORS
from app.utils import flog

__all__ = ["stream_chat", "ChatStreamState", "UnsafeProviderURL"]


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
    knowledge_pack_storage: Any = None,
    prompt_storage: Optional[_PromptStorage] = None,
    tool_storage: Optional[_ToolStorage] = None,
    attached_knowledge: Optional[List[Dict[str, Any]]] = None,
    llm_lease: LLMLease | None = None,
    stream_state: ChatStreamState | None = None,
) -> AsyncGenerator[str, None]:
    # asyncio estaba diferido aquí dentro. Es stdlib: no había ciclo que
    # romper, y _stream_tokens lo necesita a nivel de módulo.
    from app.models.agent import Agent

    if not isinstance(agent, Agent):
        agent = Agent.from_dict(agent)

    conn_type = str(conn.get("type") or "").lower()
    if conn.get(UNREADABLE_FLAG):
        # La credencial guardada no se pudo descifrar. Antes se enviaba el
        # ciphertext al proveedor y el usuario recibía un 401 ajeno que no
        # explicaba nada; el problema es local y tiene arreglo local.
        flog.warning(
            f"[chat] Credencial ilegible en la conexión {conn.get('id')}",
            username=user_id or "-",
        )
        yield _sse(
            {
                "type": "error",
                "code": "credential_unreadable",
                "message": (
                    "La credencial guardada en esta conexión no se puede leer. "
                    "Vuelve a introducirla."
                ),
            }
        )
        return
    api_key = str(conn.get("api_key") or "")
    # Model: connection → agent → provider default
    model = str(
        conn.get("model") or agent.model or PROVIDER_DEFAULT_MODELS.get(conn_type) or ""
    )
    temperature = agent.temperature
    max_tokens = agent.max_tokens
    timeout = agent.timeout

    output_reserve = min(max(int(max_tokens or 4_096), 1_024), 16_000)
    current_turn_tokens = (
        _estimate_tokens(str(history[-1].get("content", ""))) if history else 0
    )
    system_budget = max(0, _CONTEXT_TOKEN_BUDGET - output_reserve - current_turn_tokens)
    context_parts: list[str] = []
    context_tokens = 0
    truncated_sources: list[str] = []

    def add_context(source: str, content: str) -> None:
        nonlocal context_tokens
        if not content:
            return
        available = system_budget - context_tokens
        tokens = _estimate_tokens(content)
        if available <= 0:
            truncated_sources.append(source)
            return
        if tokens > available:
            content = content[: available * 4]
            tokens = _estimate_tokens(content)
            truncated_sources.append(source)
        context_parts.append(content)
        context_tokens += tokens

    # System prompt + skills
    add_context("system_prompt", agent.system_prompt)
    for sid in agent.skills:
        if skill_storage is None:
            break
        for scope in ("public", "private"):
            sk = await skill_storage.get(scope, sid)
            if sk:
                add_context(
                    "skill",
                    (f"\n\n## Skill: {sk.get('name', sid)}\n{sk.get('content', '')}"),
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
                    add_context(
                        "tool",
                        (
                            f"\n\n## Tool: {name} (binario C++)\n"
                            f"{t.get('description', '') or 'Sin descripción.'}\n"
                            "Es un binario precompilado: no hay código fuente en texto "
                            "para mostrar en el chat. Indica al usuario que puede "
                            "descargarlo desde la tarjeta de esta tool en Conocimiento."
                        ),
                    )
                else:
                    add_context(
                        "tool",
                        (
                            f"\n\n## Tool: {name} ({language})\n"
                            "No se ejecuta en el servidor: comparte este código con el "
                            "usuario, tal cual, para que lo ejecute en su propia "
                            "máquina. No digas que lo ejecutaste tú ni inventes su "
                            "resultado.\n\n"
                            f"```{language}\n{t.get('content', '')}\n```"
                        ),
                    )
                break

    # Knowledge injection (URLs + documents attached to the agent)
    covered_knowledge_ids: set[str] = set()
    if knowledge_storage is not None and knowledge_pack_storage is not None:
        for pack_id in agent.knowledge_packs:
            pack = await knowledge_pack_storage.get(pack_id)
            if not pack:
                continue
            add_context(
                "knowledge_pack",
                f"\n\n## Pack de conocimiento: {pack.get('name', pack_id)}",
            )
            for member in pack.get("items") or []:
                knowledge_id = str(member.get("id") or "")
                item = await knowledge_storage.get(knowledge_id)
                if item and item.get("content"):
                    covered_knowledge_ids.add(knowledge_id)
                    add_context(
                        "knowledge_pack",
                        f"\n\n### {member.get('relative_path', item.get('title', knowledge_id))}\n"
                        f"{item['content']}",
                    )
    if knowledge_storage is not None and agent.knowledge:
        for kid in agent.knowledge:
            if kid in covered_knowledge_ids:
                continue
            item = await knowledge_storage.get(kid)
            if item and item.get("content"):
                add_context(
                    "knowledge",
                    (
                        f"\n\n## Conocimiento: {item.get('title', kid)}\n{item['content']}"
                    ),
                )

    # Knowledge adjuntado puntualmente a este mensaje desde el chat (vía "@" en
    # el composer) — ya viene resuelto y autorizado por el llamador (la ruta
    # valida ownership/permiso de grupo antes de construir esta lista).
    if attached_knowledge:
        for item in attached_knowledge:
            content = item.get("content")
            if content:
                add_context(
                    "attached_knowledge",
                    (
                        f"\n\n## Conocimiento adjunto: {item.get('title', item.get('id', ''))}\n{content}"
                    ),
                )

    # Memory injection
    if agent.use_memory and memory_storage is not None:
        mem_file = agent.memory_file or f"{agent.id}.md"
        mem_content = await memory_storage.get(mem_file)
        if mem_content and mem_content.strip():
            add_context("agent_memory", f"\n\n## Memoria del agente\n{mem_content}")

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
                add_context(
                    "prompt",
                    f"\n\n## Prompt: {p.get('name', alias)}\n{p.get('content', '')}",
                )

    # Recuerdo de conversaciones anteriores del mismo usuario con este agente
    if agent.use_memory and chat_storage is not None and user_id:
        try:
            memory_messages = await chat_storage.list_memory_messages(
                user_id,
                agent.id,
                conversation_id,
                limit=200,
                chars_per_message=2_000,
            )
        except DB_ERRORS as exc:
            # Sin memoria el agente responde igual, solo que sin recordar la
            # conversación anterior — y eso, sin registro, se lee como "el
            # agente se ha vuelto tonto" y no como un fallo de BD.
            flog.warning(
                f"[chat] Memoria no recuperada para {agent.id}: {exc}",
                username=user_id or "-",
            )
            memory_messages = []
        past_lines: List[str] = []
        past_tokens = 0
        for message in memory_messages:
            label = "Usuario" if message.get("role") == "user" else "Agente"
            line = f"**{label}:** {message.get('content', '')}"
            past_tokens += _estimate_tokens(line)
            if past_tokens > _HISTORY_TOKEN_BUDGET:
                break
            past_lines.append(line)
        if past_lines:
            add_context(
                "conversation_memory",
                "\n\n## Conversaciones anteriores\n" + "\n\n".join(past_lines),
            )

    # Truncar history si el contexto es demasiado largo
    system = "".join(context_parts)
    _sys_tokens = _estimate_tokens(system)
    original_history = list(history)
    history = _truncate_history(
        original_history,
        _sys_tokens,
        max_context=_CONTEXT_TOKEN_BUDGET - output_reserve,
    )
    history_truncated = history != original_history
    if stream_state is not None:
        stream_state.start(
            tokens_in=_sys_tokens
            + sum(
                _estimate_tokens(str(message.get("content", ""))) for message in history
            ),
            connection_id=str(conn.get("id") or ""),
        )

    if truncated_sources or history_truncated:
        yield _sse(
            {
                "type": "context_warning",
                "code": "context_truncated",
                "message": "Parte del contexto se recortó para respetar el límite del modelo.",
                "sources": sorted(set(truncated_sources)),
            }
        )

    try:
        if conn_type in OPENAI_COMPAT_URLS:
            url = _openai_compat_chat_url(
                conn_type,
                str(conn.get("url") or ""),
            )
            _assert_provider_url(url)
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
                out,
                _do_openai_stream_with_dns_retry,
                url,
                headers,
                payload,
                timeout,
                llm_lease=llm_lease,
                stream_state=stream_state,
            ):
                yield frame
            reply, tok_in, tok_out = out[0]

        elif conn_type == "claude":
            url = (
                conn.get("url") or f"{PROVIDER_BASE_URLS['claude']}/messages"
            ).strip()
            if not url.endswith("/messages"):
                url = url.rstrip("/") + "/messages"
            _assert_provider_url(url)
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
                out,
                _do_claude_stream,
                url,
                headers,
                payload,
                timeout,
                llm_lease=llm_lease,
                stream_state=stream_state,
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
                out,
                _do_ollama_call,
                host,
                payload_o,
                timeout,
                ollama_api_key,
                llm_lease=llm_lease,
                stream_state=stream_state,
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

    except LLMCapacityError:
        flog.warning(
            f"[chat] Capacidad LLM agotada ({conn_type})",
            username=user_id or "-",
        )
        yield _sse(
            {
                "type": "error",
                "code": "llm_capacity_exceeded",
                "message": (
                    "El servidor está atendiendo el máximo de conversaciones "
                    "simultáneas. Inténtalo de nuevo en unos segundos."
                ),
            }
        )
    except UnsafeProviderURL as exc:
        # La URL la escribió el propio usuario en su conexión: decírselo no
        # filtra nada y es la única forma de que sepa qué arreglar.
        flog.warning(
            f"[chat] URL de proveedor bloqueada ({conn_type}): {exc}",
            username=user_id or "-",
        )
        yield _sse(
            {
                "type": "error",
                "code": "unsafe_url",
                "message": str(exc),
            }
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        flog.error(
            f"[chat] {conn_type} respondió HTTP {exc.code}: {body[:500]}",
            username=user_id or "-",
        )
        detail = _detalle_publico(body)
        if exc.code == 404 and conn_type in OPENAI_COMPAT_URLS:
            detail = (
                f"No se encontró el endpoint de chat o el modelo '{model}'. "
                f"URL utilizada: {url}. Revisa que la conexión use una URL base "
                "OpenAI-compatible y que el modelo admita conversaciones."
            )
        yield _sse(
            {
                "type": "error",
                "code": "provider_http_error",
                "message": f"El proveedor respondió HTTP {exc.code}: {detail}",
            }
        )
    except urllib.error.URLError as exc:
        # `exc` lleva el host y el motivo del fallo de red: al log, no al
        # navegador.
        flog.error(
            f"[chat] Fallo de red hacia {conn_type}: {exc}", username=user_id or "-"
        )
        yield _sse(
            {
                "type": "error",
                "code": "provider_unreachable",
                "message": "No se pudo contactar con el proveedor.",
            }
        )
    except Exception as exc:  # noqa: BLE001
        # Red de seguridad final del stream, deliberadamente ancha: una vez que
        # el SSE empezó a emitir, el handler global de app.py ya no puede
        # intervenir, así que cualquier fallo tiene que convertirse aquí en una
        # trama de error o el cliente se queda colgado.
        # str(exc) puede llevar rutas, hosts internos o trozos de SQL, y por eso
        # solo se registra en el log, nunca se envía al cliente.
        flog.error(
            f"[chat] Error no controlado: {type(exc).__name__}: {exc}",
            username=user_id or "-",
        )
        yield _sse(
            {
                "type": "error",
                "code": "internal_error",
                "message": "Error interno al hablar con el proveedor.",
            }
        )
