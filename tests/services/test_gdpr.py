"""Tests del servicio GDPR: export_user_data y _collect_file_owned."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

import app.config.data as _cfg
from app.auth.auth import register_user
from app.services.gdpr import _collect_file_owned, export_user_data

# ── Fixture: parcha gdpr.DB_FILE con la BD de test ───────────────────────────

@pytest.fixture(autouse=True)
def patch_gdpr_db(patch_data_dir, monkeypatch):
    import app.services.gdpr as gdpr_mod
    # gdpr.py uses open_db() — no DB_FILE to patch; just patch dir paths
    monkeypatch.setattr(gdpr_mod, "AGENTS_DIR", _cfg.AGENTS_DIR)
    monkeypatch.setattr(gdpr_mod, "SKILLS_DIR", _cfg.SKILLS_DIR)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_user(username: str, email: str | None = None) -> None:
    await register_user(username, "pass1234", email=email or f"{username}@test.com")


async def _insert_conversation(username: str, title: str = "Test conv") -> str:
    from uuid import uuid4

    from app.storage.db import open_db
    conv_id = uuid4().hex[:12]
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, user_id, title, agent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'agent', datetime('now'), datetime('now'))",
            (conv_id, username, title),
        )
        await conn.commit()
    return conv_id


async def _group_storage():
    from app.storage.groups import GroupStorage
    return GroupStorage(_cfg.DB_FILE)


def _zip_names(buf: io.BytesIO) -> list:
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        return zf.namelist()


def _zip_read(buf: io.BytesIO, name: str) -> str:
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        return zf.read(name).decode()


# ── export_user_data: estructura básica ───────────────────────────────────────

async def test_export_devuelve_bytesio(patch_gdpr_db):
    await _make_user("exp_basic")
    result = await export_user_data("exp_basic")
    assert isinstance(result, io.BytesIO)


async def test_export_es_zip_valido(patch_gdpr_db):
    await _make_user("exp_zip")
    buf = await export_user_data("exp_zip")
    assert zipfile.is_zipfile(buf)


async def test_export_contiene_ficheros_requeridos(patch_gdpr_db):
    await _make_user("exp_files")
    buf = await export_user_data("exp_files")
    names = _zip_names(buf)
    for expected in ("profile.json", "connections.json", "knowledge.json",
                     "token_usage.json", "groups.json", "accounts.json",
                     "agents.json", "skills.json"):
        assert expected in names, f"Falta {expected} en el ZIP"


# ── profile.json ─────────────────────────────────────────────────────────────

async def test_export_profile_contiene_username(patch_gdpr_db):
    await _make_user("exp_profile")
    buf = await export_user_data("exp_profile")
    profile = json.loads(_zip_read(buf, "profile.json"))
    assert profile["username"] == "exp_profile"


async def test_export_profile_no_contiene_password_hash(patch_gdpr_db):
    await _make_user("exp_nopwd")
    buf = await export_user_data("exp_nopwd")
    profile = json.loads(_zip_read(buf, "profile.json"))
    assert "password_hash" not in profile


# ── conversations ─────────────────────────────────────────────────────────────

async def test_export_conversaciones_incluidas(patch_gdpr_db):
    await _make_user("exp_conv")
    await _insert_conversation("exp_conv", "Mi conversación")
    buf = await export_user_data("exp_conv")
    conv_files = [n for n in _zip_names(buf) if n.startswith("conversations/")]
    assert len(conv_files) == 1


async def test_export_conversaciones_sin_datos_de_otros(patch_gdpr_db):
    await _make_user("exp_conv_owner")
    await _make_user("exp_conv_other")
    await _insert_conversation("exp_conv_other", "Ajena")
    buf = await export_user_data("exp_conv_owner")
    conv_files = [n for n in _zip_names(buf) if n.startswith("conversations/")]
    assert len(conv_files) == 0


async def test_export_sin_conversaciones(patch_gdpr_db):
    await _make_user("exp_noconv")
    buf = await export_user_data("exp_noconv")
    conv_files = [n for n in _zip_names(buf) if n.startswith("conversations/")]
    assert len(conv_files) == 0


# ── groups.json ───────────────────────────────────────────────────────────

async def test_export_groups_incluye_membresías(patch_gdpr_db):
    await _make_user("exp_group_owner")
    await _make_user("exp_group_member")
    group = await _group_storage()
    created = await group.create("Equipo Export", created_by="exp_group_owner")
    await group.add_member(created["id"], "exp_group_member")

    buf = await export_user_data("exp_group_member")
    groups = json.loads(_zip_read(buf, "groups.json"))
    assert any(w["id"] == created["id"] for w in groups)


async def test_export_groups_no_incluye_los_ajenos(patch_gdpr_db):
    await _make_user("exp_group_noaccess")
    await _make_user("exp_group_other_owner")
    group = await _group_storage()
    await group.create("Equipo Ajeno", created_by="exp_group_other_owner")

    buf = await export_user_data("exp_group_noaccess")
    groups = json.loads(_zip_read(buf, "groups.json"))
    assert groups == []


# ── _collect_file_owned ───────────────────────────────────────────────────────

def test_collect_file_owned_devuelve_vacios_si_no_hay_dir(tmp_path):
    result = _collect_file_owned(tmp_path / "no-existe", "any_user")
    assert result == []


def test_collect_file_owned_devuelve_items_del_owner(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    item = private / "item-1"
    item.mkdir()
    (item / "config.json").write_text(
        json.dumps({"id": "item-1", "owner_id": "usuario_a"}), encoding="utf-8"
    )
    result = _collect_file_owned(tmp_path, "usuario_a")
    assert len(result) == 1
    assert result[0]["id"] == "item-1"


def test_collect_file_owned_ignora_otros_owners(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    item = private / "item-otro"
    item.mkdir()
    (item / "config.json").write_text(
        json.dumps({"id": "item-otro", "owner_id": "otro_usuario"}), encoding="utf-8"
    )
    result = _collect_file_owned(tmp_path, "usuario_a")
    assert result == []


def test_collect_file_owned_incluye_private_y_public(tmp_path):
    for scope in ("private", "public"):
        d = tmp_path / scope / f"item-{scope}"
        d.mkdir(parents=True)
        (d / "config.json").write_text(
            json.dumps({"id": f"item-{scope}", "owner_id": "multiowner"}), encoding="utf-8"
        )
    result = _collect_file_owned(tmp_path, "multiowner")
    assert len(result) == 2


def test_collect_file_owned_ignora_json_invalido(tmp_path):
    private = tmp_path / "private" / "bad-item"
    private.mkdir(parents=True)
    (private / "config.json").write_text("no-es-json", encoding="utf-8")
    result = _collect_file_owned(tmp_path, "anyuser")
    assert result == []
