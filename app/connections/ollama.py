"""Ollama: política de destino, catálogo, diagnóstico y chat NDJSON."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Dict, List, Optional

from app.config.providers import OLLAMA_ALLOWED_INTERNAL_ORIGINS
from app.connections.cancellation import ProviderCancellation
from app.utils.safe_http import assert_url_allowed, safe_urlopen

from .base import (
    BaseProvider,
    ChatInvocation,
    FieldDef,
    TestResult,
    UnsafeProviderURL,
    register,
)

DEFAULT_HOST = "http://localhost:11434"


def _stream(
    host: str,
    payload: Dict[str, Any],
    timeout: Optional[int],
    api_key: str = "",
    on_token: Optional[Callable[[str], None]] = None,
    cancellation: Optional[ProviderCancellation] = None,
) -> tuple[str, int, int]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    full_reply = ""
    tok_in = tok_out = 0
    with safe_urlopen(
        request,
        timeout=timeout,
        allowed_internal_origins=OLLAMA_ALLOWED_INTERNAL_ORIGINS,
    ) as response:
        if cancellation is not None:
            cancellation.attach(response)
        try:
            for raw in response:
                if cancellation is not None and cancellation.cancelled:
                    break
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
                tok_in = obj.get("prompt_eval_count", tok_in)
                tok_out = obj.get("eval_count", tok_out)
        except (OSError, ValueError):
            if cancellation is None or not cancellation.cancelled:
                raise
        finally:
            if cancellation is not None:
                cancellation.detach(response)
    return full_reply, tok_in, tok_out


@register
class OllamaProvider(BaseProvider):
    type_id = "ollama"
    account_type_id = "ollama"
    supports_chat = True
    expand_models_on_list = True
    label = "Ollama"
    icon = ""
    fields = [
        FieldDef(
            "host", "URL del servidor", "text", DEFAULT_HOST, False, DEFAULT_HOST
        ),
        FieldDef(
            "model", "Modelo (opcional)", "text", "ej: llama3, mistral:latest"
        ),
        FieldDef("api_key", "API Key (opcional, para Ollama Cloud)", "password"),
    ]

    @classmethod
    def normalize_host(cls, config: Dict[str, Any] | str) -> str:
        raw = config if isinstance(config, str) else config.get("host")
        return str(raw or DEFAULT_HOST).strip().rstrip("/")

    @classmethod
    def validate_config(
        cls, config: Dict[str, Any], *, purpose: str = "use"
    ) -> None:
        host = cls.normalize_host(config)
        try:
            assert_url_allowed(
                host,
                allowed_internal_origins=OLLAMA_ALLOWED_INTERNAL_ORIGINS,
            )
        except ValueError as exc:
            raise UnsafeProviderURL(f"Host Ollama no permitido: {exc}") from exc

    @classmethod
    def _fetch_tags(cls, host: str, api_key: str = "") -> dict:
        """Compatibilidad interna: toda llamada conserva política y DNS pinning."""
        normalized = cls.normalize_host(host)
        cls.validate_config({"host": normalized}, purpose="models")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        request = urllib.request.Request(f"{normalized}/api/tags", headers=headers)
        with safe_urlopen(
            request,
            timeout=8,
            allowed_internal_origins=OLLAMA_ALLOWED_INTERNAL_ORIGINS,
        ) as response:
            return json.loads(response.read())

    @classmethod
    def _alt_host(cls, host: str) -> str | None:
        alternatives = {
            "http://localhost:11434": "http://host.docker.internal:11434",
            "http://127.0.0.1:11434": "http://host.docker.internal:11434",
            "http://host.docker.internal:11434": "http://localhost:11434",
        }
        return alternatives.get(cls.normalize_host(host))

    @classmethod
    def fetch_models(cls, config: Dict[str, Any]) -> List[str]:
        cls.validate_config(config, purpose="models")
        host = cls.normalize_host(config)
        api_key = str(config.get("api_key") or "").strip()
        try:
            data = cls._fetch_tags(host, api_key)
        except urllib.error.HTTPError:
            raise
        except OSError:
            alternative = cls._alt_host(host)
            if alternative is None:
                raise
            data = cls._fetch_tags(alternative, api_key)
        return [item["name"] for item in (data.get("models") or []) if item.get("name")]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        try:
            models = cls.fetch_models(config)
        except UnsafeProviderURL as exc:
            return TestResult(False, "Host Ollama no permitido", str(exc))
        except urllib.error.HTTPError as exc:
            return TestResult(False, f"HTTP {exc.code}", str(exc))
        except (OSError, ValueError) as exc:
            return TestResult(False, "Sin conexión al servidor Ollama", str(exc))
        names = ", ".join(models[:5])
        suffix = f"… (+{len(models) - 5})" if len(models) > 5 else ""
        return TestResult(True, f"OK — {len(models)} modelos", f"{names}{suffix}")

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
        host = cls.normalize_host(config)
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(history)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        return ChatInvocation(
            _stream,
            (host, payload, timeout, str(config.get("api_key") or "")),
            f"{host}/api/chat",
        )
