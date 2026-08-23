"""Tests unitarios para las funciones GDPR en auth.py:
schedule_user_deletion, cancel_user_deletion, purge_user_data,
purge_expired_deletions y get_owned_groups.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.auth.auth import get_user_by_username, register_user
from app.auth.gdpr import (
    cancel_user_deletion,
    get_owned_groups,
    purge_expired_deletions,
    purge_user_data,
    schedule_user_deletion,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_user(username: str, email: str | None = None) -> None:
    await register_user(username, "pass1234", email=email or f"{username}@test.com")


async def _user_id(username: str) -> str:
    user = await get_user_by_username(username)
    assert user is not None
    return str(user["id"])


async def _set_deletion_date(username: str, dt: datetime) -> None:
    from app.storage.db import open_db
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET deletion_requested_at = ? WHERE username = ?",
            (dt.isoformat(), username),
        )
        await conn.commit()


async def _group_storage():
    from app.storage.groups import GroupStorage
    return GroupStorage()


# ── get_owned_groups ──────────────────────────────────────────────────────

async def test_get_owned_groups_sin_groups(patch_data_dir):
    await _make_user("gogroup_solo")
    assert await get_owned_groups("gogroup_solo") == []


async def test_get_owned_groups_con_group(patch_data_dir):
    await _make_user("gogroup_owner")
    group = await _group_storage()
    created = await group.create("Mi Equipo", created_by=await _user_id("gogroup_owner"))
    owned = await get_owned_groups("gogroup_owner")
    assert len(owned) == 1
    assert owned[0]["id"] == created["id"]
    assert owned[0]["name"] == "Mi Equipo"


async def test_get_owned_groups_no_devuelve_los_que_no_son_suyos(patch_data_dir):
    await _make_user("gogroup_other_owner")
    await _make_user("gogroup_member")
    group = await _group_storage()
    await group.create("Equipo ajeno", created_by=await _user_id("gogroup_other_owner"))
    assert await get_owned_groups("gogroup_member") == []


# ── schedule_user_deletion ────────────────────────────────────────────────────

async def test_schedule_deletion_devuelve_token(patch_data_dir):
    await _make_user("sched_basic")
    token = await schedule_user_deletion("sched_basic")
    assert token and len(token) > 10


async def test_schedule_deletion_persiste_en_bd(patch_data_dir):
    await _make_user("sched_persist")
    await schedule_user_deletion("sched_persist")
    user = await get_user_by_username("sched_persist")
    assert user["deletion_requested_at"] is not None
    assert user["deletion_token"] is not None


async def test_schedule_deletion_fecha_aprox_30_dias(patch_data_dir):
    await _make_user("sched_date")
    await schedule_user_deletion("sched_date")
    user = await get_user_by_username("sched_date")
    deletion_dt = datetime.fromisoformat(user["deletion_requested_at"])
    now = datetime.now(timezone.utc)
    delta = deletion_dt - now
    assert timedelta(days=29) < delta < timedelta(days=31)


# ── cancel_user_deletion ──────────────────────────────────────────────────────

async def test_cancel_deletion_ok(patch_data_dir):
    await _make_user("cancel_ok")
    token = await schedule_user_deletion("cancel_ok")
    assert await cancel_user_deletion(token) is True
    user = await get_user_by_username("cancel_ok")
    assert user["deletion_requested_at"] is None
    assert user["deletion_token"] is None


async def test_cancel_deletion_token_invalido(patch_data_dir):
    assert await cancel_user_deletion("token-que-no-existe") is False


async def test_cancel_deletion_token_usado_dos_veces(patch_data_dir):
    await _make_user("cancel_twice")
    token = await schedule_user_deletion("cancel_twice")
    assert await cancel_user_deletion(token) is True
    assert await cancel_user_deletion(token) is False


# ── purge_user_data ───────────────────────────────────────────────────────────

async def test_purge_elimina_usuario_de_bd(patch_data_dir):
    await _make_user("purge_basic")
    await purge_user_data("purge_basic")
    assert await get_user_by_username("purge_basic") is None


async def test_purge_elimina_group_propio(patch_data_dir):
    await _make_user("purge_group_owner")
    group = await _group_storage()
    created = await group.create("Group a purgar", created_by=await _user_id("purge_group_owner"))
    await purge_user_data("purge_group_owner")
    assert await group.get(created["id"]) is None


async def test_purge_no_afecta_a_otros_usuarios(patch_data_dir):
    await _make_user("purge_victim")
    await _make_user("purge_bystander")
    await purge_user_data("purge_victim")
    assert await get_user_by_username("purge_bystander") is not None


async def test_purge_elimina_orquestaciones_y_bindings_privados(patch_data_dir):
    await _make_user("purge_llm_victim")
    await _make_user("purge_llm_bystander")
    victim_id = await _user_id("purge_llm_victim")
    bystander_id = await _user_id("purge_llm_bystander")
    from app.storage.db import open_db

    async with open_db() as conn:
        for item_id, owner_id in (
            ("victim-route", victim_id),
            ("bystander-route", bystander_id),
        ):
            await conn.execute(
                "INSERT INTO llm_orchestrations "
                "(id, owner_id, name, description, definition, labels, "
                "is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, '', ?, '[\"private\"]', 1, ?, ?)",
                (item_id, owner_id, item_id, '{"mode":"stack","candidates":[]}', "now", "now"),
            )
        for orchestration_id, user_id in (
            ("victim-route", bystander_id),
            ("bystander-route", victim_id),
        ):
            await conn.execute(
                "INSERT INTO llm_orchestration_bindings "
                "(orchestration_id, user_id, definition, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (orchestration_id, user_id, '{"candidates":[]}', "now", "now"),
            )
        await conn.commit()

    await purge_user_data("purge_llm_victim")

    async with open_db() as conn:
        assert not await conn.fetchval(
            "SELECT 1 FROM llm_orchestrations WHERE id = ?", ("victim-route",)
        )
        assert await conn.fetchval(
            "SELECT 1 FROM llm_orchestrations WHERE id = ?", ("bystander-route",)
        )
        assert not await conn.fetchval(
            "SELECT 1 FROM llm_orchestration_bindings "
            "WHERE orchestration_id IN (?, ?)",
            ("victim-route", "bystander-route"),
        )


async def test_purge_elimina_miembros_del_group(patch_data_dir):
    await _make_user("purge_member_owner")
    await _make_user("purge_member_user")
    group = await _group_storage()
    created = await group.create("Group compartido", created_by=await _user_id("purge_member_owner"))
    await group.add_member(created["id"], await _user_id("purge_member_user"))
    await purge_user_data("purge_member_user")
    members = await group.list_members(created["id"])
    assert not any(m["username"] == "purge_member_user" for m in members)
    assert await group.get(created["id"]) is not None


async def test_purge_elimina_los_recursos_añadidos_después_del_borrado(patch_data_dir):
    """Prompts, tools, memoria y packs no tienen REFERENCES users: si el DELETE
    no los nombra, se quedan con un owner_id que ya no apunta a nadie."""
    from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
    from app.storage.agent_storage import AgentStorage
    from app.storage.db import open_db
    from app.storage.memory_storage import MemoryStorage
    from app.storage.prompt_storage import PromptStorage
    from app.storage.tool_storage import ToolStorage

    await _make_user("purge_recursos")
    owner = await _user_id("purge_recursos")

    await AgentStorage(AGENTS_DIR).save({"name": "Agente"}, owner_id=owner)
    await PromptStorage().save(
        "private", {"name": "P", "alias": "purgealias", "content": "x"},
        owner_id=owner,
    )
    await ToolStorage().save(
        "private", {"name": "T", "language": "python", "content": "x"},
        owner_id=owner,
    )
    await MemoryStorage(MEMORY_DIR).save("notas", "contenido", owner_id=owner)
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO knowledge_packs (id, owner_id, name, created_at, updated_at) "
            "VALUES (?, ?, 'Pack', 'now', 'now')",
            ("pack-purge", owner),
        )
        await conn.execute(
            "INSERT INTO resource_versions "
            "(id, resource_type, resource_id, owner_id, version, snapshot, "
            "created_by, created_at) "
            "VALUES (?, 'agent', 'a1', ?, 1, '{}', ?, 'now')",
            ("ver-purge", owner, owner),
        )
        # El enlace declara REFERENCES official_sources: la fuente tiene que
        # existir antes, y sobrevive al usuario (es catálogo de administración).
        await conn.execute(
            "INSERT INTO official_sources "
            "(id, name, repository_url, created_at, updated_at) "
            "VALUES ('src', 'Fuente', 'https://example.test/r', 'now', 'now')"
        )
        await conn.execute(
            "INSERT INTO resource_source_links "
            "(source_id, component_key, resource_type, resource_id, "
            "resource_owner_id, created_at, updated_at) "
            "VALUES ('src', 'comp', 'agent', 'a1', ?, 'now', 'now')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO resource_labels "
            "(resource_type, resource_id, owner_id, label) "
            "VALUES ('agent', 'a1', ?, 'lang_es')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO resource_social "
            "(resource_type, resource_id, owner, name) "
            "VALUES ('agent', 'a1', ?, 'Publicado')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO resource_stars (username, resource_type, resource_id) "
            "VALUES (?, 'agent', 'other-agent')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO user_follows (follower, following) VALUES (?, 'other')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO user_follows (follower, following) VALUES ('other', ?)",
            (owner,),
        )
        await conn.commit()

    await purge_user_data("purge_recursos")

    async with open_db() as conn:
        for tabla, columna in (
            ("agents", "owner_id"),
            ("skills", "owner_id"),
            ("prompts", "owner_id"),
            ("tools", "owner_id"),
            ("memory_files", "owner_id"),
            ("knowledge_packs", "owner_id"),
            ("resource_versions", "owner_id"),
            ("resource_source_links", "resource_owner_id"),
            ("resource_labels", "owner_id"),
            ("resource_social", "owner"),
            ("resource_stars", "username"),
        ):
            assert not await conn.fetchval(
                f"SELECT 1 FROM {tabla} WHERE {columna} = ?", (owner,)
            ), f"{tabla} conserva filas del usuario borrado"
        assert not await conn.fetchval(
            "SELECT 1 FROM user_follows WHERE follower = ? OR following = ?",
            (owner, owner),
        )

    assert SKILLS_DIR is not None  # el directorio legacy no interviene ya


async def test_purge_elimina_credenciales_facturación_y_ejecuciones(patch_data_dir):
    """Las tablas con `username`/`started_by` forman parte de la misma purga."""
    from app.storage.db import open_db

    await _make_user("purge_legacy_owner")
    await _make_user("purge_legacy_bystander")
    victim = await _user_id("purge_legacy_owner")
    bystander = await _user_id("purge_legacy_bystander")

    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO personal_access_tokens "
            "(id, username, name, token_hash, prefix, created_at) "
            "VALUES ('pat-v', ?, 'CLI', 'hash-v', 'iah_v', 'now')",
            (victim,),
        )
        await conn.execute(
            "INSERT INTO vscode_auth_codes (code_hash, username, state, expires_at) "
            "VALUES ('code-v', ?, 'state', 'later')",
            (victim,),
        )
        for sub_id, owner in (("sub-v", victim), ("sub-b", bystander)):
            await conn.execute(
                "INSERT INTO subscriptions "
                "(id, username, stripe_customer_id, stripe_subscription_id, tier, "
                "seats, self_hosted, interval, amount_cents, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pro', 2, 0, 'month', 1000, 'active', 'now', 'now')",
                (sub_id, owner, f"cus-{sub_id}", f"stripe-{sub_id}"),
            )
        for sub_id, username, assigned_by, status in (
            ("sub-v", bystander, victim, "revoked"),
            ("sub-b", victim, bystander, "revoked"),
            ("sub-b", bystander, bystander, "active"),
        ):
            await conn.execute(
                "INSERT INTO subscription_license_assignments "
                "(subscription_id, username, assigned_by, assigned_at, status) "
                "VALUES (?, ?, ?, 'now', ?)",
                (sub_id, username, assigned_by, status),
            )
        await conn.execute(
            "INSERT INTO workflow_runs "
            "(id, workflow_id, started_by, group_id, workflow_name, definition, "
            "agents, input, status, heartbeat_at, created_at, updated_at) "
            "VALUES ('run-v', 'wf', ?, ?, 'WF', '{}', '[]', 'dato', "
            "'completed', 'now', 'now', 'now')",
            (victim, victim),
        )
        await conn.execute(
            "INSERT INTO workflow_run_events (run_id, sequence, payload, created_at) "
            "VALUES ('run-v', 1, '{\"output\":\"dato\"}', 'now')"
        )
        await conn.commit()

    await purge_user_data("purge_legacy_owner")

    async with open_db() as conn:
        for tabla, columna in (
            ("personal_access_tokens", "username"),
            ("vscode_auth_codes", "username"),
            ("subscriptions", "username"),
            ("workflow_runs", "started_by"),
        ):
            assert not await conn.fetchval(
                f"SELECT 1 FROM {tabla} WHERE {columna} = ?", (victim,)
            )
        assert not await conn.fetchval(
            "SELECT 1 FROM workflow_run_events WHERE run_id = 'run-v'"
        )
        assert await conn.fetchval(
            "SELECT 1 FROM subscriptions WHERE username = ?", (bystander,)
        )
        rows = await conn.fetchall(
            "SELECT username, assigned_by FROM subscription_license_assignments"
        )
        assert [tuple(row) for row in rows] == [(bystander, bystander)]


async def test_purge_no_toca_los_recursos_de_otro_usuario(patch_data_dir):
    from app.storage.prompt_storage import PromptStorage

    await _make_user("purge_res_victima")
    await _make_user("purge_res_testigo")
    testigo = await _user_id("purge_res_testigo")
    await PromptStorage().save(
        "private", {"name": "P", "alias": "testigoalias", "content": "x"},
        owner_id=testigo,
    )
    await PromptStorage().save(
        "private", {"name": "P", "alias": "victimaalias", "content": "x"},
        owner_id=await _user_id("purge_res_victima"),
    )

    await purge_user_data("purge_res_victima")

    from app.storage.db import open_db
    async with open_db() as conn:
        assert await conn.fetchval(
            "SELECT 1 FROM prompts WHERE owner_id = ?", (testigo,)
        )


async def test_purge_elimina_agents_del_filesystem(patch_data_dir, tmp_path, monkeypatch):
    import json
    agents_dir = tmp_path / "agents_purge"
    (agents_dir / "private").mkdir(parents=True)
    agent_dir = agents_dir / "private" / "agent-test"
    agent_dir.mkdir()
    import app.auth.gdpr as gdpr_mod
    monkeypatch.setattr(gdpr_mod, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(gdpr_mod, "SKILLS_DIR", tmp_path / "skills_purge")
    (tmp_path / "skills_purge").mkdir()

    await _make_user("purge_fs_user")
    (agent_dir / "config.json").write_text(
        json.dumps({"id": "agent-test", "owner_id": await _user_id("purge_fs_user")}),
        encoding="utf-8",
    )
    await purge_user_data("purge_fs_user")
    assert not agent_dir.exists()


# ── purge_expired_deletions ───────────────────────────────────────────────────

async def test_purge_expired_no_borra_activos(patch_data_dir):
    await _make_user("expired_active")
    await schedule_user_deletion("expired_active")
    count = await purge_expired_deletions()
    assert count == 0
    assert await get_user_by_username("expired_active") is not None


async def test_purge_expired_borra_caducados(patch_data_dir):
    await _make_user("expired_past")
    await schedule_user_deletion("expired_past")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _set_deletion_date("expired_past", past)
    count = await purge_expired_deletions()
    assert count == 1
    assert await get_user_by_username("expired_past") is None


async def test_purge_expired_no_toca_usuarios_sin_solicitud(patch_data_dir):
    await _make_user("expired_nosched")
    await purge_expired_deletions()
    assert await get_user_by_username("expired_nosched") is not None


async def test_purge_expired_devuelve_conteo_correcto(patch_data_dir):
    for i in range(3):
        await _make_user(f"expired_multi_{i}")
        await schedule_user_deletion(f"expired_multi_{i}")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        await _set_deletion_date(f"expired_multi_{i}", past)
    count = await purge_expired_deletions()
    assert count == 3
