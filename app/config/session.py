"""Configuración de sesión: tokens, expiración, registro y seguridad."""

from __future__ import annotations

import os

JWT_SECRET_ENV = "GAIA_AGENTS_SECRET"
JWT_ALGORITHM = "HS256"

# Procedencia del token. No aportan nada mientras el secreto se use para una
# sola cosa, pero son la defensa estándar contra reutilizar un token emitido
# por otro sistema que comparta secreto (p. ej. si en el futuro se firma algo
# más con la misma clave). Se emiten desde la migración a PyJWT; la validación
# es tolerante con los tokens anteriores, que no los llevan.
JWT_ISSUER = "iagentshub"
JWT_AUDIENCE = "iagentshub-api"

# ── Access token y refresh ────────────────────────────────────────────────────
# El access token dura minutos y el refresh horas. Ver docs/adr/008-sesiones-
# revocables.md: antes había un solo token de 12 h que no se podía revocar, así
# que la ventana de uno robado era esas 12 h completas y «cerrar sesión» solo
# borraba cookies.
#
# GAIA_JWT_EXPIRE_HOURS conserva su nombre y su default, pero ahora mide la vida
# de la SESIÓN (el refresh), no la del access: una instalación que ya lo tuviera
# puesto sigue teniendo sesiones de la duración que pidió, y gana el access
# corto sin tocar nada. La rotación mueve esa caducidad hacia delante en cada
# refresh, así que son horas de inactividad, no desde el login.
JWT_EXPIRE_HOURS = int(os.getenv("GAIA_JWT_EXPIRE_HOURS", "12"))
REFRESH_EXPIRE_HOURS = JWT_EXPIRE_HOURS
ACCESS_EXPIRE_MINUTES = max(1, int(os.getenv("GAIA_ACCESS_EXPIRE_MINUTES", "30")))
ACCESS_MAX_AGE_SECONDS = ACCESS_EXPIRE_MINUTES * 60

# La cookie del access vive lo que la sesión, no lo que el access: si el
# navegador la borrase al expirar el JWT, la request llegaría sin credencial
# ninguna y el 401 sería indistinguible de «nunca entró». Con la cookie presente
# el backend responde `token_expired` y el cliente sabe que le toca refrescar.
# Quien impone la caducidad es el `exp` del JWT, que el navegador no puede tocar.
JWT_MAX_AGE_SECONDS = JWT_EXPIRE_HOURS * 60 * 60

JWT_UNSAFE_SECRETS: frozenset[str] = frozenset(
    {
        "",
        "REEMPLAZAR_O_USAR_GAIA_AGENTS_SECRET",
        "cambia_esto_en_produccion",
    }
)

# Coste de bcrypt. 12 rondas ≈ 235 ms por hash: es el valor correcto en
# producción —235 ms de CPU por login, y `hash_password_async` ya lo saca del
# event loop— y por eso sigue siendo el default. Los tests lo bajan porque la
# suite tiene 141 puntos de registro: tests/auth son 71 tests que tardan
# 19,5 s, de los cuales 17,7 s se van esperando a bcrypt sin probar nada.
#
# El suelo de 4 y el techo de 16 son de bcrypt; el suelo importa además porque
# una variable de entorno que baje las rondas en producción es un hash débil
# con permiso. Bajar el parámetro no invalida ningún hash ya guardado: cada
# hash lleva su coste dentro, así que solo afecta a los nuevos.
BCRYPT_ROUNDS = max(4, min(int(os.getenv("GAIA_BCRYPT_ROUNDS", "12")), 16))

LOGIN_WINDOW = int(os.getenv("GAIA_LOGIN_WINDOW", "300"))  # segundos
LOGIN_MAX_FAILS = int(os.getenv("GAIA_LOGIN_MAX_FAILS", "5"))  # intentos fallidos

REGISTER_WINDOW = int(os.getenv("GAIA_REGISTER_WINDOW", "3600"))  # segundos
REGISTER_MAX = int(os.getenv("GAIA_REGISTER_MAX", "5"))  # registros por ventana

# ── Registro ──────────────────────────────────────────────────────────────────
# open   → cualquiera puede registrarse (default, backward-compatible)
# closed → registro desactivado
# invite → solo el admin puede crear usuarios vía /api/admin/users
#
# La lista vive aquí y solo aquí: PUT /api/settings/platform la usa para validar.
# Cuando estaba duplicada en settings.py, el panel rechazaba con 422 el modo
# "invite" que auth.py sí implementa y que .env y docker-compose.yml ya usaban.
REGISTRATION_MODES: frozenset[str] = frozenset({"open", "closed", "invite"})

REGISTRATION_MODE: str = os.getenv("GAIA_REGISTRATION", "open").lower()

# ── Verificación de email ──────────────────────────────────────────────────────
# false (default) → usuarios verificados automáticamente al registrarse
# true            → se envía un correo con enlace de verificación; sin verificar no pueden entrar
EMAIL_VERIFY_ENABLED: bool = os.getenv("GAIA_EMAIL_VERIFY", "false").lower() == "true"

# ── Cookies seguras ───────────────────────────────────────────────────────────
# Se activan automáticamente si GAIA_FRONTEND_URL empieza por https://,
# o explícitamente con GAIA_SECURE_COOKIES=true.
_frontend_url = os.getenv("GAIA_FRONTEND_URL", "")
SECURE_COOKIES: bool = (
    _frontend_url.startswith("https://")
    or os.getenv("GAIA_SECURE_COOKIES", "").lower() == "true"
)

# ── Anti-CSRF ─────────────────────────────────────────────────────────────────
# Dos capas independientes sobre la cookie de sesión. Ver docs/adr/006-csrf-en-
# dos-capas.md: `SameSite=Lax` era la única defensa, vive en el navegador del
# visitante y no cubre un subdominio comprometido, que para el navegador es
# «el mismo sitio».
#
# Cada capa tiene su interruptor porque protegen cosas distintas y porque un
# despliegue puede necesitar bajar una sin tocar la otra:
#
#   enforce → rechaza con 403
#   log     → deja pasar y registra el rechazo que habría hecho
#   off     → no mira nada
#
# Las dos salen en `enforce`. La del token exige que el cliente mande la
# cabecera, así que el backend NO puede llegar a producción antes que React y
# Flutter: con un bundle cacheado que aún no la manda, toda mutación es un 403.
# `log` es la salida si eso pasa —registra sin bloquear— y no hace falta
# redesplegar para usarla, basta la variable de entorno.
CSRF_MODES: frozenset[str] = frozenset({"enforce", "log", "off"})
CSRF_ORIGIN_CHECK: str = os.getenv("GAIA_CSRF_ORIGIN_CHECK", "enforce").lower()
CSRF_TOKEN_CHECK: str = os.getenv("GAIA_CSRF_TOKEN_CHECK", "enforce").lower()

CSRF_COOKIE = "ga_csrf"
CSRF_HEADER = "x-csrf-token"

# La cookie del refresh se acota al prefijo de la ruta que la canjea: es la
# credencial de largo recorrido de la sesión y no tiene por qué viajar en las
# otras ~450 rutas. `clear_session_cookies` la borra con este mismo path — un
# `delete_cookie` con path distinto no borra nada y la sesión parecería cerrada
# sin estarlo.
REFRESH_COOKIE = "ga_refresh"
REFRESH_COOKIE_PATH = "/api/auth"

# TRACE no lo sirve Starlette, pero está en la lista por lo mismo que OPTIONS:
# la definición de «método seguro» es la de RFC 9110, no la de nuestras rutas.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# ── SMTP (para verificación de email y recuperación de contraseña) ────────────
# Funciona con cualquier servidor SMTP: Postfix propio, Exchange, Gmail, etc.
# Deja GAIA_SMTP_HOST vacío para deshabilitar (los tokens se loguean en consola).
SMTP_HOST: str = os.getenv("GAIA_SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("GAIA_SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("GAIA_SMTP_USER", "")
SMTP_PASS: str = os.getenv("GAIA_SMTP_PASS", "")
SMTP_FROM: str = os.getenv("GAIA_SMTP_FROM", "") or os.getenv("GAIA_SMTP_USER", "")
# Modo TLS: "starttls" (587, recomendado), "ssl" (465), "none" (25, solo redes internas)
SMTP_TLS: str = os.getenv("GAIA_SMTP_TLS", "starttls").lower()

PASSWORD_RESET_EXPIRE_HOURS: int = int(os.getenv("GAIA_RESET_EXPIRE_HOURS", "1"))

# ── Webmail ───────────────────────────────────────────────────────────────────
# URL del cliente de correo web que se muestra en el panel de admin.
# Ejemplos: Mailpit dev → http://localhost:8025 | Dondominio → https://webmail.dondominio.com
WEBMAIL_URL: str = os.getenv("GAIA_WEBMAIL_URL", "")

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_CHAT_CALLS = int(os.getenv("GAIA_RATE_CHAT_CALLS", "30"))  # peticiones
RATE_CHAT_WINDOW = int(os.getenv("GAIA_RATE_CHAT_WINDOW", "60"))  # por segundos
# Todas las puertas interactivas al LLM comparten la cuota histórica del chat.
# Mantener GAIA_RATE_CHAT_* conserva la configuración de instalaciones existentes.
RATE_WORKFLOW_START_CALLS = int(os.getenv("GAIA_RATE_WORKFLOW_START_CALLS", "5"))
RATE_WORKFLOW_START_WINDOW = int(os.getenv("GAIA_RATE_WORKFLOW_START_WINDOW", "60"))
RATE_WORKFLOW_NODE_CALLS = int(os.getenv("GAIA_RATE_WORKFLOW_NODE_CALLS", "600"))
RATE_WORKFLOW_NODE_WINDOW = int(os.getenv("GAIA_RATE_WORKFLOW_NODE_WINDOW", "3600"))
RATE_OFFICIAL_LLM_CALLS = int(os.getenv("GAIA_RATE_OFFICIAL_LLM_CALLS", "5"))
RATE_OFFICIAL_LLM_WINDOW = int(os.getenv("GAIA_RATE_OFFICIAL_LLM_WINDOW", "3600"))
RATE_TEST_CALLS = int(os.getenv("GAIA_RATE_TEST_CALLS", "10"))
RATE_TEST_WINDOW = int(os.getenv("GAIA_RATE_TEST_WINDOW", "60"))
RATE_TESTALL_CALLS = int(os.getenv("GAIA_RATE_TESTALL_CALLS", "30"))
RATE_TESTALL_WINDOW = int(os.getenv("GAIA_RATE_TESTALL_WINDOW", "60"))
RATE_GUEST_CALLS = int(os.getenv("GAIA_RATE_GUEST_CALLS", "5"))
RATE_GUEST_WINDOW = int(os.getenv("GAIA_RATE_GUEST_WINDOW", "60"))
# ── Invitados ─────────────────────────────────────────────────────────────────
# Margen antes de considerar abandonado a un invitado sin sesión viva. Entre el
# alta del usuario y la de su sesión hay una ventana en la que todavía no tiene
# ninguna: sin margen, la purga se lo llevaría en su primera petición.
# Vive aquí y no en storage/guest.py porque quien lo lee es la purga del RGPD,
# y guest.py ya importa esa purga — al revés se cerraría un ciclo de imports.
GUEST_GRACE_SECONDS = int(os.getenv("GAIA_GUEST_GRACE_SECONDS", "3600"))
# Recuperación de contraseña: límite estricto para prevenir spam SMTP masivo
RATE_FORGOT_CALLS = int(os.getenv("GAIA_RATE_FORGOT_CALLS", "5"))  # intentos
RATE_FORGOT_WINDOW = int(os.getenv("GAIA_RATE_FORGOT_WINDOW", "3600"))  # por hora
# Reset de contraseña: token de 256 bits es imprácticable de fuerza bruta,
# pero limitamos igualmente para prevenir DoS sobre la BD
RATE_RESET_CALLS = int(os.getenv("GAIA_RATE_RESET_CALLS", "10"))
RATE_RESET_WINDOW = int(os.getenv("GAIA_RATE_RESET_WINDOW", "300"))
# Canje del refresh: un cliente legítimo renueva una vez cada
# ACCESS_EXPIRE_MINUTES, pero varias pestañas pueden coincidir tras despertar
# el equipo, así que el cupo es holgado. RateLimiter divide entre GAIA_WORKERS.
RATE_REFRESH_CALLS = int(os.getenv("GAIA_RATE_REFRESH_CALLS", "60"))
RATE_REFRESH_WINDOW = int(os.getenv("GAIA_RATE_REFRESH_WINDOW", "300"))
RATE_MAX_IPS = int(
    os.getenv("GAIA_RATE_MAX_IPS", "10000")
)  # IPs simultáneas en memoria
# Ventana secundaria por IP de los limiters con clave por usuario: el mismo
# cupo multiplicado por este factor. Existe porque la clave por principal, sola,
# regala una cuota entera por cada cuenta desechable que alguien registre; la
# IP no puede ser el límite principal (un NAT corporativo comparte una) pero sí
# el techo por encima. A 0 se desactiva y solo cuenta el principal —lo dice la
# auditoría de arranque, no se apaga en silencio.
RATE_IP_FACTOR = int(os.getenv("GAIA_RATE_IP_FACTOR", "5"))
# Tamaño máximo de cuerpo de petición, en bytes. **0 = sin límite**, que es el
# valor por defecto: lo decide el administrador desde el panel
# (`max_request_bytes` en settings.json) y esta variable es solo el valor de
# partida cuando no ha tocado nada. Antes era 2 MB fijos —un número dimensionado
# para cuerpos JSON que las subidas de ficheros heredaron sin que nadie lo
# revisara—, y nginx cortaba en su millón por defecto antes de llegar aquí.
BODY_MAX_BYTES = int(os.getenv("GAIA_BODY_MAX_BYTES", "0"))

# ── Proxies confiables para X-Forwarded-For ────────────────────────────────────
# Solo se lee el header X-Forwarded-For cuando la conexión TCP viene de una de
# estas IPs. Si está vacío, se ignora el header y se usa siempre la IP real.
# Ejemplo: "127.0.0.1,172.17.0.1" para nginx local + Docker host.
_trusted_raw = os.getenv("GAIA_TRUSTED_PROXIES", "127.0.0.1")
TRUSTED_PROXIES: frozenset[str] = frozenset(
    ip.strip() for ip in _trusted_raw.split(",") if ip.strip()
)
