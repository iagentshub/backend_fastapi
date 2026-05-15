"""Tests for user profile: register with profile fields and update_user_profile."""
from __future__ import annotations

import pytest

from app.auth.auth import (
    get_user_by_email,
    get_user_by_username,
    register_user_email,
    update_user_profile,
)


def test_register_user_email_basic(patch_data_dir):
    username = register_user_email("hello@example.com", "pass1234")
    assert username == "hello"
    user = get_user_by_email("hello@example.com")
    assert user is not None
    assert user["username"] == "hello"
    assert user["email"] == "hello@example.com"
    assert user["role"] == "standard"
    assert user["is_active"] in (1, True)


def test_register_user_email_with_profile(patch_data_dir):
    username = register_user_email(
        "profile@example.com",
        "pass1234",
        birth_date="1990-06-15",
        gender="male",
        country="ES",
        phone="+34 600 000 000",
        display_name="John Doe",
    )
    user = get_user_by_username(username)
    assert user is not None
    assert user["birth_date"] == "1990-06-15"
    assert user["gender"] == "male"
    assert user["country"] == "ES"
    assert user["phone"] == "+34 600 000 000"
    assert user["display_name"] == "John Doe"


def test_register_user_email_duplicate(patch_data_dir):
    register_user_email("dup@example.com", "pass1234")
    with pytest.raises(ValueError, match="correo"):
        register_user_email("dup@example.com", "pass5678")


def test_register_auto_username_deduplication(patch_data_dir):
    u1 = register_user_email("alice@foo.com", "pass1234")
    u2 = register_user_email("alice@bar.com", "pass1234")
    assert u1 == "alice"
    assert u2 == "alice_2"


def test_update_user_profile(patch_data_dir):
    username = register_user_email("update@example.com", "pass1234")
    update_user_profile(username, country="MX", phone="+52 55 1234 5678")
    user = get_user_by_username(username)
    assert user["country"] == "MX"
    assert user["phone"] == "+52 55 1234 5678"


def test_update_user_profile_ignores_unknown_fields(patch_data_dir):
    username = register_user_email("ignore@example.com", "pass1234")
    # Should not raise even with unknown fields
    update_user_profile(username, country="FR", unknown_field="value")
    user = get_user_by_username(username)
    assert user["country"] == "FR"


def test_update_user_profile_empty_update(patch_data_dir):
    username = register_user_email("empty@example.com", "pass1234")
    # Should be a no-op
    update_user_profile(username)
    user = get_user_by_username(username)
    assert user is not None
