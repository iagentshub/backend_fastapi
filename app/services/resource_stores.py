"""Instancias únicas de los almacenes que comparten rutas y servicios.

Construir un storage dentro de cada handler reejecutaba su migración legacy —el
flag es de instancia— y con ella un `SELECT COUNT(*)` por petición. Al repartir
`social.py` entre rutas y servicios estas instancias habrían pasado a
duplicarse una vez por módulo, así que viven en un solo sitio.
"""

from __future__ import annotations

import app.config.data as _cfg
from app.storage.agent_storage import AgentStorage
from app.storage.groups import GroupStorage
from app.storage.knowledge import KnowledgeStorage
from app.storage.knowledge_packs import KnowledgePackStorage
from app.storage.memory_storage import MemoryStorage
from app.storage.prompt_storage import PromptStorage
from app.storage.skill_storage import SkillStorage
from app.storage.tool_storage import ToolStorage
from app.storage.workflows import WorkflowStorage

# Singletons de módulo: construir un storage dentro de cada handler reejecutaba
# su migración legacy (el flag era de instancia), y con ella un SELECT COUNT(*)
# por petición. Mismo patrón que agents.py y connections.py.
_agents_store = AgentStorage(_cfg.AGENTS_DIR)

_skills_store = SkillStorage(_cfg.SKILLS_DIR)

_prompts_store = PromptStorage()

_tools_store = ToolStorage()

_knowledge_store = KnowledgeStorage()

_knowledge_packs_store = KnowledgePackStorage()

_groups_store = GroupStorage()

_workflows_store = WorkflowStorage()

_memory_store = MemoryStorage(_cfg.MEMORY_DIR)
