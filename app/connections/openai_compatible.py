"""Contrato común para proveedores que implementan la API de chat de OpenAI."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from app.connections.cancellation import ProviderCancellation
from app.utils.safe_http import assert_url_allowed, safe_urlopen

from .base import BaseProvider, ChatInvocation, TestResult, UnsafeProviderURL


def _stream(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: Optional[int],
    on_token: Optional[Callable[[str], None]] = None,
    cancellation: Optional[ProviderCancellation] = None,
) -> tuple[str, int, int]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    full_reply = ""
    tok_in = tok_out = 0
    with safe_urlopen(request, timeout=timeout) as response:
        if cancellation is not None:
            cancellation.attach(response)
        try:
            for raw in response:
                if cancellation is not None and cancellation.cancelled:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
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
        except (OSError, ValueError):
            if cancellation is None or not cancellation.cancelled:
                raise
        finally:
            if cancellation is not None:
                cancellation.detach(response)
    return full_reply, tok_in, tok_out


def _stream_with_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: Optional[int],
    on_token: Optional[Callable[[str], None]] = None,
    cancellation: Optional[ProviderCancellation] = None,
) -> tuple[str, int, int]:
    """Reintenta fallos transitorios solamente antes del primer token."""
    for attempt in range(3):
        if cancellation is not None and cancellation.cancelled:
            return "", 0, 0
        emitted = False

        def forward(token: str) -> None:
            nonlocal emitted
            emitted = emitted or bool(token)
            if on_token is not None:
                on_token(token)

        try:
            return _stream(url, headers, payload, timeout, forward, cancellation)
        except urllib.error.HTTPError as exc:
            if emitted or exc.code not in (502, 503, 504) or attempt == 2:
                raise
            exc.close()
        except TimeoutError:
            if emitted or attempt == 2:
                raise
        except urllib.error.URLError as exc:
            reason = exc.reason
            errno = getattr(reason, "errno", None)
            transient = isinstance(reason, (socket.gaierror, TimeoutError)) or errno in (
                -5,
                -2,
                11001,
            )
            if emitted or not transient or attempt == 2:
                raise
        if cancellation is not None:
            if cancellation.wait(attempt + 1):
                return "", 0, 0
        else:
            time.sleep(attempt + 1)
    raise RuntimeError("No se pudo contactar con el proveedor")


class OpenAICompatibleProvider(BaseProvider):
    """Implementación común; cada proveedor declara únicamente sus diferencias."""

    supports_chat = True
    base_url: str = ""
    default_chat_url: str = ""

    @classmethod
    def _chat_url(cls, configured_url: str = "") -> str:
        raw = configured_url.strip()
        if not raw:
            return cls.default_chat_url
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.rstrip("/")
        path = parsed.path.rstrip("/")
        if path.endswith("/chat/completions"):
            pass
        elif path.endswith("/models"):
            path = path[: -len("/models")] + "/chat/completions"
        elif path.endswith("/v1") or path.endswith("/openai"):
            path += "/chat/completions"
        elif not path:
            path = urlparse(cls.default_chat_url).path
        else:
            path += "/chat/completions"
        return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))

    @classmethod
    def validate_config(
        cls, config: Dict[str, Any], *, purpose: str = "use"
    ) -> None:
        if purpose == "chat":
            url = cls._chat_url(str(config.get("url") or ""))
        else:
            url = str(config.get("url") or "").strip() or cls.default_chat_url
        try:
            assert_url_allowed(url)
        except ValueError as exc:
            raise UnsafeProviderURL(
                f"La URL de la conexión no está permitida: {exc}"
            ) from exc

    @classmethod
    def fetch_models(cls, config: Dict[str, Any]) -> List[str]:
        api_key = str(config.get("api_key") or "").strip()
        request = urllib.request.Request(
            f"{cls.base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with safe_urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
        return sorted(
            item["id"] for item in (data.get("data") or []) if item.get("id")
        )

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            models = cls.fetch_models(config)
            return TestResult(True, f"OK — {len(models)} modelos disponibles")
        except urllib.error.HTTPError as exc:
            return TestResult(False, "Error de autenticación", cls._http_error_msg(exc))
        except (OSError, ValueError) as exc:
            return TestResult(False, "Error de conexión", str(exc))

    @classmethod
    def _augment_payload(
        cls, payload: Dict[str, Any], *, model: str, max_tokens: int | None
    ) -> None:
        return

    @classmethod
    def prepare_chat(
        cls,
        config: Dict[str, Any],
        *,
        model: str,
        history: List[Dict[str, Any]],
        system: str,
        temperature: float,
        max_tokens: int | None,
        effort_level: str | None,
        timeout: int | None,
    ) -> ChatInvocation:
        cls.validate_config(config, purpose="chat")
        url = cls._chat_url(str(config.get("url") or ""))
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(history)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if effort_level:
            payload["reasoning_effort"] = effort_level
        cls._augment_payload(payload, model=model, max_tokens=max_tokens)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {str(config.get('api_key') or '')}",
        }
        return ChatInvocation(_stream_with_retry, (url, headers, payload, timeout), url)

    @classmethod
    def http_error_detail(
        cls, status: int, model: str, invocation: ChatInvocation
    ) -> str | None:
        if status != 404:
            return None
        return (
            f"No se encontró el endpoint de chat o el modelo '{model}'. "
            f"URL utilizada: {invocation.url}. Revisa que la conexión use una URL "
            "base OpenAI-compatible y que el modelo admita conversaciones."
        )
