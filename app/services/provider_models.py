"""Descubrimiento de modelos disponibles en proveedores externos."""

from __future__ import annotations

from typing import List

import httpx

from app.errors import APIError

_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "github": "GitHub Copilot",
    "ollama": "Ollama",
    "nvidia": "NVIDIA NIM",
    "google": "Google Gemini",
    "iagentshub": "iAgents Hub",
}


async def fetch_provider_models(
    provider: str, api_key: str, host: str = ""
) -> List[str]:
    """Llama al proveedor y devuelve lista de model IDs."""
    try:
        if provider == "anthropic":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
            r.raise_for_status()
            data = r.json()
            return [m["id"] for m in data.get("data", [])]

        if provider == "openai":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
            return sorted(m["id"] for m in data.get("data", []))

        if provider == "github":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://models.inference.ai.azure.com/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            return [
                m.get("id") or m.get("name", "")
                for m in items
                if m.get("id") or m.get("name")
            ]

        if provider == "ollama":
            base = (host or "http://localhost:11434").rstrip("/")
            from app.config.security import assert_safe_url as _assu

            _assu(base)  # C3: prevenir SSRF via hosts almacenados en cuentas
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{base}/api/tags", headers=headers)
            r.raise_for_status()
            data = r.json()
            return [m["name"] for m in data.get("models", [])]

        if provider == "nvidia":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            r.raise_for_status()
            data = r.json()
            return [m["id"] for m in data.get("data", [])]

        if provider == "google":
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
            r.raise_for_status()
            data = r.json()
            return [m["name"].split("/")[-1] for m in data.get("models", [])]

    except httpx.HTTPStatusError as exc:
        raise APIError(exc.response.status_code, "upstream_error", str(exc)) from exc
    except httpx.ConnectError:
        label = _PROVIDER_LABELS.get(provider, provider)
        raise APIError(
            502,
            "upstream_error",
            f"No se puede conectar con {label}. Comprueba que el servicio está activo y la URL es correcta.",
            extra={"provider": provider},
        ) from None
    except Exception as exc:
        raise APIError(502, "upstream_error", str(exc)) from exc

    return []
