"""Ollama provider — fields + test."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from .base import BaseProvider, FieldDef, TestResult, register


@register
class OllamaProvider(BaseProvider):
    type_id = "ollama"
    label = "Ollama"
    icon = ""
    fields = [
        FieldDef("host", "URL del servidor", "text", "http://localhost:11434", False, "http://localhost:11434"),
        FieldDef("model", "Modelo (opcional)", "text", "ej: llama3, mistral:latest"),
    ]

    @classmethod
    def _fetch_tags(cls, host: str) -> dict:
        req = urllib.request.Request(f"{host}/api/tags")
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())

    @classmethod
    def _alt_host(cls, host: str) -> str | None:
        """Devuelve URL alternativa para entornos Docker vs local."""
        if "host.docker.internal" in host:
            return host.replace("host.docker.internal", "localhost")
        if "localhost" in host or "127.0.0.1" in host:
            return host.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
        return None

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        host = (config.get("host") or "http://localhost:11434").strip().rstrip("/")
        try:
            data = cls._fetch_tags(host)
        except urllib.error.HTTPError as e:
            return TestResult(False, f"HTTP {e.code}", str(e))
        except OSError:
            # DNS failure — try the Docker/local alternative
            alt = cls._alt_host(host)
            if not alt:
                return TestResult(False, "Sin conexión al servidor Ollama", f"No se pudo resolver {host}")
            try:
                data = cls._fetch_tags(alt)
            except urllib.error.HTTPError as e:
                return TestResult(False, f"HTTP {e.code}", str(e))
            except Exception as e:
                return TestResult(False, "Sin conexión al servidor Ollama", str(e))
        except Exception as e:
            return TestResult(False, "Sin conexión al servidor Ollama", str(e))
        models = data.get("models") or []
        names = ", ".join(m.get("name", "?") for m in models[:5])
        suffix = f"… (+{len(models)-5})" if len(models) > 5 else ""
        return TestResult(True, f"OK — {len(models)} modelos", f"{names}{suffix}")
