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
        json={"email": "std_set@example.com", "password": "pass1234"},
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
        json={"email": "std_set2@example.com", "password": "pass1234"},
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
