"""Contrato HTTP del catálogo de paquetes oficiales."""

from __future__ import annotations

import asyncio
import io
import zipfile

from app.models.official_package import PackageComponent
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
            package_id=package["id"], version="v1", component_id="brainstorming",
            component_type="skill", name="Brainstorming",
            source_path="skills/brainstorming/SKILL.md", content="# Brainstorming",
            targets=["hub", "codex", "claude", "cursor"], content_hash="hash",
        )
        await storage.save_version(package["id"], "v1", "sha", {}, [component], [])
        await storage.review_version(package["id"], "v1", publish=True, reviewer="admin")
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
            package_id=package_id, version="v2", component_id="brainstorming",
            component_type="skill", name="Brainstorming",
            source_path="skills/brainstorming/SKILL.md", content="# Brainstorming v2",
            targets=["hub", "codex", "claude", "cursor"], content_hash="hash-v2",
        )
        await storage.save_version(package_id, "v2", "sha-v2", {}, [component], [])
        await storage.review_version(package_id, "v2", publish=True, reviewer="admin")

    asyncio.run(publish_update())
    updated = admin_client.get("/api/official-packages/copies")
    assert updated.json()[0]["status"] == "Actualización disponible"


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
