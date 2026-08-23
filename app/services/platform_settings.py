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


# Texto de settings.json cacheado, con la huella del fichero que lo validó:
# (ruta, st_mtime_ns, st_size). Antes cada consulta leía y parseaba el fichero
# entero, síncrono y dentro del event loop, y son veinte los sitios que lo
# consultan — el login lo pide en cada carga y ahora también cada diez
# segundos mientras esa pantalla está abierta.
#
# Se cachea el TEXTO, no el diccionario: `json.loads` por llamada devuelve
# estructuras nuevas, y hay llamadores que mutan lo que reciben
# (`banners.py` hace `cfg.setdefault(...).append(...)`). Cachear el dict les
# dejaría escribir dentro de la caché.
_raw_cache: tuple[tuple[str, int, int], str] | None = None

# Red de seguridad por si la huella no cambiara (dos escrituras dentro de la
# misma marca de tiempo y con el mismo tamaño). No es el mecanismo principal.
_RAW_CACHE_TTL_S = 5.0
_raw_cache_at = 0.0


def invalidate_platform_cfg_cache() -> None:
    """A llamar tras escribir settings.json. Ver [_write_platform_cfg]."""
    global _raw_cache
    _raw_cache = None


def load_settings_raw() -> dict:
    """JSON crudo de settings.json, sin defaults ni validación.

    La validez del caché se comprueba con la huella del fichero
    (`st_mtime_ns` y tamaño), **no solo** con la invalidación explícita de la
    escritura. Esa diferencia es el motivo de este caché: uvicorn corre con
    `GAIA_WORKERS` procesos (4 por defecto), así que el guardado del admin lo
    atiende uno solo y la invalidación en memoria únicamente llega a ese. Los
    demás servían el valor viejo **hasta el siguiente reinicio** —incluidos
    `billing_enabled`, que es la puerta de cobro, y `max_request_bytes`—. Al
    mirar la huella, el resto se entera en su siguiente lectura.

    El caché anterior descartó el `mtime` a propósito, porque en segundos su
    resolución no distingue dos escrituras seguidas y un falso acierto en una
    puerta de cobro deja pasar a quien no ha pagado. Aquí se usa `st_mtime_ns`,
    y además sigue habiendo invalidación explícita y un TTL corto: el `mtime`
    es una comprobación de más, no la única.
    """
    global _raw_cache, _raw_cache_at

    import json as _json
    import time as _time

    from app.config.data import SETTINGS_FILE

    try:
        stat = SETTINGS_FILE.stat()
        huella = (str(SETTINGS_FILE), stat.st_mtime_ns, stat.st_size)
    except OSError:
        # Sin fichero no hay nada que cachear; el bloque de abajo decide qué
        # devolver y lo registra si procede.
        huella = None

    ahora = _time.monotonic()
    if (
        huella is not None
        and _raw_cache is not None
        and _raw_cache[0] == huella
        and ahora - _raw_cache_at < _RAW_CACHE_TTL_S
    ):
        try:
            return _json.loads(_raw_cache[1])
        except ValueError:  # pragma: no cover - lo cacheado ya se parseó una vez
            _raw_cache = None

    try:
        texto = SETTINGS_FILE.read_text(encoding="utf-8")
        raw = _json.loads(texto)
    except FileNotFoundError:
        # Instalación nueva: aún no se ha guardado nada. Silencio correcto.
        return {}
    except (OSError, ValueError) as exc:
        # El fichero existe pero no se puede leer o no es JSON válido. Caer a
        # los defaults es lo correcto —el servidor tiene que arrancar—, pero
        # sin registro nadie relaciona "la plataforma perdió su configuración"
        # con un settings.json corrupto.
        flog.error(f"[settings] {SETTINGS_FILE} ilegible, se usan defaults: {exc}")
        return {}

    if huella is not None and isinstance(raw, dict):
        _raw_cache = (huella, texto)
        _raw_cache_at = ahora
    return raw if isinstance(raw, dict) else {}


def _read_platform_cfg() -> dict:
    raw = load_settings_raw()
    cfg = dict(_PLATFORM_DEFAULTS)
    for k in _PLATFORM_DEFAULTS:
        if k in raw:
            cfg[k] = raw[k]
    # `app.config.session` se importa aquí dentro a propósito: los tests
    # reescriben estos valores y un binding de módulo los congelaría. Ver «La
    # trampa» en CLAUDE.md.
    import app.config.session as session_cfg

    if "max_request_bytes" not in raw:
        # Su default no es el literal de arriba: sale de GAIA_BODY_MAX_BYTES por
        # la misma configuración que usa el middleware. Se lee del módulo para
        # que los tests y la configuración de arranque vean el valor vigente,
        # sin crear un ciclo entre el servicio y el middleware.
        cfg["max_request_bytes"] = max(session_cfg.BODY_MAX_BYTES, 0)
    if "registration" not in raw:
        # Mismo caso: su default sale de GAIA_REGISTRATION, no del literal.
        #
        # Era un interruptor duplicado. El alta miraba SOLO la variable de
        # entorno y los clientes SOLO este fichero, que es el que edita el
        # panel de Admin: poner `registration: "closed"` escondía el formulario
        # y dejaba POST /api/auth/register devolviendo 200 — una instalación
        # «cerrada» seguía aceptando cuentas de cualquiera.
        cfg["registration"] = session_cfg.REGISTRATION_MODE
    if "email_verify" not in raw:
        # Y lo propio con la verificación: activarla desde Admin no mandaba
        # ningún correo, porque el alta solo miraba GAIA_EMAIL_VERIFY.
        cfg["email_verify"] = session_cfg.EMAIL_VERIFY_ENABLED
    return cfg


def registration_mode() -> str:
    """Modo de registro en vigor: `open`, `closed` o `invite`.

    El único sitio que lo resuelve. Lo que diga `settings.json` manda —es lo
    que edita el panel de Admin— y, si no lo dice, vale `GAIA_REGISTRATION`.
    Quien decide si se puede crear una cuenta y quien se lo cuenta a los
    clientes tienen que leer de aquí los dos, o el interruptor miente.

    Un modo que no existe se trata como `closed`. Antes un typo —`"cerrado"`
    en vez de `"closed"`— no casaba con ninguna de las dos comparaciones y
    dejaba el registro ABIERTO: la instalación que alguien creía cerrada
    aceptaba cuentas de cualquiera. El chequeo de arranque ya lo avisa; esto
    es lo que hace que el aviso no llegue tarde.
    """
    modo = str(_read_platform_cfg().get("registration") or "").lower()
    return modo if modo in REGISTRATION_MODES else "closed"


def email_verify_enabled() -> bool:
    """Si al crear una cuenta hay que verificar el correo antes de entrar.

    Mismo orden que [registration_mode]: `settings.json` manda y, si calla,
    `GAIA_EMAIL_VERIFY`.
    """
    return bool(_read_platform_cfg().get("email_verify", False))


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
    # El caché del texto es de este módulo y lo comparten los tres lectores;
    # `billing_enabled` y `max_request_bytes` ya no tienen el suyo propio.
    invalidate_platform_cfg_cache()
