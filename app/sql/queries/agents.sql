-- Consultas de app/storage/agent_storage.py.
-- La lista paginada y visible no está aquí: la arma scoped_resource_page.py
-- según el filtro de visibilidad, y cambia de forma con cada parámetro.

-- name: count_all
SELECT COUNT(*) FROM agents;

-- Las cuatro variantes de listado devuelven las mismas columnas a propósito:
-- _row_to_dict espera exactamente estas y nada más.

-- name: list_public
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 WHERE scope='public'
 ORDER BY created_at ASC;

-- name: list_private_by_owner
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 WHERE scope='private' AND owner_id=?
 ORDER BY created_at ASC;

-- name: list_private
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 WHERE scope='private'
 ORDER BY created_at ASC;

-- name: list_all
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 ORDER BY created_at ASC;

-- name: get_public
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 WHERE id=? AND scope='public'
 LIMIT 1;

-- name: get_private
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 WHERE id=? AND scope='private'
 LIMIT 1;

-- El privado gana al público cuando comparten id; sin el ORDER BY la fila que
-- salga depende del plan de ejecución.
-- name: get_any
SELECT id, owner_id, name, scope, data, tokens_in, tokens_out,
       is_active, deactivated_at, created_at, updated_at
  FROM agents
 WHERE id=?
 ORDER BY CASE scope WHEN 'private' THEN 0 ELSE 1 END
 LIMIT 1;

-- Upsert explícito para no perder las columnas que el INSERT no nombra (las de
-- fuente oficial). Los dos dialectos difieren solo en el nombre de la
-- pseudotabla del conflicto: EXCLUDED en PostgreSQL, excluded en SQLite.

-- name: upsert_pg
-- engine: pg
INSERT INTO agents (id, owner_id, name, scope, data, tokens_in, tokens_out,
                    is_active, deactivated_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO UPDATE SET
    name=EXCLUDED.name, scope=EXCLUDED.scope, data=EXCLUDED.data,
    tokens_in=EXCLUDED.tokens_in, tokens_out=EXCLUDED.tokens_out,
    is_active=EXCLUDED.is_active, deactivated_at=EXCLUDED.deactivated_at,
    updated_at=EXCLUDED.updated_at;

-- name: upsert_sqlite
INSERT INTO agents (id, owner_id, name, scope, data, tokens_in, tokens_out,
                    is_active, deactivated_at, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id, owner_id) DO UPDATE SET
    name=excluded.name, scope=excluded.scope, data=excluded.data,
    tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out,
    is_active=excluded.is_active, deactivated_at=excluded.deactivated_at,
    updated_at=excluded.updated_at;

-- name: add_tokens_by_owner
UPDATE agents
   SET tokens_in=tokens_in+?, tokens_out=tokens_out+?
 WHERE id=? AND scope='private' AND owner_id=?;

-- name: add_tokens
UPDATE agents
   SET tokens_in=tokens_in+?, tokens_out=tokens_out+?
 WHERE id=? AND scope='private';

-- Cada borrado comprueba antes que la fila existe y es suya: el DELETE sin
-- filas afectadas y el DELETE prohibido son indistinguibles desde el rowcount.

-- name: exists_any
SELECT id FROM agents WHERE id=? LIMIT 1;

-- name: delete_any
DELETE FROM agents WHERE id=?;

-- name: exists_owned
SELECT id FROM agents WHERE id=? AND scope!='public' AND owner_id=? LIMIT 1;

-- name: delete_owned
DELETE FROM agents WHERE id=? AND scope!='public' AND owner_id=?;

-- name: exists_not_public
SELECT id FROM agents WHERE id=? AND scope!='public' LIMIT 1;

-- name: delete_not_public
DELETE FROM agents WHERE id=? AND scope!='public';

-- name: social_category_of_agent
SELECT category, trial_missing_deps
FROM resource_social
WHERE resource_type=? AND resource_id=? AND owner=?;

-- name: delete_social_of_agent
DELETE FROM resource_social
WHERE resource_type=? AND resource_id=? AND owner=?;
