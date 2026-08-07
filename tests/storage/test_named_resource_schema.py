"""Regression tests for canonical resource names and compact JSON blobs."""

from __future__ import annotations

import json
import sqlite3

from app.config.data import AGENTS_DIR, SKILLS_DIR
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.skill_storage import SkillStorage
from app.storage.workflows import WorkflowStorage

_DUPLICATE_KEYS = {
    "id",
    "name",
    "owner_id",
    "resource_type",
    "scope",
    "tokens_in",
    "tokens_out",
    "is_active",
    "deactivated_at",
    "created_at",
    "updated_at",
}

_COMMON_RESOURCE_KEYS = {
    "id",
    "name",
    "resource_type",
    "scope",
    "owner_id",
    "labels",
    "is_active",
    "created_at",
    "updated_at",
}


async def test_all_managed_resources_expose_common_contract(
    patch_data_dir,  # noqa: ARG001
):
    resources = [
        await AgentStorage(AGENTS_DIR).save({"name": "Agente"}, owner_id="alice"),
        await SkillStorage(SKILLS_DIR).save(
            "private", {"name": "Skill", "content": "x"}, owner_id="alice"
        ),
        await ConnectionStorage().save(
            {"name": "Conexión", "type": "openai"}, owner_id="alice"
        ),
        await KnowledgeStorage().save(
            type="text",
            title="Conocimiento",
            source="manual",
            content="contenido",
            owner_id="alice",
        ),
        await WorkflowStorage().save(
            "alice",
            {
                "name": "Workflow",
                "definition": {"nodes": [], "edges": []},
            },
        ),
    ]

    for resource in resources:
        assert _COMMON_RESOURCE_KEYS <= resource.keys()


async def test_named_resources_store_metadata_once(patch_data_dir):  # noqa: ARG001
    agents = AgentStorage(AGENTS_DIR)
    skills = SkillStorage(SKILLS_DIR)
    connections = ConnectionStorage()

    agent = await agents.save({"name": "Agente SQL"}, owner_id="alice")
    skill = await skills.save(
        "private", {"name": "Skill SQL", "content": "contenido"}, owner_id="alice"
    )
    connection = await connections.save(
        {"name": "Conexión SQL", "type": "openai"}, owner_id="alice"
    )

    async with open_db() as conn:
        rows = {
            "agents": await conn.fetchone(
                "SELECT name, data FROM agents WHERE id=?", (agent["id"],)
            ),
            "skills": await conn.fetchone(
                "SELECT name, data FROM skills WHERE id=?", (skill["id"],)
            ),
            "connections": await conn.fetchone(
                "SELECT name, data FROM connections WHERE id=?", (connection["id"],)
            ),
        }

    assert rows["agents"]["name"] == "Agente SQL"
    assert rows["skills"]["name"] == "Skill SQL"
    assert rows["connections"]["name"] == "Conexión SQL"
    for row in rows.values():
        assert _DUPLICATE_KEYS.isdisjoint(json.loads(row["data"]))

    assert (await agents.get(agent["id"]))["name"] == "Agente SQL"
    assert (await skills.get("private", skill["id"]))["name"] == "Skill SQL"
    assert (await connections.get(connection["id"]))["name"] == "Conexión SQL"


async def test_migration_backfills_names_and_compacts_legacy_blobs(tmp_path):
    import app.storage.db as db_mod

    db = tmp_path / "legacy-resource-names.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE agents (
            id TEXT NOT NULL, owner_id TEXT NOT NULL, scope TEXT NOT NULL,
            data TEXT NOT NULL, tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(id, owner_id)
        );
        CREATE TABLE skills (
            id TEXT NOT NULL, owner_id TEXT NOT NULL, scope TEXT NOT NULL,
            data TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(id, owner_id)
        );
        CREATE TABLE connections (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, data TEXT NOT NULL,
            tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO agents VALUES (
            'a1', 'alice', 'private',
            '{"id":"a1","name":"Agente legado","scope":"private","model":"gpt"}',
            0, 0, 'old', 'old'
        );
        INSERT INTO skills VALUES (
            's1', 'alice', 'private',
            '{"id":"s1","name":"Skill legada","owner_id":"alice","icon":"x"}',
            'contenido', 'old', 'old'
        );
        INSERT INTO connections VALUES (
            'c1', 'alice',
            '{"id":"c1","label":"Conexión legada","type":"openai","created_at":"old"}',
            0, 0, 'old', 'old'
        );
    """)
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    migrated.row_factory = sqlite3.Row
    expected = {
        "agents": "Agente legado",
        "skills": "Skill legada",
        "connections": "Conexión legada",
    }
    for table, name in expected.items():
        row = migrated.execute(f"SELECT name, data FROM {table}").fetchone()
        assert row["name"] == name
        assert _DUPLICATE_KEYS.isdisjoint(json.loads(row["data"]))
        indexes = {
            item[1] for item in migrated.execute(f"PRAGMA index_list({table})")
        }
        assert f"idx_{table}_owner_name" in indexes
    migrated.close()


async def test_migration_replaces_binary_group_status_with_flag(tmp_path):
    import app.storage.db as db_mod

    db = tmp_path / "legacy-group-status.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE groups (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_by TEXT NOT NULL,
            created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            deactivated_at TEXT
        );
        INSERT INTO groups VALUES ('g1', 'Activo', 'alice', 'old', 'active', NULL);
        INSERT INTO groups VALUES ('g2', 'Desactivado', 'alice', 'old', 'disabled', 'old');
    """)
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    columns = {
        row[1]: row[2] for row in migrated.execute("PRAGMA table_info(groups)")
    }
    rows = dict(migrated.execute("SELECT id, is_active FROM groups"))
    migrated.close()

    assert columns["is_active"] == "INTEGER"
    assert "status" not in columns
    assert "deactivated_at" not in columns
    assert rows == {"g1": 1, "g2": 0}


async def test_migration_removes_unused_team_tables(tmp_path):
    import app.storage.db as db_mod

    db = tmp_path / "legacy-teams.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE teams (id TEXT PRIMARY KEY);
        CREATE TABLE team_members (team_id TEXT);
        CREATE TABLE team_invitations (team_id TEXT);
        CREATE TABLE resource_teams (team_id TEXT);
    """)
    conn.commit()
    conn.close()

    await db_mod.migrate_schema(db)

    migrated = sqlite3.connect(str(db))
    tables = {
        row[0]
        for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    migrated.close()
    assert {
        "teams",
        "team_members",
        "team_invitations",
        "resource_teams",
    }.isdisjoint(tables)
