<div align="center">
  <a href="index.md">← Index</a> &nbsp;·&nbsp;
  <a href="../es/providers.md">🇪🇸 Ver en Español</a>
</div>

<br>

# Providers

Each AI provider is implemented as an adapter under `app/connections/`. The adapter receives the connection credentials from `connections.json` at runtime.

| Provider | File | Models | API Style |
|---|---|---|---|
| Anthropic | `anthropic.py` | Claude family | Anthropic Messages API |
| OpenAI | `openai.py` | GPT family, o-series | OpenAI Chat Completions |
| Google Gemini | `google.py` | Gemini family | OpenAI-compatible |
| Grok (xAI) | `grok.py` | Grok family | OpenAI-compatible |
| Qwen (Alibaba) | `qwen.py` | Qwen family | OpenAI-compatible |
| Ollama | `ollama.py` | Any local model | Ollama REST API |

---

## OpenAI-compatible providers

Gemini, Grok and Qwen expose an OpenAI-compatible chat completions endpoint. The routing in `services/chat.py` maps each `conn.type` to its base URL:

| Type | Base URL |
|---|---|
| `openai` | `https://api.openai.com/v1` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `grok` | `https://api.x.ai/v1` |
| `qwen` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |

---

## Adding a new provider

1. Create `app/connections/{name}.py`.
2. Subclass `BaseProvider` from `app/connections/base.py`.
3. Set `type_id`, `label`, `icon` and `fields`.
4. Implement the `test(config)` classmethod returning a `TestResult`.
5. Decorate the class with `@register`.
6. Import the module in `app/connections/__init__.py`.

```python
from .base import BaseProvider, FieldDef, TestResult, register

@register
class MyProvider(BaseProvider):
    type_id = "myprovider"
    label = "My Provider"
    icon = "🔌"
    fields = [
        FieldDef("api_key", "API Key", "password", required=True),
        FieldDef("model", "Model", "text", "my-model-v1"),
    ]

    @classmethod
    def test(cls, config):
        # validate credentials and return TestResult
        ...
```
