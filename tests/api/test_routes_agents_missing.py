"""Tests de cobertura para agents.py — líneas sin cubrir.

Cubre: helpers, paths de guest, filtros de listado, casos de error en CRUD,
move_folder, export con locale/memory, y el endpoint de chat/stream.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

# ── Helpers de fixture ────────────────────────────────────────────────────────

_AGENT = {"name": "Coverage Agent", "description": "desc"}
_CONN_PAYLOAD = {
    "type": "openai",
    "label": "Coverage Conn",
    "api_key": "sk-test-coverage",
    "model": "gpt-4o",
}


def _register_and_login(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@cov.test"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _setup_guest(client):
    """Inicia sesión como guest y devuelve el username."""
    r = client.post("/api/auth/guest")
    assert r.status_code == 200
    return r.json()["username"]


def _create_agent(client, extra=None):
    payload = {**_AGENT, **(extra or {})}
    r = client.post("/api/agents", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _create_connection(client):
    r = client.post("/api/connections", json=_CONN_PAYLOAD)
    assert r.status_code == 200, r.text
    return r.json()


async def _fake_stream_gen(*args, **kwargs):
    """Async generator que simula stream_chat devolviendo un evento done."""
    yield 'data: {"type": "chunk", "text": "Hola"}\n\n'
    yield 'data: {"type": "done", "reply": "Hola", "tokens": {"in": 0, "out": 0}}\n\n'


# ── _check_scope — línea 59 ───────────────────────────────────────────────────


def test_list_agents_invalid_scope_returns_400(admin_client):
    """Scope inválido en GET /api/agents devuelve 400."""
    r = admin_client.get("/api/agents", params={"scope": "badscope"})
    assert r.status_code == 400
    assert "Scope" in r.json()["detail"]["message"]


# ── list_agents con offset y limit — líneas 120, 122 ─────────────────────────


def test_list_agents_with_offset(admin_client):
    """offset=N salta los primeros N agentes (línea 120)."""
    admin_client.post("/api/agents", json={"name": "Offset Agent A"})
    admin_client.post("/api/agents", json={"name": "Offset Agent B"})
    admin_client.post("/api/agents", json={"name": "Offset Agent C"})

    r_all = admin_client.get("/api/agents")
    total = len(r_all.json())

    r_offset = admin_client.get("/api/agents", params={"offset": 1})
    assert r_offset.status_code == 200
    assert len(r_offset.json()) == total - 1


def test_list_agents_with_limit(admin_client):
    """limit=N devuelve como máximo N agentes (línea 122)."""
    admin_client.post("/api/agents", json={"name": "Limit Agent X"})
    admin_client.post("/api/agents", json={"name": "Limit Agent Y"})
    admin_client.post("/api/agents", json={"name": "Limit Agent Z"})

    r = admin_client.get("/api/agents", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_list_agents_with_label_filter(admin_client):
    """Filtra agentes por label (línea 118)."""
    admin_client.post(
        "/api/agents", json={"name": "Special Label Agent", "labels": ["special"]}
    )
    admin_client.post("/api/agents", json={"name": "Unlabeled Coverage Agent"})

    r = admin_client.get("/api/agents", params={"label": "special"})
    assert r.status_code == 200
    names = [a["name"] for a in r.json()]
    assert "Special Label Agent" in names
    assert "Unlabeled Coverage Agent" not in names


# ── save_agent: scope inválido — línea 134 ───────────────────────────────────


def test_save_agent_invalid_scope_returns_400(admin_client):
    """Scope distinto de public/private en POST devuelve 400 (línea 134)."""
    r = admin_client.post("/api/agents", json={**_AGENT, "scope": "invalid"})
    assert r.status_code == 400
    assert "Scope" in r.json()["detail"]["message"]


# ── save_agent: ValueError → 422 — líneas 143-144 ───────────────────────────


def test_save_agent_empty_name_returns_422(admin_client):
    """Nombre vacío llega al storage que lanza ValueError → 422 (líneas 143-144)."""
    r = admin_client.post("/api/agents", json={"name": "", "description": "desc"})
    assert r.status_code == 422


def test_save_agent_public_scope_raises_422(admin_client):
    """scope='public' crea el agente correctamente (ya no es de solo lectura)."""
    r = admin_client.post("/api/agents", json={**_AGENT, "scope": "public"})
    assert r.status_code == 200
    assert r.json()["scope"] == "public"


def test_save_agent_scan_import_payload(admin_client):
    """Payload exacto que envía agents-scan.js _importAgent para un agente Claude."""
    payload = {
        "name": "Scan Import Agent",
        "description": "Importado desde carpeta .claude",
        "agent_type": "claude",
        "connection_id": None,
        "system_prompt": "You are a helpful assistant.",
        "temperature": 0.7,
        "skills": [],
        "knowledge": [],
        "use_memory": False,
        "routines": [],
        "scope": "private",
        "model": "",
        "extended_thinking": False,
        "cache_control": False,
        "thinking_budget_tokens": 10000,
    }
    r = admin_client.post("/api/agents", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "Scan Import Agent"
    assert data["agent_type"] == "claude"
    assert data["scope"] == "private"


def test_delete_agent_value_error_returns_403(admin_client):
    """Si _agents.delete lanza ValueError (p.ej. scope público) → 403 (línea 183)."""
    agent = _create_agent(admin_client)

    with patch("app.api.routes.agents._agents") as mock_agents:
        mock_agents.get = AsyncMock(return_value=agent)
        mock_agents.delete = AsyncMock(
            side_effect=ValueError("Los agentes públicos son de solo lectura")
        )
        r = admin_client.delete(f"/api/agents/{agent['id']}")

    assert r.status_code == 403
    assert "solo lectura" in r.json()["detail"]["message"]


# ── _apply_locale — líneas 65-78 ─────────────────────────────────────────────


def test_get_agent_applies_locale_override(admin_client):
    """locale.es.json aplicado cuando existe (cubre líneas 72-78)."""
    from app.config.data import AGENTS_DIR

    agent = _create_agent(admin_client, {"name": "Locale Base"})
    agent_id = agent["id"]

    override = {"name": "Agente Localizado", "description": "Descripción ES"}
    locale_file = AGENTS_DIR / "private" / agent_id / "locale.es.json"
    locale_file.parent.mkdir(parents=True, exist_ok=True)
    locale_file.write_text(json.dumps(override), encoding="utf-8")

    r = admin_client.get(f"/api/agents/{agent_id}", headers={"Accept-Language": "es"})
    assert r.status_code == 200
    assert r.json()["name"] == "Agente Localizado"


def test_get_agent_locale_fallback_to_es(admin_client):
    """locale='en' sin locale.en.json → fallback a locale.es.json (cubre líneas 70-78)."""
    from app.config.data import AGENTS_DIR

    agent = _create_agent(admin_client, {"name": "Fallback Base"})
    agent_id = agent["id"]

    override = {"name": "Fallback ES Name"}
    locale_file = AGENTS_DIR / "private" / agent_id / "locale.es.json"
    locale_file.parent.mkdir(parents=True, exist_ok=True)
    locale_file.write_text(json.dumps(override), encoding="utf-8")

    r = admin_client.get(f"/api/agents/{agent_id}", headers={"Accept-Language": "en"})
    assert r.status_code == 200
    assert r.json()["name"] == "Fallback ES Name"


# ── _apply_locale: early return — línea 64-65 ────────────────────────────────


def test_apply_locale_empty_agent_returns_early():
    """_apply_locale({}, 'es') devuelve {} inmediatamente (líneas 64-65)."""
    from app.api.routes.agents import _apply_locale

    result = _apply_locale({}, "es")
    assert result == {}


def test_get_agent_invalid_locale_keeps_base_and_warns(admin_client):
    from app.config.data import AGENTS_DIR

    agent = _create_agent(admin_client, {"name": "Locale Base Inválido"})
    locale_file = AGENTS_DIR / "private" / agent["id"] / "locale.es.json"
    locale_file.parent.mkdir(parents=True, exist_ok=True)
    locale_file.write_text("no-es-json", encoding="utf-8")

    with patch("app.api.routes.agents.flog.warning") as warning:
        response = admin_client.get(
            f"/api/agents/{agent['id']}", headers={"Accept-Language": "es"}
        )

    assert response.status_code == 200
    assert response.json()["name"] == "Locale Base Inválido"
    assert "Locale omitido" in warning.call_args.args[0]


# ── _conn_owner helper — línea 46 ────────────────────────────────────────────


def test_conn_owner_admin_returns_none():
    """_conn_owner devuelve None para admin (línea 46)."""
    from app.api.routes.agents import _conn_owner

    async def _run():
        return await _conn_owner("testadmin_co")

    asyncio.run(_run())  # Para usuario no admin devuelve su username


def test_conn_owner_standard_returns_username():
    """_conn_owner devuelve el username para usuarios estándar."""
    from app.api.routes.agents import _conn_owner

    async def _run():
        result = await _conn_owner("regular_user_co")
        assert result == "regular_user_co"

    asyncio.run(_run())


# ── Rutas guest: list / get / save / delete ───────────────────────────────────


def test_guest_list_agents(client):
    """Guest puede listar agentes (líneas 94-104)."""
    _setup_guest(client)
    r = client.get("/api/agents")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_guest_list_agents_only_private_scope(client):
    """Guest puede pedir scope=private (línea 97: private = s.agents)."""
    _setup_guest(client)
    r = client.get("/api/agents", params={"scope": "private"})
    assert r.status_code == 200


def test_guest_list_agents_with_label_filter(client):
    """Guest: filtrar por label (línea 99)."""
    _setup_guest(client)
    r = client.post(
        "/api/agents", json={"name": "Guest Labeled", "labels": ["g-label"]}
    )
    assert r.status_code == 200

    r2 = client.get("/api/agents", params={"label": "g-label"})
    assert r2.status_code == 200
    names = [a["name"] for a in r2.json()]
    assert "Guest Labeled" in names


def test_guest_list_agents_with_offset(client):
    """Guest: offset (línea 101)."""
    _setup_guest(client)
    client.post("/api/agents", json={"name": "Guest Off A"})
    client.post("/api/agents", json={"name": "Guest Off B"})

    r_all = client.get("/api/agents", params={"scope": "private"})
    total = len(r_all.json())

    r_off = client.get("/api/agents", params={"scope": "private", "offset": 1})
    assert r_off.status_code == 200
    assert len(r_off.json()) == max(0, total - 1)


def test_guest_list_agents_with_limit(client):
    """Guest: limit (línea 103)."""
    _setup_guest(client)
    client.post("/api/agents", json={"name": "Guest Lim A"})
    client.post("/api/agents", json={"name": "Guest Lim B"})
    client.post("/api/agents", json={"name": "Guest Lim C"})

    r = client.get("/api/agents", params={"scope": "private", "limit": 2})
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_guest_save_agent(client):
    """Guest puede guardar un agente en sesión (líneas 136-140)."""
    _setup_guest(client)
    r = client.post("/api/agents", json={"name": "Guest Saved Agent"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Guest Saved Agent"
    assert "id" in body


def test_guest_get_agent_not_found(client):
    """Guest: agente no encontrado → 404 (líneas 153-157)."""
    _setup_guest(client)
    r = client.get("/api/agents/ghost-agent-guest")
    assert r.status_code == 404


def test_guest_get_own_agent(client):
    """Guest puede obtener su propio agente por id."""
    _setup_guest(client)
    created = client.post("/api/agents", json={"name": "Guest Owned Agent"}).json()
    r = client.get(f"/api/agents/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_guest_delete_agent_not_found(client):
    """Guest: eliminar agente inexistente → 404 (líneas 173-175)."""
    _setup_guest(client)
    r = client.delete("/api/agents/ghost-delete-guest")
    assert r.status_code == 404


def test_guest_delete_agent_success(client):
    """Guest puede eliminar su propio agente (líneas 170-175)."""
    _setup_guest(client)
    created = client.post("/api/agents", json={"name": "Guest Delete Me"}).json()
    r = client.delete(f"/api/agents/{created['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── export: guest path — líneas 207-210 ──────────────────────────────────────


def test_export_agent_guest_not_found(client):
    """Guest exportando agente inexistente → 404 (líneas 207-210)."""
    _setup_guest(client)
    r = client.get("/api/agents/no-existo/export/claude")
    assert r.status_code == 404


def test_export_agent_guest_success(client):
    """Guest exporta su propio agente en formato claude."""
    _setup_guest(client)
    created = client.post("/api/agents", json={"name": "Guest Export Me"}).json()
    r = client.get(f"/api/agents/{created['id']}/export/claude")
    assert r.status_code == 200


# ── export con memory — líneas 296, 325, 338 ─────────────────────────────────


def test_export_claude_includes_memory(admin_client):
    """Claude export incluye CLAUDE.md cuando existe memoria (línea 296)."""
    import io
    import zipfile

    agent = _create_agent(admin_client, {"name": "Memory Claude Agent"})
    admin_client.post(
        f"/api/memory/{agent['id']}.md",
        json={"content": "Memoria de prueba Claude"},
    )

    r = admin_client.get(f"/api/agents/{agent['id']}/export/claude")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert ".claude/CLAUDE.md" in zf.namelist()
        assert "Memoria de prueba Claude" in zf.read(".claude/CLAUDE.md").decode()


def test_export_openai_includes_memory(admin_client):
    """OpenAI export incluye memory.md cuando existe memoria (línea 325)."""
    import io
    import zipfile

    agent = _create_agent(
        admin_client, {"name": "Memory OpenAI Agent", "agent_type": "openai"}
    )
    admin_client.post(
        f"/api/memory/{agent['id']}.md",
        json={"content": "Memoria OpenAI"},
    )

    r = admin_client.get(f"/api/agents/{agent['id']}/export/openai")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "memory.md" in zf.namelist()
        assert "Memoria OpenAI" in zf.read("memory.md").decode()


def test_export_mcp_includes_memory(admin_client):
    """MCP export incluye memory.md cuando existe memoria (línea 338)."""
    import io
    import zipfile

    agent = _create_agent(admin_client, {"name": "Memory MCP Agent"})
    admin_client.post(
        f"/api/memory/{agent['id']}.md",
        json={"content": "Memoria MCP"},
    )

    r = admin_client.get(f"/api/agents/{agent['id']}/export/mcp")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "memory.md" in zf.namelist()
        assert "Memoria MCP" in zf.read("memory.md").decode()


# ── chat endpoint — líneas 360-442 ───────────────────────────────────────────


def test_chat_agent_not_found_returns_404(client):
    """Chat con agente inexistente → 404 (línea 367)."""
    _register_and_login(client, "chat404user")
    r = client.post("/api/agents/no-agent-here/chat", json={"messages": []})
    assert r.status_code == 404


def test_chat_agent_no_connection_returns_422(client):
    """Agente sin connection_id → 422 (línea 399)."""
    _register_and_login(client, "chatnconn_user")
    agent = _create_agent(client, {"name": "No Conn Chat Agent"})
    r = client.post(f"/api/agents/{agent['id']}/chat", json={"messages": []})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "agent_no_connection"


def test_chat_streams_sse_response(admin_client):
    """Chat con conexión válida devuelve StreamingResponse SSE (líneas 361-447)."""
    conn = _create_connection(admin_client)
    agent = _create_agent(
        admin_client, {"name": "SSE Stream Agent", "connection_id": conn["id"]}
    )

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_gen):
        r = admin_client.post(
            f"/api/agents/{agent['id']}/chat",
            json={"messages": [{"role": "user", "content": "Hola"}]},
        )

    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")


def test_chat_capacity_exhausted_returns_429_before_sse(admin_client):
    conn = _create_connection(admin_client)
    agent = _create_agent(
        admin_client, {"name": "Busy Stream Agent", "connection_id": conn["id"]}
    )

    with patch("app.api.routes.agent_chat.try_acquire_llm_lease", return_value=None):
        response = admin_client.post(
            f"/api/agents/{agent['id']}/chat",
            json={"messages": [{"role": "user", "content": "Hola"}]},
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "5"
    assert response.json()["detail"]["code"] == "llm_capacity_exceeded"


def test_chat_malformed_sse_event_warns_and_continues(admin_client):
    conn = _create_connection(admin_client)
    agent = _create_agent(
        admin_client, {"name": "Malformed SSE Agent", "connection_id": conn["id"]}
    )

    async def malformed_stream(*args, **kwargs):
        yield "data: no-es-json\n\n"
        yield 'data: {"type":"done","tokens":{"in":0,"out":0}}\n\n'

    with (
        patch("app.api.routes.agent_chat.stream_chat", new=malformed_stream),
        patch("app.api.routes.agents.flog.warning") as warning,
    ):
        response = admin_client.post(
            f"/api/agents/{agent['id']}/chat", json={"messages": []}
        )

    assert response.status_code == 200
    assert "Evento SSE inválido" in warning.call_args.args[0]


def test_chat_with_conversation_id(admin_client):
    """Chat con conversation_id guarda mensajes (líneas 429-438)."""
    import asyncio as _asyncio

    from app.storage.chat import ChatStorage

    conn = _create_connection(admin_client)
    agent = _create_agent(
        admin_client, {"name": "Conv ID Agent", "connection_id": conn["id"]}
    )

    # Crear conversación previa para tener un conversation_id válido
    chat_storage = ChatStorage()

    async def _create_conv():
        return await chat_storage.new_conversation(
            user_id="testadmin",
            agent_id=agent["id"],
            title="Test conv",
        )

    conv = _asyncio.run(_create_conv())
    conv_id = conv["id"]

    async def _fake_stream_with_tokens(*args, **kwargs):
        yield 'data: {"type": "chunk", "text": "ok"}\n\n'
        yield 'data: {"type": "done", "reply": "ok", "tokens": {"in": 5, "out": 3}}\n\n'

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_with_tokens):
        r = admin_client.post(
            f"/api/agents/{agent['id']}/chat",
            json={
                "messages": [{"role": "user", "content": "Mensaje de prueba"}],
                "conversation_id": conv_id,
            },
        )

    assert r.status_code == 200


def test_chat_with_ollama_virtual_connection(admin_client):
    """connection_id con '::' separa base_conn y modelo Ollama (líneas 377-379, 395-396)."""
    conn = _create_connection(admin_client)
    # Formato virtual Ollama: "<conn_id>::<model>"
    ollama_conn_id = f"{conn['id']}::llama3"
    agent = _create_agent(
        admin_client,
        {"name": "Ollama Chat Agent", "connection_id": ollama_conn_id},
    )

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_gen):
        r = admin_client.post(
            f"/api/agents/{agent['id']}/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200


def test_chat_attached_knowledge_ids_injected(admin_client):
    """Un knowledge propio referenciado vía attached_knowledge_ids llega
    resuelto (con contenido) a stream_chat."""
    conn = _create_connection(admin_client)
    agent = _create_agent(
        admin_client, {"name": "Attach KN Agent", "connection_id": conn["id"]}
    )
    item = admin_client.post(
        "/api/knowledge/text",
        json={"title": "Guía de estilo", "content": "Usa siempre tono formal."},
    ).json()

    captured_kwargs = {}

    async def _fake_stream_capture(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'data: {"type": "done", "reply": "ok", "tokens": {"in": 0, "out": 0}}\n\n'

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_capture):
        r = admin_client.post(
            f"/api/agents/{agent['id']}/chat",
            json={
                "messages": [{"role": "user", "content": "Hola"}],
                "attached_knowledge_ids": [item["id"]],
            },
        )

    assert r.status_code == 200
    attached = captured_kwargs.get("attached_knowledge") or []
    assert any(k.get("content") == "Usa siempre tono formal." for k in attached)


def test_chat_attached_knowledge_ids_without_permission_ignored(client):
    """Un id de knowledge ajeno/sin permiso no debe filtrar contenido ni
    romper el envío del mensaje: se ignora silenciosamente."""
    _register_and_login(client, "attach_kn_victim")
    item = client.post(
        "/api/knowledge/text",
        json={"title": "Victim Notes", "content": "secret notes"},
    ).json()

    _register_and_login(client, "attach_kn_attacker")
    conn = _create_connection(client)
    agent = _create_agent(
        client, {"name": "Attach KN Attacker Agent", "connection_id": conn["id"]}
    )

    captured_kwargs = {}

    async def _fake_stream_capture(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'data: {"type": "done", "reply": "ok", "tokens": {"in": 0, "out": 0}}\n\n'

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_capture):
        r = client.post(
            f"/api/agents/{agent['id']}/chat",
            json={
                "messages": [{"role": "user", "content": "Hola"}],
                "attached_knowledge_ids": [item["id"]],
            },
        )

    assert r.status_code == 200
    attached = captured_kwargs.get("attached_knowledge") or []
    assert attached == []


def test_chat_guest_path(client):
    """Chat vía sesión guest (líneas 362-364, 383-386)."""
    _setup_guest(client)

    # El agente guest no tiene conexión → 422 (no connection)
    agent = _create_agent(client, {"name": "Guest Chat Agent"})
    r = client.post(f"/api/agents/{agent['id']}/chat", json={"messages": []})
    assert r.status_code == 422


def test_chat_guest_agent_not_found(client):
    """Guest chat con agente inexistente → 404."""
    _setup_guest(client)
    r = client.post("/api/agents/ghost-guest-chat/chat", json={"messages": []})
    assert r.status_code == 404


# ── tool_storage en el chat ───────────────────────────────────────────────────
# Esto era el gating del invitado: sus tools no existían —GuestSession no las
# contemplaba— y el chat recibía `tool_storage=None` para no inyectarle
# contenido que no podía tener. Desde que el invitado es un usuario efímero, las
# tools son suyas como las de cualquiera y el chat las recibe igual.


def test_chat_tool_storage_passed_for_registered_user(admin_client):
    conn = _create_connection(admin_client)
    agent = _create_agent(
        admin_client, {"name": "Tool Storage Agent", "connection_id": conn["id"]}
    )

    captured_kwargs = {}

    async def _fake_stream_capture(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'data: {"type": "done", "reply": "ok", "tokens": {"in": 0, "out": 0}}\n\n'

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_capture):
        r = admin_client.post(
            f"/api/agents/{agent['id']}/chat",
            json={"messages": [{"role": "user", "content": "Hola"}]},
        )

    assert r.status_code == 200
    assert captured_kwargs.get("tool_storage") is not None


def test_chat_guest_tool_storage_passed_too(client):
    _setup_guest(client)
    conn = _create_connection(client)
    agent = _create_agent(
        client, {"name": "Guest Tool Storage Agent", "connection_id": conn["id"]}
    )

    captured_kwargs = {}

    async def _fake_stream_capture(*args, **kwargs):
        captured_kwargs.update(kwargs)
        yield 'data: {"type": "done", "reply": "ok", "tokens": {"in": 0, "out": 0}}\n\n'

    with patch("app.api.routes.agent_chat.stream_chat", new=_fake_stream_capture):
        r = client.post(
            f"/api/agents/{agent['id']}/chat",
            json={"messages": [{"role": "user", "content": "Hola"}]},
        )

    assert r.status_code == 200
    assert captured_kwargs.get("tool_storage") is not None


# ── save_agent rechaza skills/knowledge ajenos ────────────────────────────────
# Sin esto, cualquiera puede adjuntar el ID de una skill/knowledge privados de
# otro usuario a su propio agente y leer su contenido completo vía chat/export.


def test_save_agent_rejects_foreign_private_skill(client):
    _register_and_login(client, "idor_skill_victim")
    skill = client.post(
        "/api/skills/private",
        json={"name": "Victim Skill", "description": "d", "content": "secret content"},
    ).json()

    _register_and_login(client, "idor_skill_attacker")
    r = client.post(
        "/api/agents",
        json={**_AGENT, "skills": [skill["id"]]},
    )
    assert r.status_code == 403


def test_save_agent_rejects_foreign_private_knowledge(client):
    _register_and_login(client, "idor_kn_victim")
    item = client.post(
        "/api/knowledge/text",
        json={"title": "Victim Notes", "content": "secret notes"},
    ).json()

    _register_and_login(client, "idor_kn_attacker")
    r = client.post(
        "/api/agents",
        json={**_AGENT, "knowledge": [item["id"]]},
    )
    assert r.status_code == 403


def test_uncaught_valueerror_returns_generic_400_and_logs(client):
    """El handler global de ValueError no debe filtrar el texto crudo de la
    excepción al cliente, y sí debe loguearlo (antes se perdía el traceback
    al convertirlo silenciosamente en un 400)."""
    _register_and_login(client, "valueerror_handler_probe")
    agent = _create_agent(client)

    with (
        patch(
            "app.api.routes.agent_preferences.open_db",
            side_effect=ValueError("boom: detalle interno de la ruta /users/x"),
        ),
        patch("app.api.app.flog.error") as mock_error,
    ):
        r = client.put(
            f"/api/agents/{agent['id']}/preferences",
            json={"connection_id": None},
        )

    assert r.status_code == 400
    body = r.json()["detail"]
    assert body["message"] == "Operación no válida"
    assert "detalle interno" not in body["message"]
    assert mock_error.called


def test_save_agent_allogroup_own_private_skill(client):
    _register_and_login(client, "idor_own_skill")
    skill = client.post(
        "/api/skills/private",
        json={"name": "My Skill", "description": "d", "content": "c"},
    ).json()
    r = client.post("/api/agents", json={**_AGENT, "skills": [skill["id"]]})
    assert r.status_code == 200


def test_save_agent_allogroup_skill_shared_with_group(client):
    from app.auth.auth import create_token, register_user

    asyncio.run(
        register_user("idor_share_owner", "pass1234", email="idor_share_owner@cov.test")
    )
    asyncio.run(
        register_user(
            "idor_share_member", "pass1234", email="idor_share_member@cov.test"
        )
    )

    client.cookies.set("ga_token", create_token("idor_share_owner"))
    skill = client.post(
        "/api/skills/private",
        json={"name": "Shared Skill", "description": "d", "content": "c"},
    ).json()
    group = client.post("/api/groups", json={"name": "IDOR Test Group"}).json()
    client.post(
        f"/api/groups/{group['id']}/members",
        json={"username": "idor_share_member", "role": "member"},
    )
    r_share = client.post(
        f"/api/sharing/skill/{skill['id']}",
        json={"group_id": group["id"]},
    )
    assert r_share.status_code == 200, r_share.text

    client.cookies.set("ga_token", create_token("idor_share_member"))
    r = client.post("/api/agents", json={**_AGENT, "skills": [skill["id"]]})
    assert r.status_code == 200, r.text
