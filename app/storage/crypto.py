"""Symmetric encryption for API keys stored in the database.

Key derivation: PBKDF2-HMAC-SHA256 from GAIA_AGENTS_SECRET with a fixed
salt (entropy comes entirely from the secret itself). Output is 32 bytes
→ base64url → Fernet key (AES-128-CBC + HMAC-SHA256, authenticated).

Encrypted values are stored with an "enc:" prefix so plaintext values
written before this feature was introduced are handled transparently —
they pass through decrypt() unchanged and get re-encrypted on next save.

If the secret changes, decrypt() returns the raw ciphertext unchanged
rather than crashing, so a rotated secret surfaces as a recoverable error
(the user re-enters the API key) rather than an unhandled exception.
"""
from __future__ import annotations

import base64
import hashlib

_PREFIX = "enc:"
_SALT = b"iagentshub-api-keys-v1"
_ITERATIONS = 100_000

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    from cryptography.fernet import Fernet
    from app.auth.auth import _secret
    raw = _secret().encode("utf-8")
    key_bytes = hashlib.pbkdf2_hmac("sha256", raw, _SALT, _ITERATIONS, dklen=32)
    _fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
    return _fernet


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return _PREFIX + token


def decrypt(value: str) -> str:
    if not value or not value.startswith(_PREFIX):
        return value
    try:
        return _get_fernet().decrypt(value[len(_PREFIX):].encode("utf-8")).decode("utf-8")
    except Exception:
        return value
