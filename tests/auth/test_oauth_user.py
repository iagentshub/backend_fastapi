"""Tests de get_or_create_oauth_user."""
from __future__ import annotations

from app.auth.auth import get_or_create_oauth_user, list_users


def test_crea_usuario_nuevo(patch_data_dir):
    username = get_or_create_oauth_user("google", "sub123", "user@example.com", "User Name")
    assert username == "user@example.com"
    assert any(u["username"] == "user@example.com" for u in list_users())


def test_devuelve_usuario_existente_mismo_sub(patch_data_dir):
    get_or_create_oauth_user("google", "sub123", "user@example.com", "User")
    username = get_or_create_oauth_user("google", "sub123", "user@example.com", "User")
    assert username == "user@example.com"
    assert len([u for u in list_users() if u["username"] == "user@example.com"]) == 1


def test_vincula_email_existente(patch_data_dir):
    from app.auth.auth import register_user
    register_user("existing", "pass1234", email="shared@example.com")
    username = get_or_create_oauth_user("google", "sub999", "shared@example.com", "Existing")
    assert username == "existing"
    users = list_users()
    user = next(u for u in users if u["username"] == "existing")
    assert user["provider"] == "google"


def test_provider_sub_no_expuesto_en_listado(patch_data_dir):
    get_or_create_oauth_user("google", "secretsub", "secret@example.com", "Secret")
    for u in list_users():
        assert "provider_sub" not in u
