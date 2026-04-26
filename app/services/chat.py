"""Servicio de chat — streaming SSE hacia los proveedores LLM."""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import urlparse

from app.config.providers import ANTHROPIC_API_VERSION, OPENAI_COMPAT_URLS, PROVIDER_BASE_URLS
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
    agent: Dict[str, Any],
    conn: Dict[str, Any],
    history: List[Dict[str, Any]],
    skill_storage: Any,
    memory_storage: Optional[Any] = None,
) -> AsyncGenerator[str, None]:
    import asyncio

    conn_type = str(conn.get("type") or "").lower()
    api_key = str(conn.get("api_key") or "")
    # Model: prefer connection's model, fall back to agent's stored model
    model = str(conn.get("model") or agent.get("model") or "")
    temperature = float(agent.get("temperature") if agent.get("temperature") is not None else 0.7)
    max_tokens = agent.get("max_tokens")
    timeout = int(agent.get("timeout")) if agent.get("timeout") is not None else 120

    # System prompt + skills
    system = str(agent.get("system_prompt") or "").strip()
    for sid in (agent.get("skills") or []):
        for scope in ("public", "private"):
            sk = skill_storage.get(scope, sid)
            if sk:
                system += f"\n\n## Skill: {sk.get('name', sid)}\n{sk.get('content', '')}"
                break

    # Memory injection
    if agent.get("use_memory") and memory_storage is not None:
        mem_file = str(agent.get("memory_file") or f"{agent.get('id', 'agent')}.md")
        mem_content = memory_storage.get(mem_file)
        if mem_content and mem_content.strip():
            system += f"\n\n## Memoria del agente\n{mem_content}"

    try:
        if conn_type in OPENAI_COMPAT_URLS:
            url = OPENAI_COMPAT_URLS[conn_type]
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

            def _stream_openai() -> str:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                full_reply = ""
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
                            delta = obj["choices"][0]["delta"].get("content") or ""
                            full_reply += delta
                        except Exception:
                            pass
                return full_reply

            reply = await asyncio.to_thread(_stream_openai)
            yield _sse({"type": "done", "reply": reply, "tokens": {}})

        elif conn_type == "claude":
            url = f"{PROVIDER_BASE_URLS['claude']}/messages"
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

            def _stream_claude() -> str:
                data = json.dumps(payload).encode()
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                full_reply = ""
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data: "):
                            continue
                        try:
                            obj = json.loads(line[6:])
                            if obj.get("type") == "content_block_delta":
                                full_reply += obj.get("delta", {}).get("text", "")
                        except Exception:
                            pass
                return full_reply

            reply = await asyncio.to_thread(_stream_claude)
            yield _sse({"type": "done", "reply": reply, "tokens": {}})

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

            def _call_ollama() -> str:
                data = json.dumps(payload_o).encode()
                req = urllib.request.Request(
                    f"{host}/api/chat", data=data,
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return body.get("message", {}).get("content") or ""

            reply = await asyncio.to_thread(_call_ollama)
            yield _sse({"type": "done", "reply": reply, "tokens": {}})

        else:
            yield _sse({"type": "error", "message": f"Tipo de conexión '{conn_type}' no soportado"})

    except urllib.error.URLError as exc:
        yield _sse({"type": "error", "message": f"Error de conexión: {exc}"})
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
