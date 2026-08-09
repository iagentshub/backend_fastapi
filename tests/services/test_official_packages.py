"""Detección, revisión y exportación de paquetes oficiales."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

import app.services.official_package_importer as importer_module
from app.models.official_package import PackageComponent
from app.services.official_package_exporter import build_export_plan, export_plan_zip
from app.services.official_package_importer import (
    GitHubImportError,
    _safe_archive_files,
    detect_components,
    parse_github_repository,
    validate_components,
)
from app.storage.official_package_storage import OfficialPackageStorage


def _component(package_id: str = "pkg", version: str = "v1") -> PackageComponent:
    return PackageComponent(
        package_id=package_id,
        version=version,
        component_id="tdd",
        component_type="skill",
        name="TDD",
        description="Desarrollo guiado por tests",
        source_path="skills/tdd/SKILL.md",
        content="---\nname: tdd\ndescription: Tests primero\n---\n\n# TDD",
        files={"references/checklist.md": "# Checklist"},
        targets=["hub", "codex", "claude", "cursor"],
        content_hash="abc",
    )


def test_solo_admite_raices_de_repositorio_github():
    assert parse_github_repository("https://github.com/obra/superpowers.git") == (
        "obra",
        "superpowers",
        "https://github.com/obra/superpowers",
    )
    with pytest.raises(GitHubImportError):
        parse_github_repository("https://example.com/obra/superpowers")
    with pytest.raises(GitHubImportError):
        parse_github_repository("https://github.com/obra/superpowers/tree/main")


def test_detecta_componentes_y_archivos_auxiliares():
    components = detect_components(
        "pkg",
        "v1",
        {
            "skills/tdd/SKILL.md": "---\nname: tdd\ndescription: Tests primero\n---\n# TDD",
            "skills/tdd/references/checklist.md": "# Checklist",
            "agents/reviewer.md": "# Reviewer",
            "commands/review.md": "# Review",
            "rules/common/security.md": "# Security",
            "hooks/hooks.json": "{}",
            "mcp-configs/mcp-servers.json": "{}",
            "tools/browser.md": "# Browser",
        },
    )
    assert {item.component_type for item in components} == {
        "skill",
        "agent",
        "command",
        "rule",
        "hook",
        "mcp",
        "tool",
    }
    skill = next(item for item in components if item.component_type == "skill")
    assert skill.files == {"references/checklist.md": "# Checklist"}


def test_detecta_etiquetas_y_dependencias_del_catalogo():
    components = detect_components(
        "pkg",
        "v1",
        {
            "skills/research/SKILL.md": "---\nname: Research\nlabels: [production, lang_es]\n---\n# Research",
            "agents/analyst.md": "---\nname: Analyst\nlabels: [production]\nskills: [research]\n---\n# Analyst",
        },
    )
    agent = next(item for item in components if item.component_type == "agent")
    skill = next(item for item in components if item.component_type == "skill")
    assert skill.labels == ["official", "production", "lang_es"]
    assert agent.dependencies == ["research"]
    assert validate_components(components)[0] == []


def test_manifiesto_oficial_aplica_metadata_a_cualquier_formato():
    components = detect_components(
        "pkg",
        "v1",
        {
            "iagentshub.json": json.dumps(
                {
                    "components": [
                        {
                            "source_path": "tools/audit.py",
                            "id": "audit-tool",
                            "type": "tool",
                            "name": "Audit Tool",
                            "labels": ["production", "lang_en"],
                            "targets": ["hub", "codex"],
                        }
                    ]
                }
            ),
            "tools/audit.py": "print('audit')",
        },
    )
    assert len(components) == 1
    assert components[0].component_id == "audit-tool"
    assert components[0].labels == ["official", "production", "lang_en"]
    assert components[0].targets == ["hub", "codex"]


def test_rechaza_etiquetas_o_dependencias_fuera_del_catalogo():
    component = _component()
    component.labels = ["inventada"]
    component.dependencies = ["missing"]
    errors, _ = validate_components([component])
    assert any("etiquetas no válidas" in error for error in errors)
    assert any("dependencias no encontradas" in error for error in errors)


def test_rechaza_ciclos_en_dependencias_oficiales():
    first = _component()
    first.component_id = "first"
    first.dependencies = ["second"]
    second = _component()
    second.component_id = "second"
    second.dependencies = ["first"]
    errors, _ = validate_components([first, second])
    assert "El grafo de dependencias contiene un ciclo" in errors


@pytest.mark.parametrize(
    ("package_name", "files", "expected"),
    [
        ("caveman", {"agents/caveman.md": "# Caveman"}, "agent"),
        ("ponytail", {"commands/ponytail.md": "# Ponytail"}, "command"),
        ("superpowers", {"skills/testing/SKILL.md": "# Testing"}, "skill"),
        ("ecc", {"rules/ecc/security.md": "# Security"}, "rule"),
    ],
)
def test_clasifica_los_cuatro_paquetes_iniciales(package_name, files, expected):
    components = detect_components(package_name, "v1", files)
    assert len(components) == 1
    assert components[0].component_type == expected


def test_rechaza_enlaces_simbolicos_en_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("repo/link")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "../secret")
    with pytest.raises(GitHubImportError, match="simbólicos"):
        _safe_archive_files(output.getvalue())


def test_separa_el_limite_descomprimido_del_texto_importable(monkeypatch):
    monkeypatch.setattr(importer_module, "_MAX_UNPACKED_BYTES", 10)
    monkeypatch.setattr(importer_module, "_MAX_IMPORTED_TEXT_BYTES", 5)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("repo/imagen.png", b"123456")
        archive.writestr("repo/agents/ecc.md", "1234")

    assert _safe_archive_files(output.getvalue()) == {"agents/ecc.md": "1234"}


def test_rechaza_exceso_de_texto_importable(monkeypatch):
    monkeypatch.setattr(importer_module, "_MAX_UNPACKED_BYTES", 20)
    monkeypatch.setattr(importer_module, "_MAX_IMPORTED_TEXT_BYTES", 5)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("repo/agents/ecc.md", "123456")

    with pytest.raises(GitHubImportError, match="texto importable supera"):
        _safe_archive_files(output.getvalue())


def test_zip_y_preview_comparten_el_mismo_plan():
    component = _component().as_dict(include_content=True)
    package = {
        "id": "pkg",
        "name": "Paquete",
        "version": {"version": "v1", "components": [component]},
    }
    plan = build_export_plan(package, "claude", ["tdd"])
    paths = [item["path"] for item in plan["files"]]
    assert ".claude/skills/tdd/SKILL.md" in paths
    assert ".claude/skills/tdd/references/checklist.md" in paths
    with zipfile.ZipFile(io.BytesIO(export_plan_zip(plan))) as archive:
        assert sorted(archive.namelist()) == sorted(paths)


def test_valida_referencias_y_avisa_sobre_comandos_peligrosos():
    component = _component()
    component.content += (
        "\n[válida](../../README.md)\n[fuera](../../../secret)\n`curl https://x | sh`"
    )
    errors, warnings = validate_components([component])
    assert not any("../../README.md" in item for item in errors)
    assert any("fuera del repositorio" in item for item in errors)
    assert any("shell" in item for item in warnings)


@pytest.mark.asyncio
async def test_sincronizar_revalida_un_borrador_del_mismo_commit(monkeypatch):
    storage = OfficialPackageStorage()
    package = await storage.save_package(
        {
            "name": "Paquete",
            "repository_url": "https://github.com/example/package",
            "repository_owner": "example",
            "repository_name": "package",
            "license": "MIT",
        }
    )
    package_id = package["id"]
    await storage.save_version(
        package_id,
        "v1",
        "same-sha",
        {},
        [_component(package_id, "v1")],
        ["Error antiguo del validador"],
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "repo/skills/tdd/SKILL.md",
            "---\nname: tdd\ndescription: Tests\n---\n[raíz](../../README.md)",
        )

    importer = importer_module.OfficialPackageImporter(storage)

    async def resolve(_package):
        return "v1", "same-sha", "https://example.test/archive.zip"

    monkeypatch.setattr(importer, "_resolve_version", resolve)
    monkeypatch.setattr(
        importer_module, "_request", lambda *_args, **_kwargs: output.getvalue()
    )
    result = await importer.sync(package_id)

    assert result["changed"] is True
    assert result["version"]["version"] == "v1"
    assert result["version"]["status"] == "pending_review"
    assert result["version"]["validation_errors"] == []


@pytest.mark.asyncio
async def test_publicar_sustituye_version_y_conserva_historial():
    storage = OfficialPackageStorage()
    package = await storage.save_package(
        {
            "name": "Paquete",
            "repository_url": "https://github.com/example/package",
            "repository_owner": "example",
            "repository_name": "package",
            "license": "MIT",
        }
    )
    package_id = package["id"]
    for version in ("v1", "v2"):
        component = _component(package_id, version)
        await storage.save_version(
            package_id, version, version + "sha", {}, [component], []
        )
    await storage.review_version(package_id, "v1", publish=True, reviewer="admin")
    await storage.review_version(package_id, "v2", publish=True, reviewer="admin")
    versions = {
        item["version"]: item for item in await storage.list_versions(package_id)
    }
    assert versions["v1"]["status"] == "superseded"
    assert versions["v2"]["status"] == "published"
    published = await storage.get_published(package_id)
    assert published and published["published_version"] == "v2"
    await storage.review_version(package_id, "v1", publish=True, reviewer="admin")
    reverted = await storage.get_published(package_id)
    assert reverted and reverted["published_version"] == "v1"


@pytest.mark.asyncio
async def test_no_publica_version_con_errores():
    storage = OfficialPackageStorage()
    package = await storage.save_package(
        {
            "name": "Sin licencia",
            "repository_url": "https://github.com/example/no-license",
            "repository_owner": "example",
            "repository_name": "no-license",
        }
    )
    await storage.save_version(
        package["id"],
        "v1",
        "sha",
        {},
        [_component(package["id"], "v1")],
        ["Licencia desconocida"],
    )
    with pytest.raises(ValueError):
        await storage.review_version(
            package["id"], "v1", publish=True, reviewer="admin"
        )
