"""Auth routes — sesión, GitHub OAuth, RGPD, PATs y login de la extensión VS Code.

`routes/auth.py` llegó a 1021 líneas mezclando el modelo de autorización
compartido por todo el backend con cinco superficies de endpoints distintas.
Partido en:
    dependencies.py   modelo de autorización (`require_auth`, `GroupContext`,
                       etc.) — el contrato que importan ~30 archivos del
                       backend. Reexportado aquí para que esos imports no
                       cambien (mismo criterio que routes/admin/__init__.py
                       documenta para su propio caso).
    session.py         registro, login, sesión y revocación.
    passwords.py       recuperación y cambio de contraseña.
    profile.py         perfil del usuario y avatar.
    oauth_device.py    login con GitHub (Device Flow).
    gdpr.py            estado/solicitud/cancelación de borrado, export RGPD.
    pat_tokens.py      personal access tokens.
    vscode_oauth.py    login de la extensión de VS Code.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.auth.dependencies import (
    GroupContext,
    require_admin,
    require_auth,
    require_group,
    require_group_session,
    require_session,
)

from . import gdpr as _gdpr
from . import legal as _legal
from . import oauth_device as _oauth_device
from . import passwords as _passwords
from . import pat_tokens as _pat_tokens
from . import profile as _profile
from . import session as _session
from . import vscode_oauth as _vscode_oauth

router = APIRouter(prefix="/api/auth", tags=["auth"])
router.include_router(_session.router)
router.include_router(_passwords.router)
router.include_router(_profile.router)
router.include_router(_oauth_device.router)
router.include_router(_gdpr.router)
router.include_router(_legal.router)
router.include_router(_pat_tokens.router)
router.include_router(_vscode_oauth.router)

__all__ = [
    "router",
    "require_auth",
    "require_group",
    "require_session",
    "require_group_session",
    "require_admin",
    "GroupContext",
]
