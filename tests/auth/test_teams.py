"""Tests de funciones auth relacionadas con equipos."""
from __future__ import annotations

import pytest

from app.auth.auth import (
    demote_if_no_teams,
    get_user_role,
    promote_to_gestor,
    register_user,
    send_team_invitation_email,
)


def _make_user(username: str, role: str = "standard") -> None:
    from app.config.data import DB_FILE
    from app.storage.db import PH, close_db, open_db
    register_user(username, "pass1234", email=f"{username}@test.com")
    if role != "standard":
        conn = open_db(DB_FILE)
        try:
            conn.cursor().execute(
                f"UPDATE users SET role = {PH} WHERE username = {PH}", (role, username)
            )
            conn.commit()
        finally:
            close_db(conn)


def test_promote_standard_to_gestor():
    _make_user("alice")
    assert get_user_role("alice") == "standard"
    result = promote_to_gestor("alice")
    assert result is True
    assert get_user_role("alice") == "gestor"


def test_promote_admin_fails():
    _make_user("sysadmin", role="admin")
    result = promote_to_gestor("sysadmin")
    assert result is False
    assert get_user_role("sysadmin") == "admin"


def test_promote_nonexistent_fails():
    assert promote_to_gestor("ghost@ghost.com") is False


def test_demote_gestor_with_no_teams():
    _make_user("gestor1")
    promote_to_gestor("gestor1")
    assert get_user_role("gestor1") == "gestor"
    # No teams created → should demote
    result = demote_if_no_teams("gestor1")
    assert result is True
    assert get_user_role("gestor1") == "standard"


def test_demote_gestor_with_active_team():
    from app.config.data import DB_FILE
    from app.storage.teams import TeamStorage

    _make_user("gestor2")
    promote_to_gestor("gestor2")
    ts = TeamStorage(DB_FILE)
    ts.create_team("Mi equipo", "gestor2")
    # Has a team → should NOT demote
    result = demote_if_no_teams("gestor2")
    assert result is False
    assert get_user_role("gestor2") == "gestor"


def test_demote_non_gestor_user():
    _make_user("plain_user")
    result = demote_if_no_teams("plain_user")
    assert result is False


def test_send_team_invitation_email_no_smtp(caplog):
    """Without SMTP configured, should log the invitation URL."""
    import logging
    send_team_invitation_email(
        "invited@x.com",
        "Equipo Test",
        "organizer@x.com",
        "test-token-abc",
        "http://localhost:8765",
    )
    # Should not raise; logs the URL
