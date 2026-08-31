-- Consultas de app/api/routes/admin/resources.py.

-- name: social_exists
SELECT 1
FROM resource_social
WHERE resource_type=? AND resource_id=?;

-- name: set_verified
UPDATE resource_social
SET verified=?
WHERE resource_type=? AND resource_id=?;

-- name: user_by_username
SELECT id, username, is_active
FROM users
WHERE username=?;

-- Recuentos que la tarjeta de grupo del panel pinta. Acotados a los grupos de
-- la página: agregar sobre las tablas completas para pintar los que caben en
-- pantalla es lo que este trabajo vino a quitar.
-- name: members_per_group
SELECT group_id, COUNT(*)
FROM group_members
WHERE group_id IN (@ids@)
GROUP BY group_id;

-- name: connections_per_owner
SELECT owner_id, COUNT(*), COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0)
FROM connections
WHERE owner_id IN (@ids@)
GROUP BY owner_id;

-- name: knowledge_per_owner
SELECT owner_id, COUNT(*)
FROM knowledge_items
WHERE owner_id IN (@ids@)
GROUP BY owner_id;

-- name: agents_per_owner
-- Salía de recorrer AGENTS_DIR/private/*/config.json, los ficheros que dejó la
-- migración a base de datos y nadie borró: en una instalación creada después
-- el glob no encuentra nada y el panel enseñaba cero agentes en todos los
-- grupos. Los agentes viven en `agents`.
SELECT owner_id, COUNT(*)
FROM agents
WHERE owner_id IN (@ids@)
GROUP BY owner_id;
