"""Contrato de las validaciones de identidad compartidas con los clientes."""

import pytest

from app.utils.validation import is_valid_email, is_valid_username


@pytest.mark.parametrize(
    "email",
    ["user@example.com", "user+ventas@example.co.uk", "USER@example.com"],
)
def test_valid_emails(email: str) -> None:
    assert is_valid_email(email)


@pytest.mark.parametrize(
    "email",
    ["", "invalido", "user@example.c", "usér@example.com"],
)
def test_invalid_emails(email: str) -> None:
    assert not is_valid_email(email)


@pytest.mark.parametrize("username", ["andres_01", " Andres_01 "])
def test_valid_usernames_are_normalized(username: str) -> None:
    assert is_valid_username(username)


@pytest.mark.parametrize("username", ["abc", "guest", "guest_123"])
def test_reserved_or_invalid_usernames(username: str) -> None:
    assert not is_valid_username(username)
