"""Symmetric encryption for API keys stored in the database.

Key derivation: PBKDF2-HMAC-SHA256 from GAIA_AGENTS_SECRET with a fixed
salt (entropy comes entirely from the secret itself). Output is 32 bytes
→ base64url → Fernet key (AES-128-CBC + HMAC-SHA256, authenticated).

Encrypted values are stored with an "enc:" prefix so plaintext values
written before this feature was introduced are handled transparently —
they pass through decrypt() unchanged and get re-encrypted on next save.

If the secret changed (rotation, backup restored with another settings.json)
the stored value can no longer be read. decrypt() raises DecryptionError in
that case: devolver el ciphertext lo mandaba tal cual al proveedor LLM en la
cabecera Authorization, y el usuario veía un 401 ajeno en vez del problema
real. Los storages usan decrypt_fields() para degradar ese fallo a una marca
en el recurso (`credentials_unreadable`) que las rutas traducen a un error
propio: «la clave guardada no se puede leer, vuelve a introducirla».
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import InvalidToken

from app.utils import flog

_PREFIX = "enc:"
_SALT = b"iagentshub-api-keys-v1"
_ITERATIONS = 100_000

#: Marca en un recurso ya materializado: alguno de sus campos cifrados no se
#: pudo descifrar y su valor se ha vaciado. Quien vaya a usar la credencial
#: debe comprobarla antes de llamar a un tercero.
UNREADABLE_FLAG = "credentials_unreadable"
#: Nombres de los campos concretos que no se pudieron descifrar.
UNREADABLE_FIELDS = "unreadable_fields"

_fernet = None


class DecryptionError(Exception):
    """El valor almacenado no se puede descifrar con la clave actual."""


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    from cryptography.fernet import Fernet

    from app.auth.passwords import _secret
    raw = _secret().encode("utf-8")
    key_bytes = hashlib.pbkdf2_hmac("sha256", raw, _SALT, _ITERATIONS, dklen=32)
    _fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
    return _fernet


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """Descifra un valor con prefijo `enc:`.

    Los valores sin prefijo (legacy en claro) se devuelven tal cual.

    Raises:
        DecryptionError: la clave de cifrado cambió, el valor fue manipulado
            o no es un token Fernet.
    """
    if not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value[len(_PREFIX):].encode("utf-8")).decode("utf-8")
    except (InvalidToken, InvalidSignature, ValueError, TypeError) as exc:
        raise DecryptionError(str(exc)) from exc


def decrypt_fields(data: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    """Descifra in-place los campos indicados, marcando los ilegibles.

    Un campo que no se puede descifrar queda vacío —nunca se propaga el
    ciphertext— y su nombre se apunta en `unreadable_fields`, con
    `credentials_unreadable` a True. Así un listado con una credencial rota
    sigue respondiendo en vez de dar un 500, y quien vaya a usar la clave
    puede negarse antes de llamar al proveedor.
    """
    unreadable: list[str] = []
    for field in fields:
        value = data.get(field)
        if not value:
            continue
        try:
            data[field] = decrypt(str(value))
        except DecryptionError as exc:
            # Sin esta línea, "mis conexiones dejaron de autenticar" no lleva
            # nunca hasta GAIA_AGENTS_SECRET.
            flog.warning(f"[crypto] Fallo al descifrar '{field}': {exc}")
            data[field] = ""
            unreadable.append(field)
    if unreadable:
        data[UNREADABLE_FIELDS] = unreadable
        data[UNREADABLE_FLAG] = True
    return data
