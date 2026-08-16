"""Auditoría de la configuración en el arranque.

El resto de `config/` lee el entorno con `os.getenv(..., "")` y, cuando falta
una variable, no falla: apaga una función sin decirlo. Un typo en el nombre de
`STRIPE_WEBHOOK_SECRET` arranca perfectamente y simplemente no cobra; sin
`GAIA_SMTP_HOST` los correos de verificación no salen y el registro queda a
medias sin error visible.

Este módulo es el único sitio donde se decide qué significa que falte cada
variable. No lee configuración nueva: mira la que los demás módulos ya han
resuelto y la clasifica.

    warning → una función queda desactivada por falta de configuración. Puede
              ser deliberado (un self-hosted sin Stripe), pero queda dicho.
    error   → la configuración se contradice: alguien activó algo que no puede
              funcionar (verificación por email sin servidor SMTP). Aquí nunca
              hay una lectura benigna.

Por defecto solo se informa —abortar el arranque dejaría inarrancable una
instalación que hoy funciona degradada, que es justo lo que `_secret()` evita
en `app/auth/passwords.py`—. Con `GAIA_STRICT_CONFIG=true`, los `error` pasan a
impedir el arranque: es la casilla que un despliegue de producción marca una
vez y le avisa para siempre.

Ningún check devuelve valores, solo nombres de variable: el informe se expone
en el panel de admin.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

# Los módulos se importan enteros, no sus símbolos: `DATA_DIR` y compañía se
# resuelven al importar, y los tests (y el propio panel de admin, que puede
# reescribir settings.json en caliente) necesitan ver el valor de ahora.
import app.config.billing as _billing
import app.config.data as _data
import app.config.session as _session

Severity = Literal["ok", "warning", "error"]

_STRICT_ENV = "GAIA_STRICT_CONFIG"


@dataclass(frozen=True)
class ConfigCheck:
    """Resultado de una comprobación. `variables` son nombres, nunca valores."""

    key: str
    feature: str
    severity: Severity
    detail: str
    variables: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "feature": self.feature,
            "severity": self.severity,
            "detail": self.detail,
            "variables": list(self.variables),
        }


class ConfigError(RuntimeError):
    """Configuración incoherente con `GAIA_STRICT_CONFIG` activo."""


def strict_mode() -> bool:
    """Se lee en cada llamada para que los tests puedan cambiarla."""
    return os.getenv(_STRICT_ENV, "").lower() in ("1", "true", "yes")


def _platform_settings() -> dict:
    """`settings.json` — la fuente real de billing_enabled y email_verify.

    El panel de admin los escribe ahí, así que el entorno no basta para saber
    si una función está activa. Un fichero ausente o ilegible no es asunto de
    este módulo: se trata como «sin overrides» y el resto de checks siguen.
    """
    try:
        data = json.loads(_data.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ── Checks ────────────────────────────────────────────────────────────────────


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
                "La puerta de licencias está activa —el acceso a agentes, "
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


def _check_registration_mode() -> ConfigCheck:
    mode = _session.REGISTRATION_MODE
    if mode not in _session.REGISTRATION_MODES:
        return ConfigCheck(
            key="registration_mode",
            feature="Modo de registro",
            severity="error",
            detail=(
                f"{mode!r} no es un modo válido ({', '.join(sorted(_session.REGISTRATION_MODES))}). "
                "El registro se comporta como abierto: un typo en «closed» deja "
                "la instalación abierta a cualquiera."
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


# ── Informe ───────────────────────────────────────────────────────────────────


def run_checks() -> list[ConfigCheck]:
    """Evalúa la configuración actual. Sin efectos: no escribe ni registra."""
    settings = _platform_settings()
    return [
        _check_jwt(settings),
        _check_data_dir(),
        _check_cors(),
        _check_smtp(),
        _check_email_verify(settings),
        _check_billing(settings),
        _check_selfhosted_prices(settings),
        _check_registration_mode(),
        _check_csrf(),
        _check_github_oauth(),
        _check_trusted_proxies(),
        _check_secure_cookies(),
    ]


def report() -> dict:
    """Informe serializable — lo consume el panel de admin. Sin valores."""
    checks = run_checks()
    return {
        "strict": strict_mode(),
        "strict_var": _STRICT_ENV,
        "errors": sum(1 for c in checks if c.severity == "error"),
        "warnings": sum(1 for c in checks if c.severity == "warning"),
        "checks": [c.as_dict() for c in checks],
    }


def log_startup_report(checks: list[ConfigCheck] | None = None) -> list[ConfigCheck]:
    """Escribe en el log qué queda desactivado y por qué variable.

    Se llama desde `_lifespan`. Devuelve los checks para que el llamante no
    tenga que reevaluarlos antes de decidir si aborta.
    """
    from app.utils import flog

    checks = run_checks() if checks is None else checks
    degraded = [c for c in checks if c.severity != "ok"]
    if not degraded:
        flog.ok("[config] Configuración completa: ninguna función degradada")
        return checks

    for check in degraded:
        variables = ", ".join(check.variables) or "—"
        linea = f"[config] {check.feature}: {check.detail} ({variables})"
        if check.severity == "error":
            flog.error(linea)
        else:
            flog.warning(linea)

    errores = sum(1 for c in degraded if c.severity == "error")
    if errores and not strict_mode():
        flog.warning(
            f"[config] {errores} problema(s) de configuración no impiden el "
            f"arranque. Define {_STRICT_ENV}=true en producción para que sí lo hagan."
        )
    return checks


def assert_config_ok(checks: list[ConfigCheck] | None = None) -> None:
    """Aborta el arranque si hay errores y el modo estricto está activo."""
    if not strict_mode():
        return
    checks = run_checks() if checks is None else checks
    errores = [c for c in checks if c.severity == "error"]
    if not errores:
        return
    detalle = "\n".join(
        f"  - {c.feature}: {c.detail} ({', '.join(c.variables) or '—'})"
        for c in errores
    )
    raise ConfigError(
        f"{_STRICT_ENV} está activo y la configuración tiene "
        f"{len(errores)} problema(s):\n{detalle}"
    )
