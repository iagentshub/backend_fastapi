"""Login de la extensión de VS Code.

La extensión abre el navegador, que ya sabe quién eres (cookie `ga_token`), y
este te devuelve a VS Code con un código de un solo uso. La extensión lo canjea
por un PAT. Ni el token en claro ni la cookie salen nunca por la URI vscode://.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.api.routes.auth.dependencies import _login_limiter, _tokens, require_auth
from app.auth.auth import get_user_by_id
from app.errors import APIError
from app.storage.tokens import DEFAULT_EXPIRY_DAYS
from app.storage.tokens import consume_auth_code as _consume_auth_code
from app.storage.tokens import create_auth_code as _create_auth_code
from app.utils import flog
from app.utils.net import json_body

router = APIRouter()

# Editores que pueden recibir el callback. Sin lista blanca, /vscode/start sería
# un redirector abierto: cualquiera podría mandar a un usuario logueado a un
# esquema arbitrario con sus parámetros.
_VSCODE_SCHEMES = frozenset(
    {"vscode", "vscode-insiders", "vscodium", "cursor", "windsurf"}
)
_VSCODE_AUTHORITY = "iagentshub.iagentshub"


def _check_callback(callback: str) -> None:
    parsed = urlsplit(callback)
    if parsed.scheme not in _VSCODE_SCHEMES or parsed.netloc != _VSCODE_AUTHORITY:
        raise APIError(400, "callback_not_allowed", "Callback no permitido")


def _public_base_url(request: Request) -> str:
    from app.api.routes.auth.login import _public_base_url as _impl

    return _impl(request)


@router.get("/vscode/start")
async def vscode_start(
    request: Request,
    state: str = Query(..., min_length=8, max_length=128),
    callback: str = Query(..., max_length=512),
) -> RedirectResponse:
    """Puente extensión → web. Manda al usuario a la pantalla de autorización.

    Existe porque la extensión solo conoce la URL de la API, que en desarrollo no
    es la misma que la de la web. Aquí el backend, que sí sabe dónde vive el
    frontend (GAIA_FRONTEND_URL), resuelve esa diferencia.
    """
    _check_callback(callback)
    query = urlencode({"state": state, "callback": callback})
    return RedirectResponse(
        f"{_public_base_url(request)}/vscode-auth/?{query}", status_code=302
    )


@router.post("/vscode/authorize")
async def vscode_authorize(
    request: Request, username: str = Depends(require_auth)
) -> dict[str, Any]:
    """Emite el código. Exige la sesión del navegador: es el consentimiento."""
    from app.storage.guest import is_guest as _is_guest

    if _is_guest(username):
        raise APIError(
            403,
            "guest_cannot_connect_vscode",
            "Las sesiones de invitado no pueden conectar VS Code.",
        )

    body = await json_body(request)
    state = str(body.get("state") or "")
    if not 8 <= len(state) <= 128:
        raise APIError(400, "invalid_field", "state inválido", extra={"field": "state"})

    return {"code": await _create_auth_code(username, state)}


@router.post("/vscode/exchange")
async def vscode_exchange(request: Request) -> dict[str, Any]:
    """Código + state → PAT. Sin cookie: quien llama aquí es la extensión.

    El PAT se crea aquí y no al autorizar, para que el token en claro exista solo
    en esta respuesta y no tenga que dormir en ninguna tabla esperando el canje.
    """
    await _login_limiter(request)

    body = await json_body(request)
    code = str(body.get("code") or "")
    state = str(body.get("state") or "")
    if not code or not state:
        raise APIError(400, "code_and_state_required", "code y state requeridos")

    user_id = await _consume_auth_code(code, state)
    if not user_id:
        raise APIError(
            400, "invalid_auth_code", "Código inválido, caducado o ya usado"
        )

    token, meta = await _tokens.create(user_id, "VS Code", DEFAULT_EXPIRY_DAYS)
    user = await get_user_by_id(user_id)
    username = user["username"] if user else ""
    flog.info(f"PAT creado desde VS Code ({meta['prefix']}…)", username=username)
    return {"token": token, "token_id": meta["id"], "username": username}
