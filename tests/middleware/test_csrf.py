"""Las dos capas anti-CSRF: verificación de Origin y token double-submit.

Ningún test anterior mandaba `Origin` en un método inseguro (el único que lo
manda, tests/api/test_paginacion_total.py, es un GET), así que toda la
cobertura de esta puerta es la de aquí.
"""

from __future__ import annotations

import pytest

import app.config.session as _session
from app.auth.passwords import create_token, derive_csrf_token

ORIGEN_PROPIO = "http://testserver"
ORIGEN_AJENO = "https://webmalvada.example"
SUBDOMINIO = "https://blog.testserver"


@pytest.fixture(autouse=True)
def modo_enforce(monkeypatch):
    """Explícito aunque coincida con el default: estos tests fijan la política
    que prueban, no la heredan de la configuración del momento."""
    monkeypatch.setattr(_session, "CSRF_ORIGIN_CHECK", "enforce")
    monkeypatch.setattr(_session, "CSRF_TOKEN_CHECK", "enforce")


@pytest.fixture()
def crudo(client):
    """Cliente sin el hook que rellena `X-CSRF-Token` en toda la suite.

    Aquí se prueba la puerta, así que la cabecera la pone —o no— cada test.
    """
    client.event_hooks["request"].clear()
    return client


def _registrar(client) -> None:
    r = client.post(
        "/api/auth/register",
        json={
            "username": "csrfuser",
            "password": "pass1234",
            "email": "csrf@example.com",
        },
    )
    assert r.status_code == 200, r.text


# ── Capa 1: Origin ────────────────────────────────────────────────────────────


def test_get_con_origen_ajeno_pasa(client):
    """Los métodos seguros no se tocan: leer no cambia nada."""
    r = client.get("/api/settings/platform/public", headers={"Origin": ORIGEN_AJENO})
    assert r.status_code == 200


def test_post_sin_origen_ni_referer_pasa(client):
    """Flutter nativo, curl y el webhook de Stripe: no son navegadores.

    A un navegador no se le puede obligar a omitir Origin en un POST, así que
    su ausencia identifica a un cliente que no es atacable por esta vía.
    """
    _registrar(client)
    r = client.get("/api/auth/me")
    assert r.status_code == 200


def test_post_con_origen_propio_pasa(client):
    r = client.post(
        "/api/auth/register",
        json={"username": "propio", "password": "pass1234", "email": "p@example.com"},
        headers={"Origin": ORIGEN_PROPIO},
    )
    assert r.status_code == 200, r.text


def test_post_con_origen_ajeno_se_rechaza(client):
    _registrar(client)
    r = client.post("/api/auth/logout", headers={"Origin": ORIGEN_AJENO})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_origin_rejected"


def test_un_subdominio_es_un_origen_distinto(client):
    """El agujero que SameSite=Lax deja abierto: para el navegador un
    subdominio es «el mismo sitio» y la cookie sale igual."""
    _registrar(client)
    r = client.post("/api/auth/logout", headers={"Origin": SUBDOMINIO})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_origin_rejected"


def test_referer_ajeno_sin_origin_se_rechaza(client):
    _registrar(client)
    r = client.post(
        "/api/auth/logout", headers={"Referer": f"{ORIGEN_AJENO}/pagina/trampa"}
    )
    assert r.status_code == 403


def test_referer_propio_sin_origin_pasa(client):
    _registrar(client)
    r = client.post("/api/auth/logout", headers={"Referer": f"{ORIGEN_PROPIO}/app/"})
    assert r.status_code == 200


def test_bearer_queda_exento(client, monkeypatch):
    """Un PAT no es una credencial ambiental: el navegador no lo adjunta solo.

    Es lo que deja funcionando a la extensión de VS Code y a los scripts.
    El token es inválido a propósito: lo que se comprueba es que la petición
    llega a la autenticación (401) en vez de morir en la puerta CSRF (403).
    """
    r = client.post(
        "/api/agents",
        json={},
        headers={"Authorization": "Bearer iah_inexistente", "Origin": ORIGEN_AJENO},
    )
    assert r.status_code == 401


def test_modo_log_no_bloquea(client, monkeypatch):
    monkeypatch.setattr(_session, "CSRF_ORIGIN_CHECK", "log")
    _registrar(client)
    r = client.post("/api/auth/logout", headers={"Origin": ORIGEN_AJENO})
    assert r.status_code == 200


def test_modo_off_no_mira_nada(client, monkeypatch):
    monkeypatch.setattr(_session, "CSRF_ORIGIN_CHECK", "off")
    _registrar(client)
    r = client.post("/api/auth/logout", headers={"Origin": ORIGEN_AJENO})
    assert r.status_code == 200


def test_x_forwarded_host_de_un_peer_no_confiable_no_amplia_la_lista(
    client, monkeypatch
):
    """Si bastara con mandar la cabecera, la comprobación no valdría nada."""
    monkeypatch.setattr(_session, "TRUSTED_PROXIES", frozenset())
    _registrar(client)
    r = client.post(
        "/api/auth/logout",
        headers={"Origin": ORIGEN_AJENO, "X-Forwarded-Host": "webmalvada.example"},
    )
    assert r.status_code == 403


# ── Capa 2: token double-submit ───────────────────────────────────────────────


def test_login_emite_las_dos_cookies(client):
    _registrar(client)
    assert client.cookies.get("ga_token")
    csrf = client.cookies.get("ga_csrf")
    assert csrf and csrf == derive_csrf_token(client.cookies.get("ga_token"))


def test_logout_borra_las_dos(client):
    _registrar(client)
    client.post("/api/auth/logout")
    assert not client.cookies.get("ga_token")
    assert not client.cookies.get("ga_csrf")


def test_sin_token_se_rechaza(crudo):
    _registrar(crudo)
    crudo.cookies.delete("ga_csrf")
    r = crudo.post("/api/auth/logout")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_token_missing"


def test_sin_token_en_modo_log_pasa(crudo, monkeypatch):
    monkeypatch.setattr(_session, "CSRF_TOKEN_CHECK", "log")
    _registrar(crudo)
    crudo.cookies.delete("ga_csrf")
    r = crudo.post("/api/auth/logout")
    assert r.status_code == 200


def test_el_token_de_otra_sesion_no_sirve(crudo):
    """El *cookie tossing*: un subdominio comprometido puede sobreescribir la
    cookie del token Y mandar el mismo valor en la cabecera. Un double-submit
    que solo compare las dos lo da por bueno; este lo recalcula desde el JWT
    de la víctima, así que no cuadra."""
    _registrar(crudo)
    # El atacante controla las dos mitades: sobreescribe la cookie y manda ese
    # mismo valor en la cabecera. Comparar una con otra daría 200.
    ajeno = derive_csrf_token(create_token("otro-usuario"))
    r = crudo.post("/api/auth/logout", headers={"X-CSRF-Token": ajeno})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "csrf_token_invalid"


def test_sin_sesion_de_cookie_no_se_exige_token(client):
    """Sin credencial ambiental no hay nada que robar: el formulario público
    de contacto y el propio login tienen que seguir funcionando."""
    r = client.post(
        "/api/auth/login",
        json={"identifier": "nadie@example.com", "password": "loquesea"},
    )
    assert r.status_code == 401  # credenciales malas, no 403 de CSRF


def test_un_get_repone_la_cookie_que_falta(crudo):
    """Lo que permite subir la capa a `enforce` sin echar a nadie: las
    sesiones abiertas antes del despliegue se curan en la primera navegación."""
    _registrar(crudo)
    crudo.cookies.delete("ga_csrf")
    r = crudo.get("/api/auth/me")
    assert r.status_code == 200
    assert "ga_csrf" in r.headers.get("set-cookie", "")
    assert crudo.cookies.get("ga_csrf") == derive_csrf_token(
        crudo.cookies.get("ga_token")
    )


def test_la_cookie_del_token_es_legible_por_javascript(client):
    """Si saliera HttpOnly, ningún cliente podría reenviarla."""
    r = client.post(
        "/api/auth/register",
        json={"username": "legible", "password": "pass1234", "email": "l@example.com"},
    )
    cookies = r.headers.get_list("set-cookie")
    ga_csrf = next(c for c in cookies if c.startswith("ga_csrf="))
    assert "httponly" not in ga_csrf.lower()
    assert "samesite=lax" in ga_csrf.lower()
