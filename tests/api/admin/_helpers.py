"""Ayudantes compartidos por los tests de `app/api/routes/admin/`."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx


def _register(username, password="pass1234"):
    """Registra un usuario directamente, sin pasar por HTTP, para no contaminar cookies."""
    import asyncio

    from app.auth.auth import register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))


_AGENT_PAYLOAD = {
    "name": "Admin Test Agent",
    "system_prompt": "Test.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


# Se inserta directamente en la BD (misma ruta que usa el endpoint admin)
# para evitar la divergencia con el _storage de module-level de
# connections.py.
def _insert_connection(owner_id: str = "testadmin") -> str:
    import asyncio

    from app.storage.connection_storage import ConnectionStorage

    c = asyncio.run(
        ConnectionStorage().save(
            {
                "type": "openai",
                "label": "test-conn",
                "api_key": "sk-test",
                "model": "gpt-4o",
            },
            owner_id=owner_id,
        )
    )
    return c["id"]


def _mock_ghcr_token_response():
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"token": "test-read-token"}
    return resp


def _mock_ghcr_tags_response(names):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.headers = {}
    resp.json.return_value = {"name": "iagentshub/app", "tags": names}
    return resp


def _ghcr_fake_get(tags_names):
    async def fake_get(*args, **kwargs):
        url = args[-1]
        if url == "https://ghcr.io/token":
            return _mock_ghcr_token_response()
        if "ghcr.io/v2/iagentshub/app/tags/list" in url:
            return _mock_ghcr_tags_response(tags_names)
        raise httpx.ConnectError(f"URL no esperada en el test: {url}")

    return fake_get
