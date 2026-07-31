"""Plantillas y entrega de correos transaccionales."""

from __future__ import annotations

import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.utils import flog

_SMTP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="iagents-smtp")


def _build_email_html(
    title: str,
    heading: str,
    body_html: str,
    cta_url: str = "",
    cta_label: str = "",
) -> str:
    cta_block = ""
    if cta_url and cta_label:
        cta_block = (
            f'<a href="{cta_url}" style="display:inline-block;background:#dc2626;color:#fff;text-decoration:none;'
            f'padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600">{cta_label}</a>'
            f'<p style="margin:28px 0 0;font-size:11px;color:#555">O copia este enlace en tu navegador:<br>'
            f'<span style="color:#888;word-break:break-all">{cta_url}</span></p>'
        )
    return f"""<!DOCTYPE html>
<html lang="es">
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


def send_verification_email(email: str, token: str, base_url: str = "") -> None:
    verify_url = f"{base_url}/verify/?token={token}"
    if not _smtp_available():
        flog.warning("[email] SMTP no configurado; no se envió la verificación")
        return
    html = _build_email_html(
        title="Verifica tu cuenta",
        heading="Confirma tu dirección de email",
        body_html="Haz clic en el botón para activar tu cuenta en iAgents.<br>El enlace expira en <strong>24 horas</strong>.",
        cta_url=verify_url,
        cta_label="Verificar cuenta",
    )
    _send_smtp(email, "Verifica tu cuenta en iAgents", html)


def send_reset_email(email: str, token: str, base_url: str = "") -> None:
    reset_url = f"{base_url}/reset-password/?token={token}"
    if not _smtp_available():
        flog.warning("[email] SMTP no configurado; no se envió la recuperación")
        return
    html = _build_email_html(
        title="Recuperar contraseña",
        heading="Restablecer contraseña",
        body_html="Recibimos una solicitud para restablecer la contraseña de tu cuenta.<br>El enlace expira en <strong>1 hora</strong>. Si no fuiste tú, ignora este mensaje.",
        cta_url=reset_url,
        cta_label="Restablecer contraseña",
    )
    _send_smtp(email, "Recupera el acceso a iAgents", html)


def send_deletion_scheduled_email(
    email: str, cancel_token: str, deletion_at: str, base_url: str = ""
) -> None:
    cancel_url = f"{base_url}/profile/?deletion_token={cancel_token}"
    try:
        date_str = datetime.fromisoformat(deletion_at).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        date_str = deletion_at[:10]
    if not _smtp_available():
        flog.warning("[email] SMTP no configurado; no se envió la cancelación")
        return
    html = _build_email_html(
        title="Eliminación de cuenta programada",
        heading="Tu cuenta será eliminada el " + date_str,
        body_html=(
            "Hemos recibido una solicitud para eliminar tu cuenta de iAgents.<br>"
            f"Todos tus datos se borrarán permanentemente el <strong>{date_str}</strong>.<br><br>"
            "Si cambiaste de opinión, cancela la eliminación antes de esa fecha."
        ),
        cta_url=cancel_url,
        cta_label="Cancelar eliminación",
    )
    _send_smtp(email, "Eliminación de tu cuenta en iAgents programada", html)


def send_account_status_email(email: str, is_active: bool, base_url: str = "") -> None:
    if not _smtp_available():
        status = "reactivada" if is_active else "suspendida"
        flog.info(f"[email] SMTP no configurado; cuenta {status}: {email}")
        return
    if is_active:
        html = _build_email_html(
            title="Estado de tu cuenta",
            heading="Tu cuenta ha sido reactivada",
            body_html="Un administrador ha reactivado tu acceso a iAgents.<br>Ya puedes iniciar sesión con normalidad.",
            cta_url=f"{base_url}/login/",
            cta_label="Entrar",
        )
        _send_smtp(email, "Tu cuenta en iAgents ha sido reactivada", html)
    else:
        html = _build_email_html(
            title="Estado de tu cuenta",
            heading="Tu cuenta ha sido suspendida",
            body_html="Un administrador ha suspendido temporalmente tu acceso a iAgents.<br>Si crees que es un error, contacta con el soporte.",
        )
        _send_smtp(email, "Tu cuenta en iAgents ha sido suspendida", html)
