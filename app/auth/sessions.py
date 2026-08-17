"""Abrir, renovar y cerrar una sesión, desde un solo sitio.

`set_session_cookies` ya centralizaba las cookies porque copiarlas era la forma
de olvidarse de una. Abrir sesión es ahora tres cosas que tienen que ocurrir
juntas —fila en `sessions`, access con el claim `sid`, refresh en su cookie— y
hay ocho emisores repartidos por cuatro módulos (registro, login, verificación
de email, invitado, GitHub, impersonación y los dos cambios de grupo). Un
emisor que se saltara la fila emitiría un token que ningún logout puede
revocar, y no fallaría nada visible.

Ver docs/adr/008-sesiones-revocables.md.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from starlette.responses import Response

from app.auth.cookies import set_session_cookies
from app.auth.passwords import create_token
from app.storage.sessions import SessionStorage
from app.utils.net import client_ip

_sessions = SessionStorage()


async def open_session(
    response: Response,
    user_id: str,
    request: Optional[Request] = None,
    group_id: Optional[str] = None,
) -> str:
    """Abre una sesión nueva y deja las cookies puestas. Devuelve el access.

    `request` es opcional solo para no obligar a los emisores que no lo tienen
    a firma nueva: sin él la sesión se guarda sin IP ni user-agent y en la
    pantalla de sesiones aparece como «origen desconocido», que es exacto.
    """
    ip = client_ip(request) if request is not None else None
    ua = request.headers.get("user-agent") if request is not None else None
    session_id, refresh = await _sessions.open(user_id, ip=ip, user_agent=ua)
    token = create_token(user_id, group_id=group_id, session_id=session_id)
    set_session_cookies(response, token, refresh=refresh)
    return token


def reissue_access(
    response: Response,
    user_id: str,
    session_id: Optional[str],
    group_id: Optional[str] = None,
) -> str:
    """Reemite el access de una sesión que ya existe, sin tocar el refresh.

    Es lo que necesita el cambio de grupo: el claim `gid` cambia, la sesión no.
    Emitir una sesión nueva ahí dejaría la anterior viva en la lista del perfil
    y multiplicaría las filas por cada vez que alguien cambia de grupo.
    """
    token = create_token(user_id, group_id=group_id, session_id=session_id)
    set_session_cookies(response, token)
    return token
