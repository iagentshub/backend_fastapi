"""Lectura y escritura de la configuración de plataforma (`settings.json`).

No tenía nada de ruta —es acceso a un fichero— y ya la importaban desde fuera
`admin/updates.py`, `centinel/_state.py` y el middleware de licencias, cada uno
alcanzando dentro de la capa de rutas para llegar aquí.

Los imports de `SETTINGS_FILE` siguen siendo diferidos a propósito: el binding
se congela al importar el módulo y los tests reescriben `DATA_DIR` después.
Ver la sección «La trampa» de CLAUDE.md.
"""

from __future__ import annotations

from app.config.session import REGISTRATION_MODES
from app.errors import APIError
from app.utils import flog

# ── Configuración de plataforma (settings.json) ───────────────────────────────

_PLATFORM_DEFAULTS: dict = {
    "billing_enabled": False,
    "registration": "open",  # open | closed | invite
    "max_users": 0,  # 0 = sin límite
    "max_concurrent_sessions": 0,  # 0 = sin límite
    # Tamaño máximo de cuerpo de petición, en bytes; 0 = sin límite, y ese es
    # el default. Lo aplica BodySizeLimitMiddleware a TODA petición, no solo a
    # las subidas, y es el único techo que queda: nginx ya no impone el suyo
    # (client_max_body_size 0) para que el rechazo sea siempre el JSON del
    # backend y no su página HTML de 413.
    "max_request_bytes": 0,
    "guest_enabled": True,
    "email_verify": False,
    "users_can_configure_theme": True,
    "default_theme": "dark-red",
    "log_retention_days": 30,
    "audit_log_retention_days": 365,
    "stress_max_concurrency": 0,  # máx peticiones en vuelo simultáneo en Centinel (0=sin límite)
    # Si está activo, "/" muestra una landing de presentación del proyecto en
    # vez de redirigir directo a /login/. Pensado para el despliegue SaaS
    # público; en instalaciones self-hosted lo natural es dejarlo desactivado.
    "landing_enabled": False,
    # Controla solo la VISIBILIDAD de los botones de login social en /login/
    # — hoy son placeholders deshabilitados ("Próximamente"), no hay
    # integración OAuth real todavía. Default True: preserva el comportamiento
    # visual actual (los 3 botones ya se muestran) en instalaciones existentes.
    "oauth_google_enabled": True,
    "oauth_apple_enabled": True,
    "oauth_microsoft_enabled": True,
    # A diferencia de los tres anteriores, GitHub sí tiene una integración
    # real (Device Flow, ver app/auth/github_device_flow.py) — este toggle solo
    # decide si el admin quiere OFRECER el botón; la capacidad real (¿hay
    # GITHUB_OAUTH_CLIENT_ID configurado?) se sigue comprobando aparte en
    # GET /platform/public, así que apagar esto nunca puede "fingir" que el
    # login funciona si no hay credenciales, y encenderlo nunca lo activa
    # si no las hay. Los endpoints /api/auth/github/* no leen este valor en
    # ningún momento — siguen respondiendo igual, esto es puramente la
    # visibilidad del botón en /login/.
    "oauth_github_enabled": True,
    # Solo lectura desde aquí — refleja si Watchtower está corriendo o no.
    # Se escribe EXCLUSIVAMENTE desde PUT /api/admin/auto-update (auth.py),
    # nunca desde PUT /api/settings/platform (por eso no está en
    # PlatformConfigUpdate más abajo): así el valor persistido nunca puede
    # desincronizarse de si el contenedor "watchtower" realmente arrancó o
    # paró — solo se guarda tras confirmar la operación contra Docker.
    "auto_update_enabled": True,
    # Banners de notificación mostrados como card en el Dashboard mientras
    # dure su rango de fechas — ver NotificationBannerPayload más abajo.
    "notification_banners": [],
    # Splash de arranque: cada ciclo es una ida y vuelta completa A→B→A del
    # logotipo. splash_end_on_logo añade un tramo final A→B para que el
    # splash se cierre mostrando la marca (B), no la forma de partida (A).
    "splash_cycles": 1,
    "splash_end_on_logo": True,
}

_VALID_REGISTRATION = REGISTRATION_MODES


def _read_platform_cfg() -> dict:
    import json as _json

    from app.config.data import SETTINGS_FILE

    try:
        raw = _json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Instalación nueva: aún no se ha guardado nada. Silencio correcto.
        raw = {}
    except (OSError, ValueError) as exc:
        # El fichero existe pero no se puede leer o no es JSON válido. Caer a
        # los defaults es lo correcto —el servidor tiene que arrancar—, pero
        # sin registro nadie relaciona "la plataforma perdió su configuración"
        # con un settings.json corrupto.
        flog.error(f"[settings] {SETTINGS_FILE} ilegible, se usan defaults: {exc}")
        raw = {}
    cfg = dict(_PLATFORM_DEFAULTS)
    for k in _PLATFORM_DEFAULTS:
        if k in raw:
            cfg[k] = raw[k]
    if "max_request_bytes" not in raw:
        # Su default no es el literal de arriba: sale de GAIA_BODY_MAX_BYTES por
        # el mismo lector que usa el middleware. Sin esto el panel enseñaría
        # «sin límite» en una instalación que sí lo tiene puesto por entorno.
        from app.middleware.body_limit import configured_max_bytes

        cfg["max_request_bytes"] = configured_max_bytes()
    return cfg


def _write_platform_cfg(cfg: dict) -> None:
    import json as _json

    from app.config.data import SETTINGS_FILE

    try:
        existing = _json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Primera escritura: no hay nada que conservar.
        existing = {}
    except (OSError, ValueError) as exc:
        # Aquí NO se cae a {}: este camino continúa escribiendo el fichero, así
        # que tratar un settings.json corrupto como vacío lo sobrescribiría
        # entero y perdería en silencio todas las claves que no vengan en `cfg`.
        # Mejor un 500 en el guardado, que es reparable, que una pérdida muda.
        raise APIError(
            500,
            "settings_file_unreadable",
            "La configuración de plataforma existe pero no se puede leer; "
            "guardar ahora la sobrescribiría. Revisa el fichero en el servidor.",
        ) from exc
    existing.update(cfg)
    SETTINGS_FILE.write_text(
        _json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # billing_enabled vive en este fichero y LicenseGateMiddleware lo cachea.
    from app.middleware.licenses import invalidate_billing_cache

    invalidate_billing_cache()
    # max_request_bytes también: mismo motivo, otro caché.
    from app.middleware.body_limit import invalidate_body_limit_cache

    invalidate_body_limit_cache()
