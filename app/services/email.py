"""Plantillas y entrega de correos transaccionales."""

from __future__ import annotations

import re
import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape, unescape

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
            flog.ok("[email] Mensaje enviado")
        except Exception as exc:  # noqa: BLE001
            # smtplib lanza una familia amplia (SMTP*, socket, SSL). Se
            # registra SOLO el tipo a propósito: str(exc) de un fallo de login
            # SMTP puede incluir el usuario y parte de la credencial.
            flog.warning(f"[email] Error SMTP: {type(exc).__name__}")

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
        flog.info(f"[email] SMTP no configurado; cambio de cuenta={status}")
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

def send_contact_notification(
    *, kind: str, name: str, email: str, message: str
) -> bool:
    """Avisa al operador de una petición del formulario público.

    Va al buzón de la propia instalación (`GAIA_SMTP_FROM`): es el único
    destino que se puede dar por bueno sin inventar otra opción de
    configuración, y es el mismo desde el que ya salen los correos.

    ponytail: si alguien necesita que los leads lleguen a otra dirección, eso
    es una clave nueva en la config de plataforma; hoy no la pide nadie.

    Devuelve si se llegó a encolar el envío. El llamador ya ha guardado la
    fila, así que un False solo significa «revísalo en la tabla».
    """
    from app.config.session import SMTP_FROM, SMTP_USER

    destino = SMTP_FROM or SMTP_USER
    if not _smtp_available() or not destino:
        flog.warning("[email] SMTP no configurado; la petición de contacto solo queda en la BD")
        return False

    # El contenido lo escribe un desconocido y acaba en el correo del operador:
    # sin escapar, cualquiera puede meter marcado en su bandeja de entrada.
    cuerpo = (
        f"<strong>Tipo:</strong> {escape(kind)}<br>"
        f"<strong>Nombre:</strong> {escape(name)}<br>"
        f"<strong>Email:</strong> {escape(email)}<br><br>"
        f"{escape(message).replace(chr(10), '<br>')}"
    )
    html = _build_email_html(
        title="Formulario de contacto",
        heading="Nueva petición de contacto",
        body_html=cuerpo,
        lang=_LANG_DEFECTO,
    )
    _send_smtp(destino, f"[iAgents Hub] Contacto: {kind}", html)
    return True


# Destino dentro de la app para cada tipo de aviso. Flutter sirve bajo
# `<base href="/app/">` y nginx cae a /app/index.html en cualquier subruta
# (`try_files $uri $uri/ /app/index.html`), así que estas URLs abren la
# pantalla directamente y no la portada.
_NOTIF_DESTINO: dict[str, str] = {
    # La invitación se acepta desde la campana, que está en la barra superior de
    # cualquier pantalla; `/app/manager` enseña las invitaciones que TU grupo ha
    # enviado, no las que has recibido, así que llevaría al sitio equivocado.
    #
    # ponytail: aterriza en el escritorio y el badge canta. Un enlace directo a
    # la invitación pediría una ruta propia en el router de Flutter, y hoy no
    # hay ninguna pantalla de notificaciones que abrir.
    "group_invite": "/app/dashboard",
    "license_assigned": "/app/profile",
}
_NOTIF_DESTINO_POR_DEFECTO = "/app/manager"


def send_notification_email(
    *, email: str, kind: str, data: dict, lang: str = _LANG_DEFECTO
) -> None:
    """Manda por correo el mismo aviso que enciende la campana de la app.

    Sin SMTP configurado —el caso por defecto de una instalación nueva— no pasa
    nada: la notificación in-app ya está guardada y esto solo deja constancia en
    el log. El correo es un canal adicional, nunca el único.
    """
    textos = _TEXTOS.get(f"notif_{kind}")
    if textos is None:
        # Un `kind` sin plantilla no es motivo para perder la notificación: ya
        # está en la BD y el usuario la verá en la campana.
        flog.warning(f"[email] Sin plantilla de correo para la notificación {kind!r}")
        return
    if not _smtp_available():
        flog.warning(f"[email] SMTP no configurado; no se envió el aviso {kind!r}")
        return

    # `actor` y `group` los escribe un usuario y acaban en el buzón de otro: sin
    # escapar, quien se ponga de nombre de grupo una etiqueta la mete en la
    # bandeja de entrada ajena. Mismo motivo que en send_contact_notification.
    campos = {clave: escape(str(valor)) for clave, valor in data.items()}
    t = _textos(f"notif_{kind}", lang)
    destino = _NOTIF_DESTINO.get(kind, _NOTIF_DESTINO_POR_DEFECTO)
    html = _build_email_html(
        title=t["title"],
        heading=_formatear(t["heading"], campos),
        body_html=_formatear(t["body"], campos),
        cta_url=f"{_base_url()}{destino}",
        cta_label=t["cta"],
        lang=lang,
    )
    _send_smtp(email, _formatear(t["asunto"], campos), html)


def _base_url() -> str:
    """Origen público para los enlaces del correo.

    Se lee en cada llamada, no al importar: `app.api.routes.auth._shared` hace
    lo mismo y sus tests parchean el entorno en caliente. Y se lee aquí en vez
    de reutilizar aquella función porque un servicio no puede importar de
    `app.api.routes` sin invertir las capas.
    """
    import os

    return os.getenv("GAIA_FRONTEND_URL", "").rstrip("/") or "http://localhost:8007"


def _formatear(plantilla: str, campos: dict) -> str:
    """`str.format` que no revienta si a la plantilla le falta un hueco."""
    try:
        return plantilla.format(**campos)
    except (KeyError, IndexError):
        return plantilla


_TEXTOS: dict[str, dict[str, dict]] = {
    "copia_enlace": {
        "es": {"texto": "O copia este enlace en tu navegador:"},
        "en": {"texto": "Or copy this link into your browser:"},
    },
    # Avisos de la campana. La clave es "notif_" + el `kind` de la fila de
    # `notifications`, y los huecos {actor} / {group} los rellena
    # send_notification_email con los valores ya escapados.
    "notif_group_invite": {
        "es": {
            "asunto": "{actor} te ha invitado al grupo {group}",
            "title": "Invitación a un grupo",
            "heading": "Te han invitado a {group}",
            "body": "<strong>{actor}</strong> te ha invitado a unirte al grupo "
                    "<strong>{group}</strong> en iAgents Hub.<br><br>Puedes "
                    "aceptarla o rechazarla desde la campana de notificaciones.",
            "cta": "Ver la invitación",
        },
        "en": {
            "asunto": "{actor} invited you to the group {group}",
            "title": "Group invitation",
            "heading": "You have been invited to {group}",
            "body": "<strong>{actor}</strong> invited you to join the group "
                    "<strong>{group}</strong> on iAgents Hub.<br><br>You can "
                    "accept or decline it from the notification bell.",
            "cta": "View the invitation",
        },
    },
    "notif_group_member_added": {
        "es": {
            "asunto": "Ya eres miembro de {group}",
            "title": "Grupos",
            "heading": "Te han añadido a {group}",
            "body": "<strong>{actor}</strong> te ha añadido al grupo "
                    "<strong>{group}</strong>. Ya puedes usar los recursos que "
                    "el grupo comparta contigo.",
            "cta": "Ir a mis grupos",
        },
        "en": {
            "asunto": "You are now a member of {group}",
            "title": "Groups",
            "heading": "You were added to {group}",
            "body": "<strong>{actor}</strong> added you to the group "
                    "<strong>{group}</strong>. You can now use the resources the "
                    "group shares with you.",
            "cta": "Go to my groups",
        },
    },
    "notif_group_member_removed": {
        "es": {
            "asunto": "Ya no perteneces a {group}",
            "title": "Grupos",
            "heading": "Te han sacado de {group}",
            "body": "<strong>{actor}</strong> te ha eliminado del grupo "
                    "<strong>{group}</strong>. Pierdes el acceso a los recursos "
                    "que compartía contigo.",
            "cta": "Ir a mis grupos",
        },
        "en": {
            "asunto": "You no longer belong to {group}",
            "title": "Groups",
            "heading": "You were removed from {group}",
            "body": "<strong>{actor}</strong> removed you from the group "
                    "<strong>{group}</strong>. You lose access to the resources "
                    "it shared with you.",
            "cta": "Go to my groups",
        },
    },
    "notif_group_role_changed": {
        "es": {
            "asunto": "Tu rol en {group} ha cambiado",
            "title": "Grupos",
            "heading": "Ahora eres {role} en {group}",
            "body": "<strong>{actor}</strong> ha cambiado tu rol en el grupo "
                    "<strong>{group}</strong>.",
            "cta": "Ir a mis grupos",
        },
        "en": {
            "asunto": "Your role in {group} has changed",
            "title": "Groups",
            "heading": "You are now {role} in {group}",
            "body": "<strong>{actor}</strong> changed your role in the group "
                    "<strong>{group}</strong>.",
            "cta": "Go to my groups",
        },
    },
    "notif_group_ownership_received": {
        "es": {
            "asunto": "Ahora eres propietario de {group}",
            "title": "Grupos",
            "heading": "{group} es tuyo",
            "body": "<strong>{actor}</strong> te ha traspasado la propiedad del "
                    "grupo <strong>{group}</strong>. Desde ahora gestionas sus "
                    "miembros y sus permisos.",
            "cta": "Gestionar el grupo",
        },
        "en": {
            "asunto": "You are now the owner of {group}",
            "title": "Groups",
            "heading": "{group} is yours",
            "body": "<strong>{actor}</strong> transferred ownership of the group "
                    "<strong>{group}</strong> to you. You now manage its members "
                    "and permissions.",
            "cta": "Manage the group",
        },
    },
    "notif_license_assigned": {
        "es": {
            "asunto": "Te han asignado una licencia de iAgents Hub",
            "title": "Suscripción",
            "heading": "Ya tienes licencia",
            "body": "<strong>{actor}</strong> te ha asignado un asiento de su "
                    "suscripción. Tu cuenta ya tiene acceso completo.",
            "cta": "Ver mi cuenta",
        },
        "en": {
            "asunto": "You have been assigned an iAgents Hub licence",
            "title": "Subscription",
            "heading": "Your licence is active",
            "body": "<strong>{actor}</strong> assigned you a seat from their "
                    "subscription. Your account now has full access.",
            "cta": "View my account",
        },
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
