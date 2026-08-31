"""El rango de los roles, en un solo sitio.

`guest:0 < standard:1 < admin:2`. Vivía dentro de
`app/api/routes/auth/dependencies.py`, que es la capa de rutas, y por eso
`admin_update_user` no podía compararlo sin montar un ciclo de imports —
`dependencies.py` importa de `app.auth.auth`—. Comparar rangos hace falta en los
dos sitios: para decidir si una petición pasa, y para decidir si un cambio de rol
es un ascenso o una degradación.
"""

from __future__ import annotations

STANDARD_RANK = 1
ROLE_RANK = {"guest": 0, "standard": STANDARD_RANK, "admin": 2}


def rank_of(role: str) -> int:
    """El rango de un rol; `standard` para cualquier valor desconocido."""
    return ROLE_RANK.get(role, STANDARD_RANK)
