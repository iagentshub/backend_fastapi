"""Defensa anti-CSRF en dos capas sobre la cookie de sesión.

Hasta aquí la única protección era `SameSite=Lax`. Es buena, pero vive en el
navegador del visitante y no en el servidor, y para el navegador un subdominio
es «el mismo sitio»: desde un subdominio comprometido, `Lax` sí manda la
cookie y detrás no había nada. Ver docs/adr/006-csrf-en-dos-capas.md.

Las dos capas y a quién le aplican:

    1. Origin/Referer — el navegador escribe esa cabecera y el JavaScript de
       una página no la puede falsificar. Aplica a todo método inseguro que la
       traiga, autenticado o no: cubrir también las peticiones anónimas es
       gratis y bloquea de paso el *login CSRF*.
    2. Token double-submit — cookie `ga_csrf` legible por JS que el cliente
       reenvía en `X-CSRF-Token`. Aplica solo a peticiones autenticadas por
       cookie.

Dos exenciones deliberadas, ambas necesarias para no romper clientes que no
son navegadores y en ninguna de las cuales hay CSRF que valer:

    · `Authorization: Bearer` (extensión de VS Code, scripts). Un PAT no es
      una credencial ambiental: el navegador no lo adjunta solo.
    · Sin `Origin` ni `Referer` (Flutter nativo, que manda la cookie a mano;
      el webhook de Stripe; curl). A un navegador no se le puede obligar a
      omitir `Origin` en un POST, así que su ausencia identifica a un cliente
      que no es atacable por esta vía.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import app.config.session as _session
from app.auth.cookies import SESSION_COOKIE
from app.config.cors import CORS_ORIGINS
from app.utils import flog
from app.utils.net import request_origin


def _origen_declarado(request: Request) -> str | None:
    """Origen desde el que se hizo la petición, o None si no viene ninguno.

    `Referer` es el plan B: los navegadores actuales mandan `Origin` en todo
    método inseguro, pero el `Referer` lleva ahí desde siempre y de él se
    extrae el mismo dato recortando la ruta.
    """
    origin = request.headers.get("origin", "").strip()
    if origin and origin.lower() != "null":
        return origin.lower()

    referer = request.headers.get("referer", "").strip()
    if not referer:
        return None
    try:
        parts = urlsplit(referer)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _origen_permitido(request: Request, origen: str) -> bool:
    if origen in {o.rstrip("/").lower() for o in CORS_ORIGINS}:
        return True
    propio = request_origin(request)
    return propio is not None and origen == propio


def _rechazo(code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=403)


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Los modos se leen a través del módulo, no importados por valor: los
        # tests los cambian con monkeypatch.setattr y un `from … import` los
        # habría congelado al importar (mismo motivo que `_db.IS_PG`).
        metodo = request.method.upper()
        if metodo in _session.SAFE_METHODS:
            return await self._reemitir_csrf(request, await call_next(request))

        # Credencial explícita, no ambiental → no hay CSRF posible.
        if request.headers.get("authorization"):
            return await call_next(request)

        rechazo = self._revisar_origen(request) or self._revisar_token(request)
        if rechazo is not None:
            return rechazo
        return await call_next(request)

    # ── Capa 1 ────────────────────────────────────────────────────────────────

    def _revisar_origen(self, request: Request) -> JSONResponse | None:
        modo = _session.CSRF_ORIGIN_CHECK
        if modo == "off":
            return None
        origen = _origen_declarado(request)
        if origen is None or _origen_permitido(request, origen):
            return None

        flog.warning(
            f"[csrf] Origen rechazado en {request.method} {request.url.path}: "
            f"{origen} (modo={modo})"
        )
        if modo != "enforce":
            return None
        return _rechazo(
            "csrf_origin_rejected",
            "Petición rechazada: origen no permitido.",
        )

    # ── Capa 2 ────────────────────────────────────────────────────────────────

    def _revisar_token(self, request: Request) -> JSONResponse | None:
        modo = _session.CSRF_TOKEN_CHECK
        if modo == "off":
            return None
        ga_token = request.cookies.get(SESSION_COOKIE)
        if not ga_token:
            return None  # Sin sesión de cookie no hay credencial que robar.

        from app.auth.passwords import csrf_token_matches

        recibido = request.headers.get(_session.CSRF_HEADER, "")
        try:
            if recibido and csrf_token_matches(ga_token, recibido):
                return None
        except RuntimeError:
            # Sin secreto de firma no hay token que comparar — ni sesión válida
            # que proteger. Lo denuncia startup_checks, no esta puerta.
            return None

        motivo = "ausente" if not recibido else "no coincide con la sesión"
        flog.warning(
            f"[csrf] Token {motivo} en {request.method} {request.url.path} "
            f"(modo={modo})"
        )
        if modo != "enforce":
            return None
        code = "csrf_token_missing" if not recibido else "csrf_token_invalid"
        return _rechazo(code, "Petición rechazada: falta el token anti-CSRF válido.")

    # ── Auto-reparación ───────────────────────────────────────────────────────

    async def _reemitir_csrf(self, request: Request, response: Response) -> Response:
        """Repone `ga_csrf` cuando hay sesión y la cookie falta o no cuadra.

        Es lo que permite subir la capa 2 a `enforce` sin echar a nadie: las
        sesiones abiertas antes del despliegue —y las de quien borre la cookie—
        se curan en la primera navegación, porque el token es una función pura
        del JWT y el servidor lo puede recalcular sin guardar nada.
        """
        if _session.CSRF_TOKEN_CHECK == "off":
            return response
        ga_token = request.cookies.get(SESSION_COOKIE)
        if not ga_token:
            return response

        from app.auth.passwords import derive_csrf_token

        try:
            esperado = derive_csrf_token(ga_token)
        except RuntimeError:
            # _secret() aborta si el secreto de firma no está configurado. Sin
            # él no hay sesión válida que proteger: no es aquí donde se avisa
            # (lo hace startup_checks), y romper un GET lo empeoraría.
            return response
        if request.cookies.get(_session.CSRF_COOKIE) == esperado:
            return response

        response.set_cookie(
            _session.CSRF_COOKIE,
            esperado,
            httponly=False,
            samesite="lax",
            secure=_session.SECURE_COOKIES,
            max_age=_session.JWT_MAX_AGE_SECONDS,
        )
        return response
