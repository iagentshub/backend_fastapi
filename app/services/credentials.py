"""Guardas para credenciales almacenadas que no se pueden descifrar.

`decrypt_fields()` deja el recurso marcado con `credentials_unreadable` en vez
de propagar el ciphertext. Todo camino que vaya a usar esa credencial contra un
tercero pasa antes por aquí: el usuario recibe «vuelve a introducir la clave»,
que es accionable, en lugar del 401 del proveedor, que no lo es.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from app.errors import APIError
from app.storage.crypto import UNREADABLE_FIELDS, UNREADABLE_FLAG

CODE = "credential_unreadable"
MESSAGE = (
    "La credencial guardada no se puede leer (el secreto de cifrado ha "
    "cambiado). Vuelve a introducirla."
)


def is_unreadable(resource: Mapping[str, Any] | None) -> bool:
    return bool(resource and resource.get(UNREADABLE_FLAG))


def credential_error(resource: Mapping[str, Any] | None = None) -> APIError:
    fields = list((resource or {}).get(UNREADABLE_FIELDS) or ["api_key"])
    return APIError(409, CODE, MESSAGE, extra={"fields": fields})


def assert_readable(resource: Mapping[str, Any] | None) -> None:
    """Corta la petición si la credencial del recurso es ilegible."""
    if is_unreadable(resource):
        raise credential_error(resource)


def test_failure(resource: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Resultado de test para una credencial ilegible.

    Los endpoints de prueba responden 200 con `ok: false` —la prueba se
    ejecutó y falló—, así que la marca viaja en el cuerpo con el mismo código
    que usaría el APIError.
    """
    return {"ok": False, "code": CODE, "message": MESSAGE, "detail": ""}
