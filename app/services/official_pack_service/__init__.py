"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

import app.config.data as _cfg
from app.models.official_source import (
    LinkOfficialPackRequest,
    LinkOfficialPackResult,
    PublicOfficialPack,
    PublicOfficialPackComponent,
    PublicOfficialPackDetail,
)
from app.services.official_pack_service._linking import _PackLinkingMixin
from app.services.official_pack_service._listing import _PackListingMixin
from app.sql import sql
from app.storage import db as _db
from app.storage.agent_storage import AgentStorage
from app.storage.db import AsyncConn, open_db
from app.storage.knowledge import KnowledgeStorage, _coerce_active
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage
from app.utils.generators import generate_id


class OfficialPackService(_PackListingMixin, _PackLinkingMixin):
    def __init__(self) -> None:
        self.agents = AgentStorage(_cfg.AGENTS_DIR)
        self.skills = SkillStorage(_cfg.SKILLS_DIR)
        self.prompts = PromptStorage()
        self.tools = ToolStorage()
        self.knowledge = KnowledgeStorage()
        self.memory = MemoryStorage(_cfg.MEMORY_DIR)
        self.workflows = WorkflowStorage()
"""

from __future__ import annotations

import app.config.data as _cfg
from app.services.official_pack_service._linking import _PackLinkingMixin
from app.services.official_pack_service._listing import _PackListingMixin
from app.storage.agent_storage import AgentStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage

__all__ = ["OfficialPackService"]


class OfficialPackService(_PackListingMixin, _PackLinkingMixin):
    def __init__(self) -> None:
        self.agents = AgentStorage(_cfg.AGENTS_DIR)
        self.skills = SkillStorage(_cfg.SKILLS_DIR)
        self.prompts = PromptStorage()
        self.tools = ToolStorage()
        self.knowledge = KnowledgeStorage()
        self.memory = MemoryStorage(_cfg.MEMORY_DIR)
        self.workflows = WorkflowStorage()
