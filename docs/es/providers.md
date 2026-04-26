<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/providers.md">🇬🇧 Read in English</a>
</div>

<br>

# Proveedores

Cada proveedor de IA está implementado como un adaptador en `app/connections/`. El adaptador recibe las credenciales de `connections.json` en tiempo de ejecución.

| Proveedor | Fichero | Modelos | Estilo de API |
|---|---|---|---|
| Anthropic | `anthropic.py` | Familia Claude | Anthropic Messages API |
| OpenAI | `openai.py` | Familia GPT, serie-o | OpenAI Chat Completions |
| Google Gemini | `google.py` | Familia Gemini | Compatible OpenAI |
| Grok (xAI) | `grok.py` | Familia Grok | Compatible OpenAI |
| Qwen (Alibaba) | `qwen.py` | Familia Qwen | Compatible OpenAI |
| Ollama | `ollama.py` | Cualquier modelo local | Ollama REST API |

---

## Proveedores compatibles con OpenAI

Gemini, Grok y Qwen exponen un endpoint de chat completions compatible con OpenAI. Las URLs se definen en `app/config/providers.py` (`OPENAI_COMPAT_URLS`) y se derivan de las URLs base centralizadas en `PROVIDER_BASE_URLS`.

| Tipo | URL base |
|---|---|
| `openai` | `PROVIDER_BASE_URLS["openai"]` |
| `gemini` | `PROVIDER_BASE_URLS["gemini"]` |
| `grok` | `PROVIDER_BASE_URLS["grok"]` |
| `qwen` | `PROVIDER_BASE_URLS["qwen"]` |

---

## Añadir un nuevo proveedor

1. Añadir la URL base y el modelo por defecto en `app/config/providers.py`.
2. Crear `app/connections/{nombre}.py`.
3. Extender `BaseProvider` de `app/connections/base.py`.
4. Definir `type_id`, `label`, `icon` y `fields` (importando los valores desde `providers.py`).
5. Implementar el método de clase `test(config)` devolviendo un `TestResult`.
6. Decorar la clase con `@register`.
7. Importar el módulo en `app/connections/__init__.py`.

```python
from app.config.providers import PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS
from .base import BaseProvider, FieldDef, TestResult, register

_BASE_URL = PROVIDER_BASE_URLS["miproveedor"]

@register
class MiProveedor(BaseProvider):
    type_id = "miproveedor"
    label = "Mi Proveedor"
    icon = "🔌"
    fields = [
        FieldDef("api_key", "API Key", "password", required=True),
        FieldDef("model", "Modelo", "text", PROVIDER_DEFAULT_MODELS["miproveedor"]),
    ]

    @classmethod
    def test(cls, config):
        # validar credenciales y devolver TestResult
        ...
```
