"""Configuración de sesión: tokens, expiración, registro y seguridad."""
from __future__ import annotations

import os

JWT_SECRET_ENV   = "GAIA_AGENTS_SECRET"
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("GAIA_JWT_EXPIRE_HOURS", "12"))

JWT_UNSAFE_SECRETS: frozenset[str] = frozenset({
    "",
    "REEMPLAZAR_O_USAR_GAIA_AGENTS_SECRET",
    "cambia_esto_en_produccion",
})

LOGIN_WINDOW    = int(os.getenv("GAIA_LOGIN_WINDOW",    "300"))   # segundos
LOGIN_MAX_FAILS = int(os.getenv("GAIA_LOGIN_MAX_FAILS", "5"))     # intentos fallidos

REGISTER_WINDOW = int(os.getenv("GAIA_REGISTER_WINDOW", "3600"))  # segundos
REGISTER_MAX    = int(os.getenv("GAIA_REGISTER_MAX",    "5"))     # registros por ventana

# ── Registro ──────────────────────────────────────────────────────────────────
# open   → cualquiera puede registrarse (default, backward-compatible)
# closed → registro desactivado
# invite → solo el admin puede crear usuarios vía /api/admin/users
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

# ── SMTP (para verificación de email y recuperación de contraseña) ────────────
# Funciona con cualquier servidor SMTP: Postfix propio, Exchange, Gmail, etc.
# Deja GAIA_SMTP_HOST vacío para deshabilitar (los tokens se loguean en consola).
SMTP_HOST: str = os.getenv("GAIA_SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("GAIA_SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("GAIA_SMTP_USER", "")
SMTP_PASS: str = os.getenv("GAIA_SMTP_PASS", "")
SMTP_FROM: str = os.getenv("GAIA_SMTP_FROM", "") or os.getenv("GAIA_SMTP_USER", "")
# Modo TLS: "starttls" (587, recomendado), "ssl" (465), "none" (25, solo redes internas)
SMTP_TLS: str  = os.getenv("GAIA_SMTP_TLS", "starttls").lower()

PASSWORD_RESET_EXPIRE_HOURS: int = int(os.getenv("GAIA_RESET_EXPIRE_HOURS", "1"))

# ── Webmail ───────────────────────────────────────────────────────────────────
# URL del cliente de correo web que se muestra en el panel de admin.
# Ejemplos: Mailpit dev → http://localhost:8025 | Dondominio → https://webmail.dondominio.com
WEBMAIL_URL: str = os.getenv("GAIA_WEBMAIL_URL", "")

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_CHAT_CALLS  = int(os.getenv("GAIA_RATE_CHAT_CALLS",  "30"))   # peticiones
RATE_CHAT_WINDOW = int(os.getenv("GAIA_RATE_CHAT_WINDOW", "60"))   # por segundos
RATE_TEST_CALLS     = int(os.getenv("GAIA_RATE_TEST_CALLS",     "10"))
RATE_TEST_WINDOW    = int(os.getenv("GAIA_RATE_TEST_WINDOW",    "60"))
RATE_TESTALL_CALLS  = int(os.getenv("GAIA_RATE_TESTALL_CALLS",  "30"))
RATE_TESTALL_WINDOW = int(os.getenv("GAIA_RATE_TESTALL_WINDOW", "60"))
RATE_GUEST_CALLS = int(os.getenv("GAIA_RATE_GUEST_CALLS", "5"))
RATE_GUEST_WINDOW= int(os.getenv("GAIA_RATE_GUEST_WINDOW","60"))
