"""Perfil del usuario: lectura, edición y avatar."""


from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.routes.auth.dependencies import (
    _groups,
    require_auth,
    require_group_session,
)
from app.auth.auth import (
    get_user_by_id,
    get_user_role,
)
from app.config.content_languages import CONTENT_LANGUAGE_SET
from app.errors import APIError
from app.middleware.ratelimit import RateLimiter, principal_key
from app.sql import sql
from app.storage import avatars
from app.storage.db import open_db
from app.utils import flog

router = APIRouter()

# Una imagen entra entera en memoria para comprobar su tipo y guardarla. El
# cliente ya la comprime a 512 px antes de enviarla, pero un `curl` no pasa por
# ahí, y `max_request_bytes` vale 0 —sin límite— mientras el administrador no
# diga otra cosa: sin freno, subir fotos es un grifo abierto contra la memoria
# del worker y contra la base.
#
# Es el mismo par que ya protege la transferencia de binarios de las tools
# (`app/api/routes/tools.py`): cuota por principal con techo por IP, y una sola
# imagen procesándose a la vez por worker —las demás esperan sin crear otra
# copia en memoria—.
_avatar_slot = asyncio.Semaphore(1)
_avatar_limiter = RateLimiter(
    calls=20,
    window=300,
    key_func=principal_key,
    shared=True,
    name="avatar-upload",
    ip_calls=60,
)


class ProfileBody(BaseModel):
    bio: str | None = Field(default=None, max_length=500)
    languages: list[str] = Field(default_factory=list, max_length=50)
    is_email_public: bool = False
    github: str | None = Field(default=None, max_length=100)
    cv: str | None = Field(default=None, max_length=20_000)

@router.get("/me")
async def me(
    ctx=Depends(require_group_session),  # noqa: B008
) -> dict[str, Any]:
    from app.config.session import WEBMAIL_URL
    from app.storage.guest import is_guest

    user_id = ctx.user
    group_id = ctx.group_id

    role = await get_user_role(user_id)
    group_name: str | None = None
    user_row: dict[str, Any] = await get_user_by_id(user_id) or {}
    username = user_row.get("username", "")
    # El invitado tiene fila como cualquiera; lo que el cliente necesita saber
    # es que su sesión es efímera, y eso viaja aquí.
    auth_method = "guest" if is_guest(user_id) else (user_row.get("provider") or "internal")
    if group_id != user_id:
        group = await _groups.get(group_id)
        group_name = group["name"] if group else group_id
    else:
        group_name = user_row.get("display_name") or username

    payload: dict[str, Any] = {
        "id": user_id,
        "username": username,
        "role": role,
        "auth_method": auth_method,
        "group_id": group_id,
        "group_personal": group_id == user_id,
    }
    if user_row:
        payload["email"] = user_row.get("email")
        payload["is_email_public"] = bool(user_row.get("is_email_public", 0))
        # La misma convención que el perfil público: la URL hecha, o `None` si
        # no hay foto. El cliente no tiene que construirla ni recibir un
        # booleano aparte que dijera lo mismo a medias — y el checksum que lleva
        # dentro es lo que hace que la caché del navegador se entere del cambio.
        payload["avatar_url"] = avatars.public_url(
            username, await avatars.checksum_by_owner(user_id)
        )
    if role == "admin" and WEBMAIL_URL:
        payload["webmail_url"] = WEBMAIL_URL
    if group_name is not None:
        payload["group_name"] = group_name
    return payload

# ── Social profile ────────────────────────────────────────────────────────────

_ALLOWED_LANGUAGES = CONTENT_LANGUAGE_SET

_ALLOWED_AVATAR_EXT = {".jpg", ".jpeg", ".png", ".webp"}

@router.put("/me/profile")
async def update_profile(
    body: ProfileBody,
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    import json

    bio = (body.bio or "").strip() or None
    raw_langs = body.languages
    languages = json.dumps([lang for lang in raw_langs if lang in _ALLOWED_LANGUAGES])
    is_email_public = 1 if body.is_email_public else 0
    # N3: solo permitir URLs https:// para el campo github (bloquear javascript: y otros)
    _github_raw = (body.github or "").strip()
    if _github_raw and not _github_raw.startswith("https://"):
        raise APIError(
            422,
            "invalid_field",
            "El campo github debe ser una URL https://",
            extra={"field": "github"},
        )
    github = _github_raw or None
    cv = (body.cv or "").strip() or None

    async with open_db() as conn:
        await conn.execute(
            sql("queries/login:update_profile"),
            (bio, languages, is_email_public, github, cv, username),
        )
        await conn.commit()
    return {"ok": True}

@router.post("/me/avatar")
async def upload_avatar(
    request: Request,
    username: str = Depends(require_auth),
    _rl: None = Depends(_avatar_limiter),  # noqa: B008
) -> dict[str, Any]:
    from pathlib import Path as _Path

    from fastapi import UploadFile
    from fastapi.datastructures import FormData
    from starlette.datastructures import UploadFile as _StarletteUploadFile

    try:
        form: FormData = await request.form()
        raw_field = form.get("avatar")
        # request.form() construye starlette.datastructures.UploadFile, no
        # fastapi.UploadFile (subclase usada solo vía inyección de FastAPI) —
        # hay que comprobar contra la clase base real que devuelve el parser.
        if not isinstance(raw_field, _StarletteUploadFile):
            raise APIError(400, "avatar_field_required", "Campo 'avatar' requerido")
        file: UploadFile = raw_field

        ext = _Path(file.filename or "").suffix.lower()
        if ext not in _ALLOWED_AVATAR_EXT:
            raise APIError(
                400,
                "avatar_format_not_allowed",
                "Formato no permitido. Usa jpg, png o webp.",
            )

        # El tamaño no se comprueba aquí: lo hace BodySizeLimitMiddleware con
        # el número que puso el administrador, y para todas las peticiones por
        # igual. El 10 MB propio que había en esta línea era el tercero de tres
        # límites distintos para la misma subida, y su mensaje mentía desde que
        # el middleware cortaba en 2.
        #
        # El semáforo empieza aquí, antes de leer: es lo que evita que veinte
        # subidas simultáneas tengan veinte imágenes en memoria a la vez.
        async with _avatar_slot:
            data = await file.read()
            from app.utils.images import detect_avatar_mime

            mime = detect_avatar_mime(data)
            if mime is None:
                raise APIError(
                    400,
                    "avatar_format_not_allowed",
                    "El contenido no es una imagen JPG, PNG o WebP válida.",
                )
            checksum = await avatars.save(username, data, mime)
        user = await get_user_by_id(username)
    except APIError:
        raise
    except Exception as exc:
        flog.error(
            f"Fallo subiendo avatar para {username}: {exc}",
            exc_info=True,
        )
        raise APIError(500, "internal_error", "Error interno del servidor.") from exc

    public_username = user["username"] if user else ""
    flog.audit(
        "avatar.updated",
        resource_type="user",
        resource_id=username,
        details={"size_bytes": len(data), "mime": mime},
        summary=f"{public_username} actualizó su foto de perfil",
        username=username,
    )
    return {
        "ok": True,
        "avatar_url": avatars.public_url(public_username, checksum),
    }


@router.delete("/me/avatar")
async def delete_avatar(
    username: str = Depends(require_auth),
) -> dict[str, Any]:
    """Quita la foto y deja la inicial.

    Sin fila en `user_avatars`, `GET /api/users/{username}/avatar` responde 204
    y el cliente pinta el fallback: no hay nada más que limpiar. Hasta que
    existió esta ruta, la única forma de deshacerse de una foto era subir otra.
    """
    tenia = await avatars.checksum_by_owner(username) is not None
    await avatars.delete(username)
    if tenia:
        # Se audita porque destruye un dato personal y no hay copia: el fichero
        # original no se guarda en ninguna parte.
        flog.audit(
            "avatar.deleted",
            resource_type="user",
            resource_id=username,
            summary="Foto de perfil eliminada",
            username=username,
        )
    return {"ok": True}
