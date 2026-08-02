"""Tests de GET /api/admin/users y DELETE /api/admin/users/{username}."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx


def _register(username, password="pass1234"):
    """Registra un usuario directamente, sin pasar por HTTP, para no contaminar cookies."""
    import asyncio

    from app.auth.auth import register_user

    asyncio.run(register_user(username, password, email=f"{username}@example.com"))


def test_list_users_as_admin(admin_client, reset_rate_limiter):
    _register("listed_user")
    r = admin_client.get("/api/admin/users")
    assert r.status_code == 200
    users = r.json()
    assert isinstance(users, list)
    assert any(u["username"] == "listed_user" for u in users)


def test_list_users_no_password_hash(admin_client, reset_rate_limiter):
    _register("nohash_user")
    r = admin_client.get("/api/admin/users")
    for u in r.json():
        assert "password_hash" not in u


def test_list_users_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser2",
            "email": "stduser2@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/users")
    assert r.status_code == 403


def test_list_users_unauthenticated(client):
    r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_delete_user_as_admin(admin_client, reset_rate_limiter):
    _register("to_delete")
    r = admin_client.delete("/api/admin/users/to_delete")
    assert r.status_code == 200
    users = admin_client.get("/api/admin/users").json()
    assert not any(u["username"] == "to_delete" for u in users)


def test_delete_nonexistent_user(admin_client):
    r = admin_client.delete("/api/admin/users/ghost_user")
    assert r.status_code == 404


def test_create_user_rejects_invalid_email(admin_client):
    response = admin_client.post(
        "/api/admin/users",
        json={"username": "invaliduser", "email": "invalid", "password": "pass1234"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_field",
        "message": "Email no válido",
        "field": "email",
    }


def test_create_user_accepts_valid_email(admin_client):
    response = admin_client.post(
        "/api/admin/users",
        json={
            "username": "createduser",
            "email": "created@example.com",
            "password": "pass1234",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "username": "createduser",
        "email": "created@example.com",
        "role": "standard",
    }


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


def test_check_update_no_version_baked(admin_client, monkeypatch):
    monkeypatch.delenv("GAIA_VERSION", raising=False)
    r = admin_client.get("/api/admin/check-update")
    assert r.status_code == 200
    data = r.json()
    assert data["checked"] is False
    assert data["reason"] == "no_version"
    assert data["current_version"] == "dev"


def test_check_update_available(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")

    fake_get = _ghcr_fake_get(
        ["latest", "legacy", "react-20260101000000", "react-20260601120000"]
    )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    assert r.status_code == 200
    data = r.json()
    assert data["checked"] is True
    assert data["current_version"] == "20260101000000"
    assert data["latest_version"] == "20260601120000"
    assert data["update_available"] is True


def test_check_update_ignores_other_tag_family(admin_client, monkeypatch):
    """Un tag de otra familia no debe disparar una actualización React."""
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")

    fake_get = _ghcr_fake_get(
        ["latest", "legacy-20260601120000", "react-20260101000000"]
    )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["checked"] is True
    assert data["latest_version"] == "20260101000000"
    assert data["update_available"] is False


def test_check_update_up_to_date(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260601120000")

    fake_get = _ghcr_fake_get(
        ["latest", "react-20260101000000", "react-20260601120000"]
    )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["checked"] is True
    assert data["update_available"] is False


def test_check_update_no_remote_versions(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")

    fake_get = _ghcr_fake_get(["latest", "legacy"])

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["checked"] is False
    assert data["reason"] == "no_remote_versions"


def test_check_update_ghcr_error(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")

    async def fake_get(*args, **kwargs):
        raise httpx.ConnectError("boom")

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    assert r.status_code == 502


def test_check_update_uses_configured_image_variant(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.setenv("IMAGE_REPOSITORY", "ghcr.io/iagentshub/backend")
    monkeypatch.setenv("IMAGE_VARIANT", "fastapi")
    latest = AsyncMock(return_value="20260101000000")

    with patch("app.api.routes.admin._latest_ghcr_version", latest):
        r = admin_client.get("/api/admin/check-update")

    assert r.status_code == 200
    latest.assert_awaited_once_with("ghcr.io/iagentshub/backend", "fastapi")


def _mock_github_commit_response(sha):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = {"sha": sha}
    return resp


def _routed_fake_get(tags_names, github_shas):
    """Enruta según la URL: GHCR -> token/tags, api.github.com -> commit."""

    async def fake_get(*args, **kwargs):
        url = args[-1]
        if url == "https://ghcr.io/token":
            return _mock_ghcr_token_response()
        if "ghcr.io/v2/iagentshub/app/tags/list" in url:
            return _mock_ghcr_tags_response(tags_names)
        for repo, sha in github_shas.items():
            if repo in url:
                return _mock_github_commit_response(sha)
        raise httpx.ConnectError(f"URL no esperada en el test: {url}")

    return fake_get


def test_check_update_backend_frontend_commits_up_to_date(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.setenv("BACKEND_COMMIT", "abc1234")
    monkeypatch.setenv("FRONTEND_COMMIT", "def5678")

    fake_get = _routed_fake_get(
        ["latest", "react-20260101000000"],
        {
            "iagentshub/backend_fastapi": "abc1234567890abcdef",
            "iagentshub/frontend_react": "def5678901234abcdef",
        },
    )
    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["backend_commit"] == "abc1234"
    assert data["backend_commit_latest"] == "abc1234"
    assert data["backend_up_to_date"] is True
    assert data["frontend_commit"] == "def5678"
    assert data["frontend_commit_latest"] == "def5678"
    assert data["frontend_up_to_date"] is True


def test_check_update_backend_commit_outdated(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.setenv("BACKEND_COMMIT", "abc1234")
    monkeypatch.setenv("FRONTEND_COMMIT", "def5678")

    fake_get = _routed_fake_get(
        ["latest", "react-20260101000000"],
        {
            "iagentshub/backend_fastapi": "9999999999999999999",
            "iagentshub/frontend_react": "def5678901234abcdef",
        },
    )
    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["backend_commit_latest"] == "9999999"
    assert data["backend_up_to_date"] is False
    assert data["frontend_up_to_date"] is True


def test_check_update_app_commit_up_to_date(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.setenv("APP_COMMIT", "aaa1111")

    fake_get = _routed_fake_get(
        ["latest", "react-20260101000000"],
        {"iagentshub/app_flutter": "aaa1111222333444555"},
    )
    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["app_commit"] == "aaa1111"
    assert data["app_commit_latest"] == "aaa1111"
    assert data["app_up_to_date"] is True


def test_check_update_app_commit_outdated(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.setenv("APP_COMMIT", "aaa1111")

    fake_get = _routed_fake_get(
        ["latest", "react-20260101000000"],
        {"iagentshub/app_flutter": "9999999999999999999"},
    )
    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["app_commit_latest"] == "9999999"
    assert data["app_up_to_date"] is False


def test_check_update_commits_not_baked_are_omitted(admin_client, monkeypatch):
    """Sin BACKEND_COMMIT/FRONTEND_COMMIT/APP_COMMIT (instalaciones previas a
    este cambio) no debe intentarse ninguna llamada a la API de GitHub."""
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.delenv("BACKEND_COMMIT", raising=False)
    monkeypatch.delenv("FRONTEND_COMMIT", raising=False)
    monkeypatch.delenv("APP_COMMIT", raising=False)

    fake_get = _ghcr_fake_get(["latest", "react-20260101000000"])

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    data = r.json()
    assert data["backend_commit"] == "dev"
    assert data["backend_commit_latest"] is None
    assert data["backend_up_to_date"] is None
    assert data["frontend_up_to_date"] is None
    assert data["app_commit"] == "dev"
    assert data["app_commit_latest"] is None
    assert data["app_up_to_date"] is None


def test_check_update_github_api_failure_is_not_fatal(admin_client, monkeypatch):
    monkeypatch.setenv("GAIA_VERSION", "20260101000000")
    monkeypatch.setenv("BACKEND_COMMIT", "abc1234")
    monkeypatch.setenv("FRONTEND_COMMIT", "def5678")

    async def fake_get(*args, **kwargs):
        url = args[-1]
        if url == "https://ghcr.io/token":
            return _mock_ghcr_token_response()
        if "ghcr.io/v2/iagentshub/app/tags/list" in url:
            return _mock_ghcr_tags_response(["latest", "react-20260101000000"])
        raise httpx.ConnectError("GitHub no disponible")

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        r = admin_client.get("/api/admin/check-update")

    assert r.status_code == 200
    data = r.json()
    assert data["checked"] is True
    assert data["backend_commit_latest"] is None
    assert data["backend_up_to_date"] is None


def test_check_update_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "checkupdate",
            "email": "checkupdate@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/check-update")
    assert r.status_code == 403


def test_check_update_unauthenticated(client):
    r = client.get("/api/admin/check-update")
    assert r.status_code == 401


def test_update_now_no_token_configured(admin_client, monkeypatch):
    monkeypatch.delenv("WATCHTOWER_HTTP_API_TOKEN", raising=False)
    r = admin_client.post("/api/admin/update-now")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "update_now_unavailable"


def test_update_now_triggers_watchtower(admin_client, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_HTTP_API_TOKEN", "secret-token")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append((url, kwargs.get("headers")))
        return _mock_action_response(200)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.post("/api/admin/update-now")

    assert r.status_code == 200
    assert r.json() == {"triggered": True}
    assert calls == [
        ("http://watchtower:8080/v1/update", {"Authorization": "Bearer secret-token"})
    ]


def test_update_now_swallows_connection_errors(admin_client, monkeypatch):
    """Watchtower puede sustituir este mismo contenedor a mitad de la
    petición si aplica una actualización — perder la conexión no debe
    convertirse en un error de cara al cliente."""
    monkeypatch.setenv("WATCHTOWER_HTTP_API_TOKEN", "secret-token")

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("conexión perdida")

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.post("/api/admin/update-now")

    assert r.status_code == 200
    assert r.json() == {"triggered": True}


def test_update_now_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "updatenow",
            "email": "updatenow@example.com",
            "password": "pass1234",
        },
    )
    r = client.post("/api/admin/update-now")
    assert r.status_code == 403


def test_update_now_unauthenticated(client):
    r = client.post("/api/admin/update-now")
    assert r.status_code == 401


def _mock_action_response(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def test_auto_update_no_proxy_configured(admin_client, monkeypatch):
    monkeypatch.delenv("DOCKER_PROXY_URL", raising=False)
    r = admin_client.put("/api/admin/auto-update", json={"enabled": False})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "auto_update_proxy_unavailable"


def test_auto_update_disable_success(admin_client, monkeypatch):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _mock_action_response(204)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.put("/api/admin/auto-update", json={"enabled": False})

    assert r.status_code == 200
    assert r.json() == {"auto_update_enabled": False}
    assert calls == ["http://docker-proxy:2375/containers/watchtower/stop"]

    # Persistido de verdad — el GET de platform config lo refleja.
    cfg = admin_client.get("/api/settings/platform").json()
    assert cfg["auto_update_enabled"] is False


def test_auto_update_enable_uses_configured_container_name(admin_client, monkeypatch):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")
    monkeypatch.setenv("WATCHTOWER_CONTAINER_NAME", "my-watchtower")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _mock_action_response(204)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.put("/api/admin/auto-update", json={"enabled": True})

    assert r.status_code == 200
    assert calls == ["http://docker-proxy:2375/containers/my-watchtower/start"]


def test_auto_update_idempotent_304_is_success(admin_client, monkeypatch):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")

    async def fake_post(self, url, **kwargs):
        return _mock_action_response(304)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.put("/api/admin/auto-update", json={"enabled": True})

    assert r.status_code == 200
    assert r.json() == {"auto_update_enabled": True}


def test_auto_update_docker_rejects_it(admin_client, monkeypatch):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")

    async def fake_post(self, url, **kwargs):
        return _mock_action_response(403)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.put("/api/admin/auto-update", json={"enabled": True})

    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "auto_update_apply_failed"


def test_auto_update_proxy_unreachable(admin_client, monkeypatch):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("boom")

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.put("/api/admin/auto-update", json={"enabled": True})

    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "auto_update_apply_failed"


def test_auto_update_invalid_body(admin_client, monkeypatch):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")
    r = admin_client.put("/api/admin/auto-update", json={})
    assert r.status_code == 422


def test_auto_update_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "autoupdate",
            "email": "autoupdate@example.com",
            "password": "pass1234",
        },
    )
    r = client.put("/api/admin/auto-update", json={"enabled": False})
    assert r.status_code == 403


def test_auto_update_unauthenticated(client):
    r = client.put("/api/admin/auto-update", json={"enabled": False})
    assert r.status_code == 401


def test_generic_platform_put_cannot_change_auto_update(admin_client, monkeypatch):
    """PlatformConfigUpdate no tiene auto_update_enabled a propósito — el PUT
    genérico de /api/settings/platform nunca debe poder tocarlo (solo
    PUT /api/admin/auto-update, que sí aplica el cambio de verdad)."""
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")
    before = admin_client.get("/api/settings/platform").json()["auto_update_enabled"]
    r = admin_client.put(
        "/api/settings/platform", json={"auto_update_enabled": not before}
    )
    assert r.status_code == 200
    after = admin_client.get("/api/settings/platform").json()["auto_update_enabled"]
    assert after == before


def test_admin_cannot_self_delete(admin_client):
    r = admin_client.delete("/api/admin/users/testadmin")
    assert r.status_code == 400


def test_delete_user_forbidden_for_standard(client, reset_rate_limiter):
    _register("victim_user")
    # autenticarse como otro usuario estándar
    client.post(
        "/api/auth/register",
        json={
            "username": "attacker",
            "email": "attacker@example.com",
            "password": "pass1234",
        },
    )
    r = client.delete("/api/admin/users/victim_user")
    assert r.status_code == 403


# ── Admin agents ──────────────────────────────────────────────────────────────

_AGENT_PAYLOAD = {
    "name": "Admin Test Agent",
    "system_prompt": "Test.",
    "model": "gpt-4o",
    "temperature": 0.7,
}


def test_admin_list_agents(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    r = admin_client.get("/api/admin/agents")
    assert r.status_code == 200
    agents = r.json()
    assert isinstance(agents, list)
    assert any(a["name"] == "Admin Test Agent" for a in agents)


def test_admin_list_agents_has_owner_username(admin_client):
    admin_client.post("/api/agents", json=_AGENT_PAYLOAD)
    agents = admin_client.get("/api/admin/agents").json()
    private = [a for a in agents if a.get("scope") == "private"]
    assert private, "se esperaba al menos un agente privado"
    assert private[0]["owner_username"] == "testadmin"


def test_admin_delete_agent(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.delete(f"/api/admin/agents/{created['id']}?scope=private")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    agents = admin_client.get("/api/admin/agents").json()
    assert not any(a["id"] == created["id"] for a in agents)


def test_admin_delete_agent_not_found(admin_client):
    r = admin_client.delete("/api/admin/agents/ghost-agent?scope=private")
    assert r.status_code == 404


def test_admin_delete_public_agent(admin_client):
    """El admin puede borrar agentes públicos (antes daba 500: eran de solo
    lectura y el ValueError no se capturaba en la ruta admin)."""
    created = admin_client.post(
        "/api/agents", json={**_AGENT_PAYLOAD, "scope": "public"}
    ).json()
    r = admin_client.delete(f"/api/admin/agents/{created['id']}?scope=public")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    agents = admin_client.get("/api/admin/agents").json()
    assert not any(a["id"] == created["id"] for a in agents)


def test_admin_set_agent_owner(admin_client):
    _register("new_owner_a1")
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.put(
        f"/api/admin/resources/agent/{created['id']}/owner",
        json={"username": "new_owner_a1"},
    )
    assert r.status_code == 200
    agents = admin_client.get("/api/admin/agents").json()
    moved = next(a for a in agents if a["id"] == created["id"])
    import asyncio

    from app.auth.auth import get_user_by_username

    assert moved["owner_id"] == asyncio.run(get_user_by_username("new_owner_a1"))["id"]


def test_admin_set_owner_unknown_user_returns_404(admin_client):
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.put(
        f"/api/admin/resources/agent/{created['id']}/owner",
        json={"owner_id": "ghost_user_xyz"},
    )
    assert r.status_code == 404


def test_admin_set_owner_inactive_user_returns_400(admin_client):
    _register("new_owner_a3")
    admin_client.patch("/api/admin/users/new_owner_a3", json={"is_active": False})
    created = admin_client.post("/api/agents", json=_AGENT_PAYLOAD).json()
    r = admin_client.put(
        f"/api/admin/resources/agent/{created['id']}/owner",
        json={"owner_id": "new_owner_a3"},
    )
    assert r.status_code == 400


def test_admin_set_owner_invalid_resource_type_returns_422(admin_client):
    _register("new_owner_a2")
    r = admin_client.put(
        "/api/admin/resources/bogus/some-id/owner",
        json={"owner_id": "new_owner_a2"},
    )
    assert r.status_code == 422


def test_admin_agents_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser",
            "email": "std@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/agents")
    assert r.status_code == 403


# ── Admin connections ─────────────────────────────────────────────────────────
# Se inserta directamente en la BD (misma ruta que usa el endpoint admin) para
# evitar la divergencia con el _storage de module-level de connections.py.


def _insert_connection(owner_id: str = "testadmin") -> str:
    import asyncio

    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage

    c = asyncio.run(
        ConnectionStorage(DB_FILE).save(
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


def test_admin_list_connections(admin_client):
    _insert_connection()
    r = admin_client.get("/api/admin/connections")
    assert r.status_code == 200
    conns = r.json()
    assert isinstance(conns, list)
    assert len(conns) >= 1


def test_admin_list_connections_has_owner_username(admin_client):
    _insert_connection()
    conns = admin_client.get("/api/admin/connections").json()
    assert conns[0]["owner_username"] == "testadmin"


def test_admin_delete_connection(admin_client):
    conn_id = _insert_connection()
    r = admin_client.delete(f"/api/admin/connections/{conn_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    conns = admin_client.get("/api/admin/connections").json()
    assert not any(c["id"] == conn_id for c in conns)


def test_admin_delete_connection_not_found(admin_client):
    r = admin_client.delete("/api/admin/connections/ghost-conn")
    assert r.status_code == 404


def test_admin_connections_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser2",
            "email": "std2@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/connections")
    assert r.status_code == 403


# ── Admin knowledge ───────────────────────────────────────────────────────────


def test_admin_list_knowledge(admin_client):
    r = admin_client.get("/api/admin/knowledge")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_knowledge_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser3",
            "email": "std3@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/knowledge")
    assert r.status_code == 403


def test_admin_list_skills(admin_client):
    r = admin_client.get("/api/admin/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_skills_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser4",
            "email": "std4@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/skills")
    assert r.status_code == 403


def test_admin_delete_skill(admin_client):
    skill = admin_client.post(
        "/api/skills/private",
        json={
            "name": "Admin delete me",
            "description": "temp",
            "content": "do the thing",
        },
    ).json()

    r = admin_client.delete(f"/api/admin/skills/{skill['id']}")

    assert r.status_code == 200
    remaining = admin_client.get("/api/admin/skills").json()
    assert skill["id"] not in {item["id"] for item in remaining}


def test_admin_delete_skill_not_found(admin_client):
    r = admin_client.delete("/api/admin/skills/missing")
    assert r.status_code == 404


def test_admin_list_memory(admin_client):
    r = admin_client.get("/api/admin/memory")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_memory_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "stduser5",
            "email": "std5@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/admin/memory")
    assert r.status_code == 403


def test_admin_delete_memory(admin_client):
    admin_client.post(
        "/api/memory/admin-delete-me", json={"content": "some notes"}
    )

    memory = admin_client.get("/api/admin/memory").json()
    entry = next(m for m in memory if m["filename"] == "admin-delete-me")

    r = admin_client.delete(f"/api/admin/memory/{entry['id']}")

    assert r.status_code == 200
    remaining = admin_client.get("/api/admin/memory").json()
    assert entry["id"] not in {m["id"] for m in remaining}


def test_admin_delete_memory_invalid_id(admin_client):
    r = admin_client.delete("/api/admin/memory/no-separator")
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "item_id"


def test_admin_delete_memory_not_found(admin_client):
    r = admin_client.delete("/api/admin/memory/testadmin::missing")
    assert r.status_code == 404


# ── Admin explore y grafo relacional ─────────────────────────────────────────


def test_admin_explore_unifies_and_filters_resource_types(admin_client):
    created = admin_client.post(
        "/api/agents", json={**_AGENT_PAYLOAD, "name": "Explore Agent Unique"}
    ).json()

    response = admin_client.get("/api/admin/explore?type=agent&q=unique")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == created["id"]
    assert payload["items"][0]["resource_type"] == "agent"
    assert set(payload["counts"]) == {
        "user",
        "group",
        "agent",
        "connection",
        "knowledge",
        "workflow",
        "skill",
        "memory",
    }


def test_admin_explore_rejects_unknown_type(admin_client):
    response = admin_client.get("/api/admin/explore?type=folder")

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "type"


def test_admin_explore_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "explorestandard",
            "email": "explorestandard@example.com",
            "password": "pass1234",
        },
    )

    assert client.get("/api/admin/explore").status_code == 403


def test_admin_agent_graph_contains_owner_connection_and_workflow(admin_client):
    import asyncio

    from app.config.data import DB_FILE
    from app.storage.storage import ConnectionStorage

    admin_user = next(
        user
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )
    connection = asyncio.run(
        ConnectionStorage(DB_FILE).save(
            {
                "type": "openai",
                "label": "graph-connection",
                "api_key": "sk-test",
                "model": "gpt-4o",
            },
            owner_id=admin_user["id"],
        )
    )
    skill = admin_client.post(
        "/api/skills/private",
        json={
            "name": "Graph skill name",
            "description": "for graph test",
            "content": "do the thing",
        },
    ).json()
    admin_client.post(
        "/api/memory/graph-memory-file", json={"content": "some notes"}
    )
    memory_id = f"{admin_user['id']}::graph-memory-file"
    knowledge = admin_client.post(
        "/api/knowledge/text",
        json={"title": "Graph knowledge", "content": "Graph content"},
    ).json()
    agent = admin_client.post(
        "/api/agents",
        json={
            **_AGENT_PAYLOAD,
            "connection_id": connection["id"],
            "skills": [skill["id"]],
            "knowledge": [knowledge["id"]],
            "use_memory": True,
            "memory_file": "graph-memory-file",
        },
    ).json()
    workflow = admin_client.post(
        "/api/workflows",
        json={
            "name": "Graph workflow",
            "definition": {
                "nodes": [{"id": "step-one", "agent_id": agent["id"]}],
                "edges": [],
            },
        },
    )
    assert workflow.status_code in (200, 201)
    group = admin_client.post("/api/groups", json={"name": "Graph test group"}).json()

    response = admin_client.get(f"/api/admin/resources/agent/{agent['id']}/graph")

    assert response.status_code == 200
    payload = response.json()
    assert payload["root_id"] == f"agent:{agent['id']}"
    node_types = {node["type"] for node in payload["nodes"]}
    assert {
        "agent",
        "user",
        "connection",
        "workflow",
        "skill",
        "memory",
        "knowledge",
    }.issubset(node_types)
    assert {edge["relation"] for edge in payload["edges"]} >= {
        "owns",
        "uses",
        "orchestrates",
    }
    skill_node = next(n for n in payload["nodes"] if n["type"] == "skill")
    assert skill_node["label"] == "Graph skill name"
    memory_node = next(n for n in payload["nodes"] if n["type"] == "memory")
    assert memory_node["label"] == "graph-memory-file"
    assert memory_node["id"] == f"memory:{memory_id}"

    for resource_type, resource_id in (
        ("user", admin_user["id"]),
        ("group", group["id"]),
        ("connection", connection["id"]),
        ("knowledge", knowledge["id"]),
        ("workflow", workflow.json()["id"]),
        ("skill", skill["id"]),
        ("memory", memory_id),
    ):
        related = admin_client.get(
            f"/api/admin/resources/{resource_type}/{resource_id}/graph"
        )
        assert related.status_code == 200
        assert related.json()["root_id"] == f"{resource_type}:{resource_id}"

    # El grafo del usuario propietario también debe incluir la skill y la
    # memoria — antes ninguno de los dos era un tipo conocido por Admin.
    user_graph = admin_client.get(
        f"/api/admin/resources/user/{admin_user['id']}/graph"
    ).json()
    user_node_types = {node["type"] for node in user_graph["nodes"]}
    assert "skill" in user_node_types
    assert "memory" in user_node_types

    # No basta con que los nodos aparezcan sueltos bajo el usuario: el grafo
    # debe dejar claro que es el AGENTE quien usa la skill/memoria/knowledge,
    # no solo que el usuario "posee" ambos por separado sin conectar.
    agent_node_id = f"agent:{agent['id']}"
    uses_targets = {
        edge["target_id"]
        for edge in user_graph["edges"]
        if edge["source_id"] == agent_node_id and edge["relation"] == "uses"
    }
    assert f"skill:{skill['id']}" in uses_targets
    assert f"knowledge:{knowledge['id']}" in uses_targets
    assert f"memory:{memory_id}" in uses_targets
    assert f"connection:{connection['id']}" in uses_targets


def test_admin_resource_graph_not_found(admin_client):
    response = admin_client.get("/api/admin/resources/agent/missing/graph")

    assert response.status_code == 404


# ── Admin stats ───────────────────────────────────────────────────────────────


def test_admin_stats(admin_client):
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    stats = r.json()
    assert "users" in stats or isinstance(stats, dict)


def _insert_log(
    db_path,
    *,
    date,
    time_="10:00:00",
    level="INFO",
    source="BE",
    summary="GET /api/health → 200 (10ms)",
):
    import sqlite3
    from datetime import datetime as _datetime

    ts = _datetime.strptime(f"{date} {time_}", "%Y-%m-%d %H:%M:%S").timestamp()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO app_logs (ts, date, time, ip, username, level, source, summary) "
        "VALUES (?, ?, ?, '127.0.0.1', 'admin', ?, ?, ?)",
        (ts, date, time_, level, source, summary),
    )
    conn.commit()
    conn.close()


def test_admin_stats_health_no_logs_today(admin_client):
    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["requests_today"] == 0
    assert stats["errors_today"] == 0
    assert stats["failure_rate_pct"] == 0.0
    assert stats["avg_latency_ms"] == 0
    assert stats["top_error_endpoint"] is None
    assert stats["top_error_count"] == 0


def test_admin_stats_server_health(admin_client):
    """Disco siempre disponible (shutil es stdlib multiplataforma); memoria
    depende de /proc/meminfo (Linux, ausente en runners macOS) así que puede
    venir a None ahí — el contrato es "no rompe /stats", no un valor fijo."""
    r = admin_client.get("/api/admin/stats")
    assert r.status_code == 200
    stats = r.json()

    assert stats["disk_total_gb"] > 0
    assert 0 <= stats["disk_used_pct"] <= 100
    assert 0 <= stats["disk_used_gb"] <= stats["disk_total_gb"]

    if stats["memory_total_gb"] is not None:
        assert stats["memory_total_gb"] > 0
        assert 0 <= stats["memory_used_pct"] <= 100

    if stats["cpu_cores"] is not None:
        assert stats["cpu_cores"] >= 1
        assert stats["cpu_load_pct"] >= 0


def test_admin_stats_health_counts_and_failure_rate(admin_client, tmp_path):
    from datetime import datetime as _datetime

    db = tmp_path / "hub.db"
    today = _datetime.now().strftime("%Y-%m-%d")
    _insert_log(db, date=today, level="INFO", summary="GET /api/agents → 200 (40ms)")
    _insert_log(db, date=today, level="INFO", summary="GET /api/agents → 200 (60ms)")
    _insert_log(
        db, date=today, level="WARNING", summary="POST /api/auth/login → 401 (20ms)"
    )
    _insert_log(
        db,
        date=today,
        level="ERROR",
        summary="POST /api/agents/chat → 500 (120ms)",
    )

    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["requests_today"] == 4
    assert stats["errors_today"] == 1
    assert stats["failure_rate_pct"] == 25.0
    assert stats["avg_latency_ms"] == round((40 + 60 + 20 + 120) / 4)
    assert stats["top_error_endpoint"] == "POST /api/agents/chat"
    assert stats["top_error_count"] == 1


def test_admin_stats_health_top_error_endpoint_by_frequency(admin_client, tmp_path):
    from datetime import datetime as _datetime

    db = tmp_path / "hub.db"
    today = _datetime.now().strftime("%Y-%m-%d")
    _insert_log(
        db, date=today, level="ERROR", summary="POST /api/agents/chat → 500 (100ms)"
    )
    _insert_log(
        db, date=today, level="ERROR", summary="POST /api/agents/chat → 500 (110ms)"
    )
    _insert_log(
        db, date=today, level="ERROR", summary="GET /api/knowledge → 500 (90ms)"
    )

    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["top_error_endpoint"] == "POST /api/agents/chat"
    assert stats["top_error_count"] == 2
    assert stats["errors_today"] == 3


def test_admin_stats_health_excludes_other_days_and_frontend(admin_client, tmp_path):
    from datetime import datetime as _datetime
    from datetime import timedelta as _timedelta

    db = tmp_path / "hub.db"
    today = _datetime.now().strftime("%Y-%m-%d")
    yesterday = (_datetime.now() - _timedelta(days=1)).strftime("%Y-%m-%d")
    _insert_log(
        db, date=yesterday, level="ERROR", summary="GET /api/old → 500 (50ms)"
    )
    _insert_log(
        db,
        date=today,
        level="ERROR",
        source="FE",
        summary="Uncaught TypeError → 0 (0ms)",
    )

    r = admin_client.get("/api/admin/stats")
    stats = r.json()
    assert stats["requests_today"] == 0
    assert stats["errors_today"] == 0
    assert stats["top_error_endpoint"] is None


# ── Admin PATCH password ───────────────────────────────────────────────────────


def test_admin_patch_password(admin_client):
    _register("pw_target")
    r = admin_client.patch(
        "/api/admin/users/pw_target", json={"password": "newpass123"}
    )
    assert r.status_code == 200


def test_admin_patch_short_password_rejected(admin_client):
    _register("pw_short")
    r = admin_client.patch("/api/admin/users/pw_short", json={"password": "ab"})
    assert r.status_code == 400


def test_admin_patch_empty_password_no_change(admin_client):
    _register("pw_empty")
    r = admin_client.patch("/api/admin/users/pw_empty", json={"password": ""})
    assert r.status_code == 400
