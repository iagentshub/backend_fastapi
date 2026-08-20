"""Tests de app.config.startup_checks — la auditoría de configuración.

Lo que se protege aquí es la distinción que da sentido al módulo: una función
apagada por falta de una variable es un *warning* (puede ser deliberado), y una
configuración que se contradice a sí misma —verificación por email sin SMTP,
cobro activo sin claves de Stripe— es un *error*. Y que un error solo impide el
arranque cuando alguien pidió explícitamente que lo impidiera.
"""

from __future__ import annotations

import json

import pytest

import app.config.session as session_cfg
import app.config.startup_checks as checks


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """settings.json propio del test, apuntado desde app.config.data."""
    f = tmp_path / "settings.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(checks._data, "SETTINGS_FILE", f)

    def write(**data) -> None:
        f.write_text(json.dumps(data), encoding="utf-8")

    return write


def _check(key: str) -> checks.ConfigCheck:
    resultado = next((c for c in checks.run_checks() if c.key == key), None)
    assert resultado is not None, f"no hay ningún check con clave {key!r}"
    return resultado


# ── Ausencia vs contradicción ─────────────────────────────────────────────────


def test_sin_smtp_avisa_pero_no_es_error(settings, monkeypatch):
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")
    assert _check("smtp").severity == "warning"


def test_verificacion_de_email_sin_smtp_es_error(settings, monkeypatch):
    """El caso del informe: el registro queda a medias y nadie se entera."""
    settings(email_verify=True)
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")
    resultado = _check("email_verify")
    assert resultado.severity == "error"
    assert "GAIA_SMTP_HOST" in resultado.variables


def test_verificacion_de_email_con_smtp_esta_bien(settings, monkeypatch):
    settings(email_verify=True)
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "smtp.example.com")
    assert _check("email_verify").severity == "ok"


def test_billing_desactivado_no_exige_stripe(settings, monkeypatch):
    settings(billing_enabled=False)
    monkeypatch.setattr(checks._billing, "STRIPE_SECRET_KEY", "")
    assert _check("billing").severity == "ok"


def test_billing_activo_sin_claves_es_error(settings, monkeypatch):
    """Un typo en el nombre de la variable dejaba de cobrar sin decir nada."""
    settings(billing_enabled=True)
    monkeypatch.setattr(checks._billing, "STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setattr(checks._billing, "STRIPE_WEBHOOK_SECRET", "")
    monkeypatch.setattr(checks._billing, "STRIPE_PRODUCT_SEATS", "prod_x")
    resultado = _check("billing")
    assert resultado.severity == "error"
    # Solo la que falta, no las tres.
    assert resultado.variables == ("STRIPE_WEBHOOK_SECRET",)


def test_billing_activo_y_completo_esta_bien(settings, monkeypatch):
    settings(billing_enabled=True)
    for nombre in checks._STRIPE_REQUIRED:
        monkeypatch.setattr(checks._billing, nombre, "valor")
    assert _check("billing").severity == "ok"


def test_modo_de_registro_invalido_es_error(settings, monkeypatch):
    """«cerrado» en vez de «closed» deja la instalación abierta a cualquiera."""
    monkeypatch.setattr(session_cfg, "REGISTRATION_MODE", "cerrado")
    assert _check("registration_mode").severity == "error"


def test_modo_de_registro_valido_esta_bien(settings, monkeypatch):
    monkeypatch.setattr(session_cfg, "REGISTRATION_MODE", "invite")
    assert _check("registration_mode").severity == "ok"


def test_secreto_jwt_de_ejemplo_es_error(settings, monkeypatch):
    monkeypatch.setenv(session_cfg.JWT_SECRET_ENV, "cambia_esto_en_produccion")
    assert _check("jwt_secret").severity == "error"


def test_secreto_jwt_corto_avisa(settings, monkeypatch):
    monkeypatch.setenv(session_cfg.JWT_SECRET_ENV, "corto")
    assert _check("jwt_secret").severity == "warning"


def test_sin_origen_de_frontend_avisa(settings, monkeypatch):
    monkeypatch.delenv("GAIA_FRONTEND_URL", raising=False)
    monkeypatch.delenv("GAIA_CORS_ORIGINS", raising=False)
    assert _check("cors").severity == "warning"


def test_https_sin_proxies_de_confianza_avisa(settings, monkeypatch):
    monkeypatch.setenv("GAIA_FRONTEND_URL", "https://www.iagentshub.com")
    monkeypatch.setattr(session_cfg, "TRUSTED_PROXIES", frozenset())
    assert _check("trusted_proxies").severity == "warning"


def test_sin_cupo_de_invitados_avisa(settings, monkeypatch):
    """A 0 la demo queda apagada y el alta responde 503, sin que nada más lo
    diga: la ruta sigue publicada y el cliente sigue ofreciendo el botón."""
    import app.storage.guest as guest_mod

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 0)
    assert _check("guest_demo").severity == "warning"


def test_con_cupo_de_invitados_esta_bien(settings, monkeypatch):
    import app.storage.guest as guest_mod

    monkeypatch.setattr(guest_mod, "MAX_SESSIONS", 200)
    assert _check("guest_demo").severity == "ok"


# ── El informe ────────────────────────────────────────────────────────────────


def test_sin_limite_de_tamano_avisa(settings, monkeypatch):
    """0 es el default y es legítimo, pero no puede quedar sin decir: desde que
    nginx no impone el suyo, es lo único que limita el tamaño de una subida."""
    monkeypatch.setattr(session_cfg, "BODY_MAX_BYTES", 0)
    settings()
    assert _check("body_limit").severity == "warning"


def test_con_limite_de_tamano_configurado_esta_bien(settings, monkeypatch):
    settings(max_request_bytes=10 * 1024 * 1024)
    assert _check("body_limit").severity == "ok"


def test_limite_de_tamano_no_numerico_es_error(settings, monkeypatch):
    settings(max_request_bytes="diez megas")
    assert _check("body_limit").severity == "error"


def test_el_informe_no_lleva_valores(settings, monkeypatch):
    """Lo ve cualquier admin desde el panel: nombres de variable, nunca valores."""
    settings(billing_enabled=True)
    monkeypatch.setattr(checks._billing, "STRIPE_SECRET_KEY", "sk_live_supersecreto")
    monkeypatch.setenv(session_cfg.JWT_SECRET_ENV, "secreto-de-firma-que-no-debe-salir")
    serializado = json.dumps(checks.report())
    assert "sk_live_supersecreto" not in serializado
    assert "secreto-de-firma-que-no-debe-salir" not in serializado


def test_el_informe_cuenta_por_severidad(settings, monkeypatch):
    settings(email_verify=True)
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")
    informe = checks.report()
    assert informe["errors"] >= 1
    assert informe["warnings"] >= 1
    assert len(informe["checks"]) == len(checks.run_checks())


def test_settings_ilegible_no_rompe_la_auditoria(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text("{esto no es json", encoding="utf-8")
    monkeypatch.setattr(checks._data, "SETTINGS_FILE", f)
    assert checks.run_checks()  # sin excepción


# ── Modo estricto ─────────────────────────────────────────────────────────────


def test_sin_modo_estricto_los_errores_no_abortan(settings, monkeypatch):
    """Una instalación que hoy arranca degradada tiene que seguir arrancando."""
    settings(email_verify=True)
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")
    monkeypatch.delenv(checks._STRICT_ENV, raising=False)
    checks.assert_config_ok()  # no lanza


def test_con_modo_estricto_los_errores_abortan(settings, monkeypatch):
    settings(email_verify=True)
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")
    monkeypatch.setenv(checks._STRICT_ENV, "true")
    with pytest.raises(checks.ConfigError) as exc:
        checks.assert_config_ok()
    assert "GAIA_SMTP_HOST" in str(exc.value)


def test_con_modo_estricto_los_avisos_no_abortan(settings, monkeypatch):
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")
    monkeypatch.setattr(session_cfg, "REGISTRATION_MODE", "open")
    monkeypatch.setenv(session_cfg.JWT_SECRET_ENV, "x" * 32)
    monkeypatch.setenv(checks._STRICT_ENV, "1")
    sin_errores = [c for c in checks.run_checks() if c.severity != "error"]
    checks.assert_config_ok(sin_errores)  # no lanza


def test_el_arranque_registra_lo_degradado(settings, monkeypatch):
    """_lifespan llama aquí: el log dice qué función y por qué variable."""
    settings(email_verify=True)
    monkeypatch.setattr(session_cfg, "SMTP_HOST", "")

    lineas: list[tuple[str, str]] = []
    from app.utils import flog

    monkeypatch.setattr(flog, "error", lambda m, *a, **k: lineas.append(("error", m)))
    monkeypatch.setattr(flog, "warning", lambda m, *a, **k: lineas.append(("warn", m)))
    monkeypatch.setattr(flog, "ok", lambda m, *a, **k: lineas.append(("ok", m)))

    checks.log_startup_report()

    errores = [m for nivel, m in lineas if nivel == "error"]
    assert any("GAIA_SMTP_HOST" in m for m in errores)
