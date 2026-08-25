"""Regression coverage for Tool artifacts inherited through public Agents."""

from __future__ import annotations

import asyncio
import hashlib

from app.services.resource_inheritance import _inherit_resource_ids
from app.storage.tool_storage import ToolStorage

_ELF_X64 = b"\x7fELF\x02\x01" + (b"\x00" * 12) + b"\x3e\x00"


def test_inheriting_cpp_tool_copies_its_immutable_artifact() -> None:
    async def scenario() -> None:
        storage = ToolStorage()
        source = await storage.save(
            "private",
            {
                "name": "Native helper",
                "language": "cpp",
                "target_os": "linux",
                "target_arch": "x64",
                "labels": ["private"],
            },
            owner_id="source-owner",
        )
        digest = hashlib.sha256(_ELF_X64).hexdigest()
        assert await storage.save_binary(
            source["id"],
            "source-owner",
            _ELF_X64,
            "helper",
            len(_ELF_X64),
            sha256=digest,
            uploaded_by="source-owner",
            add_review=False,
        )

        inherited_ids = await _inherit_resource_ids(
            [source["id"]], "tool", "target-owner"
        )

        assert len(inherited_ids) == 1
        inherited = await storage.get(
            "private", inherited_ids[0], owner_id="target-owner"
        )
        assert inherited is not None
        assert inherited["ready"] is True
        assert inherited["binary_sha256"] == digest
        artifact = await storage.get_binary("private", inherited_ids[0])
        assert artifact is not None
        assert bytes(artifact["binary_data"]) == _ELF_X64

    asyncio.run(scenario())
