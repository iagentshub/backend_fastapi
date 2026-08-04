"""Perfil de usuario: registro con los campos de perfil.

Aquí vivían tres tests de update_user_profile(), borrados con la función: no
la llamaba nadie, porque no hay endpoint que edite el perfil. Los campos se
escriben en el registro y ya no se vuelven a tocar.
"""
from __future__ import annotations

import pytest

from app.auth.auth import (
    get_user_by_email,
    get_user_by_username,
    register_user_email,
)


async def test_register_user_email_basic(patch_data_dir):
    username, token = await register_user_email("hello", "hello@example.com", "pass1234")
    assert username == "hello"
    assert token is None  # EMAIL_VERIFY_ENABLED is False in tests
    user = await get_user_by_email("hello@example.com")
    assert user is not None
    assert user["username"] == "hello"
    assert user["email"] == "hello@example.com"
    assert user["role"] == "standard"
    assert user["is_active"] in (1, True)
    assert user["is_verified"] in (1, True)


async def test_register_user_email_with_profile(patch_data_dir):
    username, _ = await register_user_email(
        "profileuser",
        "profile@example.com",
        "pass1234",
        birth_date="1990-06-15",
        gender="male",
        country="ES",
        phone="+34 600 000 000",
        display_name="John Doe",
    )
    user = await get_user_by_username(username)
    assert user is not None
    assert user["birth_date"] == "1990-06-15"
    assert user["gender"] == "male"
    assert user["country"] == "ES"
    assert user["phone"] == "+34 600 000 000"
    assert user["display_name"] == "John Doe"


async def test_register_user_email_duplicate(patch_data_dir):
    await register_user_email("dupuser", "dup@example.com", "pass1234")
    with pytest.raises(ValueError, match="correo"):
        await register_user_email("different", "dup@example.com", "pass5678")


async def test_register_username_is_separate_and_unique(patch_data_dir):
    u1, _ = await register_user_email("alice", "alice@foo.com", "pass1234")
    assert u1 == "alice"
    with pytest.raises(ValueError, match="usuario"):
        await register_user_email("alice", "alice@bar.com", "pass1234")
