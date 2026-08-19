"""Fuentes oficiales: lo que traen son recursos normales marcados.

El contrato que se prueba aquí es el que hace que oficial sea "solo una
etiqueta": el contenido sincronizado vive en las tablas de siempre, aparece en
Explorar como cualquier recurso público y se borra como cualquier recurso.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from app.models.official_source import INTERNAL_SOURCE_ID, PackageComponent
from app.services.official_source_sync import OfficialSourceMaterializer
from app.storage.db import open_db
from app.storage.knowledge import KnowledgeStorage
from app.storage.official_source_storage import OfficialSourceStorage


def _components(source_id: str) -> List[PackageComponent]:
    return [
        PackageComponent(
            source_id=source_id,
            component_id="brainstorming",
            component_type="skill",
            name="Brainstorming",
            source_path="skills/brainstorming/SKILL.md",
            content="# Brainstorming",
            content_hash="hash-skill",
        ),
        PackageComponent(
            source_id=source_id,
            component_id="researcher",
            component_type="agent",
            name="Researcher",
            source_path="agents/researcher.md",
            content="# Researcher",
            content_hash="hash-agent",
            dependencies=["brainstorming"],
        ),
        PackageComponent(
            source_id=source_id,
            component_id="checklists",
            component_type="knowledge",
            name="Checklists",
            source_path="knowledge/checklists.md",
            content="# Checklists",
            content_hash="hash-knowledge",
        ),
    ]


def _seed_source() -> str:
    async def seed() -> str:
        source = await OfficialSourceStorage().save_source(
            {
                "name": "Superpowers",
                "description": "Flujo de ingeniería",
                "repository_url": "https://github.com/obra/superpowers",
                "repository_owner": "obra",
                "repository_name": "superpowers",
                "license": "MIT",
            }
        )
        return str(source["id"])

    return asyncio.run(seed())


def _materialize(
    source_id: str, component_ids: List[str] | None, owner_id: str
) -> Dict[str, Any]:
    async def run() -> Dict[str, Any]:
        storage = OfficialSourceStorage()
        source = await storage.get_source(source_id)
        assert source is not None
        return await OfficialSourceMaterializer(storage).materialize(
            source, _components(source_id), component_ids, owner_id=owner_id
        )

    return asyncio.run(run())


@pytest.fixture
def admin_id(admin_client) -> str:
    return next(
        user["id"]
        for user in admin_client.get("/api/admin/users").json()
        if user["username"] == "testadmin"
    )


def test_lo_sincronizado_es_un_recurso_normal_marcado_con_su_fuente(
    admin_client, admin_id
):
    source_id = _seed_source()

    applied = _materialize(source_id, ["researcher"], admin_id)

    # La dependencia entra sola: un agente sin su skill no sirve de nada.
    materialized = {item["component_id"]: item for item in applied["resources"]}
    assert set(materialized) == {"researcher", "brainstorming"}

    skill_id = materialized["brainstorming"]["resource_id"]
    skill = admin_client.get(f"/api/skills/private/{skill_id}")
    assert skill.status_code == 200
    assert "official" in skill.json()["labels"]
    assert "public" in skill.json()["labels"]

    agent_id = materialized["researcher"]["resource_id"]
    agent = admin_client.get(f"/api/agents/{agent_id}").json()
    assert agent["skills"] == [skill_id]

    async def columns() -> List[Any]:
        async with open_db() as conn:
            return await conn.fetchall(
                "SELECT official_source_id, official_component_id FROM skills "
                "WHERE id=?",
                (skill_id,),
            )

    rows = asyncio.run(columns())
    assert rows[0]["official_source_id"] == source_id
    assert rows[0]["official_component_id"] == "brainstorming"


def test_lo_oficial_aparece_en_explore_como_una_fila_mas(
    client, admin_client, admin_id
):
    source_id = _seed_source()
    _materialize(source_id, ["checklists"], admin_id)

    client.post(
        "/api/auth/register",
        json={
            "username": "curioso",
            "email": "curioso@example.com",
            "password": "pass1234",
        },
    )
    explore = client.get("/api/explore")

    assert explore.status_code == 200
    rows = [item for item in explore.json() if item["name"] == "Checklists"]
    assert len(rows) == 1
    assert rows[0]["resource_type"] == "knowledge"
    assert "official" in rows[0]["labels"]
    # Ni ids compuestos ni campos propios: el cliente no distingue el origen.
    assert ":" not in rows[0]["resource_id"]
    assert "official_package_id" not in rows[0]


def test_explore_agrupa_los_recursos_oficiales_por_fuente(
    client, admin_client, admin_id
):
    source_id = _seed_source()
    _materialize(source_id, None, admin_id)
    client.post(
        "/api/auth/register",
        json={
            "username": "packviewer",
            "email": "packviewer@example.com",
            "password": "pass1234",
        },
    )

    response = client.get("/api/explore/official-packs")

    assert response.status_code == 200
    assert response.json() == [
        {
            "item_kind": "official_pack",
            "source_id": source_id,
            "name": "Superpowers",
            "description": "Flujo de ingeniería",
            "repository_url": "https://github.com/obra/superpowers",
            "repository_owner": "obra",
            "repository_name": "superpowers",
            "provider": "github",
            "license": "MIT",
            "commit_sha": "",
            "labels": ["official"],
            "counts": {"skill": 1, "agent": 1, "knowledge": 1},
            "matching_count": 3,
            "total_count": 3,
            "linked_count": 0,
            "link_state": "none",
            "owned_by_requester": False,
        }
    ]
    flat = client.get("/api/explore", params={"include_official": "false"})
    assert flat.status_code == 200
    assert all("official" not in item["labels"] for item in flat.json())


def test_detalle_y_grafo_del_pack_conservan_relaciones(client, admin_client, admin_id):
    source_id = _seed_source()
    _materialize(source_id, None, admin_id)
    client.post(
        "/api/auth/register",
        json={
            "username": "packgraph",
            "email": "packgraph@example.com",
            "password": "pass1234",
        },
    )

    detail = client.get(f"/api/explore/official-packs/{source_id}")
    assert detail.status_code == 200
    components = {item["component_key"]: item for item in detail.json()["components"]}
    assert components["researcher"]["dependencies"] == ["brainstorming"]

    # El pack oficial ya no tiene endpoint propio de grafo: entra por el
    # mismo `/relations` que el resto de recursos de Explorar.
    relations = client.get(f"/api/explore/official_source/{source_id}/relations")
    assert relations.status_code == 200
    payload = relations.json()
    assert payload["root"]["id"] == source_id
    assert any(item["relation"] == "uses" for item in payload["items"])


def test_vincular_pack_es_atomico_idempotente_y_reutiliza_dependencias(
    client, admin_client, admin_id
):
    source_id = _seed_source()
    _materialize(source_id, None, admin_id)
    client.post(
        "/api/auth/register",
        json={
            "username": "packlinker",
            "email": "packlinker@example.com",
            "password": "pass1234",
        },
    )

    first = client.post(
        f"/api/explore/official-packs/{source_id}/link",
        json={"mode": "selected", "component_keys": ["researcher"]},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert {item["component_key"] for item in body["created"]} == {
        "brainstorming",
        "researcher",
    }
    assert body["included_dependencies"] == ["brainstorming"]
    ids = {item["component_key"]: item["resource_id"] for item in body["created"]}
    linked_agent = client.get(f"/api/agents/{ids['researcher']}")
    assert linked_agent.status_code == 200
    assert linked_agent.json()["skills"] == [ids["brainstorming"]]

    second = client.post(
        f"/api/explore/official-packs/{source_id}/link",
        json={"mode": "selected", "component_keys": ["researcher"]},
    )
    assert second.status_code == 200
    assert second.json()["created"] == []
    assert {item["component_key"] for item in second.json()["existing"]} == {
        "brainstorming",
        "researcher",
    }


def test_vincular_seleccion_no_carga_componentes_ajenos(
    client, admin_client, admin_id, monkeypatch
):
    from app.api.routes.explore import official_packs as explore_routes

    source_id = _seed_source()
    _materialize(source_id, None, admin_id)
    client.post(
        "/api/auth/register",
        json={
            "username": "selectivepack",
            "email": "selectivepack@example.com",
            "password": "pass1234",
        },
    )
    service = explore_routes._official_packs
    original = service._load_resources
    loaded_types: list[set[str]] = []

    async def record_loads(rows):
        materialized = list(rows)
        loaded_types.append({str(row["resource_type"]) for row in materialized})
        return await original(materialized)

    monkeypatch.setattr(service, "_load_resources", record_loads)
    response = client.post(
        f"/api/explore/official-packs/{source_id}/link",
        json={"mode": "selected", "component_keys": ["researcher"]},
    )

    assert response.status_code == 200, response.text
    assert [types for types in loaded_types if types] == [{"agent"}, {"skill"}]
    assert all("knowledge" not in types for types in loaded_types)


def test_vincular_pack_revierte_todo_si_falla_un_componente(
    client, admin_client, admin_id, monkeypatch
):
    from app.api.routes.explore import official_packs as explore_routes
    from app.auth.auth import get_user_by_username

    source_id = _seed_source()
    _materialize(source_id, None, admin_id)
    client.post(
        "/api/auth/register",
        json={
            "username": "packrollback",
            "email": "packrollback@example.com",
            "password": "pass1234",
        },
    )
    user_id = asyncio.run(get_user_by_username("packrollback"))["id"]
    original_copy = explore_routes._official_packs._copy

    async def fail_on_agent(row, *args, **kwargs):
        if row["resource_type"] == "agent":
            raise ValueError("induced_failure")
        return await original_copy(row, *args, **kwargs)

    monkeypatch.setattr(explore_routes._official_packs, "_copy", fail_on_agent)
    response = client.post(
        f"/api/explore/official-packs/{source_id}/link",
        json={"mode": "selected", "component_keys": ["researcher"]},
    )

    assert response.status_code == 422

    async def linked_rows() -> tuple[List[Any], List[Any]]:
        async with open_db() as conn:
            social = await conn.fetchall(
                "SELECT resource_id FROM resource_social "
                "WHERE owner=? AND linked_to_id IS NOT NULL",
                (user_id,),
            )
            skills = await conn.fetchall(
                "SELECT id FROM skills WHERE owner_id=?",
                (user_id,),
            )
            return social, skills

    assert asyncio.run(linked_rows()) == ([], [])


def test_completar_pack_adopta_dependencias_de_un_enlace_individual_legacy(
    client, admin_client, admin_id
):
    source_id = _seed_source()
    applied = _materialize(source_id, None, admin_id)
    source_ids = {
        item["component_id"]: item["resource_id"] for item in applied["resources"]
    }
    client.post(
        "/api/auth/register",
        json={
            "username": "packlegacy",
            "email": "packlegacy@example.com",
            "password": "pass1234",
        },
    )
    legacy = client.post(f"/api/agents/private/{source_ids['researcher']}/link")
    assert legacy.status_code == 200
    linked_agent = client.get(f"/api/agents/{legacy.json()['agent_id']}").json()
    inherited_skill_id = linked_agent["skills"][0]

    completed = client.post(
        f"/api/explore/official-packs/{source_id}/link",
        json={"mode": "selected", "component_keys": ["researcher"]},
    )

    assert completed.status_code == 200, completed.text
    assert completed.json()["created"] == []
    existing = {
        item["component_key"]: item["resource_id"]
        for item in completed.json()["existing"]
    }
    assert existing == {
        "researcher": legacy.json()["agent_id"],
        "brainstorming": inherited_skill_id,
    }

    async def social_row() -> Dict[str, Any]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT linked_to_id FROM resource_social "
                "WHERE resource_type='skill' AND resource_id=?",
                (inherited_skill_id,),
            )
            return dict(row) if row else {}

    social = asyncio.run(social_row())
    assert social["linked_to_id"] == source_ids["brainstorming"]


def test_desmarcar_borra_el_recurso_y_volver_a_marcarlo_lo_recrea(
    admin_client, admin_id
):
    source_id = _seed_source()
    first = _materialize(source_id, ["brainstorming", "checklists"], admin_id)
    knowledge_id = next(
        item["resource_id"]
        for item in first["resources"]
        if item["component_id"] == "checklists"
    )

    second = _materialize(source_id, ["brainstorming"], admin_id)

    assert second["removed"] == 1
    assert asyncio.run(KnowledgeStorage().get(knowledge_id, admin_id)) is None

    third = _materialize(source_id, ["brainstorming", "checklists"], admin_id)
    assert {item["component_id"] for item in third["resources"]} == {
        "brainstorming",
        "checklists",
    }


def test_borrar_la_fuente_borra_todo_lo_que_trajo(admin_client, admin_id):
    source_id = _seed_source()
    applied = _materialize(source_id, None, admin_id)
    skill_id = next(
        item["resource_id"]
        for item in applied["resources"]
        if item["component_id"] == "brainstorming"
    )

    response = admin_client.delete(f"/api/admin/official-sources/{source_id}")

    assert response.status_code == 200
    assert response.json()["removed_resources"] == len(applied["resources"])
    assert admin_client.get(f"/api/skills/private/{skill_id}").status_code == 404
    assert admin_client.get("/api/admin/official-sources").json() == []


def test_admin_marca_un_recurso_suyo_como_oficial(admin_client):
    skill = admin_client.post(
        "/api/skills/private",
        json={
            "name": "Skill de la casa",
            "description": "Escrita a mano",
            "content": "haz la cosa",
        },
    ).json()

    response = admin_client.post(f"/api/admin/resources/skill/{skill['id']}/official")

    assert response.status_code == 200
    assert response.json()["official_source_id"] == INTERNAL_SOURCE_ID

    async def source_of(skill_id: str) -> Any:
        async with open_db() as conn:
            return await conn.fetchval(
                "SELECT official_source_id FROM skills WHERE id=?", (skill_id,)
            )

    assert asyncio.run(source_of(skill["id"])) == INTERNAL_SOURCE_ID

    admin_client.post(
        f"/api/admin/resources/skill/{skill['id']}/official", json={"official": False}
    )
    assert asyncio.run(source_of(skill["id"])) is None


def test_marcar_oficial_rechaza_tipos_desconocidos(admin_client):
    response = admin_client.post("/api/admin/resources/folder/x/official")

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "resource_type"


def test_solo_admin_gestiona_fuentes(client, reset_rate_limiter):
    client.post(
        "/api/auth/register",
        json={
            "username": "nosources",
            "email": "nosources@example.com",
            "password": "pass1234",
        },
    )

    assert client.get("/api/admin/official-sources").status_code == 403


def test_sync_por_el_endpoint_materializa_la_seleccion(
    admin_client, admin_id, monkeypatch
):
    """Camino real del panel: POST /sync con la selección del diálogo."""
    from app.api.routes.admin import official_sources as routes

    source_id = _seed_source()

    async def fake_snapshot(self, *_args, **_kwargs):
        source = await OfficialSourceStorage().get_source(source_id)
        return {
            "source": source,
            "version": "v1",
            "commit_sha": "sha",
            "files": {},
        }

    def fake_analyze(self, snapshot):
        return {
            "source": snapshot["source"],
            "version": snapshot["version"],
            "commit_sha": snapshot["commit_sha"],
            "components": _components(requested_id),
            "errors": [],
            "security_warnings": [],
        }

    requested_id = source_id
    monkeypatch.setattr(routes._importer.__class__, "inspect_snapshot", fake_snapshot)
    monkeypatch.setattr(routes._importer.__class__, "analyze_snapshot", fake_analyze)

    mirar = admin_client.post(f"/api/admin/official-sources/{source_id}/sync")
    assert mirar.status_code == 200
    assert {item["component_id"] for item in mirar.json()["components"]} == {
        "brainstorming",
        "researcher",
        "checklists",
    }
    assert mirar.json()["selected"] == []
    assert mirar.json()["applied"] is None

    aplicar = admin_client.post(
        f"/api/admin/official-sources/{source_id}/sync",
        json={"component_ids": ["checklists"]},
    )
    assert aplicar.status_code == 200
    payload = aplicar.json()
    assert payload["applied"] is not None, "el sync con selección debe materializar"
    assert {item["component_id"] for item in payload["applied"]["resources"]} == {
        "checklists"
    }
    assert payload["selected"] == ["checklists"]

    listado = admin_client.get("/api/admin/official-sources").json()
    assert [item["component_id"] for item in listado[0]["resources"]] == ["checklists"]


def test_el_admin_ve_en_explore_lo_que_el_mismo_sincroniza(admin_client, admin_id):
    """El contenido oficial vive en la cuenta del admin, pero es del hub.

    Sin esta excepción el admin era el único que no lo veía en el catálogo,
    justo la persona que necesita comprobar que la sincronización funcionó.
    """
    source_id = _seed_source()
    _materialize(source_id, ["checklists"], admin_id)

    explore = admin_client.get("/api/explore")

    assert explore.status_code == 200
    assert [item["name"] for item in explore.json()] == ["Checklists"]


def test_lo_propio_no_oficial_sigue_fuera_del_catalogo(admin_client):
    admin_client.post(
        "/api/skills/private",
        json={
            "name": "Skill propia publicada",
            "description": "mía",
            "content": "haz la cosa",
            "labels": ["public"],
        },
    )

    explore = admin_client.get("/api/explore")

    assert "Skill propia publicada" not in [item["name"] for item in explore.json()]


def test_una_seleccion_vacia_deja_la_fuente_sin_objetos(admin_client, admin_id):
    """Desmarcar todo es una orden, no la ausencia de una."""
    source_id = _seed_source()
    _materialize(source_id, None, admin_id)

    vaciado = _materialize(source_id, [], admin_id)

    assert vaciado["resources"] == []
    assert vaciado["removed"] == 3
    assert await_resources(source_id) == []


def await_resources(source_id: str) -> List[Any]:
    return asyncio.run(OfficialSourceStorage().list_resources(source_id))


def test_un_comando_se_materializa_como_prompt(admin_client, admin_id):
    """Un comando de barra es un prompt: dejarlo fuera perdía objetos."""
    source_id = _seed_source()

    async def run() -> Dict[str, Any]:
        storage = OfficialSourceStorage()
        source = await storage.get_source(source_id)
        assert source is not None
        command = PackageComponent(
            source_id=source_id,
            component_id="caveman-commit",
            component_type="command",
            name="Caveman Commit",
            source_path="commands/caveman-commit.md",
            content="# Commit",
            content_hash="hash-command",
        )
        return await OfficialSourceMaterializer(storage).materialize(
            source, [command], None, owner_id=admin_id
        )

    applied = asyncio.run(run())

    assert [item["resource_type"] for item in applied["resources"]] == ["prompt"]


def test_inspeccion_stream_emite_progreso_y_resultado(admin_client, monkeypatch):
    from app.api.routes.admin import official_sources as routes

    async def fake_inspect(*_args, progress=None, **_kwargs):
        assert progress is not None
        await progress({"stage": "downloading", "current": 0, "total": 0})
        await progress(
            {
                "stage": "llm_analyzing",
                "current": 1,
                "total": 2,
                "files": 20,
                "components": 4,
            }
        )
        return {"id": "draft-stream"}

    async def fake_payload(draft, **_kwargs):
        return {"draft_id": draft["id"], "components": []}

    monkeypatch.setattr(routes._drafts, "inspect", fake_inspect)
    monkeypatch.setattr(routes, "_draft_payload", fake_payload)

    with admin_client.stream(
        "POST",
        "/api/admin/official-sources/inspect-stream",
        json={
            "repository_url": "https://github.com/example/demo",
            "import_mode": "llm",
            "llm_connection_id": "connection-1",
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"type": "started"' in body
    assert '"stage": "llm_analyzing"' in body
    assert '"type": "result"' in body
    assert '"draft_id": "draft-stream"' in body
