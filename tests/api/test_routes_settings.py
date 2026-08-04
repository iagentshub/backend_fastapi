"""Tests para /api/settings — tema, idioma, layout y config del dashboard."""

from __future__ import annotations

import asyncio


def _auth_client(client):
    """Registra 'alice' y autentica el client devuelto."""
    from app.auth.auth import create_token, register_user

    asyncio.run(register_user("alice", "pass1234", email="alice@test.com"))
    token = create_token("alice")
    client.cookies.set("ga_token", token)
    return client


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


def test_get_settings_unauthenticated(client):
    """Sin auth debe devolver 401."""
    r = client.get("/api/settings")
    assert r.status_code == 401


def test_get_settings_defaults(client):
    """Con auth devuelve defaults theme=dark-red, language=es."""
    _auth_client(client)
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert data["theme"] == "dark-red"
    assert data["language"] == "es"
    assert data["theme_configurable"] is True
    assert data["default_theme"] == "dark-red"


# ---------------------------------------------------------------------------
# PUT /api/settings
# ---------------------------------------------------------------------------


def test_put_settings_valid_theme(client):
    """PUT theme válido devuelve 200 con el nuevo theme."""
    _auth_client(client)
    r = client.put("/api/settings", json={"theme": "dark-blue"})
    assert r.status_code == 200
    assert r.json()["theme"] == "dark-blue"


def test_put_settings_valid_language(client):
    """PUT language válido devuelve 200 con el nuevo idioma."""
    _auth_client(client)
    r = client.put("/api/settings", json={"language": "en"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"


def test_put_settings_invalid_theme(client):
    """PUT theme inválido devuelve 422."""
    _auth_client(client)
    r = client.put("/api/settings", json={"theme": "rainbow"})
    assert r.status_code == 422


def test_put_settings_invalid_language(client):
    """PUT idioma inválido devuelve 422."""
    _auth_client(client)
    r = client.put("/api/settings", json={"language": "fr"})
    assert r.status_code == 422


def test_put_settings_partial_update_preserves_other_keys(client):
    """PUT solo con theme no pisa el language previo."""
    _auth_client(client)
    client.put("/api/settings", json={"language": "en"})
    r = client.put("/api/settings", json={"theme": "dark-orange"})
    assert r.status_code == 200
    data = r.json()
    assert data["theme"] == "dark-orange"
    assert data["language"] == "en"


# ---------------------------------------------------------------------------
# GET /api/settings/dashboard-layout
# ---------------------------------------------------------------------------


def test_get_dashboard_layout_default(client):
    """Sin layout guardado devuelve {layout: null}."""
    _auth_client(client)
    r = client.get("/api/settings/dashboard-layout")
    assert r.status_code == 200
    assert r.json() == {"layout": None}


def test_get_dashboard_layout_unauthenticated(client):
    """Sin auth debe devolver 401."""
    r = client.get("/api/settings/dashboard-layout")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/settings/dashboard-layout
# ---------------------------------------------------------------------------


def test_put_dashboard_layout_valid(client):
    """PUT con widgets válidos devuelve 200 con el layout."""
    _auth_client(client)
    widgets = ["summary", "activity", "recent"]
    r = client.put("/api/settings/dashboard-layout", json={"layout": widgets})
    assert r.status_code == 200
    assert r.json()["layout"] == widgets


def test_put_dashboard_layout_all_valid_widgets(client):
    """PUT con todos los widgets conocidos devuelve 200."""
    _auth_client(client)
    widgets = ["summary", "token-usage", "activity", "conn-status", "recent"]
    r = client.put("/api/settings/dashboard-layout", json={"layout": widgets})
    assert r.status_code == 200
    assert r.json()["layout"] == widgets


def test_put_dashboard_layout_invalid_widget(client):
    """PUT con widget desconocido devuelve 422."""
    _auth_client(client)
    r = client.put(
        "/api/settings/dashboard-layout",
        json={"layout": ["summary", "unknown-widget"]},
    )
    assert r.status_code == 422


def test_put_dashboard_layout_empty_list(client):
    """PUT con lista vacía es válido (sin widgets)."""
    _auth_client(client)
    r = client.put("/api/settings/dashboard-layout", json={"layout": []})
    assert r.status_code == 200
    assert r.json()["layout"] == []


# ---------------------------------------------------------------------------
# Dashboard layout v2: instancias, tamaños y compatibilidad
# ---------------------------------------------------------------------------


def test_get_dashboard_layout_v2_default(client):
    _auth_client(client)
    r = client.get("/api/settings/dashboard-layout-v2")
    assert r.status_code == 200
    assert r.json() == {"version": 2, "items": None}


def test_put_dashboard_layout_v2_persists_instances_and_legacy_layout(client):
    _auth_client(client)
    items = [
        {
            "id": "tokens-today",
            "type": "token-kpi",
            "size": "compact",
            "config": {"period": "today"},
        },
        {
            "id": "tokens-month",
            "type": "token-kpi",
            "size": "compact",
            "config": {"period": "30d"},
        },
        {
            "id": "actions",
            "type": "quick-actions",
            "size": "medium",
            "config": {},
        },
    ]

    saved = client.put(
        "/api/settings/dashboard-layout-v2",
        json={"version": 2, "items": items},
    )
    assert saved.status_code == 200
    assert saved.json()["items"] == items

    loaded = client.get("/api/settings/dashboard-layout-v2")
    assert loaded.status_code == 200
    assert loaded.json()["items"] == items

    legacy = client.get("/api/settings/dashboard-layout")
    assert legacy.status_code == 200
    assert legacy.json()["layout"] == ["token-kpi", "quick-actions"]


def test_put_dashboard_layout_v2_rejects_unknown_duplicate_and_bad_size(client):
    _auth_client(client)
    base = {
        "id": "widget-1",
        "type": "summary",
        "size": "medium",
        "config": {},
    }

    unknown = client.put(
        "/api/settings/dashboard-layout-v2",
        json={"version": 2, "items": [{**base, "type": "unknown"}]},
    )
    assert unknown.status_code == 422

    duplicate = client.put(
        "/api/settings/dashboard-layout-v2",
        json={"version": 2, "items": [base, base]},
    )
    assert duplicate.status_code == 422

    bad_size = client.put(
        "/api/settings/dashboard-layout-v2",
        json={"version": 2, "items": [{**base, "size": "gigantic"}]},
    )
    assert bad_size.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/settings/dashboard-config
# ---------------------------------------------------------------------------


def test_get_dashboard_config_default(client):
    """Sin config guardada devuelve {config: {}}."""
    _auth_client(client)
    r = client.get("/api/settings/dashboard-config")
    assert r.status_code == 200
    assert r.json() == {"config": {}}


def test_get_dashboard_config_unauthenticated(client):
    """Sin auth debe devolver 401."""
    r = client.get("/api/settings/dashboard-config")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/settings/dashboard-config
# ---------------------------------------------------------------------------


def test_put_dashboard_config(client):
    """PUT config devuelve 200 con el objeto config guardado."""
    _auth_client(client)
    cfg = {"sidebar": "collapsed", "zoom": 1.2}
    r = client.put("/api/settings/dashboard-config", json={"config": cfg})
    assert r.status_code == 200
    assert r.json()["config"] == cfg


# ---------------------------------------------------------------------------
# Persistencia: PUT seguido de GET confirma el valor guardado
# ---------------------------------------------------------------------------


def test_persistence_theme(client):
    """PUT theme + GET confirma que el valor persiste."""
    _auth_client(client)
    client.put("/api/settings", json={"theme": "light-purple"})
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["theme"] == "light-purple"


def test_persistence_language(client):
    """PUT language + GET confirma que el valor persiste."""
    _auth_client(client)
    client.put("/api/settings", json={"language": "en"})
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["language"] == "en"


def test_persistence_dashboard_layout(client):
    """PUT layout + GET confirma persistencia."""
    _auth_client(client)
    widgets = ["summary", "token-usage", "conn-status"]
    client.put("/api/settings/dashboard-layout", json={"layout": widgets})
    r = client.get("/api/settings/dashboard-layout")
    assert r.status_code == 200
    assert r.json()["layout"] == widgets


def test_persistence_dashboard_config(client):
    """PUT config + GET confirma persistencia."""
    _auth_client(client)
    cfg = {"panels": ["a", "b"], "compact": True}
    client.put("/api/settings/dashboard-config", json={"config": cfg})
    r = client.get("/api/settings/dashboard-config")
    assert r.status_code == 200
    assert r.json()["config"] == cfg


# ---------------------------------------------------------------------------
# GET /api/settings/admin — solo admin
# ---------------------------------------------------------------------------


def test_get_admin_settings_unauthenticated(client):
    """Sin auth devuelve 401."""
    r = client.get("/api/settings/admin")
    assert r.status_code == 401


def test_get_admin_settings_forbidden_non_admin(client, reset_rate_limiter):
    """Usuario estándar devuelve 403."""
    client.post(
        "/api/auth/register",
        json={
            "username": "stdsettings",
            "email": "std_set@example.com",
            "password": "pass1234",
        },
    )
    client.post(
        "/api/auth/login", json={"email": "std_set@example.com", "password": "pass1234"}
    )
    r = client.get("/api/settings/admin")
    assert r.status_code == 403


def test_get_admin_settings_default(admin_client):
    """Admin sin preferencias guardadas devuelve log_retention_days=30."""
    r = admin_client.get("/api/settings/admin")
    assert r.status_code == 200
    assert r.json()["log_retention_days"] == 30


# ---------------------------------------------------------------------------
# PUT /api/settings/admin — solo admin
# ---------------------------------------------------------------------------


def test_put_admin_settings_valid(admin_client):
    """PUT con valor válido devuelve 200 con el nuevo valor."""
    r = admin_client.put("/api/settings/admin", json={"log_retention_days": 90})
    assert r.status_code == 200
    assert r.json()["log_retention_days"] == 90


def test_put_admin_settings_min_value(admin_client):
    """Valor mínimo permitido: 1 día."""
    r = admin_client.put("/api/settings/admin", json={"log_retention_days": 1})
    assert r.status_code == 200
    assert r.json()["log_retention_days"] == 1


def test_put_admin_settings_max_value(admin_client):
    """Valor máximo permitido: 365 días."""
    r = admin_client.put("/api/settings/admin", json={"log_retention_days": 365})
    assert r.status_code == 200
    assert r.json()["log_retention_days"] == 365


def test_put_admin_settings_zero_invalid(admin_client):
    """0 días no es válido — devuelve 422."""
    r = admin_client.put("/api/settings/admin", json={"log_retention_days": 0})
    assert r.status_code == 422


def test_put_admin_settings_above_max_invalid(admin_client):
    """Más de 365 días no es válido — devuelve 422."""
    r = admin_client.put("/api/settings/admin", json={"log_retention_days": 366})
    assert r.status_code == 422


def test_put_admin_settings_negative_invalid(admin_client):
    """Valor negativo no es válido — devuelve 422."""
    r = admin_client.put("/api/settings/admin", json={"log_retention_days": -10})
    assert r.status_code == 422


def test_put_admin_settings_forbidden_non_admin(client, reset_rate_limiter):
    """Usuario estándar no puede modificar ajustes admin."""
    client.post(
        "/api/auth/register",
        json={
            "username": "stdsettings2",
            "email": "std_set2@example.com",
            "password": "pass1234",
        },
    )
    client.post(
        "/api/auth/login",
        json={"email": "std_set2@example.com", "password": "pass1234"},
    )
    r = client.put("/api/settings/admin", json={"log_retention_days": 60})
    assert r.status_code == 403


def test_put_admin_settings_unauthenticated(client):
    """Sin auth devuelve 401."""
    r = client.put("/api/settings/admin", json={"log_retention_days": 60})
    assert r.status_code == 401


def test_put_admin_settings_partial_body(admin_client):
    """Body vacío (sin campos) no cambia nada y devuelve 200."""
    admin_client.put("/api/settings/admin", json={"log_retention_days": 45})
    r = admin_client.put("/api/settings/admin", json={})
    assert r.status_code == 200
    assert r.json()["log_retention_days"] == 45


# ---------------------------------------------------------------------------
# Persistencia: PUT + GET confirma el valor guardado
# ---------------------------------------------------------------------------


def test_admin_settings_persistence(admin_client):
    """PUT log_retention_days + GET confirma persistencia."""
    admin_client.put("/api/settings/admin", json={"log_retention_days": 14})
    r = admin_client.get("/api/settings/admin")
    assert r.status_code == 200
    assert r.json()["log_retention_days"] == 14


# ---------------------------------------------------------------------------
# Configuración de plataforma — /api/settings/platform(/public)
# ---------------------------------------------------------------------------


def test_get_platform_config_unauthenticated(client):
    r = client.get("/api/settings/platform")
    assert r.status_code == 401


def test_get_platform_config_forbidden_for_standard(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "platformcfg",
            "email": "platformcfg@example.com",
            "password": "pass1234",
        },
    )
    r = client.get("/api/settings/platform")
    assert r.status_code == 403


def test_get_platform_public_oauth_toggles_default_true(client):
    """Sin ninguna config previa, los 3 toggles OAuth son visibles por defecto."""
    r = client.get("/api/settings/platform/public")
    assert r.status_code == 200
    data = r.json()
    assert data["oauth_google_enabled"] is True
    assert data["oauth_apple_enabled"] is True
    assert data["oauth_microsoft_enabled"] is True


def test_put_platform_config_oauth_toggles(admin_client):
    r = admin_client.put(
        "/api/settings/platform",
        json={"oauth_google_enabled": False, "oauth_apple_enabled": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["oauth_google_enabled"] is False
    assert data["oauth_apple_enabled"] is False
    assert data["oauth_microsoft_enabled"] is True  # no tocado, sigue en su default


def test_platform_public_reflects_admin_oauth_toggle(admin_client, client):
    admin_client.put("/api/settings/platform", json={"oauth_microsoft_enabled": False})
    r = client.get("/api/settings/platform/public")
    assert r.json()["oauth_microsoft_enabled"] is False


def test_platform_public_oauth_github_hidden_without_client_id(client):
    """A diferencia de google/apple/microsoft, GitHub exige credenciales
    reales en el servidor — el toggle de admin (True por defecto) nunca
    basta por sí solo para mostrar el botón."""
    from unittest.mock import patch

    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", ""):
        r = client.get("/api/settings/platform/public")
    assert r.json()["oauth_github_enabled"] is False


def test_platform_public_oauth_github_visible_with_client_id_and_default_toggle(
    client,
):
    from unittest.mock import patch

    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"):
        r = client.get("/api/settings/platform/public")
    assert r.json()["oauth_github_enabled"] is True


def test_put_platform_config_oauth_github_toggle_hides_button_despite_client_id(
    admin_client, client
):
    """Apagar el toggle oculta el botón aunque el servidor sí tenga
    credenciales — pero no debe tocar los endpoints /api/auth/github/*."""
    from unittest.mock import patch

    r = admin_client.put(
        "/api/settings/platform", json={"oauth_github_enabled": False}
    )
    assert r.status_code == 200
    assert r.json()["oauth_github_enabled"] is False

    with patch("app.config.providers.GITHUB_OAUTH_CLIENT_ID", "test-client-id"):
        r = client.get("/api/settings/platform/public")
        assert r.json()["oauth_github_enabled"] is False

    # El endpoint real de login (probado a fondo en
    # test_routes_auth_github_login.py, incluyendo con este mismo toggle en
    # False) no lee `oauth_github_enabled` en ningún momento — no se
    # duplica esa cobertura aquí.


def test_admin_can_force_platform_theme(admin_client, client):
    r = admin_client.put(
        "/api/settings/platform",
        json={
            "users_can_configure_theme": False,
            "default_theme": "light-purple",
        },
    )
    assert r.status_code == 200

    public = client.get("/api/settings/platform/public")
    assert public.status_code == 200
    assert public.json()["users_can_configure_theme"] is False
    assert public.json()["default_theme"] == "light-purple"

    _auth_client(client)
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    assert settings.json()["theme"] == "light-purple"
    assert settings.json()["theme_configurable"] is False
    assert settings.json()["default_theme"] == "light-purple"


def test_forced_theme_rejects_user_theme_but_allows_language(admin_client, client):
    admin_client.put(
        "/api/settings/platform",
        json={
            "users_can_configure_theme": False,
            "default_theme": "dark-blue",
        },
    )
    _auth_client(client)

    theme = client.put("/api/settings", json={"theme": "light-red"})
    language = client.put("/api/settings", json={"language": "en"})

    assert theme.status_code == 403
    assert language.status_code == 200
    assert language.json()["language"] == "en"
    assert language.json()["theme"] == "dark-blue"


def test_reenabling_theme_restores_user_preference(admin_client, client):
    from app.auth.auth import create_token

    _auth_client(client)
    chosen = client.put("/api/settings", json={"theme": "light-orange"})
    assert chosen.status_code == 200

    client.cookies.set("ga_token", create_token("testadmin"))
    admin_client.put(
        "/api/settings/platform",
        json={
            "users_can_configure_theme": False,
            "default_theme": "dark-purple",
        },
    )
    client.cookies.set("ga_token", create_token("alice"))
    assert client.get("/api/settings").json()["theme"] == "dark-purple"

    client.cookies.set("ga_token", create_token("testadmin"))
    admin_client.put(
        "/api/settings/platform",
        json={"users_can_configure_theme": True},
    )
    client.cookies.set("ga_token", create_token("alice"))
    assert client.get("/api/settings").json()["theme"] == "light-orange"


def test_platform_rejects_invalid_default_theme(admin_client):
    r = admin_client.put(
        "/api/settings/platform",
        json={"default_theme": "rainbow"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "default_theme"


def test_platform_public_maintenance_defaults_off(client):
    r = client.get("/api/settings/platform/public")
    assert r.status_code == 200
    data = r.json()
    assert data["maintenance_enabled"] is False
    assert data["maintenance_message"] == ""
    assert data["maintenance_at"] is None


def test_admin_sets_maintenance_notice(admin_client, client):
    r = admin_client.put(
        "/api/settings/platform",
        json={
            "maintenance_enabled": True,
            "maintenance_message": "Mantenimiento programado el viernes a las 20h",
            "maintenance_at": "2026-08-07T20:00:00",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["maintenance_enabled"] is True
    assert data["maintenance_message"] == "Mantenimiento programado el viernes a las 20h"
    assert data["maintenance_at"] == "2026-08-07T20:00:00"

    public = client.get("/api/settings/platform/public")
    assert public.json()["maintenance_enabled"] is True
    assert public.json()["maintenance_message"] == (
        "Mantenimiento programado el viernes a las 20h"
    )


def test_platform_rejects_maintenance_message_too_long(admin_client):
    r = admin_client.put(
        "/api/settings/platform",
        json={"maintenance_message": "x" * 501},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "maintenance_message"


def test_platform_rejects_invalid_maintenance_at(admin_client):
    r = admin_client.put(
        "/api/settings/platform",
        json={"maintenance_at": "not-a-date"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "maintenance_at"


def test_platform_public_splash_defaults(client):
    r = client.get("/api/settings/platform/public")
    assert r.status_code == 200
    data = r.json()
    assert data["splash_cycles"] == 1
    assert data["splash_end_on_logo"] is True


def test_admin_sets_splash_config(admin_client, client):
    r = admin_client.put(
        "/api/settings/platform",
        json={"splash_cycles": 3, "splash_end_on_logo": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["splash_cycles"] == 3
    assert data["splash_end_on_logo"] is False

    public = client.get("/api/settings/platform/public")
    assert public.json()["splash_cycles"] == 3
    assert public.json()["splash_end_on_logo"] is False


def test_platform_rejects_splash_cycles_out_of_range(admin_client):
    r = admin_client.put(
        "/api/settings/platform",
        json={"splash_cycles": 11},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["field"] == "splash_cycles"

    r_zero = admin_client.put(
        "/api/settings/platform",
        json={"splash_cycles": 0},
    )
    assert r_zero.status_code == 422
