"""Comprobación de versión, actualización manual y auto-update."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from tests.api.admin._helpers import (
    _ghcr_fake_get,
    _mock_ghcr_tags_response,
    _mock_ghcr_token_response,
)


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

    with patch("app.api.routes.admin.updates._latest_ghcr_version", latest):
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
        (
            "http://watchtower:8080/v1/update?async=true",
            {"Authorization": "Bearer secret-token"},
        )
    ]


def test_update_now_reports_unreachable_watchtower(admin_client, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_HTTP_API_TOKEN", "secret-token")

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("conexión rechazada")

    with (
        patch.object(httpx.AsyncClient, "post", new=fake_post),
        patch("app.api.routes.admin.updates.flog.warning") as warning,
    ):
        r = admin_client.post("/api/admin/update-now")

    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "update_now_unavailable"
    assert "Watchtower no está accesible" in warning.call_args.args[0]


def test_update_now_reports_watchtower_rejection(admin_client, monkeypatch):
    monkeypatch.setenv("WATCHTOWER_HTTP_API_TOKEN", "secret-token")

    async def fake_post(self, url, **kwargs):
        return _mock_action_response(401)

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        r = admin_client.post("/api/admin/update-now")

    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "update_now_rejected"


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


def test_platform_marca_auto_update_no_disponible_sin_proxy(admin_client, monkeypatch):
    """Sin DOCKER_PROXY_URL no hay forma de arrancar ni parar Watchtower, así
    que el panel no debe ofrecer el interruptor."""
    monkeypatch.delenv("DOCKER_PROXY_URL", raising=False)
    r = admin_client.get("/api/settings/platform")
    assert r.status_code == 200
    assert r.json()["auto_update_available"] is False


def test_platform_marca_auto_update_no_disponible_si_el_proxy_no_responde(
    admin_client, monkeypatch
):
    """El caso de producción: la variable está puesta, pero "docker-proxy" vive
    en el perfil `manual-updates` y no se ha arrancado. El interruptor salía
    encendido —su valor persistido por defecto— sobre un contenedor ausente."""
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")

    async def refuse(*args, **kwargs):
        raise OSError("Name or service not known")

    monkeypatch.setattr("asyncio.open_connection", refuse)
    r = admin_client.get("/api/settings/platform")
    assert r.status_code == 200
    assert r.json()["auto_update_available"] is False
    # El valor persistido sigue intacto: se oculta el control, no se reescribe.
    assert r.json()["auto_update_enabled"] is True


def test_platform_marca_auto_update_disponible_si_el_proxy_acepta(
    admin_client, monkeypatch
):
    monkeypatch.setenv("DOCKER_PROXY_URL", "http://docker-proxy:2375")

    async def accept(host, port):
        assert (host, port) == ("docker-proxy", 2375)
        return MagicMock(), MagicMock()

    monkeypatch.setattr("asyncio.open_connection", accept)
    r = admin_client.get("/api/settings/platform")
    assert r.json()["auto_update_available"] is True
