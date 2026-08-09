"""NVIDIA NIM provider — fields + test."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from app.config.providers import PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS
from app.utils.safe_http import safe_urlopen

from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["nvidia"]
_TEST_MODEL = "meta/llama-3.1-8b-instruct"


@register
class NvidiaProvider(BaseProvider):
    type_id = "nvidia"
    label = "NVIDIA NIM"
    icon = ""
    fields = [
        FieldDef("api_key", "API Key", "password", "nvapi-...", required=True),
        FieldDef(
            "model", "Modelo por defecto", "text", PROVIDER_DEFAULT_MODELS["nvidia"]
        ),
        FieldDef("url", "URL", "text", default=f"{_BASE_URL}/chat/completions"),
    ]

    @classmethod
    def test(cls, config: Dict[str, Any]) -> TestResult:
        """Valida la conexión en dos pasos rápidos, sin generar con el modelo.

        Generar con el modelo configurado no sirve como test: un modelo de
        razonamiento puede tardar minutos hasta el primer token (NVIDIA no
        envía el 200 hasta entonces) y el test fallaría por timeout aunque la
        conexión sea perfecta. En cambio, un error de credencial o de modelo
        llega en las cabeceras al instante. Así que:
          1. La API key se valida con un modelo LIGERO (~1 s).
          2. El modelo configurado se comprueba en el catálogo /models (instantáneo).
        """
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            return TestResult(False, "Falta la API Key")

        auth = cls._probe_auth(api_key)
        if not auth.ok:
            return auth

        model = (config.get("model") or "").strip()
        if model and model != _TEST_MODEL:
            available = cls._model_available(api_key, model)
            if available is False:
                return TestResult(
                    False,
                    "Modelo no disponible",
                    f"'{model}' no está en el catálogo de NVIDIA NIM.",
                )
        return TestResult(True, "OK")

    @classmethod
    def _probe_auth(cls, api_key: str) -> TestResult:
        """Comprueba la credencial con una generación mínima y un modelo ligero."""
        try:
            payload = json.dumps(
                {
                    "model": _TEST_MODEL,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                }
            ).encode()
            req = urllib.request.Request(
                f"{_BASE_URL}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with safe_urlopen(req, timeout=20) as r:
                r.read()
            return TestResult(True, "OK")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                msg = (
                    json.loads(body).get("detail")
                    or json.loads(body).get("message")
                    or body[:200]
                )
            except Exception:
                msg = body[:200]
            return TestResult(
                False, f"HTTP {e.code}", msg
            )  # nvidia usa "detail", no "error.message"
        except Exception as e:
            return TestResult(False, "Error de conexión", str(e))

    @classmethod
    def _model_available(cls, api_key: str, model: str) -> Optional[bool]:
        """True/False si el modelo está en el catálogo; None si no se pudo comprobar.

        None (no penaliza): si /models no responde no marcamos la conexión como
        rota — la credencial ya se validó en el paso anterior.
        """
        try:
            req = urllib.request.Request(
                f"{_BASE_URL}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with safe_urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            ids = {m.get("id") for m in (data.get("data") or [])}
            return model in ids
        except Exception:
            return None
