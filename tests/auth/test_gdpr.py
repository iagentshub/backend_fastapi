"""Tests unitarios para las funciones GDPR en auth.py:
schedule_user_deletion, cancel_user_deletion, purge_user_data,
purge_expired_deletions y get_owned_workspaces.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth.auth import (
    cancel_user_deletion,
    get_owned_workspaces,
    get_user_by_username,
    purge_expired_deletions,
    purge_user_data,
    register_user,
    schedule_user_deletion,
)
import app.config.data as _cfg


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(username: str, email: str | None = None) -> None:
    register_user(username, "pass1234", email=email or f"{username}@test.com")


def _set_deletion_date(username: str, dt: datetime) -> None:
    from app.storage.db import PH, close_db, open_db
    conn = open_db(_cfg.DB_FILE)
    try:
        conn.cursor().execute(
            f"UPDATE users SET deletion_requested_at = {PH} WHERE username = {PH}",
            (dt.isoformat(), username),
        )
        conn.commit()
    finally:
        close_db(conn)


def _workspace_storage():
    from app.storage.workspaces import WorkspaceStorage
    return WorkspaceStorage(_cfg.DB_FILE)


# ── get_owned_workspaces ──────────────────────────────────────────────────────

def test_get_owned_workspaces_sin_workspaces(patch_data_dir):
    _make_user("gows_solo")
    assert get_owned_workspaces("gows_solo") == []


def test_get_owned_workspaces_con_workspace(patch_data_dir):
    _make_user("gows_owner")
    created = _workspace_storage().create("Mi Equipo", created_by="gows_owner")
    owned = get_owned_workspaces("gows_owner")
    assert len(owned) == 1
    assert owned[0]["id"] == created["id"]
    assert owned[0]["name"] == "Mi Equipo"


def test_get_owned_workspaces_no_devuelve_los_que_no_son_suyos(patch_data_dir):
    _make_user("gows_other_owner")
    _make_user("gows_member")
    _workspace_storage().create("Equipo ajeno", created_by="gows_other_owner")
    assert get_owned_workspaces("gows_member") == []


# ── schedule_user_deletion ────────────────────────────────────────────────────

def test_schedule_deletion_devuelve_token(patch_data_dir):
    _make_user("sched_basic")
    token = schedule_user_deletion("sched_basic")
    assert token and len(token) > 10


def test_schedule_deletion_persiste_en_bd(patch_data_dir):
    _make_user("sched_persist")
    schedule_user_deletion("sched_persist")
    user = get_user_by_username("sched_persist")
    assert user["deletion_requested_at"] is not None
    assert user["deletion_token"] is not None


def test_schedule_deletion_fecha_aprox_30_dias(patch_data_dir):
    _make_user("sched_date")
    schedule_user_deletion("sched_date")
    user = get_user_by_username("sched_date")
    deletion_dt = datetime.fromisoformat(user["deletion_requested_at"])
    now = datetime.now(timezone.utc)
    delta = deletion_dt - now
    assert timedelta(days=29) < delta < timedelta(days=31)


# ── cancel_user_deletion ──────────────────────────────────────────────────────

def test_cancel_deletion_ok(patch_data_dir):
    _make_user("cancel_ok")
    token = schedule_user_deletion("cancel_ok")
    assert cancel_user_deletion(token) is True
    user = get_user_by_username("cancel_ok")
    assert user["deletion_requested_at"] is None
    assert user["deletion_token"] is None


def test_cancel_deletion_token_invalido(patch_data_dir):
    assert cancel_user_deletion("token-que-no-existe") is False


def test_cancel_deletion_token_usado_dos_veces(patch_data_dir):
    _make_user("cancel_twice")
    token = schedule_user_deletion("cancel_twice")
    assert cancel_user_deletion(token) is True
    assert cancel_user_deletion(token) is False


# ── purge_user_data ───────────────────────────────────────────────────────────

def test_purge_elimina_usuario_de_bd(patch_data_dir):
    _make_user("purge_basic")
    purge_user_data("purge_basic")
    assert get_user_by_username("purge_basic") is None


def test_purge_elimina_workspace_propio(patch_data_dir):
    _make_user("purge_ws_owner")
    ws = _workspace_storage()
    created = ws.create("Workspace a purgar", created_by="purge_ws_owner")
    purge_user_data("purge_ws_owner")
    assert ws.get(created["id"]) is None


def test_purge_no_afecta_a_otros_usuarios(patch_data_dir):
    _make_user("purge_victim")
    _make_user("purge_bystander")
    purge_user_data("purge_victim")
    assert get_user_by_username("purge_bystander") is not None


def test_purge_elimina_miembros_del_workspace(patch_data_dir):
    _make_user("purge_member_owner")
    _make_user("purge_member_user")
    ws = _workspace_storage()
    created = ws.create("Workspace compartido", created_by="purge_member_owner")
    ws.add_member(created["id"], "purge_member_user")
    purge_user_data("purge_member_user")
    members = ws.list_members(created["id"])
    assert not any(m["username"] == "purge_member_user" for m in members)
    assert ws.get(created["id"]) is not None


def test_purge_elimina_agents_del_filesystem(patch_data_dir, tmp_path, monkeypatch):
    import json
    agents_dir = tmp_path / "agents_purge"
    (agents_dir / "private").mkdir(parents=True)
    agent_dir = agents_dir / "private" / "agent-test"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"id": "agent-test", "owner_id": "purge_fs_user"}), encoding="utf-8"
    )
    import app.auth.auth as auth_mod
    monkeypatch.setattr(auth_mod, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(auth_mod, "SKILLS_DIR", tmp_path / "skills_purge")
    (tmp_path / "skills_purge").mkdir()

    _make_user("purge_fs_user")
    purge_user_data("purge_fs_user")
    assert not agent_dir.exists()


# ── purge_expired_deletions ───────────────────────────────────────────────────

def test_purge_expired_no_borra_activos(patch_data_dir):
    _make_user("expired_active")
    schedule_user_deletion("expired_active")  # 30 días en el futuro
    count = purge_expired_deletions()
    assert count == 0
    assert get_user_by_username("expired_active") is not None


def test_purge_expired_borra_caducados(patch_data_dir):
    _make_user("expired_past")
    schedule_user_deletion("expired_past")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _set_deletion_date("expired_past", past)
    count = purge_expired_deletions()
    assert count == 1
    assert get_user_by_username("expired_past") is None


def test_purge_expired_no_toca_usuarios_sin_solicitud(patch_data_dir):
    _make_user("expired_nosched")
    purge_expired_deletions()
    assert get_user_by_username("expired_nosched") is not None


def test_purge_expired_devuelve_conteo_correcto(patch_data_dir):
    for i in range(3):
        _make_user(f"expired_multi_{i}")
        schedule_user_deletion(f"expired_multi_{i}")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        _set_deletion_date(f"expired_multi_{i}", past)
    count = purge_expired_deletions()
    assert count == 3
