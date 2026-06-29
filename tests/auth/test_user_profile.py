"""Tests for user profile: register with profile fields and update_user_profile."""
from __future__ import annotations

import pytest

from app.auth.auth import (
    get_user_by_email,
    get_user_by_username,
    register_user_email,
    update_user_profile,
)


async def test_register_user_email_basic(patch_data_dir):
    username, token = await register_user_email("hello@example.com", "pass1234")
    assert username == "hello@example.com"
    assert token is None  # EMAIL_VERIFY_ENABLED is False in tests
    user = await get_user_by_email("hello@example.com")
    assert user is not None
    assert user["username"] == "hello@example.com"
    assert user["email"] == "hello@example.com"
    assert user["role"] == "standard"
    assert user["is_active"] in (1, True)
    assert user["is_verified"] in (1, True)


async def test_register_user_email_with_profile(patch_data_dir):
    username, _ = await register_user_email(
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
    await register_user_email("dup@example.com", "pass1234")
    with pytest.raises(ValueError, match="correo"):
        await register_user_email("dup@example.com", "pass5678")


async def test_register_username_is_full_email(patch_data_dir):
    u1, _ = await register_user_email("alice@foo.com", "pass1234")
    u2, _ = await register_user_email("alice@bar.com", "pass1234")
    assert u1 == "alice@foo.com"
    assert u2 == "alice@bar.com"


async def test_update_user_profile(patch_data_dir):
    username, _ = await register_user_email("update@example.com", "pass1234")
    await update_user_profile(username, country="MX", phone="+52 55 1234 5678")
    user = await get_user_by_username(username)
    assert user["country"] == "MX"
    assert user["phone"] == "+52 55 1234 5678"


async def test_update_user_profile_ignores_unknown_fields(patch_data_dir):
    username, _ = await register_user_email("ignore@example.com", "pass1234")
    await update_user_profile(username, country="FR", unknown_field="value")
    user = await get_user_by_username(username)
    assert user["country"] == "FR"


async def test_update_user_profile_empty_update(patch_data_dir):
    username, _ = await register_user_email("empty@example.com", "pass1234")
    await update_user_profile(username)
    user = await get_user_by_username(username)
    assert user is not None
