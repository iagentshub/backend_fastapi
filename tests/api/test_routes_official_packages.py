"""Contrato HTTP del catálogo de paquetes oficiales."""

from __future__ import annotations

import asyncio
import io
import zipfile

from app.config import data as _cfg
from app.models.official_package import PackageComponent
from app.storage.agent_storage import AgentStorage
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
                labels=["production", "lang_es"],
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
                labels=["production"],
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
    assert official["labels"] == ["production"]
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
    assert agent["labels"] == ["private", "fork", "production"]

    repeated = admin_client.post(
        f"/api/official-packages/{package_id}/copy",
        json={"component_ids": ["analyst"]},
    )
    assert repeated.status_code == 200
    assert {item["id"] for item in repeated.json()["copies"]} == {
        item["id"] for item in copies
    }


def test_solo_admin_puede_revisar(client):
    response = client.get("/api/admin/official-packages")
    assert response.status_code == 401


def test_admin_elimina_fuente_y_su_historial(admin_client):
    package_id = _seed_published()
    deleted = admin_client.delete(f"/api/admin/official-packages/{package_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}

    storage = OfficialPackageStorage()
    assert asyncio.run(storage.get_package(package_id)) is None
    assert asyncio.run(storage.list_versions(package_id)) == []
    missing = admin_client.delete(f"/api/admin/official-packages/{package_id}")
    assert missing.status_code == 404
