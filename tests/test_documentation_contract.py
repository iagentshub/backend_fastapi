"""Checks that keep the maintained documentation aligned with the product."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _documented_markdown() -> list[Path]:
    return [ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]


@pytest.mark.parametrize("document", _documented_markdown(), ids=lambda path: path.name)
def test_local_markdown_links_exist(document: Path) -> None:
    for match in MARKDOWN_LINK.finditer(document.read_text(encoding="utf-8")):
        raw_target = match.group(1).strip().strip("<>")
        if raw_target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative_target = unquote(raw_target.split("#", 1)[0])
        if not relative_target:
            continue
        target = (document.parent / relative_target).resolve()
        assert target.exists(), f"{document.relative_to(ROOT)} links to missing {raw_target}"


def test_spanish_and_english_guides_have_the_same_pages() -> None:
    spanish = {path.name for path in (DOCS / "es").glob("*.md")}
    english = {path.name for path in (DOCS / "en").glob("*.md")}
    assert spanish == english


@pytest.mark.parametrize("language", ["es", "en"])
def test_api_guide_covers_every_tool_route(language: str) -> None:
    contract = (ROOT / "tests/api/contrato_rutas.txt").read_text(encoding="utf-8")
    tool_routes = [line for line in contract.splitlines() if " /api/tools" in line]
    guide = (DOCS / language / "api.md").read_text(encoding="utf-8")
    for route in tool_routes:
        method, path = route.split(" ", 1)
        assert f"`{method}`" in guide and f"`{path}`" in guide, route


@pytest.mark.parametrize("language", ["es", "en"])
def test_api_guide_does_not_restore_removed_graph_endpoint(language: str) -> None:
    guide = (DOCS / language / "api.md").read_text(encoding="utf-8")
    assert "/api/admin/resources/{type}/{id}/graph" not in guide

