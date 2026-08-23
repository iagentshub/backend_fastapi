"""Orquestación del catálogo; cada proveedor conserva su protocolo en connections/."""

from __future__ import annotations

import asyncio
import urllib.error
from typing import List

from app.connections import UnsafeProviderURL, get_account_provider, get_provider
from app.errors import APIError


async def fetch_provider_models(
    provider: str, api_key: str, host: str = ""
) -> List[str]:
    implementation = get_account_provider(provider) or get_provider(provider)
    if implementation is None:
        return []
    config = {"api_key": api_key, "host": host}
    try:
        implementation.validate_config(config, purpose="models")
        return await asyncio.to_thread(implementation.fetch_models, config)
    except UnsafeProviderURL as exc:
        raise APIError(
            422, "unsafe_url", str(exc), extra={"field": "host"}
        ) from exc
    except urllib.error.HTTPError as exc:
        raise APIError(exc.code, "upstream_error", str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise APIError(
            502,
            "upstream_error",
            f"No se puede consultar {implementation.label}: {exc}",
            extra={"provider": provider},
        ) from exc
