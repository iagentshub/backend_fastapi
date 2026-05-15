<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/architecture.md">🇬🇧 Read in English</a>
</div>

<br>

# Arquitectura del backend

---

## Visión general

El backend es un servicio sin estado. Toda la información del usuario —agentes, conexiones, skills, memoria— se almacena en un directorio de datos externo montado en el servicio. Esto facilita las copias de seguridad, la migración y la actualización sin pérdida de datos.

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

---

## Almacenamiento

No se utiliza ninguna base de datos. Toda la información se guarda como ficheros en el directorio de datos. Esto hace que el sistema sea predecible, fácil de respaldar y portátil entre entornos.

---

## Control de acceso

Existen dos formas de acceder a la plataforma:

**Email y contraseña** — el método de acceso para usuarios registrados. Las cuentas se crean mediante el flujo de registro.

**Acceso de invitado** — permite usar la plataforma sin necesidad de cuenta. El acceso de invitado tiene permisos limitados.

Las sesiones se mantienen de forma segura sin exponer información sensible al navegador.
