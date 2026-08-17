"""Las cookies de sesión, emitidas y borradas desde un solo sitio.

El bloque `set_cookie("ga_token", …, httponly=True, samesite="lax", …)` estaba
copiado literalmente en ocho handlers de cuatro módulos (registro, login,
verificación de email, invitado, cambio de grupo ×2, impersonación y login con
GitHub). Con una sola cookie era duplicación tolerable; en cuanto la sesión
pasa a ser DOS cookies que tienen que viajar juntas, deja de serlo: la defensa
anti-CSRF dependería de que nadie se olvide del noveno sitio, y una cookie
`ga_csrf` que falta no rompe nada visible —solo desactiva la comprobación—.

Ver docs/adr/006-csrf-en-dos-capas.md.
"""

from __future__ import annotations

from starlette.responses import Response

from app.auth.passwords import derive_csrf_token
from app.config.session import (
    CSRF_COOKIE,
    JWT_MAX_AGE_SECONDS,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    SECURE_COOKIES,
)

SESSION_COOKIE = "ga_token"


def set_session_cookies(
    response: Response, token: str, refresh: str | None = None
) -> None:
    """Emite la cookie de sesión, su token anti-CSRF derivado y el refresh.

    `refresh` solo se pasa cuando hay uno nuevo que entregar: al abrir sesión y
    al rotarlo. Cambiar de grupo reemite el access con el mismo `sid` y no toca
    el refresh, porque no es una sesión nueva.
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=JWT_MAX_AGE_SECONDS,
    )
    # Sin httponly a propósito: el cliente tiene que leerla para reenviarla en
    # la cabecera X-CSRF-Token. No es una credencial —sin la cookie de sesión
    # no autentica nada— y del HMAC no se vuelve al JWT.
    response.set_cookie(
        CSRF_COOKIE,
        derive_csrf_token(token),
        httponly=False,
        samesite="lax",
        secure=SECURE_COOKIES,
        max_age=JWT_MAX_AGE_SECONDS,
    )
    if refresh is not None:
        response.set_cookie(
            REFRESH_COOKIE,
            refresh,
            httponly=True,
            samesite="lax",
            secure=SECURE_COOKIES,
            max_age=JWT_MAX_AGE_SECONDS,
            path=REFRESH_COOKIE_PATH,
        )


def clear_session_cookies(response: Response) -> None:
    """Cierra la sesión: las tres cookies se van juntas.

    El refresh se borra con el mismo `path` con el que se puso: `delete_cookie`
    con otro path no borra nada y dejaría al cliente con la credencial que
    reabre la sesión que acaba de cerrar.
    """
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
