# El SQL del backend

Todo el SQL estático vive aquí y el código lo pide por identificador:

```python
from app.sql import sql

sql("schema/agents")           # app/sql/schema/agents.sql, entero
sql("queries/agents:get_any")  # la sección `-- name: get_any`
```

- `schema/` — una tabla por fichero, con sus índices al lado. El orden de
  creación está en `TABLAS`, en `app/storage/schema.py` (lo imponen las claves
  ajenas). Tabla nueva = fichero nuevo + su nombre en esa lista.
- `queries/` — un fichero por módulo, dividido en secciones `-- name: nombre`.

Ver `docs/adr/007-sql-en-ficheros.md` para el porqué.

## Los dos motores

El backend corre sobre SQLite o PostgreSQL con el mismo código. **De las 441
secciones, 412 valen para los dos**; las otras 29 existen por duplicado o
tienen una sola versión porque solo tienen sentido en un motor.

Lo que **sí** comparten todo:

- **Los placeholders.** Se escribe siempre `?` y `AsyncConn` los traduce a `$N`
  para asyncpg. Nunca escribas `$1` a mano.
- **Los booleanos.** El esquema usa `INTEGER` en SQLite y `SMALLINT` en
  PostgreSQL —no `BOOLEAN`— justo para que `is_active = 1` funcione igual en
  los dos.
- **El resto del DDL**, salvo cuatro marcadores que `schema.py` traduce:
  `@BOOL@`, `@SERIAL@`, `@FLOAT@` y `@NOW@`.

### Cómo se marca

Una consulta que solo vale en un motor **lo declara**, justo bajo su nombre:

```sql
-- name: upsert_pg
-- engine: pg
INSERT INTO agents (…) VALUES (…)
ON CONFLICT (id, owner_id) DO UPDATE SET name=EXCLUDED.name, …;

-- name: upsert_sqlite
-- engine: sqlite
INSERT INTO agents (…) VALUES (…)
ON CONFLICT (id, owner_id) DO UPDATE SET name=excluded.name, …;
```

`-- engine:` es metadato: el cargador lo saca del cuerpo, así que no llega al
motor. Se declara en vez de deducirse del sufijo `_pg` del nombre porque el
sufijo es una convención y basta olvidarlo una vez.

Las dos variantes de la misma operación viven **juntas, en el fichero de su
dominio**, y no en directorios separados por motor: son la misma sentencia
escrita dos veces, y separarlas es como se añade una columna a una y no a la
otra — el fallo original del punto 43, que la suite no ve porque corre en
SQLite.

### Qué delata que una consulta es de un solo motor

| Solo SQLite | Solo PostgreSQL | Equivalente común |
|---|---|---|
| `INSERT OR IGNORE` | `ON CONFLICT … DO NOTHING` | el de PostgreSQL vale en ambos |
| `INSERT OR REPLACE` | `ON CONFLICT … DO UPDATE` | escribir las dos versiones |
| `excluded.col` | `EXCLUDED.col` | irrelevante: PostgreSQL acepta ambas grafías |
| `datetime('now')`, `strftime(…)` | `now()`, `::TEXT` | pasar la fecha como parámetro |
| `sqlite_master`, `dbstat`, `PRAGMA` | `information_schema`, `pg_*`, `to_regclass` | no lo hay: son catálogos propios |

Los upserts son el grueso: casi siempre existen como `…_pg` y `…_sqlite`, y el
código elige con `_db.IS_PG` **leído en la llamada**, nunca importado por valor.

### Cómo saber en qué grupo cae una consulta

No hace falta fiarse del sufijo del nombre. Los tests lo clasifican por la
sintaxis y lo dicen:

```bash
python3.11 -m pytest tests/storage/test_sql_en_ficheros.py -q
```

Dos tests trabajan juntos:

- `test_toda_consulta_dialectal_declara_su_motor` compara la **sintaxis** con lo
  declarado: sintaxis de un solo motor sin `-- engine:` es un error, y un
  `-- engine:` que contradiga a la sintaxis también.
- `test_las_consultas_dialectales_solo_corren_en_su_motor` comprueba que lo
  declarado coincide con la rama de `IS_PG` desde la que se usa, y nombra
  fichero y línea.

Las listas `SOLO_SQLITE` y `SOLO_PG` del primero son la definición operativa de
"esto es dialectal": si añades una construcción que solo entiende un motor y no
está en esas listas, nadie te avisará de que falta el marcador.

Para comprobar que una consulta es **válida** —y no solo que está en la rama
correcta— están las pruebas de preparación:

```bash
python3.11 -m pytest tests/storage/test_sql_contra_motores.py -q     # SQLite

docker run -d --rm --name sqltest-pg -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=sqltest -p 55433:5432 postgres:16-alpine
GAIA_TEST_PG_DSN=postgresql://postgres:test@127.0.0.1:55433/sqltest \
    python3.11 -m pytest tests/storage/test_sql_contra_motores.py -q  # + PostgreSQL
```

Preparan cada sentencia contra el esquema real, lo que valida sintaxis, tablas
y columnas sin ejecutar nada. **La de PostgreSQL se salta sin `GAIA_TEST_PG_DSN`,
así que sus 12 consultas propias solo se prueban si alguien levanta la base.**

### Lo que ninguna herramienta detecta

Estas diferencias son de comportamiento, no de sintaxis: pasan los dos tests y
aun así se comportan distinto.

- **`LIKE` distingue mayúsculas en PostgreSQL y no en SQLite.** Por eso las
  búsquedas de usuario llevan `LOWER()` en los dos lados.
- **Los tipos.** PostgreSQL es estricto: pasar un `int` donde la columna es
  `TEXT` funciona en SQLite y falla en PostgreSQL. Y `SUM()` devuelve `Decimal`
  en PostgreSQL frente a `int` en SQLite, así que conviene envolver el
  resultado en `int(...)`.
- **`GROUP BY` es estricto en PostgreSQL**: toda columna del `SELECT` que no
  sea un agregado tiene que estar agrupada. SQLite lo perdona.
- **El orden sin `ORDER BY`** no está garantizado en ninguno de los dos, pero
  difieren en la práctica; los listados paginados llevan una clave única al
  final del `ORDER BY` por eso.
