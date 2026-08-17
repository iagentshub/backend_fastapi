# 007 · El SQL estático vive en ficheros, no en literales de Python

- **Fecha**: 2026-08-17
- **Estado**: aceptada
- **Afecta a**: `backend_fastapi` — `app/sql/`, `app/storage/schema.py`,
  y los 40 módulos de `storage/`, `auth/`, `services/` y `api/routes/` que
  ejecutaban SQL

## Contexto

El backend tenía unas 470 sentencias SQL escritas como literales de Python,
repartidas por más de cuarenta módulos: `storage/`, pero también `auth/`,
`services/` y las propias rutas de la API. Revisar "todas las consultas sobre
`agents`" obligaba a abrir media docena de ficheros y a leer SQL partido en
trozos de cadena concatenados, con la indentación al servicio del Python y no
de la consulta.

El DDL ya había pasado por su propia versión de este problema. `schema.py`
tenía dos constantes de 331 líneas —`SCHEMA_SQLITE` y `SCHEMA_PG`— idénticas al
91%; se unificaron en una plantilla con marcadores `@BOOL@`, `@SERIAL@`,
`@FLOAT@` y `@NOW@`, pero el resultado seguía siendo un literal de 584 líneas
dentro de un módulo Python, con un consumidor —`flog`— que necesitaba una sola
tabla y la extraía **filtrando el DDL completo por la substring `app_logs`**.

Dos condiciones previas hacían el cambio barato:

- Los placeholders ya son neutros. Todo el código escribe `?` y `AsyncConn` los
  traduce a `$N` para asyncpg, así que la SQL de aplicación no depende del
  motor salvo en los upserts, donde el dialecto está en el nombre de la
  pseudotabla del conflicto (`EXCLUDED` / `excluded`).
- El empaquetado no exige nada: el Dockerfile hace `COPY . .` y `.dockerignore`
  no excluye `.sql`.

## Decisión

El SQL estático vive en `app/sql/` y el código lo pide por identificador:

    sql("schema/agents")           -> app/sql/schema/agents.sql, entero
    sql("queries/agents:get_any")  -> la sección `-- name: get_any`

El esquema va a **fichero por tabla** (36 ficheros, cada uno con sus índices al
lado) porque hay consumidores que quieren una sola tabla: `tabla_ddl("app_logs",
"sqlite")` sustituye al filtrado por substring, que se habría llevado por
delante cualquier otra tabla o índice que mencionara ese nombre.

Las consultas van **agrupadas por módulo con secciones** (42 ficheros, 440
secciones): un fichero por sentencia serían seiscientos ficheros, y el objetivo
era poder leerlas juntas, no dispersarlas de otra manera.

`schema.py` conserva lo que no es SQL: la lista ordenada de tablas —el orden
importa por las claves ajenas— y la tabla de traducción de marcadores por
dialecto. `SCHEMA_SQLITE` y `SCHEMA_PG` siguen existiendo con el mismo valor,
así que ninguno de sus consumidores se enteró del cambio.

## Lo que queda deliberadamente fuera

- **Las 66 consultas que se construyen en tiempo de ejecución**: filtros
  opcionales, listas `IN` de longitud variable, la tabla como parámetro. Un
  fichero estático no las representa sin inventar un lenguaje de plantillas,
  que es la complejidad que este diseño evita. Siguen en su módulo.
- **Los `PRAGMA` de `db.py`**: son configuración de la conexión SQLite, no
  consultas.
- **Las migraciones** (`storage/migrations/`): son una secuencia histórica y su
  SQL está entrelazado con el Python que transforma los datos de cada paso. La
  duplicación entre `sqlite.py` y `postgres.py` sigue abierta; es lo que queda
  vivo del punto 43 de la revisión.

## Alternativas descartadas

**Un ORM.** Resolvería la duplicación del DDL, pero el modelo de datos guarda
los recursos como blobs JSON y las consultas los filtran por columnas sueltas:
un ORM encima de eso añade una capa que hay que pelear en cada consulta no
trivial, y son la mayoría.

**Un fichero `.sql` por sentencia.** Seiscientos ficheros para poder leer el
SQL junto es cambiar un problema de dispersión por otro.

**Sacar también las consultas dinámicas, con plantillas.** Es el punto donde
este diseño se convertiría en un mini-lenguaje que hay que aprender y depurar,
sin ganar legibilidad: una consulta que cambia de forma según los parámetros se
entiende mejor junto al código que decide esa forma.

## Consecuencias

El modo de fallo cambia, y esa es la parte que hay que vigilar: antes una
consulta rota era un error de sintaxis SQL a la vista en el módulo; ahora un
identificador mal escrito es un `LookupError` que no aparece hasta que esa rama
se ejecuta —y varias ramas solo se ejecutan en PostgreSQL, que la suite no
prueba—. `tests/storage/test_sql_en_ficheros.py` cubre las tres formas de que
eso pase: SQL que vuelve al Python, un identificador que no resuelve, y una
sección que ya no usa nadie. El test de referencias busca la forma del
identificador y no la llamada `sql(...)`, porque varias eligen sección con un
condicional y anclar en el paréntesis dejaba fuera justo la rama de PostgreSQL.

El contenido se cachea al primer acceso: `flog` pide SQL al arrancar cada
proceso y las rutas en cada petición.

Tres divergencias salieron a la luz al mover el código, y las tres están
corregidas:

- `CREATE INDEX idx_connections_owner` estaba declarado suelto en
  `db.py::migrate_schema`, dentro de la rama de SQLite: **PostgreSQL nunca lo
  creaba**. Ahora está en `sql/schema/connections.sql` y lo obtienen los dos.
- La migración legacy de `memory_files` ejecutaba `INSERT OR IGNORE` **sin
  mirar el motor**: en PostgreSQL es un error de sintaxis que el `except` de al
  lado degradaba a un warning por fichero, así que no migraba nada y no lo
  decía. Ahora elige sección según `IS_PG`, como el resto.
- `flog` extraía el DDL de `app_logs` por substring.

Las 29 consultas de un solo motor lo **declaran** con `-- engine: pg|sqlite`
bajo su nombre; el cargador trata la línea como metadato y no la manda al
motor. Se descartó separarlas en directorios (`queries/pg`, `queries/sqlite`):
habría repartido 13 de los 42 dominios entre carpetas y, sobre todo, habría
separado **21 pares que son la misma operación en dos dialectos** — y tenerlos
adyacentes es lo que evita añadir una columna a uno y olvidarla en el otro, que
es el fallo original de este punto.

Dos tests lo sostienen: uno compara la sintaxis con lo declarado (sintaxis
dialectal sin marcador, o marcador que la contradice), y otro comprueba que lo
declarado coincide con la rama de `IS_PG` desde la que se usa. La sintaxis
manda sobre el nombre: un sufijo `_pg` es una convención, y la convención es
justo lo que se olvida.

`tests/storage/test_sql_contra_motores.py` da el otro nivel de garantía:
**prepara** cada consulta contra una base con el esquema real, lo que valida
sintaxis, tablas y columnas sin ejecutar nada. En SQLite corre siempre; contra
PostgreSQL usa `prepare()` de asyncpg y se salta sin `GAIA_TEST_PG_DSN`, igual
que `test_flog_postgres.py`.

**Se ejecutó contra un PostgreSQL 16 real** —esquema, las 26 migraciones y el
catálogo entero— y encontró tres cosas que nadie había visto porque la suite
corre en SQLite:

- **La migración 12 estaba copiada de `sqlite.py` sin traducir**: `fetchall`,
  `fetchone` y marcadores `?` sobre la conexión asyncpg en crudo, que no tiene
  esos métodos. Reventaba con `AttributeError`, así que **ninguna instalación
  nueva sobre PostgreSQL llegaba a arrancar**. La cubre ahora
  `tests/storage/test_migraciones_pg_traducidas.py`, que rechaza esos tres
  patrones sin necesitar una base.
- **`pg_table_stats` leía `tablename` de `pg_stat_user_tables`**, columna que
  se llama `relname` (`tablename` es de `pg_tables`): el panel de tablas del
  admin fallaba entero en PostgreSQL.
- Un comentario con un `;` dentro partía en dos el `CREATE TABLE` de `skills`,
  porque `migrate_schema` trocea el DDL de PostgreSQL por `;`. Lo vigilan dos
  tests nuevos en `test_schema_dialectos.py`: ningún comentario del esquema
  lleva `;`, y cada trozo del DDL troceado es una sentencia completa.

`tests/storage/test_schema_usage.py`, que comprueba que ninguna tabla declarada
se queda sin consumidor, tuvo que aprender a leer los `.sql`: buscar la tabla
solo en los `.py` daba por muerta cualquiera cuyo único consumidor fuera una
consulta en fichero. Cualquier otro guard que haga grep sobre el código tiene
el mismo punto ciego.

## Lo que el catálogo hizo visible después

Con todas las consultas en un sitio, revisarlas en bloque dejó de ser
impracticable. La primera revisión encontró esto:

- **`app_logs` tenía seis índices y solo dos servían.** El visor filtra `ip` y
  `username` con `LIKE '%x%'` —comodín inicial, un B-tree no se puede usar— y
  siempre ordena por `ts DESC` con LIMIT. Los de `level` y `source` sí se
  elegían y por eso hacían daño: al entrar por ellos se pierde el orden y hay
  que ordenar el resultado entero. Medido sobre 200.000 filas con ERROR al 1%:
  filtrar por fuente pasó de 18,66 ms a 0,06 ms, insertar 200.000 filas de
  1.685 ms a 565 ms, y la base ocupa un 27% menos (migración 26).
- **Siete índices más duplicaban un `UNIQUE` o una `PRIMARY KEY`**
  (`idx_users_email`, `idx_users_username`, `idx_pat_hash`,
  `idx_group_share_resource`, `idx_resource_source_resource`,
  `idx_resource_versions_lookup`, `idx_workflow_run_events_run`). Comparados
  los planes de las 97 consultas que tocan esas seis tablas, 14 cambian de
  índice y ninguna a peor —pasan al implícito, con el mismo tipo de acceso—; en
  cuatro casos el planificador ya prefería el implícito teniendo ambos.
  Costaban entre un 11% y un 20% del tamaño de esas tablas y en torno a un 25%
  del tiempo de inserción (migración 27).
- **`official_component_id` se escribe en seis tablas y no la lee nadie**, pero
  **se queda**. La revisión tumbó el argumento que la señalaba: a `NULL` —el
  caso de todo recurso que no venga de una fuente oficial— no ocupa nada, 0
  bytes por fila medidos sobre 100.000. Además viaja en el export RGPD, que
  hace `SELECT *`, y `docs/es/api.md` la documenta como el registro de
  procedencia de un recurso materializado. Eliminarla serían seis migraciones
  destructivas para no ganar espacio. Lo que sí se hizo fue dejarlo escrito en
  el esquema: es trazabilidad de solo escritura, y quien necesite el dato entra
  por `resource_source_links.component_key`.
- El guard tenía un agujero: solo miraba el argumento de `conn.execute(...)`,
  así que un `query = "INSERT …"` en la línea anterior pasaba de largo —por ahí
  seguía el UPSERT del rate limiter—. Lo cierra
  `test_no_hay_sql_estatica_asignada_a_variables`, con lista explícita de los
  cuatro fragmentos que sí se completan en ejecución.

