"""Anthropic / Claude provider — fields + test."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Dict, List, Optional

from app.config.providers import (
    ANTHROPIC_API_VERSION,
    PROVIDER_BASE_URLS,
)
from app.connections.cancellation import ProviderCancellation
from app.utils import flog
from app.utils.safe_http import assert_url_allowed, safe_urlopen

from .base import (
    BaseProvider,
    ChatInvocation,
    FieldDef,
    TestResult,
    UnsafeProviderURL,
    register,
)

_BASE_URL = PROVIDER_BASE_URLS["claude"]


def _stream(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: Optional[int],
    on_token: Optional[Callable[[str], None]] = None,
    cancellation: Optional[ProviderCancellation] = None,
) -> tuple[str, int, int]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
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
                try:
                    obj = json.loads(line[6:])
                    event_type = obj.get("type")
                    if event_type == "message_start":
                        tok_in = (obj.get("message", {}).get("usage") or {}).get(
                            "input_tokens", 0
                        )
                    elif event_type == "content_block_delta":
                        token = obj.get("delta", {}).get("text", "")
                        full_reply += token
                        if token and on_token is not None:
                            on_token(token)
                    elif event_type == "message_delta":
                        tok_out = (obj.get("usage") or {}).get(
                            "output_tokens", tok_out
                        )
                except (json.JSONDecodeError, AttributeError, TypeError) as exc:
                    flog.warning(f"[chat] Evento Anthropic inválido omitido: {exc}")
        except (OSError, ValueError):
            if cancellation is None or not cancellation.cancelled:
                raise
        finally:
            if cancellation is not None:
                cancellation.detach(response)
    return full_reply, tok_in, tok_out


@register
class AnthropicProvider(BaseProvider):
    type_id = "claude"
    account_type_id = "anthropic"
    supports_chat = True
    label = "Anthropic (Claude)"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "sk-ant-...", required=True),
        FieldDef("model", "Modelo", "text"),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/messages"),
    ]

    @classmethod
    def _messages_url(cls, configured_url: str = "") -> str:
        url = configured_url.strip() or f"{_BASE_URL}/messages"
        return url if url.endswith("/messages") else url.rstrip("/") + "/messages"

    @classmethod
    def validate_config(
        cls, config: Dict[str, Any], *, purpose: str = "use"
    ) -> None:
        url = cls._messages_url(str(config.get("url") or ""))
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
            f"{_BASE_URL}/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
        )
        with safe_urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
        return [item["id"] for item in (data.get("data") or []) if item.get("id")]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")
        try:
            req = urllib.request.Request(
                f"{_BASE_URL}/models?limit=1",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_API_VERSION,
                },
            )
            with safe_urlopen(req, timeout=15) as r:
                r.read()
            return TestResult(True, "OK — API key válida")
        except urllib.error.HTTPError as e:
            return TestResult(False, f"HTTP {e.code}", cls._http_error_msg(e))
        except (OSError, ValueError) as e:
            # OSError cubre URLError, timeouts y fallos de socket/DNS;
            # ValueError, el JSONDecodeError de una respuesta que no es JSON.
            # El mensaje viaja al usuario en TestResult.detail.
            return TestResult(False, "Error de conexión", str(e))

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
        url = cls._messages_url(str(config.get("url") or ""))
        payload: Dict[str, Any] = {
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
            "x-api-key": str(config.get("api_key") or ""),
            "anthropic-version": ANTHROPIC_API_VERSION,
        }
        return ChatInvocation(_stream, (url, headers, payload, timeout), url)
