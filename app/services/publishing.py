"""Quién puede publicar en la vitrina pública.

Publicar es sacar un recurso del espacio propio y ponerlo en Explorar, donde lo
ve y lo enlaza cualquiera. El invitado no puede: su cuenta se borra al cerrar
sesión, así que lo que publicase dejaría en el catálogo fichas que se
desvanecen —y enlaces rotos en quien las hubiera enlazado— sin que nadie haya
borrado nada.

Vive aquí, y no repetido en cada ruta, porque es **política de producto**: la
misma frase se aplica a agentes, skills, prompts, tools, knowledge y packs, y
cada sitio que la escribiera por su cuenta sería un sitio donde olvidarla. No
confundir con las ramas `is_guest` que había antes por todo el backend: aquellas
eran almacenamiento —dónde guardaba el invitado— y murieron con la GuestSession.
Ver docs/adr/012-el-invitado-es-un-usuario-efimero.md.
"""

from __future__ import annotations

from app.errors import APIError
from app.storage.guest import is_guest


def assert_can_publish(user: str) -> None:
    """403 si el principal no puede publicar. Hoy solo se lo impide al invitado."""
    if is_guest(user):
        raise APIError(
            403,
            "guest_cannot_publish",
            "Las sesiones de invitado no pueden publicar recursos.",
        )
