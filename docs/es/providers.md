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
| **Ollama** | Cualquier modelo local |

---

## Cómo funciona

Cada proveedor requiere una clave de API (o la URL del servidor, en el caso de Ollama). Las credenciales se almacenan de forma privada en el directorio de datos y nunca se exponen en la interfaz ni en los logs.

Cuando un agente inicia una conversación, el backend selecciona el proveedor configurado para ese agente, establece la conexión y transmite la respuesta en tiempo real.

---

## Ollama

Ollama permite ejecutar modelos de IA directamente en la máquina local, sin depender de servicios externos ni incurrir en costes por uso. Es la opción recomendada para entornos sin acceso a internet o para quienes prefieren mantener todos los datos en local.

---

## Seguimiento de tokens por conexión

Cada conexión lleva la cuenta acumulada de tokens consumidos a través de ella — tanto los tokens enviados (entrada) como los recibidos (salida). Este contador se actualiza automáticamente tras cada conversación con un agente y se mantiene entre sesiones. En la página de Conexiones, el total es visible directamente en cada tarjeta de conexión.
