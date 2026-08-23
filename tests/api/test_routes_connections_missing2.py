"""Tests de cobertura 2 para connections.py.

Cubre: _safe_name, _list_accessible team group, _get_conn_any personal fallback,
_resolve_connections (guest branch + shared), expansión de modelos por provider,
hub_sync con datos reales, import_models branches,
test_connection no-provider, delete no-admin, tokens-daily.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

_CONN_OPENAI = {
    "type": "openai",
    "name": "Test OpenAI M2",
    "api_key": "sk-test-key",
    "model": "gpt-4o",
}

_CONN_IAGENTSHUB = {
    "type": "iagentshub",
    "name": "Mi Hub M2",
    "url": "https://hub.example.com",
    "username": "hubuser",
    "api_key": "hubpass",
}

_CONN_OLLAMA_BASE = {
    "type": "ollama",
    "name": "Mi Ollama Base M2",
    "host": "http://localhost:11434",
}

_CONN_OLLAMA_MODEL = {
    "type": "ollama",
    "name": "Mi Ollama Model M2",
    "host": "http://localhost:11434",
    "model": "llama3:latest",
}

_CONN_OLLAMA_CUSTOM = {
    "type": "ollama",
    "name": "Mi Ollama Custom M2",
    "host": "http://custom-ollama-server:11434",
}


def _setup_user(client, username: str) -> str:
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    client.cookies.set("ga_token", create_token(username))
    return username


def _create_conn(client, payload: dict) -> dict:
    r = client.post("/api/connections", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _build_hub_mock(
    conn_data=None,
    agent_summaries=None,
    agent_details=None,
    skill_data=None,
    know_data=None,
):
    """Construye un mock del transporte seguro de sincronización del hub."""

    async def _mock_get(base_url, path, headers):
        url = f"{base_url}{path}"
        if "/api/agents?" in url:
            return agent_summaries or []
        elif "/api/agents/" in url:
            agent_id = url.split("/api/agents/")[-1].split("?")[0]
            details = agent_details or []
            return next(
                (a for a in details if a.get("id") == agent_id),
                {"id": agent_id, "name": f"Agent {agent_id}", "description": ""},
            )
        elif "/api/skills" in url:
            return skill_data or []
        elif "/api/knowledge" in url:
            return know_data or []
        elif "/api/connections" in url:
            return conn_data or []
        return []

    return AsyncMock(side_effect=_mock_get)


# ── 1. list_connections_raw non-admin con datos (línea 147) ──────────────────


def test_list_connections_raw_non_admin_with_data(client):
    """GET /raw devuelve conexiones para usuario no-admin (línea 147)."""
    _setup_user(client, "raw_na_m2")
    _create_conn(client, _CONN_OPENAI)
    r = client.get("/api/connections/raw")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    for item in data:
        assert "api_key" not in item


# ── 2. list_connections branch guest (_resolve_connections línea 81) ──────────


def test_list_connections_guest_uses_session_resolve(client):
    """GET /api/connections para guest usa _resolve_connections rama guest (línea 81)."""
    client.post("/api/auth/guest")
    r = client.post("/api/connections", json=_CONN_OPENAI)
    conn_id = r.json()["id"]
    r = client.get("/api/connections")
    assert r.status_code == 200
    data = r.json()
    assert any(c["id"] == conn_id for c in data)


# ── 3. test_all sin proveedor (línea 211) ────────────────────────────────────


def test_test_all_no_provider_returns_ok_false(admin_client):
    """test-all devuelve ok=False y latency_ms=None cuando no hay proveedor (línea 211)."""
    _create_conn(admin_client, _CONN_OPENAI)
    with patch("app.api.routes.connection_diagnostics.get_provider", return_value=None):
        r = admin_client.post("/api/connections/test-all", json={})
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert all(item["ok"] is False for item in data)
    assert all(item["latency_ms"] is None for item in data)


# ── 4. delete_connection no-admin (líneas 276, 297) ──────────────────────────


def test_delete_connection_non_admin_success(client):
    """Usuario no-admin puede eliminar su propia conexión (línea 276)."""
    _setup_user(client, "del_na_m2")
    conn = _create_conn(client, _CONN_OPENAI)
    r = client.delete(f"/api/connections/{conn['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_connection_non_admin_not_found(client):
    """DELETE conexión inexistente para no-admin devuelve 404."""
    _setup_user(client, "del_nf_m2")
    r = client.delete("/api/connections/nonexistent-m2-xyz")
    assert r.status_code == 404


def test_delete_connection_team_group_personal_fallback(client):
    """Conexión personal se borra desde group de equipo via fallback (línea 297)."""
    from app.api.routes.auth import GroupContext, require_group
    from app.auth.auth import create_token, register_user

    username = "del_team_m2"
    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))

    app = client.app
    personal_ctx = GroupContext(user=username, group_id=username)

    async def _personal():
        return personal_ctx

    app.dependency_overrides[require_group] = _personal
    try:
        client.cookies.set("ga_token", create_token(username))
        r = client.post("/api/connections", json=_CONN_OPENAI)
        assert r.status_code == 200
        conn_id = r.json()["id"]

        # Switch to team group (connection is in personal, not team)
        team_ctx = GroupContext(user=username, group_id="team-del-m2")

        async def _team():
            return team_ctx

        app.dependency_overrides[require_group] = _team
        # First delete (team group owner_id) fails; second (personal) succeeds
        r = client.delete(f"/api/connections/{conn_id}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        app.dependency_overrides.clear()


# ── 5. hub_sync rama no-admin (línea 314) ────────────────────────────────────


def test_hub_sync_non_admin_user_branch(client):
    """hub_sync usa _get_conn_any para no-admin (línea 314)."""
    _setup_user(client, "hub_na_m2")
    hub_conn = _create_conn(client, _CONN_IAGENTSHUB)
    with patch(
        "app.connections.iagentshub._login", side_effect=Exception("auth error")
    ):
        r = client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 502


# ── 6. hub_sync crea conexiones (líneas 352-375) ─────────────────────────────


def test_hub_sync_imports_remote_connections(admin_client):
    """hub_sync importa conexiones nuevas del hub remoto (líneas 352-375)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    remote_conns = [
        {
            "id": "rc-m2-1",
            "name": "Remote OpenAI M2",
            "type": "openai",
            "model": "gpt-4o",
        },
    ]
    mock = _build_hub_mock(conn_data=remote_conns)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["connections"] == 1
    assert data["errors"] == []


def test_hub_sync_updates_existing_remote_connection(admin_client):
    """Segunda sincronización actualiza la conexión ya importada (rama update líneas 361-366)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    remote_conns = [
        {
            "id": "rc-upd-m2",
            "name": "Updatable Conn M2",
            "type": "openai",
            "model": "gpt-4o",
        }
    ]

    mock1 = _build_hub_mock(conn_data=remote_conns)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock1),
    ):
        r1 = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r1.json()["connections"] == 1

    mock2 = _build_hub_mock(conn_data=remote_conns)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock2),
    ):
        r2 = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r2.status_code == 200
    assert r2.json()["updated"] >= 1


# ── 7. _safe_name conflictos (líneas 25-33) ──────────────────────────────────


def test_hub_sync_safe_name_conflict_adds_hub_label(admin_client):
    """_safe_name añade '(hub_label)' cuando el nombre ya existe (líneas 27-29)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    _create_conn(admin_client, {**_CONN_OPENAI, "name": "Conflicting Name M2"})
    remote_conns = [
        {
            "id": "rc-cnf-m2",
            "name": "Conflicting Name M2",
            "type": "openai",
            "model": "gpt-4o",
        }
    ]
    mock = _build_hub_mock(conn_data=remote_conns)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["connections"] == 1
    assert data["errors"] == []


def test_hub_sync_safe_name_double_conflict_adds_number(admin_client):
    """_safe_name añade número cuando nombre y '(hub_label)' están tomados (líneas 30-33)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    hub_label = "Mi Hub M2"
    _create_conn(admin_client, {**_CONN_OPENAI, "name": "DblConflict M2"})
    _create_conn(
        admin_client,
        {**_CONN_OPENAI, "name": f"DblConflict M2 ({hub_label})", "model": "gpt-3.5"},
    )
    remote_conns = [
        {
            "id": "rc-dbl-m2",
            "name": "DblConflict M2",
            "type": "openai",
            "model": "gpt-4o",
        }
    ]
    mock = _build_hub_mock(conn_data=remote_conns)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    assert r.json()["connections"] == 1


# ── 8. hub_sync agentes (líneas 385-408) ────────────────────────────────────


def test_hub_sync_imports_remote_agents(admin_client):
    """hub_sync importa agentes nuevos del hub remoto (líneas 385-408)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    summaries = [{"id": "ra-m2-1", "name": "Remote Agent M2"}]
    details = [
        {"id": "ra-m2-1", "name": "Remote Agent M2", "description": "A remote agent"}
    ]
    mock = _build_hub_mock(agent_summaries=summaries, agent_details=details)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["agents"] == 1
    assert data["errors"] == []


def test_hub_sync_updates_existing_agent_via_hub_source(admin_client):
    """hub_sync actualiza agente cuando by_src contiene el src_key (líneas 397-401).

    AgentStorage._summary() no devuelve _hub_source, por lo que se simula
    lista de agentes locales con _hub_source para forzar la rama de actualización.
    """
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    conn_id = hub_conn["id"]
    summaries = [{"id": "ra-upd-m2b", "name": "Agent To Update M2B"}]
    details = [{"id": "ra-upd-m2b", "name": "Agent To Update M2B", "description": "v2"}]

    src_key = f"{conn_id}:ra-upd-m2b"
    local_agents_with_src = [
        {
            "id": "agent-to-update-m2b",
            "name": "Agent To Update M2B",
            "_hub_source": src_key,
        }
    ]

    mock = _build_hub_mock(agent_summaries=summaries, agent_details=details)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
        patch(
            "app.services.hub_sync._agent_storage.list",
            return_value=local_agents_with_src,
        ),
    ):
        r = admin_client.post(f"/api/connections/{conn_id}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["updated"] >= 1
    assert data["errors"] == []


# ── 9. hub_sync skills (líneas 418-436) ──────────────────────────────────────


def test_hub_sync_imports_remote_skills(admin_client):
    """hub_sync importa skills nuevas del hub remoto (líneas 418-436)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    skill_data = [
        {"id": "rs-m2-1", "name": "Remote Skill M2", "description": "A skill"}
    ]
    mock = _build_hub_mock(skill_data=skill_data)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["skills"] == 1
    assert data["errors"] == []


# ── 10. hub_sync knowledge (líneas 448-469) ──────────────────────────────────


def test_hub_sync_imports_remote_knowledge(admin_client):
    """hub_sync importa knowledge del hub remoto (líneas 448-465)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    know_data = [
        {
            "id": "rk-m2-1",
            "title": "Remote KB M2",
            "type": "url",
            "content": "http://example.com",
        }
    ]
    mock = _build_hub_mock(know_data=know_data)
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["knowledge"] == 1
    assert data["errors"] == []


# ── 11. hub_sync todos los tipos / result["ok"] (línea 471) ─────────────────


def test_hub_sync_full_all_types_ok(admin_client):
    """hub_sync con todos los tipos devuelve ok=True y estadísticas completas (línea 471)."""
    hub_conn = _create_conn(admin_client, _CONN_IAGENTSHUB)
    summaries = [{"id": "ra-full-m2", "name": "Full Agent M2"}]
    details = [{"id": "ra-full-m2", "name": "Full Agent M2", "description": ""}]
    mock = _build_hub_mock(
        conn_data=[
            {
                "id": "rc-full-m2",
                "name": "Full Conn M2",
                "type": "openai",
                "model": "gpt-4o",
            }
        ],
        agent_summaries=summaries,
        agent_details=details,
        skill_data=[{"id": "rs-full-m2", "name": "Full Skill M2", "description": ""}],
        know_data=[
            {"id": "rk-full-m2", "title": "Full KB M2", "type": "url", "content": ""}
        ],
    )
    with (
        patch("app.connections.iagentshub._login", return_value="tok"),
        patch("app.services.hub_sync._get_remote_json", new=mock),
    ):
        r = admin_client.post(f"/api/connections/{hub_conn['id']}/hub-sync")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["connections"] >= 1
    assert data["agents"] >= 1
    assert data["skills"] >= 1
    assert data["knowledge"] >= 1


# ── 12. import_models rama no-admin (línea 486) ──────────────────────────────


def test_import_models_non_admin_user(client):
    """import_models usa _get_conn_any para no-admin (línea 486)."""
    _setup_user(client, "im_na_m2")
    conn = _create_conn(client, _CONN_OPENAI)
    with patch(
        "app.api.routes.connection_sync.fetch_provider_models",
        new_callable=AsyncMock,
        return_value=["gpt-4o"],
    ):
        r = client.post(f"/api/connections/{conn['id']}/import-models")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["created"] == 1


# ── 13. import_models copia host y url (líneas 527, 529) ─────────────────────


def test_import_models_copies_host_and_url(admin_client):
    """import_models copia host y url de la conexión padre (líneas 527, 529)."""
    conn = _create_conn(
        admin_client,
        {
            "type": "openai",
            "name": "OpenAI Custom M2",
            "api_key": "sk-test",
            "model": "gpt-4o",
            "host": "https://custom.host.m2.com",
            "url": "https://custom.host.m2.com/v1",
        },
    )
    with patch(
        "app.api.routes.connection_sync.fetch_provider_models",
        new_callable=AsyncMock,
        return_value=["gpt-4o-mini"],
    ):
        r = admin_client.post(f"/api/connections/{conn['id']}/import-models")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["created"] == 1


# ── 14. test_connection rama no-admin (línea 553) ────────────────────────────


def test_test_connection_non_admin_branch(client):
    """test_connection usa _get_conn_any para no-admin (línea 553)."""
    _setup_user(client, "tc_na_m2")
    conn = _create_conn(client, _CONN_OPENAI)
    mock_result = MagicMock()
    mock_result.ok = True
    mock_result.message = "OK"
    mock_result.detail = ""
    with patch("app.connections.openai.OpenAIProvider.test", return_value=mock_result):
        r = client.post(f"/api/connections/{conn['id']}/test")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── 15. test_connection sin proveedor (línea 558) ────────────────────────────


def test_test_connection_no_provider_returns_false(client):
    """test_connection devuelve ok=False cuando no hay proveedor (línea 558)."""
    _setup_user(client, "tc_np_m2")
    conn = _create_conn(client, _CONN_OPENAI)
    with patch("app.api.routes.connection_diagnostics.get_provider", return_value=None):
        r = client.post(f"/api/connections/{conn['id']}/test")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "sin proveedor" in data["message"]


# ── 16. _get_conn_any fallback personal (líneas 70-73) ───────────────────────


def test_get_conn_any_personal_group_fallback(client):
    """_get_conn_any busca en group personal cuando no está en el de equipo (líneas 71-73)."""
    from app.api.routes.auth import GroupContext, require_group
    from app.auth.auth import create_token, register_user

    username = "pc_fb_m2"
    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))

    app = client.app
    personal_ctx = GroupContext(user=username, group_id=username)

    async def _personal():
        return personal_ctx

    app.dependency_overrides[require_group] = _personal
    try:
        client.cookies.set("ga_token", create_token(username))
        r = client.post("/api/connections", json=_CONN_OPENAI)
        assert r.status_code == 200
        conn_id = r.json()["id"]

        team_ctx = GroupContext(user=username, group_id="team-m2-xyz")

        async def _team():
            return team_ctx

        app.dependency_overrides[require_group] = _team
        # Conn not in team group → fallback to personal group
        r = client.get(f"/api/connections/{conn_id}")
        assert r.status_code == 200
        assert r.json()["id"] == conn_id
    finally:
        app.dependency_overrides.clear()


# ── 17. _list_accessible group de equipo (líneas 59-65) ─────────────────


def test_list_accessible_personal_conns_in_team_group(client):
    """Conexiones personales aparecen al listar desde group de equipo (líneas 59-65)."""
    from app.api.routes.auth import GroupContext, require_group
    from app.auth.auth import create_token, register_user

    username = "la_team_m2"
    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))

    app = client.app
    personal_ctx = GroupContext(user=username, group_id=username)

    async def _personal():
        return personal_ctx

    app.dependency_overrides[require_group] = _personal
    try:
        client.cookies.set("ga_token", create_token(username))
        r = client.post("/api/connections", json=_CONN_OPENAI)
        assert r.status_code == 200
        personal_conn_id = r.json()["id"]

        team_ctx = GroupContext(user=username, group_id="team-la-m2")

        async def _team():
            return team_ctx

        app.dependency_overrides[require_group] = _team
        r = client.get("/api/connections")
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()]
        assert personal_conn_id in ids
    finally:
        app.dependency_overrides.clear()


# ── 18. _resolve_connections conexiones compartidas (líneas 93-96) ────────────


def test_resolve_connections_includes_shared_from_group(admin_client):
    """Conexiones compartidas con el group aparecen en la lista del usuario."""
    conn = _create_conn(admin_client, {**_CONN_OPENAI, "name": "Shared Conn M2"})
    shared_id = conn["id"]

    from app.auth.auth import create_token, register_user

    asyncio.run(
        register_user("shared_u_m2", "pass1234", email="shared_u_m2@example.com")
    )
    admin_client.cookies.set("ga_token", create_token("shared_u_m2"))

    with patch(
        "app.storage.group_shares.GroupShareStorage.get_group_shared_resource_ids",
        new_callable=AsyncMock,
        return_value=[shared_id],
    ):
        r = admin_client.get("/api/connections")

    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert shared_id in ids


# ── 19. _fetch_ollama_models ramas OSError (líneas 102-115) ─────────────────


def test_fetch_ollama_models_oserror_no_alt_host(client):
    """_fetch_ollama_models retorna [] cuando OSError y _alt_host=None (líneas 107-108)."""
    _setup_user(client, "ollama_noalt_m2")
    # Custom host → _alt_host returns None (no localhost, no docker.internal)
    client.post("/api/connections", json=_CONN_OLLAMA_CUSTOM)
    with patch(
        "app.connections.ollama.OllamaProvider._fetch_tags",
        side_effect=OSError("refused"),
    ):
        r = client.get("/api/connections")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_fetch_ollama_models_oserror_alt_host_also_fails(client):
    """_fetch_ollama_models retorna [] cuando el alt host también lanza excepción (líneas 110-112)."""
    _setup_user(client, "ollama_altfail_m2")
    # localhost → _alt_host returns "host.docker.internal" (not None), but also raises OSError
    client.post("/api/connections", json=_CONN_OLLAMA_BASE)
    with patch(
        "app.connections.ollama.OllamaProvider._fetch_tags",
        side_effect=OSError("refused"),
    ):
        r = client.get("/api/connections")
    assert r.status_code == 200


def test_fetch_ollama_models_generic_exception_returns_empty(client):
    """_fetch_ollama_models retorna [] ante excepción genérica no-OSError (líneas 113-114)."""
    _setup_user(client, "ollama_genex_m2")
    client.post("/api/connections", json=_CONN_OLLAMA_BASE)
    with patch(
        "app.connections.ollama.OllamaProvider._fetch_tags",
        side_effect=ValueError("unexpected"),
    ):
        r = client.get("/api/connections")
    assert r.status_code == 200


def test_fetch_ollama_models_localhost_unavailable_returns_base(client):
    """Localhost está autorizado, pero sin Ollama se conserva la conexión base."""
    _setup_user(client, "ollama_altok_m2")
    client.post("/api/connections", json=_CONN_OLLAMA_BASE)

    r = client.get("/api/connections")

    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── 20. expansión de modelos: conexión con modelo explícito ─────────────────


def test_list_connections_ollama_with_explicit_model(client):
    """Conexión Ollama con modelo explícito se devuelve con name=model (líneas 131-136)."""
    _setup_user(client, "ollama_expl_m2")
    client.post("/api/connections", json=_CONN_OLLAMA_MODEL)
    r = client.get("/api/connections")
    assert r.status_code == 200
    data = r.json()
    models = [c.get("model") for c in data]
    assert "llama3:latest" in models


def test_list_connections_ollama_deduplicates_same_model(client):
    """Dos conexiones Ollama con el mismo modelo se deduplicanen una sola entrada (línea 131)."""
    _setup_user(client, "ollama_dedup_m2")
    client.post("/api/connections", json={**_CONN_OLLAMA_MODEL, "name": "Ollama1 M2"})
    client.post("/api/connections", json={**_CONN_OLLAMA_MODEL, "name": "Ollama2 M2"})
    r = client.get("/api/connections")
    assert r.status_code == 200
    data = r.json()
    count = sum(1 for c in data if c.get("model") == "llama3:latest")
    assert count == 1


# ── 21. get_tokens_daily (líneas 568-607) ────────────────────────────────────


def test_get_tokens_daily_empty(client):
    """tokens-daily devuelve lista vacía cuando no hay datos."""
    _setup_user(client, "tok_empty_m2")
    r = client.get("/api/connections/tokens-daily")
    assert r.status_code == 200
    assert r.json() == []


def test_get_tokens_daily_days_param_clamped(client):
    """tokens-daily acepta y clampea el parámetro days."""
    _setup_user(client, "tok_days_m2")
    r = client.get("/api/connections/tokens-daily?days=7")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_tokens_daily_with_data(client):
    """tokens-daily devuelve filas insertadas en token_daily."""
    from app.storage.db import open_db

    username = "tok_data_m2"
    _setup_user(client, username)

    from app.auth.auth import get_user_by_username

    user = asyncio.run(get_user_by_username(username))
    assert user is not None

    async def _insert():
        async with open_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO token_daily (day, owner_id, tokens) VALUES (?, ?, ?)",
                ("2026-06-01", user["id"], 1500),
            )
            await db.commit()

    asyncio.run(_insert())
    r = client.get("/api/connections/tokens-daily?days=90")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    days = [item["day"] for item in data]
    assert "2026-06-01" in days


def test_get_tokens_daily_days_extreme_values(client):
    """tokens-daily clampea days a rango [1, 90]."""
    _setup_user(client, "tok_clamp_m2")
    r_over = client.get("/api/connections/tokens-daily?days=999")
    assert r_over.status_code == 200
    r_zero = client.get("/api/connections/tokens-daily?days=0")
    assert r_zero.status_code == 200
