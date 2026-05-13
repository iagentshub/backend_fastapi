"""Tests del cap de sesiones guest."""
from __future__ import annotations

import pytest

import app.storage.guest as guest_mod
from app.storage.guest import GuestSession, get_session, new_guest_id


@pytest.fixture(autouse=True)
def clean_sessions():
    guest_mod._sessions.clear()
    original_max = guest_mod.MAX_SESSIONS
    yield
    guest_mod._sessions.clear()
    guest_mod.MAX_SESSIONS = original_max


def test_creates_session():
    gid = new_guest_id()
    s = get_session(gid)
    assert isinstance(s, GuestSession)


def test_returns_same_session():
    gid = new_guest_id()
    s1 = get_session(gid)
    s2 = get_session(gid)
    assert s1 is s2


def test_cap_raises_503(monkeypatch):
    from fastapi import HTTPException
    guest_mod.MAX_SESSIONS = 2
    get_session(new_guest_id())
    get_session(new_guest_id())
    with pytest.raises(HTTPException) as exc:
        get_session(new_guest_id())
    assert exc.value.status_code == 503


def test_expired_sessions_cleaned():
    import time
    guest_mod.MAX_SESSIONS = 2
    gid = new_guest_id()
    s = get_session(gid)
    # Hacer que la sesión parezca expirada
    s.created_at = time.time() - guest_mod.TTL - 1
    # Una nueva sesión debe poder crearse (limpia las expiradas)
    get_session(new_guest_id())
    assert gid not in guest_mod._sessions
