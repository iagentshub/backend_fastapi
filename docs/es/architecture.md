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
| **Memoria** | Almacena y recupera el contexto persistente de cada agente entre conversaciones |
| **Conexiones** | Gestiona las credenciales y la comunicación con los proveedores de IA |
| **Groups** | Multi-tenancy: grupos de usuarios que comparten recursos |

---

## Almacenamiento

El sistema usa **SQLite** (desarrollo) o **PostgreSQL** (producción), seleccionado automáticamente según `DATABASE_URL`. Los ficheros de agentes y skills se almacenan en disco en `AGENTS_DIR` y `SKILLS_DIR`.

El helper `PH` en `app/storage/db.py` abstrae el dialecto SQL (`?` en SQLite, `%s` en PostgreSQL). Nunca interpolar valores de usuario directamente en SQL.

---

## Listados y paginación

`app/pagination/` define páginas offset y cursor, cabeceras HTTP y el codec de
cursores. `app/storage/page_query.py` ejecuta `COUNT(*)` y `LIMIT/OFFSET` sobre
el mismo `WHERE`; cada storage selecciona sus columnas y decodifica únicamente
las filas de la página. `app/services/resource_visibility.py` centraliza
propiedad, shares, grupos activos y permisos para que seguridad y paginación no
divergan entre agentes, skills, prompts, tools y Knowledge.

La migración 23 añade índices compuestos para órdenes estables (`fecha DESC, id
DESC`) en SQLite y PostgreSQL. El benchmark
`tests/performance/test_pagination.py` verifica que una página de 50 sobre
10.000 filas decodifica solo esos 50 objetos.

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
