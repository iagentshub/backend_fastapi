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

# Los módulos se importan enteros, no sus símbolos: `DATA_DIR` y compañía se
# resuelven al importar, y los tests (y el propio panel de admin, que puede
# reescribir settings.json en caliente) necesitan ver el valor de ahora.
# Reexportados a propósito: los tests y `patch_data_dir` alcanzan
# `startup_checks._data` / `._billing` para reescribir el valor de ahora. Como
# es el módulo y no una copia del valor, el parche llega también a `checks.py`.
import app.config.billing as _billing  # noqa: F401
import app.config.data as _data  # noqa: F401
import app.config.maintenance as _maintenance  # noqa: F401
import app.config.session as _session  # noqa: F401
from app.config.startup_checks._model import (
    _STRICT_ENV,
    ConfigCheck,
    ConfigError,
    Severity,
    _platform_settings,
    strict_mode,
)
from app.config.startup_checks.checks import (
    _STRIPE_REQUIRED,  # noqa: F401 — lo itera tests/config/test_startup_checks.py
    _check_billing,
    _check_billing_tax,
    _check_body_limit,
    _check_cors,
    _check_csrf,
    _check_data_dir,
    _check_email_verify,
    _check_github_oauth,
    _check_guest_demo,
    _check_jwt,
    _check_maintenance_intervals,
    _check_push,
    _check_rate_limit_ip_ceiling,
    _check_registration_mode,
    _check_secure_cookies,
    _check_sla_support_price,
    _check_smtp,
    _check_trusted_proxies,
)
from app.config.startup_checks.legal import _check_legal_contract

__all__ = [
    "ConfigCheck",
    "ConfigError",
    "Severity",
    "run_checks",
    "report",
    "log_startup_report",
    "assert_config_ok",
    "strict_mode",
]


def run_checks() -> list[ConfigCheck]:
    """Evalúa la configuración actual. Sin efectos: no escribe ni registra."""
    settings = _platform_settings()
    return [
        _check_jwt(settings),
        _check_data_dir(),
        _check_cors(),
        _check_smtp(),
        _check_push(),
        _check_email_verify(settings),
        _check_billing(settings),
        _check_billing_tax(settings),
        _check_sla_support_price(settings),
        _check_registration_mode(settings),
        _check_legal_contract(settings),
        _check_csrf(),
        _check_github_oauth(),
        _check_trusted_proxies(),
        _check_secure_cookies(),
        _check_rate_limit_ip_ceiling(),
        _check_guest_demo(),
        _check_body_limit(settings),
        _check_maintenance_intervals(),
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

    errores_no_bloqueantes = sum(
        1 for c in degraded if c.severity == "error" and c.key != "legal_contract"
    )
    if errores_no_bloqueantes and not strict_mode():
        flog.warning(
            f"[config] {errores_no_bloqueantes} problema(s) de configuración no impiden el "
            f"arranque. Define {_STRICT_ENV}=true en producción para que sí lo hagan."
        )
    return checks


def assert_config_ok(checks: list[ConfigCheck] | None = None) -> None:
    """Aborta por errores estrictos o por un contrato legal exigido inválido."""
    checks = run_checks() if checks is None else checks
    errores = [
        c
        for c in checks
        if c.severity == "error" and (strict_mode() or c.key == "legal_contract")
    ]
    if not errores:
        return
    detalle = "\n".join(
        f"  - {c.feature}: {c.detail} ({', '.join(c.variables) or '—'})"
        for c in errores
    )
    raise ConfigError(
        "La configuración tiene errores que impiden un arranque seguro: "
        f"{len(errores)} problema(s):\n{detalle}"
    )
