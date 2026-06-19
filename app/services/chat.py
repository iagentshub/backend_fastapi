"""Servicio de chat — streaming SSE hacia los proveedores LLM."""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, AsyncGenerator, Dict, List, Optional

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


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _validate_ollama_host(host: str) -> None:
    """Rechaza hosts que apunten a rangos privados o de metadata de cloud (SSRF)."""
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Protocolo no permitido para Ollama: {parsed.scheme!r}")
    hostname = (parsed.hostname or "").lower()
    if hostname in ("localhost",):
        return  # localhost es el caso de uso legítimo habitual
    # Bloquear por prefijo de texto (cubre la mayoría de casos sin DNS)
    if any(hostname.startswith(p) for p in PRIVATE_HOST_PREFIXES):
        raise ValueError(f"Host Ollama no permitido: {hostname!r}")
    # Bloquear mediante ipaddress si es una IP literal
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError(f"Host Ollama no permitido: {hostname!r}")
    except ValueError as exc:
        if "no permitido" in str(exc):
            raise
        # No es una IP literal — es un hostname; se permite (el usuario lo configuró)


async def stream_chat(
    agent: "Dict[str, Any] | Agent",
    conn: Dict[str, Any],
    history: List[Dict[str, Any]],
    skill_storage: Any,
    memory_storage: Optional[Any] = None,
    knowledge_storage: Optional[Any] = None,
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
        for scope in ("public", "private"):
            sk = skill_storage.get(scope, sid)
            if sk:
                system += (
                    f"\n\n## Skill: {sk.get('name', sid)}\n{sk.get('content', '')}"
                )
                break

    # Knowledge injection (URLs + documents attached to the agent)
    if knowledge_storage is not None and agent.knowledge:
        for kid in agent.knowledge:
            item = knowledge_storage.get(kid)
            if item and item.get("content"):
                system += (
                    f"\n\n## Conocimiento: {item.get('title', kid)}\n{item['content']}"
                )

    # Memory injection
    if agent.use_memory and memory_storage is not None:
        mem_file = agent.memory_file or f"{agent.id}.md"
        mem_content = memory_storage.get(mem_file)
        if mem_content and mem_content.strip():
            system += f"\n\n## Memoria del agente\n{mem_content}"

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
            }
            if max_tokens:
                payload["max_tokens"] = int(max_tokens)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            payload["stream_options"] = {"include_usage": True}

            def _stream_openai() -> tuple:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url, data=data, headers=headers, method="POST"
                )
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
                            full_reply += (
                                choices[0].get("delta", {}).get("content") or ""
                            )
                        usage = obj.get("usage") or {}
                        if usage:
                            tok_in = usage.get("prompt_tokens", tok_in)
                            tok_out = usage.get("completion_tokens", tok_out)
                return full_reply, tok_in, tok_out

            reply, tok_in, tok_out = await asyncio.to_thread(_stream_openai)
            yield _sse(
                {
                    "type": "done",
                    "reply": reply,
                    "tokens": {"in": tok_in, "out": tok_out},
                }
            )

        elif conn_type == "claude":
            url = (
                conn.get("url") or f"{PROVIDER_BASE_URLS['claude']}/messages"
            ).strip()
            # Asegurar que termina en /messages
            if not url.endswith("/messages"):
                url = url.rstrip("/") + "/messages"

            payload = {
                "model": model,
                "messages": history,
                "temperature": temperature,
                "stream": True,
            }
            if system:
                payload["system"] = system
            if max_tokens:
                payload["max_tokens"] = int(max_tokens)
            else:
                payload["max_tokens"] = 4096
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            }

            def _stream_claude() -> tuple:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url, data=data, headers=headers, method="POST"
                )
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

            reply, tok_in, tok_out = await asyncio.to_thread(_stream_claude)
            yield _sse(
                {
                    "type": "done",
                    "reply": reply,
                    "tokens": {"in": tok_in, "out": tok_out},
                }
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

            def _call_ollama() -> tuple:
                data = json.dumps(payload_o).encode()
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

            reply, tok_in, tok_out = await asyncio.to_thread(_call_ollama)
            yield _sse(
                {
                    "type": "done",
                    "reply": reply,
                    "tokens": {"in": tok_in, "out": tok_out},
                }
            )

        else:
            yield _sse(
                {
                    "type": "error",
                    "message": f"Tipo de conexión '{conn_type}' no soportado",
                }
            )

    except urllib.error.URLError as exc:
        yield _sse({"type": "error", "message": f"Error de conexión: {exc}"})
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})


async def auto_update_memory(
    agent: "Dict[str, Any] | Agent",
    conn: Dict[str, Any],
    history: List[Dict[str, Any]],
    reply: str,
    memory_storage: Any,
) -> None:
    """Tras cada turno de chat, pide al LLM que actualice el fichero de memoria del agente."""
    from app.models.agent import Agent

    if not isinstance(agent, Agent):
        agent = Agent.from_dict(agent)
    mem_file = agent.memory_file or f"{agent.id}.md"
    existing = memory_storage.get(mem_file) or ""

    conv_lines: List[str] = []
    for m in history:
        label = "Usuario" if m.get("role") == "user" else "Agente"
        conv_lines.append(f"**{label}:** {m.get('content', '')}")
    conv_lines.append(f"**Agente:** {reply}")
    conv_text = "\n\n".join(conv_lines)

    mem_system = (
        "Eres un sistema de memoria para un agente de IA. "
        "Actualiza el fichero de memoria con los hechos importantes de esta conversación.\n"
        "Reglas:\n"
        "- Incluye solo hechos concretos y útiles: preferencias del usuario, datos personales "
        "relevantes, tareas pendientes, contexto del proyecto, decisiones tomadas.\n"
        "- Elimina información obsoleta o redundante de la memoria anterior.\n"
        "- Formato: lista de puntos en Markdown (- hecho).\n"
        "- Responde ÚNICAMENTE con el contenido del fichero de memoria actualizado, sin explicaciones."
    )

    user_content = ""
    if existing.strip():
        user_content += f"## Memoria actual\n{existing.strip()}\n\n"
    user_content += f"## Conversación\n{conv_text}"

    mem_agent: Dict[str, Any] = {
        "id": "_memory_updater",
        "system_prompt": mem_system,
        "temperature": 0.3,
        "max_tokens": 1500,
        "skills": [],
        "use_memory": False,
        "timeout": 60,
    }

    try:
        updated = ""
        async for chunk in stream_chat(
            mem_agent, conn, [{"role": "user", "content": user_content}], None, None
        ):
            if chunk.startswith("data: "):
                try:
                    ev = json.loads(chunk[6:].strip())
                    if ev.get("type") == "done":
                        updated = ev.get("reply", "")
                except Exception:
                    pass
        if updated.strip():
            memory_storage.save(mem_file, updated.strip())
    except Exception:
        pass  # Los errores de memoria no deben afectar al usuario
