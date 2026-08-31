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
