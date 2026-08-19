"""La llamada a cada proveedor, y la comprobación de a dónde se llama.

El formato de hilo es lo único que cambia entre ellos y es lo único que debería
cambiar: OpenAI-compat y Claude mandan SSE (`data: ` delante), **Ollama manda
NDJSON** —un objeto JSON por línea, sin prefijo—.

`_assert_provider_url` es la puerta anti-SSRF: la URL del proveedor la escribe
el usuario en una conexión, así que apuntar a `169.254.169.254` es un intento
razonable. `_detalle_publico` decide qué se le cuenta del fallo sin filtrar la
clave ni el host interno.
"""


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
    Callable,
    Dict,
    Optional,
)

if TYPE_CHECKING:
    pass
from urllib.parse import urlparse, urlunparse

from app.config.providers import (
    OPENAI_COMPAT_URLS,
)
from app.config.security import PRIVATE_HOST_PREFIXES, assert_safe_url
from app.utils import flog
from app.utils.safe_http import safe_urlopen

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
    with safe_urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                # Trama SSE que no es JSON (keep-alive del proveedor, línea
                # partida). Se descarta sin registrar: esto corre una vez por
                # token y logearlo llenaría el fichero de logs con cada chat.
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
    with safe_urlopen(req, timeout=timeout) as resp:
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

class UnsafeProviderURL(ValueError):
    """La URL configurada en la conexión apunta a la red interna."""

def _assert_provider_url(url: str) -> None:
    """SSRF: la URL de un proveedor la escribe el usuario y apunta fuera.

    Ollama es la excepción deliberada (``_validate_ollama_host``): un servidor
    en ``localhost`` es su caso de uso normal. El resto de proveedores son
    servicios remotos, así que una URL hacia la red interna del despliegue solo
    puede ser un intento de alcanzarla a través del backend.
    """
    try:
        assert_safe_url(url)
    except ValueError as exc:
        raise UnsafeProviderURL(
            f"La URL de la conexión no está permitida: {exc}"
        ) from exc

# Claves donde los proveedores ponen el mensaje pensado para enseñar al usuario
# ("modelo no encontrado", "cuota agotada"). Lo que no encaje en esta forma es
# cuerpo ajeno —y con una URL apuntando dentro, contenido de la red interna—,
# así que no se reenvía.
def _detalle_publico(body: str) -> str:
    """Extrae el mensaje de negocio del proveedor; nunca el cuerpo crudo."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return "sin detalle utilizable en la respuesta."
    if not isinstance(parsed, dict):
        return "sin detalle utilizable en la respuesta."
    error = parsed.get("error") or {}
    detail = (
        parsed.get("detail")
        or parsed.get("message")
        or (error.get("message") if isinstance(error, dict) else error)
    )
    if not isinstance(detail, str) or not detail.strip():
        return "sin detalle utilizable en la respuesta."
    return detail.strip()[:500]

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
