"""El modo de registro es uno solo, lo mire quien lo mire.

Había dos interruptores con el mismo nombre: el alta comprobaba
`GAIA_REGISTRATION` (leída una vez al importar) y los clientes preguntaban por
`registration` en `settings.json`, que es lo que edita el panel de Admin.
Cerrar el registro desde Admin escondía el formulario en el cliente y dejaba
`POST /api/auth/register` devolviendo 200: la instalación se veía cerrada y
seguía aceptando cuentas de cualquiera.

Orden que se comprueba aquí: manda `settings.json`, y si no dice nada, la
variable de entorno.
"""

from __future__ import annotations

import json
from unittest.mock import patch


def _escribir_ajuste(clave: str, valor) -> None:
    """Deja [clave] en settings.json, o la quita si [valor] es None."""
    import app.config.data as cfg

    datos = json.loads(cfg.SETTINGS_FILE.read_text(encoding="utf-8"))
    if valor is None:
        datos.pop(clave, None)
    else:
        datos[clave] = valor
    cfg.SETTINGS_FILE.write_text(json.dumps(datos), encoding="utf-8")


def _escribir_registro(modo: str | None) -> None:
    _escribir_ajuste("registration", modo)


def _alta(client, correo: str):
    return client.post(
        "/api/auth/register",
        json={"username": "usuario.prueba", "email": correo, "password": "pass12345"},
    )


def test_cerrado_en_settings_bloquea_el_alta(client, reset_rate_limiter):
    """El fallo original: Admin cierra el registro y el endpoint lo ignoraba."""
    _escribir_registro("closed")

    r = _alta(client, "cerrado@test.com")

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "registration_disabled"


def test_la_config_publica_dice_lo_mismo_que_el_alta(client):
    """Si el cliente y el servidor no coinciden, el interruptor miente."""
    _escribir_registro("closed")

    publico = client.get("/api/settings/platform/public").json()

    assert publico["registration"] == "closed"


def test_invitacion_en_settings_bloquea_el_alta(client, reset_rate_limiter):
    _escribir_registro("invite")

    r = _alta(client, "invitacion@test.com")

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "registration_invite_only"


def test_sin_clave_en_settings_manda_la_variable(client, reset_rate_limiter):
    """El fichero calla: sigue decidiendo GAIA_REGISTRATION, como siempre."""
    _escribir_registro(None)

    with patch("app.config.session.REGISTRATION_MODE", "closed"):
        r = _alta(client, "porvariable@test.com")

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "registration_disabled"


def test_settings_abierto_gana_a_la_variable_cerrada(client, reset_rate_limiter):
    """Abrirlo desde Admin tiene que funcionar aunque el .env diga lo contrario.

    Es la otra mitad: si la variable ganase siempre, el panel serviría para
    cerrar pero no para abrir, y eso vuelve a ser un interruptor que miente.
    """
    _escribir_registro("open")

    with patch("app.config.session.REGISTRATION_MODE", "closed"):
        r = _alta(client, "abierto@test.com")

    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_un_modo_que_no_existe_cierra_el_registro(client, reset_rate_limiter):
    """Un typo no puede dejar la instalación abierta.

    `"cerrado"` no casaba con `"closed"` ni con `"invite"`, así que las dos
    comparaciones fallaban y el alta seguía adelante: quien escribía mal el
    modo creía haber cerrado el registro y lo tenía abierto de par en par.
    El chequeo de arranque ya lo avisaba por escrito; esto lo hace inofensivo.
    """
    _escribir_registro("cerrado")

    r = _alta(client, "typo@test.com")

    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "registration_disabled"


def test_verificacion_de_correo_activada_desde_settings(client, reset_rate_limiter):
    """El otro interruptor duplicado: `email_verify` tenía la misma forma.

    El panel lo escribía en settings.json y el alta miraba GAIA_EMAIL_VERIFY,
    así que activarlo desde Admin no mandaba ningún correo ni dejaba la cuenta
    pendiente: entraba directa.
    """
    _escribir_ajuste("email_verify", True)

    with patch("app.api.routes.auth.session.send_verification_email"):
        r = _alta(client, "verificame@test.com")

    assert r.status_code == 200
    assert r.json()["pending_verification"] is True


def test_verificacion_apagada_desde_settings_gana_a_la_variable(
    client, reset_rate_limiter
):
    _escribir_ajuste("email_verify", False)

    with patch("app.config.session.EMAIL_VERIFY_ENABLED", True):
        r = _alta(client, "sinverificar@test.com")

    assert r.status_code == 200
    assert r.json()["pending_verification"] is False
