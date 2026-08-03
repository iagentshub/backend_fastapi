<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/providers.md">🇬🇧 Read in English</a>
</div>

<br>

# Proveedores de IA

El backend puede conectarse a múltiples proveedores de inteligencia artificial. Cada proveedor se configura de forma independiente desde la interfaz de gestión de conexiones.

---

## Proveedores compatibles

| Proveedor | Modelos |
|---|---|
| **Anthropic** | Familia Claude |
| **OpenAI** | Familia GPT, serie-o |
| **Google Gemini** | Familia Gemini |
| **Grok (xAI)** | Familia Grok |
| **Qwen (Alibaba)** | Familia Qwen |
| **NVIDIA NIM** | Llama, Mistral, Nemotron y otros modelos alojados en la nube de NVIDIA |
| **Ollama** | Cualquier modelo local |

---

## Cómo funciona

Cada proveedor requiere una clave de API (o la URL del servidor, en el caso de Ollama). Las credenciales se almacenan de forma privada en el directorio de datos y nunca se exponen en la interfaz ni en los logs.

Cuando un agente inicia una conversación, el backend selecciona el proveedor configurado para ese agente, establece la conexión y transmite la respuesta en tiempo real.

---

## NVIDIA NIM

NVIDIA NIM da acceso a más de 140 modelos alojados en la infraestructura de NVIDIA, incluyendo modelos propios (Llama, Mistral, Nemotron) y modelos de terceros (DeepSeek, Qwen, Moonshot, Mistral AI, entre otros). Requiere una clave de API obtenida desde [build.nvidia.com](https://build.nvidia.com).

El identificador del modelo sigue el formato `organización/nombre-modelo` tal como aparece en el catálogo de NVIDIA — por ejemplo, `meta/llama-3.3-70b-instruct` o `z-ai/glm4.7`. Es importante usar el nombre exacto que figura en el catálogo, ya que pequeñas diferencias pueden impedir la conexión.

---

## Ollama

Ollama permite ejecutar modelos de IA directamente en la máquina local, sin depender de servicios externos ni incurrir en costes por uso. Es la opción recomendada para entornos sin acceso a internet o para quienes prefieren mantener todos los datos en local.

---

## Seguimiento de tokens por conexión

Cada conexión lleva la cuenta acumulada de tokens consumidos a través de ella — tanto los tokens enviados (entrada) como los recibidos (salida). Este contador se actualiza automáticamente tras cada conversación con un agente y se mantiene entre sesiones. En la página de Conexiones, el total es visible directamente en cada tarjeta de conexión.

---

## Cuentas de proveedor (pestaña Proveedores)

Además de las conexiones sueltas, la pestaña "Proveedores" de Connections permite vincular una cuenta por proveedor (Anthropic, OpenAI, GitHub Copilot, Ollama, NVIDIA, Google, o una instancia remota de iAgents Hub) y sincronizar de golpe los modelos disponibles — o, para iAgents Hub, agentes/skills/conocimiento/conexiones — como recursos normales.

Para GitHub, en vez de pegar un Personal Access Token a mano, se puede usar el botón "Conectar con GitHub" (OAuth Device Flow: visitar una URL, introducir un código, autorizar desde el navegador). Requiere una GitHub OAuth App propia con "Device Flow" habilitado, configurada vía la variable de entorno `GITHUB_OAUTH_CLIENT_ID`. Sin esa variable, el botón devuelve un error claro y el usuario puede seguir pegando el token manualmente.

La misma OAuth App (mismo `GITHUB_OAUTH_CLIENT_ID`) se reutiliza también para "Continuar con GitHub" en la pantalla de login (`/login`) — un mecanismo distinto: ahí no hace falta sesión previa, y si es la primera vez que esa identidad de GitHub entra, se crea la cuenta local automáticamente (salta el modo de registro `invite`/`closed`, ya que la autorización de GitHub cuenta como verificación suficiente). El botón solo aparece si `GITHUB_OAUTH_CLIENT_ID` está configurado (`GET /api/settings/platform/public` → `oauth_github_enabled`).
