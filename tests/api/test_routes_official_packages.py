"""Contrato HTTP del catálogo de paquetes oficiales."""

from __future__ import annotations

import asyncio
import io
import zipfile

from app.config import data as _cfg
from app.models.official_package import PackageComponent
from app.storage.agent_storage import AgentStorage
from app.storage.db import open_db
from app.storage.official_package_storage import OfficialPackageStorage


def _seed_published() -> str:
    async def seed() -> str:
        storage = OfficialPackageStorage()
        package = await storage.save_package(
            {
                "name": "Superpowers",
                "description": "Flujo de ingeniería",
                "repository_url": "https://github.com/obra/superpowers",
                "repository_owner": "obra",
                "repository_name": "superpowers",
                "license": "MIT",
            }
        )
        component = PackageComponent(
            package_id=package["id"],
            version="v1",
            component_id="brainstorming",
            component_type="skill",
            name="Brainstorming",
            source_path="skills/brainstorming/SKILL.md",
            content="# Brainstorming",
            targets=["hub", "codex", "claude", "cursor"],
            content_hash="hash",
        )
        await storage.save_version(package["id"], "v1", "sha", {}, [component], [])
        await storage.review_version(
            package["id"], "v1", publish=True, reviewer="admin"
        )
        return str(package["id"])

    return asyncio.run(seed())


def test_usuario_lista_previsualiza_y_exporta(admin_client):
    package_id = _seed_published()
    listed = admin_client.get("/api/official-packages")
    assert listed.status_code == 200
    assert listed.json()[0]["is_official"] is True

    preview = admin_client.post(
        f"/api/official-packages/{package_id}/export-preview",
        json={"target": "codex", "component_ids": ["brainstorming"]},
    )
    assert preview.status_code == 200
    assert preview.json()["files"][0]["path"].startswith(".agents/skills/")

    exported = admin_client.post(
        f"/api/official-packages/{package_id}/export/claude",
        json={"component_ids": ["brainstorming"]},
    )
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert ".claude/skills/brainstorming/SKILL.md" in archive.namelist()


def test_copia_pierde_sello_oficial(admin_client):
    package_id = _seed_published()
    response = admin_client.post(
        f"/api/official-packages/{package_id}/copy",
        json={"component_ids": ["brainstorming"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_official"] is False
    assert body["copies"][0]["source_version"] == "v1"
    assert body["copies"][0]["source_package_id"] == package_id
    assert body["copies"][0]["source_component_id"] == "brainstorming"
    assert body["copies"][0]["content_hash"] == "hash"
    assert body["copies"][0]["is_official"] is False
    assert body["copies"][0]["status"] == "Sin cambios"
    assert body["copies"][0]["resource_type"] == "skill"

    copies = admin_client.get("/api/official-packages/copies")
    assert copies.status_code == 200
    assert copies.json()[0]["status"] == "Sin cambios"
    assert copies.json()[0]["is_official"] is False

    async def publish_update() -> None:
        storage = OfficialPackageStorage()
        component = PackageComponent(
            package_id=package_id,
            version="v2",
            component_id="brainstorming",
            component_type="skill",
            name="Brainstorming",
            source_path="skills/brainstorming/SKILL.md",
            content="# Brainstorming v2",
            targets=["hub", "codex", "claude", "cursor"],
            content_hash="hash-v2",
        )
        await storage.save_version(package_id, "v2", "sha-v2", {}, [component], [])
        await storage.review_version(package_id, "v2", publish=True, reviewer="admin")

    asyncio.run(publish_update())
    updated = admin_client.get("/api/official-packages/copies")
    assert updated.json()[0]["status"] == "Actualización disponible"


def test_usuario_normal_puede_enlazar_recurso_oficial(client):
    from app.auth.auth import create_token, register_user

    asyncio.run(
        register_user(
            "officiallinkuser",
            "pass1234",
            email="officiallinkuser@example.com",
        )
    )
    client.cookies.set("ga_token", create_token("officiallinkuser"))
    package_id = _seed_published()

    response = client.post(
        f"/api/official-packages/{package_id}/link",
        json={"component_ids": ["brainstorming"]},
    )

    assert response.status_code == 200
    assert response.json()["is_official"] is True
    assert response.json()["links"][0]["mode"] == "link"


def test_explore_individualiza_oficiales_con_labels_y_dependencias(admin_client):
    async def seed() -> str:
        storage = OfficialPackageStorage()
        package = await storage.save_package(
            {
                "name": "Research Pack",
                "description": "Recursos de investigación",
                "repository_url": "https://github.com/example/research-pack",
                "repository_owner": "example",
                "repository_name": "research-pack",
                "license": "MIT",
            }
        )
        components = [
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="research",
                component_type="skill",
                name="Research",
                source_path="skills/research/SKILL.md",
                content="# Research",
                targets=["hub", "codex"],
                content_hash="skill-hash",
                labels=["official", "lang_es"],
            ),
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="analyst",
                component_type="agent",
                name="Analyst",
                source_path="agents/analyst.md",
                content="# Analyst",
                targets=["hub", "codex"],
                content_hash="agent-hash",
                labels=["official"],
                dependencies=["research"],
            ),
        ]
        await storage.save_version(package["id"], "v1", "sha", {}, components, [])
        await storage.review_version(
            package["id"], "v1", publish=True, reviewer="admin"
        )
        return str(package["id"])

    package_id = asyncio.run(seed())
    response = admin_client.get("/api/explore", params={"type": "agent"})
    assert response.status_code == 200
    official = next(item for item in response.json() if item.get("is_official"))
    assert official["resource_id"] == f"{package_id}:analyst"
    assert official["hub_installable"] is True
    assert official["labels"] == ["official"]
    assert official["dependencies"] == [
        {
            "component_id": "research",
            "name": "Research",
            "component_type": "skill",
            "dependencies": [],
        }
    ]
    assert official["direct_dependency_ids"] == ["research"]

    export_preview = admin_client.post(
        f"/api/official-packages/{package_id}/export-preview",
        json={"target": "codex", "component_ids": ["analyst"]},
    )
    assert export_preview.status_code == 200
    exported_component_ids = {
        item["component_id"]
        for item in export_preview.json()["files"]
        if item["component_id"] != "_manifest"
    }
    assert exported_component_ids == {"research", "analyst"}

    copied = admin_client.post(
        f"/api/official-packages/{package_id}/copy",
        json={"component_ids": ["analyst"]},
    )
    assert copied.status_code == 200
    copies = copied.json()["copies"]
    assert {item["source_component_id"] for item in copies} == {"research", "analyst"}
    agent_copy = next(item for item in copies if item["resource_type"] == "agent")
    skill_copy = next(item for item in copies if item["resource_type"] == "skill")
    agent = asyncio.run(
        AgentStorage(_cfg.AGENTS_DIR).get(agent_copy["resource_id"], scope="private")
    )
    assert agent is not None
    assert agent["skills"] == [skill_copy["resource_id"]]
    assert agent["labels"] == ["private", "community", "fork"]

    repeated = admin_client.post(
        f"/api/official-packages/{package_id}/copy",
        json={"component_ids": ["analyst"]},
    )
    assert repeated.status_code == 200
    assert {item["id"] for item in repeated.json()["copies"]} == {
        item["id"] for item in copies
    }

    linked = admin_client.post(
        f"/api/official-packages/{package_id}/link",
        json={"component_ids": ["analyst"]},
    )
    assert linked.status_code == 200
    assert linked.json()["is_official"] is True
    links = linked.json()["links"]
    assert {item["source_component_id"] for item in links} == {
        "research",
        "analyst",
    }
    linked_agent = next(item for item in links if item["resource_type"] == "agent")
    linked_skill = next(item for item in links if item["resource_type"] == "skill")
    agent = asyncio.run(
        AgentStorage(_cfg.AGENTS_DIR).get(
            linked_agent["resource_id"], scope="private"
        )
    )
    assert agent is not None
    assert agent["skills"] == [linked_skill["resource_id"]]
    assert agent["labels"] == ["private", "official", "linked"]

    async def linked_social_row():
        async with open_db() as conn:
                return await conn.fetchone(
                    "SELECT linked_to_id, labels FROM resource_social "
                    "WHERE resource_type='agent' AND resource_id=?",
                    (linked_agent["resource_id"],),
                )

    social = asyncio.run(linked_social_row())
    assert social is not None
    assert social["linked_to_id"] == f"{package_id}:analyst"
    assert social["labels"] == '["private", "official", "linked"]'

    profile_resources = admin_client.get("/api/social/me/resources")
    assert profile_resources.status_code == 200
    profile_link = next(
        item
        for item in profile_resources.json()["resources"]
        if item["resource_id"] == linked_agent["resource_id"]
    )
    assert profile_link["linked_broken"] is False

    repeated_link = admin_client.post(
        f"/api/official-packages/{package_id}/link",
        json={"component_ids": ["analyst"]},
    )
    assert repeated_link.status_code == 200
    assert {item["id"] for item in repeated_link.json()["links"]} == {
        item["id"] for item in links
    }


def test_admin_elige_componentes_al_publicar_y_conserva_dependencias(admin_client):
    async def seed() -> str:
        storage = OfficialPackageStorage()
        package = await storage.save_package(
            {
                "name": "Selective Pack",
                "repository_url": "https://github.com/example/selective-pack",
                "repository_owner": "example",
                "repository_name": "selective-pack",
            }
        )
        components = [
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="base-skill",
                component_type="skill",
                name="Base Skill",
                source_path="skills/base/SKILL.md",
                content="# Base",
                content_hash="base",
            ),
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="selected-agent",
                component_type="agent",
                name="Selected Agent",
                source_path="agents/selected.md",
                content="# Agent",
                content_hash="agent",
                dependencies=["base-skill"],
            ),
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="unused-knowledge",
                component_type="knowledge",
                name="Unused Knowledge",
                source_path="knowledge/unused.md",
                content="# Unused",
                content_hash="unused",
            ),
        ]
        await storage.save_version(package["id"], "v1", "sha", {}, components, [])
        return str(package["id"])

    package_id = asyncio.run(seed())
    published = admin_client.post(
        f"/api/admin/official-packages/{package_id}/versions/v1/publish",
        json={"component_ids": ["selected-agent"]},
    )
    assert published.status_code == 200

    package = admin_client.get(f"/api/official-packages/{package_id}")
    component_ids = {
        item["component_id"] for item in package.json()["version"]["components"]
    }
    assert component_ids == {"selected-agent", "base-skill"}

    explore = admin_client.get("/api/explore")
    official_ids = {
        item["official_component_id"]
        for item in explore.json()
        if item.get("official_package_id") == package_id
    }
    assert official_ids == {"selected-agent", "base-skill"}


def test_solo_admin_puede_revisar(client):
    response = client.get("/api/admin/official-packages")
    assert response.status_code == 401


def test_admin_elimina_fuente_y_su_historial(admin_client):
    package_id = _seed_published()
    deleted = admin_client.delete(f"/api/admin/official-packages/{package_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "retired_links": 0}

    storage = OfficialPackageStorage()
    assert asyncio.run(storage.get_package(package_id)) is None
    assert asyncio.run(storage.list_versions(package_id)) == []
    missing = admin_client.delete(f"/api/admin/official-packages/{package_id}")
    assert missing.status_code == 404


def test_eliminar_fuente_retira_explore_y_rompe_links_pero_conserva_forks(
    admin_client,
):
    package_id = _seed_published()
    linked = admin_client.post(
        f"/api/official-packages/{package_id}/link",
        json={"component_ids": ["brainstorming"]},
    )
    copied = admin_client.post(
        f"/api/official-packages/{package_id}/copy",
        json={"component_ids": ["brainstorming"]},
    )
    assert linked.status_code == copied.status_code == 200
    link_id = linked.json()["links"][0]["resource_id"]
    fork_id = copied.json()["copies"][0]["resource_id"]

    before = admin_client.get("/api/explore")
    assert any(
        item.get("official_package_id") == package_id for item in before.json()
    )

    deleted = admin_client.delete(f"/api/admin/official-packages/{package_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "retired_links": 1}

    after = admin_client.get("/api/explore")
    assert not any(
        item.get("official_package_id") == package_id for item in after.json()
    )
    assert admin_client.get(f"/api/skills/private/{link_id}").status_code == 404
    fork = admin_client.get(f"/api/skills/private/{fork_id}")
    assert fork.status_code == 200
    assert "fork" in fork.json()["labels"]


def test_publicar_sin_un_componente_retira_solo_sus_enlaces(admin_client):
    package_id = _seed_published()
    linked = admin_client.post(
        f"/api/official-packages/{package_id}/link",
        json={"component_ids": ["brainstorming"]},
    )
    link_id = linked.json()["links"][0]["resource_id"]

    async def seed_v2() -> None:
        storage = OfficialPackageStorage()
        replacement = PackageComponent(
            package_id=package_id,
            version="v2",
            component_id="replacement",
            component_type="knowledge",
            name="Replacement",
            source_path="knowledge/replacement.md",
            content="# Replacement",
            content_hash="replacement-v2",
        )
        await storage.save_version(
            package_id, "v2", "sha-v2", {}, [replacement], []
        )

    asyncio.run(seed_v2())
    published = admin_client.post(
        f"/api/admin/official-packages/{package_id}/versions/v2/publish",
        json={"component_ids": ["replacement"]},
    )
    assert published.status_code == 200
    assert published.json()["retired_links"] == 1
    assert admin_client.get(f"/api/skills/private/{link_id}").status_code == 404

    explore = admin_client.get("/api/explore")
    package_components = {
        item["official_component_id"]
        for item in explore.json()
        if item.get("official_package_id") == package_id
    }
    assert package_components == {"replacement"}


def test_desmarcar_borra_el_enlace_y_volver_a_marcar_lo_recupera(admin_client):
    """La selección se guarda; no destruye el componente de la versión.

    Republicar la misma versión con menos componentes retira los enlaces, y
    volver a marcarlo lo devuelve al catálogo sin resincronizar GitHub.
    """
    async def seed() -> str:
        storage = OfficialPackageStorage()
        package = await storage.save_package(
            {
                "name": "Toggle Pack",
                "repository_url": "https://github.com/example/toggle-pack",
                "repository_owner": "example",
                "repository_name": "toggle-pack",
                "license": "MIT",
            }
        )
        components = [
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="brainstorming",
                component_type="skill",
                name="Brainstorming",
                source_path="skills/brainstorming/SKILL.md",
                content="# Brainstorming",
                content_hash="hash",
            ),
            PackageComponent(
                package_id=package["id"],
                version="v1",
                component_id="checklists",
                component_type="knowledge",
                name="Checklists",
                source_path="knowledge/checklists.md",
                content="# Checklists",
                content_hash="checklists",
            ),
        ]
        await storage.save_version(package["id"], "v1", "sha", {}, components, [])
        await storage.review_version(
            package["id"], "v1", publish=True, reviewer="admin"
        )
        return str(package["id"])

    package_id = asyncio.run(seed())
    linked = admin_client.post(
        f"/api/official-packages/{package_id}/link",
        json={"component_ids": ["brainstorming"]},
    )
    link_id = linked.json()["links"][0]["resource_id"]

    retired = admin_client.post(
        f"/api/admin/official-packages/{package_id}/versions/v1/publish",
        json={"component_ids": ["checklists"]},
    )
    assert retired.status_code == 200
    assert retired.json()["retired_links"] == 1
    assert admin_client.get(f"/api/skills/private/{link_id}").status_code == 404
    catalogue = admin_client.get(f"/api/official-packages/{package_id}")
    assert {
        item["component_id"] for item in catalogue.json()["version"]["components"]
    } == {"checklists"}

    # El componente descartado sigue disponible para volver a publicarlo.
    restored = admin_client.post(
        f"/api/admin/official-packages/{package_id}/versions/v1/publish",
        json={"component_ids": ["checklists", "brainstorming"]},
    )
    assert restored.status_code == 200
    assert restored.json()["retired_links"] == 0
    catalogue = admin_client.get(f"/api/official-packages/{package_id}")
    assert {
        item["component_id"] for item in catalogue.json()["version"]["components"]
    } == {"checklists", "brainstorming"}


def test_admin_edita_fuente_oficial_sin_cambiar_su_id(admin_client):
    package_id = _seed_published()

    response = admin_client.put(
        f"/api/admin/official-packages/{package_id}",
        json={
            "name": "Superpowers revisado",
            "description": "Descripción administrada",
            "repository_url": "https://github.com/obra/superpowers-renamed.git",
            "tracking_mode": "branch",
            "tracking_ref": "stable",
            "license": "Apache-2.0",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == package_id
    assert body["name"] == "Superpowers revisado"
    assert body["repository_url"] == "https://github.com/obra/superpowers-renamed"
    assert body["repository_owner"] == "obra"
    assert body["repository_name"] == "superpowers-renamed"
    assert body["tracking_mode"] == "branch"
    assert body["tracking_ref"] == "stable"
