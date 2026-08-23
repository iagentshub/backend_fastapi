"""La lista de migraciones, una sola vez para los dos motores.

Antes vivía duplicada: `sqlite.py` y `postgres.py` declaraban cada uno sus 29
`Migration` con las mismas versiones y los mismos nombres, y solo cambiaba la
función. Añadir un paso obligaba a tocar las dos listas y nada avisaba si se
tocaba una sola — que es exactamente el fallo que ya se materializó una vez y
motivó `tests/storage/test_migraciones_pg_traducidas.py`.

Ahora cada paso se declara **una vez** con sus dos implementaciones. Un paso
nuevo sin variante de PostgreSQL no compila la lista, en vez de arrancar y
fallar sobre una base real.

Los pasos cuyo SQL es idéntico en ambos motores viven en `shared.py` y se pasan
como la misma función a los dos lados.
"""

from __future__ import annotations

from app.storage.migrations.legacy import (
    _migrate_pg,
    _migrate_sqlite,
    _migrate_users_json_pg,
    _migrate_users_json_sqlite,
)
from app.storage.migrations.registry import MigrationPair
from app.storage.migrations.steps.knowledge import (
    _knowledge_file_metadata_pg,
    _knowledge_file_metadata_sqlite,
    _knowledge_item_checksums_pg,
    _knowledge_item_checksums_sqlite,
    _knowledge_item_metadata_repair_pg,
    _knowledge_item_metadata_repair_sqlite,
    _knowledge_items_pack_membership_pg,
    _knowledge_items_pack_membership_sqlite,
    _knowledge_pack_sources_pg,
    _knowledge_pack_sources_sqlite,
    _knowledge_pack_upload_sessions_pg,
    _knowledge_pack_upload_sessions_sqlite,
    _knowledge_packs_pg,
    _knowledge_packs_sqlite,
    _knowledge_truncation_metadata_pg,
    _knowledge_truncation_metadata_sqlite,
    _remove_obsolete_knowledge_pack_items_pg,
    _remove_obsolete_knowledge_pack_items_sqlite,
)
from app.storage.migrations.steps.misc import (
    _app_logs_structured_audit_pg,
    _app_logs_structured_audit_sqlite,
    _chat_message_interrupted_pg,
    _chat_message_interrupted_sqlite,
    _connection_provider_accounts_pg,
    _connection_provider_accounts_sqlite,
    _content_activation_pg,
    _content_activation_sqlite,
    _gdpr_legacy_owner_orphans_pg,
    _gdpr_legacy_owner_orphans_sqlite,
    _gdpr_orphan_resources_pg,
    _gdpr_orphan_resources_sqlite,
    _group_share_cascade_flag_pg,
    _group_share_cascade_flag_sqlite,
    _public_agents_in_social_catalog_pg,
    _public_agents_in_social_catalog_sqlite,
    _remove_content_activation_pg,
    _remove_content_activation_sqlite,
    _resource_origin_labels_pg,
    _resource_origin_labels_sqlite,
    _unused_indexes_audit_pg,
    _unused_indexes_audit_sqlite,
)
from app.storage.migrations.steps.official import (
    _official_component_metadata_pg,
    _official_component_metadata_sqlite,
    _official_content_as_resources_pg,
    _official_content_as_resources_sqlite,
    _official_copy_mode_pg,
    _official_copy_mode_sqlite,
    _official_explicit_selection_pg,
    _official_explicit_selection_sqlite,
    _official_published_components_pg,
    _official_published_components_sqlite,
    _official_source_import_modes_pg,
    _official_source_import_modes_sqlite,
    _official_source_provenance_pg,
    _official_source_provenance_sqlite,
    _official_tool_languages_pg,
    _official_tool_languages_sqlite,
)
from app.storage.migrations.steps.shared import (
    _app_logs_index_diet,
    _drop_redundant_indexes,
    _pagination_indexes,
    _resource_execution_leases,
    _resource_social_origin_index,
    _resource_social_page_index,
)

MIGRATION_PAIRS: tuple[MigrationPair, ...] = (
    MigrationPair(1, "legacy_schema_catchup", _migrate_sqlite, _migrate_pg, repeatable=True),
    MigrationPair(2, "users_json_to_relational", _migrate_users_json_sqlite, _migrate_users_json_pg, repeatable=True),
    MigrationPair(3, "official_component_metadata", _official_component_metadata_sqlite, _official_component_metadata_pg),
    MigrationPair(4, "resource_origin_labels", _resource_origin_labels_sqlite, _resource_origin_labels_pg),
    MigrationPair(5, "official_copy_mode", _official_copy_mode_sqlite, _official_copy_mode_pg),
    MigrationPair(6, "official_published_components", _official_published_components_sqlite, _official_published_components_pg),
    MigrationPair(7, "official_content_as_resources", _official_content_as_resources_sqlite, _official_content_as_resources_pg),
    MigrationPair(8, "official_source_provenance", _official_source_provenance_sqlite, _official_source_provenance_pg),
    MigrationPair(9, "official_explicit_selection", _official_explicit_selection_sqlite, _official_explicit_selection_pg),
    MigrationPair(10, "official_source_import_modes", _official_source_import_modes_sqlite, _official_source_import_modes_pg),
    MigrationPair(11, "official_tool_languages", _official_tool_languages_sqlite, _official_tool_languages_pg),
    MigrationPair(12, "connection_provider_accounts", _connection_provider_accounts_sqlite, _connection_provider_accounts_pg),
    # Idéntico en ambos motores: una sola función a los dos lados.
    MigrationPair(13, "resource_social_origin_index", _resource_social_origin_index, _resource_social_origin_index),
    MigrationPair(14, "public_agents_in_social_catalog", _public_agents_in_social_catalog_sqlite, _public_agents_in_social_catalog_pg),
    MigrationPair(15, "knowledge_packs", _knowledge_packs_sqlite, _knowledge_packs_pg),
    MigrationPair(16, "knowledge_file_metadata", _knowledge_file_metadata_sqlite, _knowledge_file_metadata_pg),
    MigrationPair(17, "knowledge_pack_sources", _knowledge_pack_sources_sqlite, _knowledge_pack_sources_pg),
    MigrationPair(18, "knowledge_pack_upload_sessions", _knowledge_pack_upload_sessions_sqlite, _knowledge_pack_upload_sessions_pg),
    MigrationPair(19, "knowledge_item_checksums", _knowledge_item_checksums_sqlite, _knowledge_item_checksums_pg),
    MigrationPair(20, "knowledge_items_pack_membership", _knowledge_items_pack_membership_sqlite, _knowledge_items_pack_membership_pg),
    MigrationPair(21, "knowledge_item_metadata_repair", _knowledge_item_metadata_repair_sqlite, _knowledge_item_metadata_repair_pg),
    MigrationPair(22, "remove_obsolete_knowledge_pack_items", _remove_obsolete_knowledge_pack_items_sqlite, _remove_obsolete_knowledge_pack_items_pg),
    MigrationPair(23, "pagination_indexes", _pagination_indexes, _pagination_indexes),
    MigrationPair(24, "resource_social_page_index", _resource_social_page_index, _resource_social_page_index),
    MigrationPair(25, "group_share_cascade_flag", _group_share_cascade_flag_sqlite, _group_share_cascade_flag_pg),
    MigrationPair(26, "app_logs_index_diet", _app_logs_index_diet, _app_logs_index_diet),
    MigrationPair(27, "drop_redundant_indexes", _drop_redundant_indexes, _drop_redundant_indexes),
    MigrationPair(28, "gdpr_orphan_resources", _gdpr_orphan_resources_sqlite, _gdpr_orphan_resources_pg),
    MigrationPair(29, "unused_indexes_audit", _unused_indexes_audit_sqlite, _unused_indexes_audit_pg),
    MigrationPair(30, "chat_message_interrupted", _chat_message_interrupted_sqlite, _chat_message_interrupted_pg),
    MigrationPair(31, "remove_content_activation", _remove_content_activation_sqlite, _remove_content_activation_pg),
    MigrationPair(32, "app_logs_structured_audit", _app_logs_structured_audit_sqlite, _app_logs_structured_audit_pg),
    MigrationPair(33, "gdpr_legacy_owner_orphans", _gdpr_legacy_owner_orphans_sqlite, _gdpr_legacy_owner_orphans_pg),
    MigrationPair(34, "resource_execution_leases", _resource_execution_leases, _resource_execution_leases),
    MigrationPair(35, "content_activation", _content_activation_sqlite, _content_activation_pg),
    MigrationPair(36, "knowledge_truncation_metadata", _knowledge_truncation_metadata_sqlite, _knowledge_truncation_metadata_pg),
)

__all__ = ["MIGRATION_PAIRS"]
