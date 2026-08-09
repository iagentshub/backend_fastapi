"""Plantillas y entrega de correos transaccionales."""

from __future__ import annotations

import re
import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape

from app.utils import flog

_SMTP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="iagents-smtp")

# Idioma por defecto de los correos. `lang` SIEMPRE se resuelve en el handler y
# se pasa como argumento: `get_locale()` es un ContextVar y `_send_smtp` encola
# en un ThreadPoolExecutor donde ese contexto ya no existe, así que llamarlo
# desde aquí devolvería siempre el valor por defecto sin que nada fallara.
_LANG_DEFECTO = "es"


def _textos(clave: str, lang: str) -> dict:
    """Textos de un correo en el idioma pedido, con caída al español."""
    por_idioma = _TEXTOS[clave]
    return por_idioma.get(lang) or por_idioma[_LANG_DEFECTO]


def _build_email_html(
    title: str,
    heading: str,
    body_html: str,
    cta_url: str = "",
    cta_label: str = "",
    lang: str = _LANG_DEFECTO,
) -> str:
    cta_block = ""
    if cta_url and cta_label:
        copia = _textos("copia_enlace", lang)["texto"]
        cta_block = (
            f'<a href="{cta_url}" style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;'
            f'padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600">{cta_label}</a>'
            f'<p style="margin:28px 0 0;font-size:11px;color:#555">{copia}<br>'
            f'<span style="color:#888;word-break:break-all">{cta_url}</span></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f10;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f10;padding:40px 16px">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#1a1a1b;border-radius:12px;padding:40px 36px;max-width:480px">
        <tr><td>
          <p style="margin:0 0 4px;font-size:20px;font-weight:700;color:#fff">iAgents</p>
          <p style="margin:0 0 32px;font-size:13px;color:#666">{title}</p>
          <h1 style="margin:0 0 12px;font-size:22px;font-weight:600;color:#e8e8e8">{heading}</h1>
          <div style="font-size:14px;color:#aaa;line-height:1.6;margin-bottom:28px">{body_html}</div>
          {cta_block}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _html_a_texto(html: str) -> str:
    """Versión en texto plano del correo: el enlace importa más que el estilo."""
    texto = re.sub(r"<br\s*/?>", "\n", html)
    texto = re.sub(r"</(p|div|tr|h1|h2|table)>", "\n", texto)
    # El enlace vive dentro del <a>; sin conservar la URL, un "haz clic aquí"
    # en texto plano no lleva a ningún sitio.
    texto = re.sub(
        r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"\2: \1", texto, flags=re.S
    )
    texto = re.sub(r"<[^>]+>", "", texto)
    return re.sub(r"\n{3,}", "\n\n", unescape(texto)).strip()


def _send_smtp(to: str, subject: str, html: str) -> None:
    """Programa el envío SMTP sin bloquear el thread de la petición."""
    from app.config.session import (
        SMTP_FROM,
        SMTP_HOST,
        SMTP_PASS,
        SMTP_PORT,
        SMTP_TLS,
        SMTP_USER,
    )

    if not SMTP_HOST:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM or SMTP_USER
    msg["To"] = to
    # multipart/alternative es un contenedor cuyo sentido es ofrecer la misma
    # información en varios formatos, y llevaba una sola parte: quien lee en
    # modo texto (lector de pantalla, cliente de terminal, HTML desactivado por
    # política) recibía el marcado en crudo o nada —y el enlace de verificación
    # viaja solo dentro del <a>—.
    #
    # El ORDEN es semántico: el cliente muestra la ÚLTIMA parte que sabe
    # interpretar, así que la de texto va primero. Al revés, los clientes
    # gráficos enseñarían el texto pelado.
    msg.attach(MIMEText(_html_a_texto(html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    def _send() -> None:
        try:
            if SMTP_TLS == "ssl":
                server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
            else:
                server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
                if SMTP_TLS == "starttls":
                    server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(msg["From"], [to], msg.as_string())
            server.quit()
            flog.ok(f"[email] Enviado a {to}: {subject}")
        except Exception as exc:
            flog.warning(f"[email] Error al enviar a {to}: {exc}")

    _SMTP_EXECUTOR.submit(_send)


def _smtp_available() -> bool:
    from app.config.session import SMTP_HOST

    return bool(SMTP_HOST)


def send_verification_email(
    email: str, token: str, base_url: str = "", lang: str = _LANG_DEFECTO
) -> None:
    verify_url = f"{base_url}/verify/?token={token}"
    if not _smtp_available():
        flog.warning("[email] SMTP no configurado; no se envió la verificación")
        return
    t = _textos("verify", lang)
    html = _build_email_html(
        title=t["title"],
        heading=t["heading"],
        body_html=t["body"],
        cta_url=verify_url,
        cta_label=t["cta"],
        lang=lang,
    )
    _send_smtp(email, t["asunto"], html)


def send_reset_email(
    email: str, token: str, base_url: str = "", lang: str = _LANG_DEFECTO
) -> None:
    reset_url = f"{base_url}/reset-password/?token={token}"
    if not _smtp_available():
        flog.warning("[email] SMTP no configurado; no se envió la recuperación")
        return
    t = _textos("reset", lang)
    html = _build_email_html(
        title=t["title"],
        heading=t["heading"],
        body_html=t["body"],
        cta_url=reset_url,
        cta_label=t["cta"],
        lang=lang,
    )
    _send_smtp(email, t["asunto"], html)


def send_deletion_scheduled_email(
    email: str,
    cancel_token: str,
    deletion_at: str,
    base_url: str = "",
    lang: str = _LANG_DEFECTO,
) -> None:
    cancel_url = f"{base_url}/profile/?deletion_token={cancel_token}"
    try:
        date_str = datetime.fromisoformat(deletion_at).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        date_str = deletion_at[:10]
    if not _smtp_available():
        flog.warning("[email] SMTP no configurado; no se envió la cancelación")
        return
    t = _textos("deletion", lang)
    html = _build_email_html(
        title=t["title"],
        heading=t["heading"].format(fecha=date_str),
        body_html=t["body"].format(fecha=date_str),
        cta_url=cancel_url,
        cta_label=t["cta"],
        lang=lang,
    )
    _send_smtp(email, t["asunto"], html)


def send_account_status_email(
    email: str, is_active: bool, base_url: str = "", lang: str = _LANG_DEFECTO
) -> None:
    if not _smtp_available():
        status = "reactivada" if is_active else "suspendida"
        flog.info(f"[email] SMTP no configurado; cuenta {status}: {email}")
        return
    t = _textos("reactivada" if is_active else "suspendida", lang)
    html = _build_email_html(
        title=t["title"],
        heading=t["heading"],
        body_html=t["body"],
        cta_url=f"{base_url}/login/" if is_active else "",
        cta_label=t.get("cta", ""),
        lang=lang,
    )
    _send_smtp(email, t["asunto"], html)


# Los cuatro correos transaccionales, en los idiomas que resuelve
# LocaleMiddleware (SUPPORTED_LOCALES). Un idioma que falte cae al español.
_TEXTOS: dict[str, dict[str, dict]] = {
    "copia_enlace": {
        "es": {"texto": "O copia este enlace en tu navegador:"},
        "en": {"texto": "Or copy this link into your browser:"},
    },
    "verify": {
        "es": {
            "asunto": "Verifica tu cuenta en iAgents",
            "title": "Verifica tu cuenta",
            "heading": "Confirma tu dirección de email",
            "body": "Haz clic en el botón para activar tu cuenta en iAgents.<br>"
                    "El enlace expira en <strong>24 horas</strong>.",
            "cta": "Verificar cuenta",
        },
        "en": {
            "asunto": "Verify your iAgents account",
            "title": "Verify your account",
            "heading": "Confirm your email address",
            "body": "Click the button to activate your iAgents account.<br>"
                    "The link expires in <strong>24 hours</strong>.",
            "cta": "Verify account",
        },
    },
    "reset": {
        "es": {
            "asunto": "Recupera el acceso a iAgents",
            "title": "Recuperar contraseña",
            "heading": "Restablecer contraseña",
            "body": "Recibimos una solicitud para restablecer la contraseña de tu "
                    "cuenta.<br>El enlace expira en <strong>1 hora</strong>. Si no "
                    "fuiste tú, ignora este mensaje.",
            "cta": "Restablecer contraseña",
        },
        "en": {
            "asunto": "Recover access to iAgents",
            "title": "Password recovery",
            "heading": "Reset your password",
            "body": "We received a request to reset your account password.<br>"
                    "The link expires in <strong>1 hour</strong>. If it wasn't you, "
                    "ignore this message.",
            "cta": "Reset password",
        },
    },
    "deletion": {
        "es": {
            "asunto": "Eliminación de tu cuenta en iAgents programada",
            "title": "Eliminación de cuenta programada",
            "heading": "Tu cuenta será eliminada el {fecha}",
            "body": "Hemos recibido una solicitud para eliminar tu cuenta de "
                    "iAgents.<br>Todos tus datos se borrarán permanentemente el "
                    "<strong>{fecha}</strong>.<br><br>Si cambiaste de opinión, "
                    "cancela la eliminación antes de esa fecha.",
            "cta": "Cancelar eliminación",
        },
        "en": {
            "asunto": "Your iAgents account deletion is scheduled",
            "title": "Account deletion scheduled",
            "heading": "Your account will be deleted on {fecha}",
            "body": "We received a request to delete your iAgents account.<br>"
                    "All your data will be permanently erased on "
                    "<strong>{fecha}</strong>.<br><br>If you changed your mind, "
                    "cancel the deletion before that date.",
            "cta": "Cancel deletion",
        },
    },
    "reactivada": {
        "es": {
            "asunto": "Tu cuenta en iAgents ha sido reactivada",
            "title": "Estado de tu cuenta",
            "heading": "Tu cuenta ha sido reactivada",
            "body": "Un administrador ha reactivado tu acceso a iAgents.<br>"
                    "Ya puedes iniciar sesión con normalidad.",
            "cta": "Entrar",
        },
        "en": {
            "asunto": "Your iAgents account has been reactivated",
            "title": "Account status",
            "heading": "Your account has been reactivated",
            "body": "An administrator has restored your access to iAgents.<br>"
                    "You can sign in as usual again.",
            "cta": "Sign in",
        },
    },
    "suspendida": {
        "es": {
            "asunto": "Tu cuenta en iAgents ha sido suspendida",
            "title": "Estado de tu cuenta",
            "heading": "Tu cuenta ha sido suspendida",
            "body": "Un administrador ha suspendido temporalmente tu acceso a "
                    "iAgents.<br>Si crees que es un error, contacta con el soporte.",
        },
        "en": {
            "asunto": "Your iAgents account has been suspended",
            "title": "Account status",
            "heading": "Your account has been suspended",
            "body": "An administrator has temporarily suspended your access to "
                    "iAgents.<br>If you think this is a mistake, contact support.",
        },
    },
}
