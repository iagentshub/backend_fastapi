"""Idioma y parte de texto plano de los correos (mejoras #13 y #14).

Los cuatro correos transaccionales salían siempre en español con `lang="es"`
fijo en el `<html>`, y el `multipart/alternative` llevaba una sola parte: quien
lee en modo texto recibía el marcado en crudo o nada, con el enlace de
verificación atrapado dentro del `<a>`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import email as email_mod


@pytest.fixture()
def capturar(monkeypatch):
    """Intercepta el envío y devuelve los mensajes MIME construidos."""
    monkeypatch.setattr(email_mod, "_smtp_available", lambda: True)
    enviados = []

    def _spy(to, subject, html):
        enviados.append({"to": to, "subject": subject, "html": html})

    monkeypatch.setattr(email_mod, "_send_smtp", _spy)
    return enviados


# ── #13 · idioma ────────────────────────────────────────────────────────────

def test_verificacion_en_ingles(capturar):
    email_mod.send_verification_email("a@b.test", "tok", "https://hub.test", lang="en")

    msg = capturar[0]
    assert msg["subject"] == "Verify your iAgents account"
    assert 'lang="en"' in msg["html"]
    assert "Confirm your email address" in msg["html"]
    assert "Or copy this link" in msg["html"]


def test_verificacion_en_espanol_por_defecto(capturar):
    email_mod.send_verification_email("a@b.test", "tok", "https://hub.test")

    msg = capturar[0]
    assert msg["subject"] == "Verifica tu cuenta en iAgents"
    assert 'lang="es"' in msg["html"]


def test_idioma_desconocido_cae_al_espanol(capturar):
    email_mod.send_verification_email("a@b.test", "tok", "https://hub.test", lang="fr")

    assert capturar[0]["subject"] == "Verifica tu cuenta en iAgents"


def test_los_cuatro_correos_tienen_ingles():
    for clave in ("verify", "reset", "deletion", "reactivada", "suspendida"):
        assert "en" in email_mod._TEXTOS[clave], f"falta el inglés de {clave}"
        assert email_mod._TEXTOS[clave]["en"]["asunto"]


def test_fecha_de_borrado_se_interpola_en_ambos_idiomas(capturar):
    email_mod.send_deletion_scheduled_email(
        "a@b.test", "tok", "2026-09-01T00:00:00+00:00", "https://hub.test", lang="en"
    )

    html = capturar[0]["html"]
    assert "01/09/2026" in html
    assert "{fecha}" not in html


# ── #14 · parte de texto plano ──────────────────────────────────────────────

def _mensaje_mime(monkeypatch, html: str = "<p>Hola</p>"):
    """Construye el MIME real de _send_smtp sin llegar a enviarlo."""
    import app.config.session as cfg

    monkeypatch.setattr(cfg, "SMTP_HOST", "smtp.test", raising=False)
    monkeypatch.setattr(cfg, "SMTP_PORT", 25, raising=False)
    monkeypatch.setattr(cfg, "SMTP_USER", "no-reply@test", raising=False)
    monkeypatch.setattr(cfg, "SMTP_PASS", "", raising=False)
    monkeypatch.setattr(cfg, "SMTP_FROM", "no-reply@test", raising=False)
    monkeypatch.setattr(cfg, "SMTP_TLS", "", raising=False)

    with patch.object(email_mod._SMTP_EXECUTOR, "submit") as submit:
        email_mod._send_smtp("a@b.test", "Asunto", html)
    assert submit.called
    return submit


def test_el_correo_lleva_parte_de_texto_y_html(monkeypatch):
    from email.mime.multipart import MIMEMultipart

    original_attach = MIMEMultipart.attach
    partes = []

    def _spy_attach(self, payload):
        partes.append(payload.get_content_type())
        return original_attach(self, payload)

    monkeypatch.setattr(MIMEMultipart, "attach", _spy_attach)
    _mensaje_mime(monkeypatch, "<p>Hola</p>")

    assert partes == ["text/plain", "text/html"], (
        "en multipart/alternative el cliente muestra la ÚLTIMA parte que "
        "entiende: la de texto va primero"
    )


def test_la_version_de_texto_conserva_la_url_del_enlace():
    html = (
        '<p>Pulsa aquí</p>'
        '<a href="https://hub.test/verify/?token=abc123">Verificar cuenta</a>'
    )
    texto = email_mod._html_a_texto(html)

    assert "https://hub.test/verify/?token=abc123" in texto
    assert "Verificar cuenta" in texto
    assert "<a " not in texto and "<p>" not in texto


def test_la_version_de_texto_desescapa_entidades():
    assert "ñ" in email_mod._html_a_texto("<p>a&ntilde;o</p>")
