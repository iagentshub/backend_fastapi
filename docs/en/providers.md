<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/providers.md">🇪🇸 Ver en Español</a>
</div>

<br>

# AI Providers

The backend can connect to multiple AI providers. Each provider is configured independently from the connections management interface.

---

## Supported providers

| Provider | Models |
|---|---|
| **Anthropic** | Claude family |
| **OpenAI** | GPT family, o-series |
| **Google Gemini** | Gemini family |
| **Grok (xAI)** | Grok family |
| **Qwen (Alibaba)** | Qwen family |
| **Ollama** | Any local model |

---

## How it works

Each provider requires an API key (or server URL, in the case of Ollama). Credentials are stored privately in the data directory and are never exposed in the interface or in logs.

When an agent starts a conversation, the backend selects the provider configured for that agent, establishes the connection, and streams the response in real time.

---

## Ollama

Ollama allows running AI models directly on the local machine, without depending on external services or incurring usage costs. It is the recommended option for environments without internet access or for those who prefer to keep all data local.

---

## Token tracking per connection

Each connection keeps a cumulative count of tokens consumed through it — both tokens sent (input) and received (output). This counter updates automatically after every agent conversation and persists between sessions. On the Connections page, the total is visible directly on each connection card.
