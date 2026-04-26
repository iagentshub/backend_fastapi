<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/architecture.md">🇬🇧 Read in English</a>
</div>

<br>

# Arquitectura

---

## Visión general

El backend es un servicio sin estado. Todo el contenido del usuario (agentes, conexiones, skills, memoria) se almacena en un directorio de datos externo que se monta en el servidor. Esto facilita las copias de seguridad, la migración y el escalado.

```
Usuario → API → Lógica de negocio → Directorio de datos
                     ↓
              Proveedor de IA (Anthropic, OpenAI, Google…)
```

---

## Componentes principales

| Componente | Qué hace |
|---|---|
| **API** | Recibe las peticiones de la interfaz de usuario y las valida |
| **Autenticación** | Verifica la identidad del usuario (Google o admin local) y protege el acceso |
| **Agentes** | Gestiona la configuración y las conversaciones de cada agente |
| **Skills** | Carga y sirve las habilidades que pueden añadirse a los agentes |
| **Memoria** | Almacena y recupera el contexto persistente de cada agente |
| **Conexiones** | Gestiona las credenciales y la comunicación con los proveedores de IA |
| **Configuración** | Lee los ajustes del sistema desde variables de entorno |

---

## Almacenamiento

No se utiliza ninguna base de datos. Toda la información se guarda como ficheros en el directorio de datos (`GAIA_DATA_DIR`):

| Contenido | Ubicación |
|---|---|
| Agentes | `data/agents/` |
| Conexiones | `data/connections/connections.json` |
| Skills | `data/skills/` |
| Memoria | `data/memory/` |
| Configuración del sistema | `data/settings.json` |

---

## Control de acceso

Existen dos formas de autenticarse:

1. **Google Sign-In** — método principal para usuarios. No requiere gestionar contraseñas.
2. **Admin local** — acceso de emergencia para el administrador mediante `GAIA_ADMIN_PASSWORD`. Útil cuando Google no está disponible o durante la configuración inicial.

Las sesiones se mantienen de forma segura sin exponer información sensible al navegador.
