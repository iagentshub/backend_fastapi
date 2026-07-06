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
| **Workspaces** | Multi-tenancy: grupos de usuarios que comparten recursos |

---

## Almacenamiento

El sistema usa **SQLite** (desarrollo) o **PostgreSQL** (producción), seleccionado automáticamente según `DATABASE_URL`. Los ficheros de agentes y skills se almacenan en disco en `AGENTS_DIR` y `SKILLS_DIR`.

El helper `PH` en `app/storage/db.py` abstrae el dialecto SQL (`?` en SQLite, `%s` en PostgreSQL). Nunca interpolar valores de usuario directamente en SQL.

---

## Control de acceso

Existen dos formas de acceder a la plataforma:

**Email y contraseña** — el método de acceso para usuarios registrados. Las cuentas se crean mediante el flujo de registro.

**Acceso de invitado** — permite usar la plataforma sin necesidad de cuenta. El acceso de invitado tiene permisos limitados.

Las sesiones se gestionan mediante **JWT en cookie HttpOnly** (`ga_token`). Los tokens incluyen el claim `iat` (issued-at), que se valida contra `password_changed_at` en la base de datos — cambiar la contraseña invalida todas las sesiones anteriores automáticamente.

---

## Seguridad

- **Cabeceras HTTP**: `SecurityHeadersMiddleware` añade CSP, HSTS, X-Frame-Options y otras cabeceras de seguridad en cada respuesta.
- **Rate limiting**: endpoints sensibles (login, registro, hub-sync, operaciones sociales) tienen límites por IP.
- **SSRF**: `assert_safe_url()` bloquea peticiones HTTP a rangos privados y loopback.
- **Cookies**: siempre `httponly`, `samesite=lax`, `secure` (en producción), `max_age=43200`.
