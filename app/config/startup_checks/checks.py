"""Las comprobaciones, una por función. Aquí se añade una nueva.

Ninguna lee configuración por su cuenta: miran la que los demás módulos ya han
resuelto y la clasifican. Por eso importan los módulos (`_billing`, `_data`) y
no sus valores — así el módulo sigue siendo el mismo objeto que parchean los
tests y que reescribe `patch_data_dir`.
"""


from __future__ import annotations

import os

# Los módulos se importan enteros, no sus símbolos: `DATA_DIR` y compañía se
# resuelven al importar, y los tests (y el propio panel de admin, que puede
# reescribir settings.json en caliente) necesitan ver el valor de ahora.
import app.config.billing as _billing
import app.config.data as _data
import app.config.maintenance as _maintenance
import app.config.session as _session
from app.config.startup_checks._model import (
    ConfigCheck,
)


def _check_jwt(settings: dict) -> ConfigCheck:
    secret = os.environ.get(_session.JWT_SECRET_ENV) or str(
        settings.get("jwt_secret", "")
    )
    if secret in _session.JWT_UNSAFE_SECRETS:
        return ConfigCheck(
            key="jwt_secret",
            feature="Sesiones y cifrado de credenciales",
            severity="error",
            detail=(
                "El secreto de firma no está configurado o es uno de los valores "
                "de ejemplo. Ninguna petición autenticada funcionará: la primera "
                "que llegue aborta al firmar."
            ),
            variables=(_session.JWT_SECRET_ENV,),
        )
    if len(secret.encode("utf-8")) < 32:
        return ConfigCheck(
            key="jwt_secret",
            feature="Sesiones y cifrado de credenciales",
            severity="warning",
            detail=(
                "El secreto tiene menos de 32 bytes; RFC 7518 pide al menos ese "
                "tamaño para HS256. Firma, pero más débil de lo que promete."
            ),
            variables=(_session.JWT_SECRET_ENV,),
        )
    return ConfigCheck(
        key="jwt_secret",
        feature="Sesiones y cifrado de credenciales",
        severity="ok",
        detail="Secreto de firma configurado.",
        variables=(_session.JWT_SECRET_ENV,),
    )

def _check_cors() -> ConfigCheck:
    configured = os.getenv("GAIA_CORS_ORIGINS") or os.getenv("GAIA_FRONTEND_URL", "")
    if not configured:
        return ConfigCheck(
            key="cors",
            feature="Origen del frontend (CORS)",
            severity="warning",
            detail=(
                "Sin origen configurado solo se aceptan peticiones desde "
                "localhost. Un frontend desplegado recibirá error de CORS en "
                "todas sus llamadas."
            ),
            variables=("GAIA_FRONTEND_URL", "GAIA_CORS_ORIGINS"),
        )
    return ConfigCheck(
        key="cors",
        feature="Origen del frontend (CORS)",
        severity="ok",
        detail="Orígenes permitidos configurados explícitamente.",
        variables=("GAIA_FRONTEND_URL", "GAIA_CORS_ORIGINS"),
    )

def _check_smtp() -> ConfigCheck:
    if not _session.SMTP_HOST:
        return ConfigCheck(
            key="smtp",
            feature="Envío de correo",
            severity="warning",
            detail=(
                "Sin servidor SMTP no sale ningún correo: los enlaces de "
                "verificación y de restablecimiento de contraseña se escriben "
                "en el log en vez de enviarse."
            ),
            variables=("GAIA_SMTP_HOST",),
        )
    if _session.SMTP_TLS not in ("starttls", "ssl", "none"):
        return ConfigCheck(
            key="smtp",
            feature="Envío de correo",
            severity="error",
            detail=(
                f"Modo TLS desconocido ({_session.SMTP_TLS!r}); se esperaba "
                "starttls, ssl o none. El envío usará conexión en claro."
            ),
            variables=("GAIA_SMTP_TLS",),
        )
    return ConfigCheck(
        key="smtp",
        feature="Envío de correo",
        severity="ok",
        detail="Servidor SMTP configurado.",
        variables=("GAIA_SMTP_HOST",),
    )

def _check_push() -> ConfigCheck:
    """Web Push: las tres variables o ninguna.

    Ninguna es un aviso —una instalación puede querer solo campana y correo—,
    pero a medias es un error: la aplicación ofrece el interruptor, el usuario
    lo activa y el envío falla después sin que nadie lo vea. El contacto no es
    opcional, la firma exige `sub` (RFC 8292).
    """
    faltan = tuple(
        var
        for var, valor in (
            ("GAIA_VAPID_PUBLIC_KEY", _session.VAPID_PUBLIC_KEY),
            ("GAIA_VAPID_PRIVATE_KEY", _session.VAPID_PRIVATE_KEY),
            ("GAIA_VAPID_SUBJECT", _session.VAPID_SUBJECT),
        )
        if not valor
    )
    gravedad = "ok" if not faltan else ("warning" if len(faltan) == 3 else "error")
    detalle = {
        "ok": "Claves VAPID configuradas.",
        "warning": "Sin claves VAPID no salta ningún aviso fuera de la "
        "aplicación; campana y correo siguen igual. Genéralas con "
        "`python -m py_vapid --gen`.",
        "error": "Configuración de push incompleta: queda desactivado aunque "
        "haya claves puestas.",
    }[gravedad]
    return ConfigCheck(
        key="push",
        feature="Notificaciones push",
        severity=gravedad,
        detail=detalle,
        variables=faltan or ("GAIA_VAPID_PUBLIC_KEY",),
    )

def _check_email_verify(settings: dict) -> ConfigCheck:
    enabled = bool(settings.get("email_verify", _session.EMAIL_VERIFY_ENABLED))
    if not enabled:
        return ConfigCheck(
            key="email_verify",
            feature="Verificación de email al registrarse",
            severity="ok",
            detail="Desactivada: las cuentas quedan verificadas al crearse.",
            variables=("GAIA_EMAIL_VERIFY",),
        )
    if not _session.SMTP_HOST:
        return ConfigCheck(
            key="email_verify",
            feature="Verificación de email al registrarse",
            severity="error",
            detail=(
                "La verificación está activa pero no hay servidor SMTP: nadie "
                "recibe el enlace y ningún registro nuevo llega a poder entrar."
            ),
            variables=("GAIA_EMAIL_VERIFY", "GAIA_SMTP_HOST"),
        )
    return ConfigCheck(
        key="email_verify",
        feature="Verificación de email al registrarse",
        severity="ok",
        detail="Activa, con servidor SMTP configurado.",
        variables=("GAIA_EMAIL_VERIFY", "GAIA_SMTP_HOST"),
    )

_STRIPE_REQUIRED = (
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRODUCT_SEATS",
)

def _check_billing(settings: dict) -> ConfigCheck:
    missing = tuple(
        name for name in _STRIPE_REQUIRED if not getattr(_billing, name, "")
    )
    if not bool(settings.get("billing_enabled", False)):
        return ConfigCheck(
            key="billing",
            feature="Cobro de suscripciones (Stripe)",
            severity="ok",
            detail="Desactivado en la configuración de plataforma.",
            variables=_STRIPE_REQUIRED,
        )
    if missing:
        return ConfigCheck(
            key="billing",
            feature="Cobro de suscripciones (Stripe)",
            severity="error",
            detail=(
                "La puerta de suscripción está activa —el acceso a agentes, "
                "conexiones y conocimiento depende de una suscripción— pero "
                "Stripe no puede cobrar sin estas variables."
            ),
            variables=missing,
        )
    return ConfigCheck(
        key="billing",
        feature="Cobro de suscripciones (Stripe)",
        severity="ok",
        detail="Activo, con claves de Stripe configuradas.",
        variables=_STRIPE_REQUIRED,
    )

def _check_billing_tax(settings: dict) -> ConfigCheck:
    """Impuestos sobre las suscripciones.

    Apagarlo no rompe nada visible —la suscripción se cobra igual, sin IVA— y
    ese es justo el motivo por el que tiene que salir en el informe: el
    descuadre no aparece hasta la declaración.
    """
    variables = ("STRIPE_TAX",)
    if not bool(settings.get("billing_enabled", False)):
        return ConfigCheck(
            key="billing_tax",
            feature="Impuestos de las suscripciones (Stripe Tax)",
            severity="ok",
            detail="No aplica: la facturación está desactivada.",
            variables=variables,
        )
    if not _billing.STRIPE_TAX_ENABLED:
        return ConfigCheck(
            key="billing_tax",
            feature="Impuestos de las suscripciones (Stripe Tax)",
            severity="warning",
            detail=(
                "Desactivado: se cobra el importe neto y no se repercute IVA. "
                "Vendiendo servicios digitales dentro de la UE eso deja las "
                "facturas incompletas y el importe cobrado por debajo del debido."
            ),
            variables=variables,
        )
    return ConfigCheck(
        key="billing_tax",
        feature="Impuestos de las suscripciones (Stripe Tax)",
        severity="ok",
        detail=(
            "Activo. Exige en el panel de Stripe: Tax habilitado, las "
            "obligaciones fiscales («registrations») declaradas, un tax_code en "
            "el producto de asientos y tax_behavior en el precio del add-on "
            "self-hosted. Si falta algo, el alta falla al crear la suscripción."
        ),
        variables=variables,
    )


def _check_selfhosted_prices(settings: dict) -> ConfigCheck:
    variables = ("STRIPE_PRICE_SELFHOSTED_MONTHLY", "STRIPE_PRICE_SELFHOSTED_ANNUAL")
    if not bool(settings.get("billing_enabled", False)):
        return ConfigCheck(
            key="billing_selfhosted",
            feature="Add-on self-hosted",
            severity="ok",
            detail="No aplica: la facturación está desactivada.",
            variables=variables,
        )
    missing = tuple(name for name in variables if not getattr(_billing, name, ""))
    if missing:
        return ConfigCheck(
            key="billing_selfhosted",
            feature="Add-on self-hosted",
            severity="warning",
            detail=(
                "Sin identificadores de precio, el add-on self-hosted no se "
                "puede contratar aunque aparezca en la página de planes."
            ),
            variables=missing,
        )
    return ConfigCheck(
        key="billing_selfhosted",
        feature="Add-on self-hosted",
        severity="ok",
        detail="Precios mensual y anual configurados.",
        variables=variables,
    )

def _check_registration_mode(settings: dict) -> ConfigCheck:
    # El modo en vigor sale de settings.json si está, y si no de la variable:
    # el mismo orden que resuelve `registration_mode()` para el alta. Mirar
    # solo la variable haría que este panel dijera «open» en una instalación
    # cerrada desde Admin.
    mode = str(settings.get("registration", _session.REGISTRATION_MODE)).lower()
    if mode not in _session.REGISTRATION_MODES:
        return ConfigCheck(
            key="registration_mode",
            feature="Modo de registro",
            severity="error",
            detail=(
                f"{mode!r} no es un modo válido ({', '.join(sorted(_session.REGISTRATION_MODES))}). "
                "El registro se trata como cerrado y nadie puede crear una "
                "cuenta hasta que el valor sea uno de los tres. Antes un typo "
                "hacía lo contrario —dejaba la instalación abierta a "
                "cualquiera—, así que revisa si esto era lo que querías."
            ),
            variables=("GAIA_REGISTRATION",),
        )
    return ConfigCheck(
        key="registration_mode",
        feature="Modo de registro",
        severity="ok",
        detail=f"Modo {mode!r}.",
        variables=("GAIA_REGISTRATION",),
    )

_CSRF_VARS = ("GAIA_CSRF_ORIGIN_CHECK", "GAIA_CSRF_TOKEN_CHECK")

def _check_csrf() -> ConfigCheck:
    modos = {
        "GAIA_CSRF_ORIGIN_CHECK": _session.CSRF_ORIGIN_CHECK,
        "GAIA_CSRF_TOKEN_CHECK": _session.CSRF_TOKEN_CHECK,
    }
    invalidos = tuple(
        var for var, modo in modos.items() if modo not in _session.CSRF_MODES
    )
    if invalidos:
        return ConfigCheck(
            key="csrf",
            feature="Protección anti-CSRF",
            severity="error",
            detail=(
                f"Modo desconocido; se esperaba {', '.join(sorted(_session.CSRF_MODES))}. "
                "Un valor no reconocido no activa nada: la sesión vuelve a "
                "depender solo de SameSite=Lax sin decirlo."
            ),
            variables=invalidos,
        )
    apagados = tuple(var for var, modo in modos.items() if modo == "off")
    if apagados:
        return ConfigCheck(
            key="csrf",
            feature="Protección anti-CSRF",
            severity="warning",
            detail=(
                "Comprobación desactivada. La única defensa contra CSRF vuelve "
                "a ser SameSite=Lax, que no cubre un subdominio comprometido."
            ),
            variables=apagados,
        )
    en_log = tuple(var for var, modo in modos.items() if modo == "log")
    if en_log:
        return ConfigCheck(
            key="csrf",
            feature="Protección anti-CSRF",
            severity="warning",
            detail=(
                "En modo log: los rechazos se registran pero no bloquean. Es la "
                "válvula de escape para diagnosticar, no un estado en el que "
                "quedarse: mientras dure, la protección está anotando, no "
                "protegiendo."
            ),
            variables=en_log,
        )
    return ConfigCheck(
        key="csrf",
        feature="Protección anti-CSRF",
        severity="ok",
        detail="Origen y token verificados en cada petición con efectos.",
        variables=_CSRF_VARS,
    )

def _check_github_oauth() -> ConfigCheck:
    from app.config.providers import GITHUB_OAUTH_CLIENT_ID

    if not GITHUB_OAUTH_CLIENT_ID:
        return ConfigCheck(
            key="github_oauth",
            feature="Conectar con GitHub (Device Flow)",
            severity="warning",
            detail=(
                "Sin client_id el botón queda deshabilitado; conectar GitHub "
                "exige pegar un token personal a mano."
            ),
            variables=("GITHUB_OAUTH_CLIENT_ID",),
        )
    return ConfigCheck(
        key="github_oauth",
        feature="Conectar con GitHub (Device Flow)",
        severity="ok",
        detail="OAuth App configurada.",
        variables=("GITHUB_OAUTH_CLIENT_ID",),
    )

def _check_trusted_proxies() -> ConfigCheck:
    behind_proxy = os.getenv("GAIA_FRONTEND_URL", "").startswith("https://")
    if behind_proxy and not _session.TRUSTED_PROXIES:
        return ConfigCheck(
            key="trusted_proxies",
            feature="IP real del cliente tras el proxy",
            severity="warning",
            detail=(
                "El despliegue sirve por https —hay un proxy delante— y no hay "
                "proxies de confianza: se ignora X-Forwarded-For, así que el "
                "rate limiting cuenta a todo el mundo contra la IP del proxy."
            ),
            variables=("GAIA_TRUSTED_PROXIES",),
        )
    return ConfigCheck(
        key="trusted_proxies",
        feature="IP real del cliente tras el proxy",
        severity="ok",
        detail=(
            "Proxies de confianza declarados."
            if _session.TRUSTED_PROXIES
            else "Sin proxy delante: se usa la IP de la conexión."
        ),
        variables=("GAIA_TRUSTED_PROXIES",),
    )

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")

def _check_secure_cookies() -> ConfigCheck:
    frontend = os.getenv("GAIA_FRONTEND_URL", "")
    is_local = any(host in frontend for host in _LOCAL_HOSTS)
    if frontend and not _session.SECURE_COOKIES and not is_local:
        return ConfigCheck(
            key="secure_cookies",
            feature="Cookies de sesión seguras",
            severity="warning",
            detail=(
                "El frontend no es https ni local, y las cookies salen sin el "
                "flag Secure: la sesión viaja en claro por la red."
            ),
            variables=("GAIA_FRONTEND_URL", "GAIA_SECURE_COOKIES"),
        )
    return ConfigCheck(
        key="secure_cookies",
        feature="Cookies de sesión seguras",
        severity="ok",
        detail=(
            "Cookies marcadas como Secure."
            if _session.SECURE_COOKIES
            else "Despliegue local: no se exige Secure."
        ),
        variables=("GAIA_FRONTEND_URL", "GAIA_SECURE_COOKIES"),
    )

def _check_maintenance_intervals() -> ConfigCheck:
    """Cadencia de los bucles de fondo, cuando el entorno trae algo inservible.

    Un intervalo a 0 no es «purgar constantemente»: es un `while True` sin
    espera quemando una CPU por worker. `config/maintenance.py` lo sube al
    suelo y lo anota aquí, porque una corrección silenciosa haría creer que el
    valor pedido está en vigor.
    """
    if _maintenance.ANOMALIAS:
        return ConfigCheck(
            key="maintenance_intervals",
            feature="Cadencia de los bucles de mantenimiento",
            severity="warning",
            detail=(
                "Un intervalo no era un número o quedaba por debajo del mínimo: "
                "se aplica el mínimo, no el valor pedido."
            ),
            variables=tuple(dict.fromkeys(_maintenance.ANOMALIAS)),
        )
    return ConfigCheck(
        key="maintenance_intervals",
        feature="Cadencia de los bucles de mantenimiento",
        severity="ok",
        detail="Purgas de RGPD, logs, rate limit y workflows con cadencia válida.",
    )

def _check_rate_limit_ip_ceiling() -> ConfigCheck:
    """El techo por IP de los limiters con clave por usuario.

    A 0 la cuota queda solo por principal, y quien registra cuentas desechables
    se lleva un cupo entero con cada una. Es una decisión legítima —una
    instalación interna detrás de un NAT puede quererlo— pero silenciosa: la
    variable no apaga nada visible y nadie lo notaría hasta el abuso.
    """
    if _session.RATE_IP_FACTOR <= 0:
        return ConfigCheck(
            key="rate_limit_ip_ceiling",
            feature="Techo por IP del rate limiting",
            severity="warning",
            detail=(
                "Los limiters con cuota por usuario no tienen techo por IP: "
                "cada cuenta nueva suma un cupo completo."
            ),
            variables=("GAIA_RATE_IP_FACTOR",),
        )
    return ConfigCheck(
        key="rate_limit_ip_ceiling",
        feature="Techo por IP del rate limiting",
        severity="ok",
        detail="Cuota por usuario con un techo por IP por encima.",
        variables=("GAIA_RATE_IP_FACTOR",),
    )

def _check_guest_demo() -> ConfigCheck:
    """El tope de invitados simultáneos, que es también el interruptor del demo.

    A 0 el alta responde 503 siempre: la demo queda apagada. Es una decisión
    legítima en una instalación privada, pero no lo dice nada más — la ruta
    sigue publicada y el cliente sigue ofreciendo el botón «entrar como
    invitado», así que sin este aviso el síntoma es un 503 sin explicación.

    Aquí había otra cosa: con varios workers el tope real era el declarado por
    worker, porque las sesiones vivían en memoria de proceso. Ya no — el
    invitado es una fila en la BD y el número es el del clúster.
    """
    import app.storage.guest as _guest

    if _guest.MAX_SESSIONS <= 0:
        return ConfigCheck(
            key="guest_demo",
            feature="Sesiones de invitado",
            severity="warning",
            detail=(
                "El demo de invitado está desactivado: el alta responde 503 "
                "a cualquiera que lo intente."
            ),
            variables=("GAIA_MAX_GUEST_SESSIONS",),
        )
    return ConfigCheck(
        key="guest_demo",
        feature="Sesiones de invitado",
        severity="ok",
        detail="Demo activo, con tope de invitados simultáneos en el clúster.",
        variables=("GAIA_MAX_GUEST_SESSIONS",),
    )


def _check_body_limit(settings: dict) -> ConfigCheck:
    """El techo de tamaño de cuerpo de petición.

    A 0 no hay ninguno: cualquier POST puede llegar del tamaño que sea, y los
    handlers de subida hacen `await file.read()` entero en memoria. Es el valor
    por defecto y una decisión legítima —una instalación interna que sube packs
    grandes lo quiere así—, pero no puede quedar sin decir: es lo único que
    limita el tamaño desde que nginx dejó de imponer el suyo.
    """
    raw = settings.get("max_request_bytes", _session.BODY_MAX_BYTES)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return ConfigCheck(
            key="body_limit",
            feature="Tamaño máximo de petición",
            severity="error",
            detail=(
                "max_request_bytes no es un número; se aplica el valor del "
                "entorno en su lugar."
            ),
            variables=("GAIA_BODY_MAX_BYTES",),
        )
    if limit <= 0:
        return ConfigCheck(
            key="body_limit",
            feature="Tamaño máximo de petición",
            severity="warning",
            detail=(
                "Sin límite de tamaño de petición: una subida puede ocupar "
                "tanta memoria como quiera quien la envía. Se configura en el "
                "panel de administración."
            ),
            variables=("GAIA_BODY_MAX_BYTES",),
        )
    return ConfigCheck(
        key="body_limit",
        feature="Tamaño máximo de petición",
        severity="ok",
        detail="Las peticiones tienen un techo de tamaño configurado.",
        variables=("GAIA_BODY_MAX_BYTES",),
    )

def _check_data_dir() -> ConfigCheck:
    existing = _data.DATA_DIR
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not os.access(existing, os.W_OK):
        return ConfigCheck(
            key="data_dir",
            feature="Directorio de datos",
            severity="error",
            detail=(
                "El directorio de datos no es escribible: la base de datos, los "
                "agentes y el conocimiento no se pueden guardar."
            ),
            variables=("GAIA_DATA_DIR",),
        )
    return ConfigCheck(
        key="data_dir",
        feature="Directorio de datos",
        severity="ok",
        detail="Directorio de datos escribible.",
        variables=("GAIA_DATA_DIR",),
    )
