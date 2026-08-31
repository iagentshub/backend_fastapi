<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/architecture.md">🇬🇧 Read in English</a>
</div>

<br>

# Arquitectura del backend

---

## Visión general

El backend es un servicio sin estado. Toda la información del usuario —agentes, conexiones, skills, memoria— se almacena en una base de datos relacional y en un directorio de datos externo montado en el servicio. Esto facilita las copias de seguridad, la migración y la actualización sin pérdida de datos.

Cuando el frontend envía una petición, el backend la autentica, ejecuta la lógica correspondiente e interactúa con el proveedor de IA o el sistema de ficheros según sea necesario.

---

## Componentes principales

| Componente | Qué hace |
|---|---|
| **API** | Recibe las peticiones y las valida |
| **Autenticación** | Verifica la identidad del usuario y protege el acceso |
| **Agentes** | Gestiona la configuración y las conversaciones de cada agente |
| **Skills** | Carga y sirve las capacidades que pueden añadirse a los agentes |
| **Prompts y Tools** | Gestiona contenido reutilizable, implementaciones y artefactos versionados |
| **Memoria** | Almacena y recupera el contexto persistente de cada agente entre conversaciones |
| **Conexiones** | Gestiona las credenciales y la comunicación con los proveedores de IA |
| **Groups** | Multi-tenancy: grupos de usuarios que comparten recursos |

---

## Almacenamiento

El sistema usa **SQLite** (desarrollo) o **PostgreSQL** (producción), seleccionado automáticamente según `DATABASE_URL`. La base de datos es la fuente de verdad de agentes, skills, prompts, Tools, conexiones, Knowledge, relaciones y versiones. `AGENTS_DIR` y `SKILLS_DIR` se conservan únicamente para importar instalaciones legacy; no son el almacenamiento activo.

Los storages escriben placeholders `?` en ambos motores. `AsyncConn` los deja
intactos en SQLite y los traduce a `$1`, `$2`, etc. para PostgreSQL. Nunca se
interpolan valores de usuario directamente en SQL.

---

## Listados y paginación

`app/pagination/` contiene una única implementación keyset, el envoltorio
tipado v2, métricas, timeout del total y el codec HMAC. Agents,
Skills, Prompts, Tools, Knowledge y Knowledge Packs recorren exclusivamente el
índice con un keyset temporal firmado. Explore, usuarios, logs y componentes
administrativos usan el mismo contrato HTTP sobre un motor keyset compuesto
capaz de expresar órdenes ascendentes, descendentes y mixtas,
caducable y vinculado al usuario y a los filtros. Obtienen `LIMIT + 1`, derivan
`has_more` y no ejecutan `COUNT(*)` salvo con `include_total=true`; ese total se
transporta firmado durante el resto del recorrido. No existe una rama de
paginación posicional ni un paginador materializado. El catálogo de importación
usa el mismo principio para sus cinco tipos, con cursores independientes ligados
a cada filtro.
Los totales exactos comparten un cupo acotado por worker. PostgreSQL usa el
timeout nativo de asyncpg; SQLite interrumpe la máquina virtual y espera a que
el hilo de aiosqlite termine antes de reutilizar la conexión, de modo que un
timeout no siga consumiendo el pool en segundo plano.
`app/services/resource_visibility.py` centraliza propiedad, shares, grupos
activos y permisos para que seguridad y paginación no diverjan entre agentes,
skills, prompts, tools y Knowledge.

La migración 42 alinea agentes, skills, prompts y tools con su orden visible
global (`updated_at DESC, id DESC`) y los catálogos de importación de Knowledge
y Knowledge Packs con (`created_at DESC, id DESC`) en SQLite y PostgreSQL.
También retira los cuatro índices `*_owner_page` que duplicaban los índices
canónicos por propietario.
La migración 43 ajusta el índice público al orden real de Explore y añade a los
componentes oficiales columnas derivadas e índices específicos para recorridos
sin filtro, por estado, por tipo y por estado+tipo. Los filtros se aplican en
SQL antes de `LIMIT + 1`; ya no se carga el borrador entero en Python.
La migración 44 añade los órdenes físicos de Feed, Connections y
orquestaciones. Feed termina en `(resource_type, resource_id, owner)`;
Connections mezcla filas persistidas y virtuales mediante
`(updated_at, tipo_fuente, id)`. Admin Explore pagina primero identificadores de
una unión normalizada e hidrata solo la página visible; el visor de tablas usa
la clave primaria simple o compuesta y rechaza tablas sin clave estable.
El watermark `snapshot_at`, activo por defecto, acota el recorrido frente a
nuevas altas sin mantener una transacción abierta. No es aislamiento
transaccional frente a ediciones del campo de orden.
El benchmark `tests/performance/test_pagination.py` verifica tanto que una
página decodifica solo sus objetos como que el listado visible, incluido su
predicado keyset, no vuelve a una ordenación temporal.
`tests/performance/test_pagination_postgres.py` ejecuta además `EXPLAIN
(ANALYZE, BUFFERS)` sobre PostgreSQL 16 con el predicado real de propietario,
grupo activo y share. En la medición local de 100.000 filas, la referencia
posicional recorrió 90.050 filas en 156,429 ms y keyset 50 en 3,585 ms. El workflow
`pagination-postgres.yml` corre manualmente y en PR cuando cambian paginación,
SQL, índices, storages o visibilidad; también arranca una API real sobre
PostgreSQL y comprueba Feed, Connections, Admin Explore y metadata.

Flutter, su Dashboard, Hub Sync y la extensión VS Code consumen `/api/v2` y el
envoltorio estricto `items + page`; no aceptan listas legacy como respuesta v2.
Los GET de listado anteriores y sus cabeceras de transición se eliminaron;
Chat conserva sus cabeceras porque ya usa cursor con un contrato distinto.
`/api/admin/stats` agrega por tipo peticiones, latencia, profundidad, totales,
reutilización y timeouts, sin etiquetas por
usuario ni cursor que disparen la cardinalidad. Los acumuladores viven en
memoria y son por proceso; una instalación multi-worker debe agregarlos en su
sistema de observabilidad si necesita una vista global.

El catálogo público (`resource_social`) sigue la misma regla: su orden termina
en la clave primaria para que dos páginas nunca repitan ni pierdan una fila
—las publicaciones de una sincronización oficial comparten `updated_at`— y la
migración 24 añade el índice que cubre ese orden. La procedencia de los
documentos de la página se resuelve con `KnowledgeStorage.pack_locations()`, una
sola consulta sin la columna `content`, en vez de un `get()` por fila.


El repositorio tiene **dos** motores keyset y la diferencia es de plan, no de
resultado: `cursor_page_query` compara tuplas —`(posicion, id) < (?, ?)`, un
solo descenso por el índice— y solo sirve con todas las columnas en la misma
dirección; `composite_cursor_page` expande a `OR`/`AND` porque sus consumidores
mezclan direcciones. Devuelven las mismas filas, así que intercambiarlos no
rompe ningún test y sí degrada la consulta.

### El panel de administración también

Los listados de `/api/admin` se quedaron fuera de esa migración, y eran los
únicos del producto cuyo tamaño no lo decide un usuario sino la instalación
entera: once `GET` devolvían `SELECT … FROM tabla` sin `WHERE` y sin cota.
**Se retiraron.** El inventario del panel se pide por `/api/v2/admin/explore`,
que ya pagina y cubre los once tipos con columnas normalizadas; de los listados
por tipo solo queda `/api/v2/admin/connections`, que es el único con consumidor
—el selector de conexiones LLM de la importación oficial—. Publicar diez rutas
más que nadie llamaba era superficie que mantener sin nadie a quien servir.

Tres cosas que se aprendieron paginándolos:

- **La clave keyset del panel lleva `owner_id`.** Seis de esas tablas tienen PK
  compuesta `(id, owner_id)`: para un usuario, que solo ve lo suyo, `id` basta;
  el administrador los ve todos a la vez y ahí `(updated_at, id)` deja de ser
  única. Un keyset con clave repetida se salta filas en el corte de página sin
  que nada falle.
- **El nombre del dueño sale del `JOIN`.** Cada listado llamaba a
  `_username_map` —la tabla `users` completa— y el panel pinta varias pestañas
  por carga: nueve copias en la misma sesión.
- **Los filtros del directorio de usuarios viajan a SQL.** Aplicarlos en
  Python sobre una página devuelve resultados incompletos sin que se note: el
  usuario existe, no cae en la página filtrada, y la pantalla dice que no hay
  ninguno.

`GET /api/admin/memory` es el caso que más costaba: traía la columna `content`
—la memoria de largo plazo de cada agente de cada usuario, texto libre y sin
cota— para hacerle `len()` y tirarla. Hoy el tamaño lo calcula
`LENGTH(content)` en SQL. Es la misma lección que dejó escrita la mudanza del
avatar, en otra tabla.

Los recuentos que acompañan a grupos y usuarios se piden solo para los
identificadores de la página, no agregando sobre las tablas completas. El de
agentes por grupo, además, se leía recorriendo `AGENTS_DIR/private/*/config.json`
—ficheros que dejó la migración a base de datos y nadie borró—, así que en
cualquier instalación creada después daba cero; ahora sale de `agents`.

El recuento de agentes por grupo y los `agents_public`/`agents_private` de
`GET /api/admin/stats` se leían recorriendo `AGENTS_DIR/*/config.json` —los
ficheros que dejó la migración fichero→base de datos—, así que en cualquier
instalación creada después daban cero. Los dos salen ya de la tabla `agents`.
La migración 45 promueve además `connection_id` del blob JSON a columna con
índice, para que «¿qué agentes usan esta conexión?» sea un `COUNT(*)` en vez de
traerse todos los agentes de la instalación y filtrarlos en Python.

Retirar los once dejó a la vista que el inventario arrastraba los mismos
defectos: hacía `SELECT *` sobre `memory_files` y `len(content)` en Python —el
recorte que este trabajo venía a quitar, vivo justo donde se ejecuta—, no
excluía a los invitados y no servía ni `avatar_url` ni los recuentos que pintan
las tarjetas del panel. Su proyección lleva ahora `LENGTH(content)`, el `JOIN`
de la foto y los recuentos pedidos solo para los identificadores de la página.

`tests/api/test_listados_con_cota.py` es lo que evita que esto vuelva:
recorre `app/api/routes/` y falla si un `GET` devuelve una lista sin `limit` ni
`cursor`. Lo que ya estaba y sigue sin cota se declara en su `DEUDA` con el
motivo, y esa lista solo puede encoger.

## Control de acceso

Existen dos formas de acceder a la plataforma:

**Username o email y contraseña** — el username público y el email privado identifican la misma cuenta. Las relaciones y los recursos personales usan el `users.id` interno, nunca el username; los recursos del espacio de un grupo usan su `group_id`.

**Acceso de invitado** — permite usar la plataforma sin necesidad de cuenta. El acceso de invitado tiene permisos limitados.

Las sesiones se gestionan mediante **JWT en cookie HttpOnly** (`ga_token`). Los tokens incluyen el claim `iat` (issued-at), que se valida contra `password_changed_at` en la base de datos — cambiar la contraseña invalida todas las sesiones anteriores automáticamente.

---

## Seguridad

- **Cabeceras HTTP**: `SecurityHeadersMiddleware` añade CSP, HSTS, X-Frame-Options y otras cabeceras de seguridad en cada respuesta.
- **Rate limiting**: endpoints sensibles (login, registro, hub-sync, operaciones sociales) tienen límites por IP.
- **SSRF**: `assert_safe_url()` bloquea peticiones HTTP a rangos privados y loopback.
- **Cookies**: siempre `httponly`, `samesite=lax`, `secure` (en producción), `max_age=43200`.
