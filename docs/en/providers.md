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
| **NVIDIA NIM** | Llama, Mistral, Nemotron and other models hosted on NVIDIA's cloud |
| **Ollama** | Any local model |

---

## How it works

Each provider requires an API key (or server URL, in the case of Ollama). Credentials are stored privately in the data directory and are never exposed in the interface or in logs.

When an agent starts a conversation, the backend selects the provider configured for that agent, establishes the connection, and streams the response in real time.

---

## NVIDIA NIM

NVIDIA NIM provides access to over 140 models hosted on NVIDIA's infrastructure, including NVIDIA's own models (Llama, Mistral, Nemotron) and third-party models (DeepSeek, Qwen, Moonshot, Mistral AI, and others). Requires an API key obtained from [build.nvidia.com](https://build.nvidia.com).

The model identifier follows the format `organization/model-name` as shown in the NVIDIA catalog — for example, `meta/llama-3.3-70b-instruct` or `z-ai/glm4.7`. Using the exact name from the catalog is important, as small differences can prevent the connection from working.

---

## Ollama

Ollama allows running AI models directly on the local machine, without depending on external services or incurring usage costs. It is the recommended option for environments without internet access or for those who prefer to keep all data local.

---

## Token tracking per connection

Each connection keeps a cumulative count of tokens consumed through it — both tokens sent (input) and received (output). This counter updates automatically after every agent conversation and persists between sessions. On the Connections page, the total is visible directly on each connection card.
