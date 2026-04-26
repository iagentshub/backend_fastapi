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

Gemini, Grok and Qwen expose an OpenAI-compatible chat completions endpoint. All URLs are defined in `app/config/providers.py` (`OPENAI_COMPAT_URLS`) and derived from the centralised base URLs in `PROVIDER_BASE_URLS`.

| Type | Base URL |
|---|---|
| `openai` | `PROVIDER_BASE_URLS["openai"]` |
| `gemini` | `PROVIDER_BASE_URLS["gemini"]` |
| `grok` | `PROVIDER_BASE_URLS["grok"]` |
| `qwen` | `PROVIDER_BASE_URLS["qwen"]` |

---

## Adding a new provider

1. Add the base URL and default model to `app/config/providers.py`.
2. Create `app/connections/{name}.py`.
3. Subclass `BaseProvider` from `app/connections/base.py`.
4. Set `type_id`, `label`, `icon` and `fields` (importing values from `providers.py`).
5. Implement the `test(config)` classmethod returning a `TestResult`.
6. Decorate the class with `@register`.
7. Import the module in `app/connections/__init__.py`.

```python
from app.config.providers import PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS
from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["myprovider"]

@register
class MyProvider(BaseProvider):
    type_id = "myprovider"
    label = "My Provider"
    icon = "🔌"
    fields = [
        FieldDef("api_key", "API Key", "password", required=True),
        FieldDef("model", "Model", "text", PROVIDER_DEFAULT_MODELS["myprovider"]),
    ]

    @classmethod
    def test(cls, config):
        # validate credentials and return TestResult
        ...
```
