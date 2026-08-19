"""Almacenes que comparten el enlazado, la sincronización y la prueba."""


from __future__ import annotations

import app.config.data as _cfg

# db se importa como MÓDULO a propósito: IS_PG debe leerse en el momento de
# la llamada. Traerlo por valor congela el dialecto en el arranque y el
# monkeypatch de los tests no llega — ver
# tests/storage/test_is_pg_en_tiempo_de_llamada.py.
from app.storage.agent_storage import AgentStorage
from app.storage.connection_storage import ConnectionStorage
from app.storage.knowledge import KnowledgeStorage
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

_workflows_store = WorkflowStorage()

_conns_store = ConnectionStorage()
