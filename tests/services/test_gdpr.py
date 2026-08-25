"""Tests del servicio GDPR: export_user_data.

Los tests de aquí afirman **contenido**, no presencia. Comprobar que
`agents.json` está en el ZIP no distinguía una exportación correcta de una
vacía, y eso es exactamente lo que pasaba: los recursos se leían de
`AGENTS_DIR`, un directorio que no existe desde que viven en la base de datos.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.auth.auth import register_user
from app.services.gdpr import export_user_data


@pytest.fixture(autouse=True)
def patch_gdpr_db(patch_data_dir):
    """Toda la exportación sale de open_db(); basta con aislar el DATA_DIR."""
    return patch_data_dir


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_user(username: str, email: str | None = None) -> None:
    await register_user(username, "pass1234", email=email or f"{username}@test.com")


async def _user_id(username: str) -> str:
    from app.auth.auth import get_user_by_username
    user = await get_user_by_username(username)
    assert user is not None
    return str(user["id"])


async def _insert_conversation(username: str, title: str = "Test conv") -> str:
    from uuid import uuid4

    from app.storage.db import open_db
    conv_id = uuid4().hex[:12]
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, user_id, title, agent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'agent', datetime('now'), datetime('now'))",
            (conv_id, await _user_id(username), title),
        )
        await conn.commit()
    return conv_id


async def _group_storage():
    from app.storage.groups import GroupStorage
    return GroupStorage()


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
                     "agents.json", "skills.json", "prompts.json", "tools.json",
                     "workflows.json", "knowledge_packs.json", "memory.json",
                     "stars.json", "follows.json", "personal_access_tokens.json",
                     "subscriptions.json", "subscription_license_assignments.json",
                     "workflow_runs.json", "workflow_run_events.json"):
        assert expected in names, f"Falta {expected} en el ZIP"


# ── profile.json ─────────────────────────────────────────────────────────────

async def test_export_profile_contiene_username(patch_gdpr_db):
    await _make_user("exp_profile")
    buf = await export_user_data("exp_profile")
    profile = json.loads(_zip_read(buf, "profile.json"))
    assert profile["username"] == "exp_profile"


async def test_export_profile_json_invalido_se_conserva_y_avisa(
    patch_gdpr_db, monkeypatch
):
    import app.services.gdpr as gdpr_mod
    from app.storage.db import open_db

    await _make_user("exp_bad_prefs")
    user_id = await _user_id("exp_bad_prefs")
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET preferences=? WHERE id=?", ("no-es-json", user_id)
        )
        await conn.commit()

    warnings = []
    monkeypatch.setattr(gdpr_mod.flog, "warning", warnings.append)

    buf = await export_user_data("exp_bad_prefs")
    profile = json.loads(_zip_read(buf, "profile.json"))
    assert profile["preferences"] == "no-es-json"
    assert any("Preferencias no normalizadas" in message for message in warnings)


async def test_export_profile_no_contiene_password_hash(patch_gdpr_db):
    await _make_user("exp_nopwd")
    buf = await export_user_data("exp_nopwd")
    profile = json.loads(_zip_read(buf, "profile.json"))
    assert "password_hash" not in profile


async def test_export_credenciales_facturación_y_workflows_sin_hash(patch_gdpr_db):
    from app.storage.db import open_db

    await _make_user("exp_legacy_owner")
    owner = await _user_id("exp_legacy_owner")
    async with open_db() as conn:
        await conn.execute(
            "INSERT INTO personal_access_tokens "
            "(id, username, name, token_hash, prefix, created_at) "
            "VALUES ('pat-exp', ?, 'CLI', 'hash-secreto', 'iah_visible', 'now')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO subscriptions "
            "(id, username, stripe_customer_id, stripe_subscription_id, tier, "
            "seats, self_hosted, interval, amount_cents, status, created_at, updated_at) "
            "VALUES ('sub-exp', ?, 'cus-exp', 'stripe-exp', 'pro', 1, 0, "
            "'month', 1000, 'active', 'now', 'now')",
            (owner,),
        )
        await conn.execute(
            "INSERT INTO subscription_license_assignments "
            "(subscription_id, username, assigned_by, assigned_at, status) "
            "VALUES ('sub-exp', ?, ?, 'now', 'active')",
            (owner, owner),
        )
        await conn.execute(
            "INSERT INTO workflow_runs "
            "(id, workflow_id, started_by, group_id, workflow_name, definition, "
            "agents, input, status, heartbeat_at, created_at, updated_at) "
            "VALUES ('run-exp', 'wf', ?, ?, 'WF', '{\"nodes\":[]}', '[]', "
            "'pregunta', 'completed', 'now', 'now', 'now')",
            (owner, owner),
        )
        await conn.execute(
            "INSERT INTO workflow_run_events (run_id, sequence, payload, created_at) "
            "VALUES ('run-exp', 1, '{\"answer\":42}', 'now')"
        )
        await conn.commit()

    buf = await export_user_data("exp_legacy_owner")

    pats = json.loads(_zip_read(buf, "personal_access_tokens.json"))
    assert pats == [{
        "id": "pat-exp",
        "name": "CLI",
        "prefix": "iah_visible",
        "created_at": "now",
        "expires_at": None,
        "last_used_at": None,
        "revoked_at": None,
    }]
    assert "hash-secreto" not in _zip_read(buf, "personal_access_tokens.json")
    assert json.loads(_zip_read(buf, "subscriptions.json"))[0]["id"] == "sub-exp"
    assignments = json.loads(
        _zip_read(buf, "subscription_license_assignments.json")
    )
    assert len(assignments) == 1
    runs = json.loads(_zip_read(buf, "workflow_runs.json"))
    assert runs[0]["definition"] == {"nodes": []}
    events = json.loads(_zip_read(buf, "workflow_run_events.json"))
    assert events[0]["payload"] == {"answer": 42}


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
    created = await group.create("Equipo Export", created_by=await _user_id("exp_group_owner"))
    await group.add_member(created["id"], await _user_id("exp_group_member"))

    buf = await export_user_data("exp_group_member")
    groups = json.loads(_zip_read(buf, "groups.json"))
    assert any(w["id"] == created["id"] for w in groups)


async def test_export_groups_no_incluye_los_ajenos(patch_gdpr_db):
    await _make_user("exp_group_noaccess")
    await _make_user("exp_group_other_owner")
    group = await _group_storage()
    await group.create("Equipo Ajeno", created_by=await _user_id("exp_group_other_owner"))

    buf = await export_user_data("exp_group_noaccess")
    groups = json.loads(_zip_read(buf, "groups.json"))
    assert groups == []


# ── Recursos: el ZIP entrega contenido, no ficheros vacíos ────────────────────

async def _crear_recursos(username: str) -> dict:
    """Un recurso de cada tipo para el usuario. Devuelve {tipo: recurso}."""
    from app.config.data import AGENTS_DIR, MEMORY_DIR, SKILLS_DIR
    from app.storage.agent_storage import AgentStorage
    from app.storage.memory_storage import MemoryStorage
    from app.storage.prompt_storage import PromptStorage
    from app.storage.skill_storage import SkillStorage
    from app.storage.tool_storage import ToolStorage
    from app.storage.workflows import WorkflowStorage

    owner = await _user_id(username)
    return {
        "agent": await AgentStorage(AGENTS_DIR).save(
            {"name": "Agente exportable"}, owner_id=owner
        ),
        "skill": await SkillStorage(SKILLS_DIR).save(
            "private", {"name": "Skill exportable", "content": "x"}, owner_id=owner
        ),
        "prompt": await PromptStorage().save(
            "private",
            {"name": "Prompt exportable", "alias": "expalias", "content": "hola"},
            owner_id=owner,
        ),
        "tool": await ToolStorage().save(
            "private",
            {"name": "Tool exportable", "language": "python", "content": "print(1)"},
            owner_id=owner,
        ),
        "workflow": await WorkflowStorage().save(
            owner,
            {"name": "Workflow exportable", "definition": {"nodes": [], "edges": []}},
        ),
        "memory": await MemoryStorage(MEMORY_DIR).save(
            "recuerdos", "lo que dijo el usuario", owner_id=owner
        ),
    }


async def test_export_agents_json_contiene_el_agente(patch_gdpr_db):
    await _make_user("exp_res_agent")
    creados = await _crear_recursos("exp_res_agent")
    buf = await export_user_data("exp_res_agent")
    agents = json.loads(_zip_read(buf, "agents.json"))
    assert [a["id"] for a in agents] == [creados["agent"]["id"]]
    assert agents[0]["name"] == "Agente exportable"


async def test_export_entrega_todos_los_tipos_de_recurso(patch_gdpr_db):
    await _make_user("exp_res_todos")
    await _crear_recursos("exp_res_todos")
    buf = await export_user_data("exp_res_todos")
    for fichero in ("agents.json", "skills.json", "prompts.json", "tools.json",
                    "workflows.json", "memory.json"):
        contenido = json.loads(_zip_read(buf, fichero))
        assert contenido, f"{fichero} llegó vacío"


async def test_export_incluye_el_artefacto_binario_de_la_tool(patch_gdpr_db):
    from app.storage.tool_storage import ToolStorage

    await _make_user("exp_tool_binary")
    owner = await _user_id("exp_tool_binary")
    storage = ToolStorage()
    tool = await storage.save(
        "private",
        {"name": "Binaria", "language": "cpp", "content": ""},
        owner_id=owner,
    )
    binary = b"portable-binary"
    digest = hashlib.sha256(binary).hexdigest()
    await storage.save_binary(
        tool["id"],
        owner,
        binary,
        "runner",
        len(binary),
        sha256=digest,
        uploaded_by="exp_tool_binary",
    )

    buf = await export_user_data("exp_tool_binary")
    with zipfile.ZipFile(buf) as archive:
        assert archive.read(f"tool_artifacts/{digest}.bin") == binary
        manifest = json.loads(archive.read("tool_artifacts/manifest.json"))
    assert manifest[0]["sha256"] == digest


async def test_export_no_incluye_recursos_de_otro_usuario(patch_gdpr_db):
    await _make_user("exp_res_mio")
    await _make_user("exp_res_ajeno")
    await _crear_recursos("exp_res_ajeno")
    buf = await export_user_data("exp_res_mio")
    for fichero in ("agents.json", "skills.json", "prompts.json", "tools.json",
                    "workflows.json", "memory.json"):
        assert json.loads(_zip_read(buf, fichero)) == []


async def test_export_deserializa_el_blob_del_recurso(patch_gdpr_db):
    """`data` es JSON guardado como texto: sin deserializar, el ZIP entrega
    una cadena escapada dentro de otra y no es portable a ningún sitio."""
    await _make_user("exp_res_blob")
    await _crear_recursos("exp_res_blob")
    buf = await export_user_data("exp_res_blob")
    agent = json.loads(_zip_read(buf, "agents.json"))[0]
    assert isinstance(agent["data"], dict)
    # El nombre vive en su columna, no en el blob: la fila entera es lo que
    # reconstruye el recurso, y por eso se exporta con todas sus columnas.
    assert agent["name"] == "Agente exportable"
    assert agent["data"]["agent_type"] == "generic"


async def test_export_blob_ilegible_se_conserva_y_avisa(patch_gdpr_db, monkeypatch):
    import app.services.gdpr as gdpr_mod
    from app.storage.db import open_db

    await _make_user("exp_res_roto")
    creados = await _crear_recursos("exp_res_roto")
    async with open_db() as conn:
        await conn.execute(
            "UPDATE agents SET data=? WHERE id=?",
            ("no-es-json", creados["agent"]["id"]),
        )
        await conn.commit()

    warnings = []
    monkeypatch.setattr(gdpr_mod.flog, "warning", warnings.append)

    buf = await export_user_data("exp_res_roto")
    agent = json.loads(_zip_read(buf, "agents.json"))[0]
    assert agent["data"] == "no-es-json"
    assert any("no normalizada" in message for message in warnings)
