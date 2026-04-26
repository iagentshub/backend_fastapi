"""Tests de hash y verificación de contraseñas."""
from __future__ import annotations

from app.auth.auth import hash_password, verify_password


def test_hash_correcto():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed) is True


def test_hash_incorrecto():
    hashed = hash_password("mysecret")
    assert verify_password("wrong", hashed) is False


def test_hash_vacio():
    hashed = hash_password("pass")
    assert verify_password("", hashed) is False
