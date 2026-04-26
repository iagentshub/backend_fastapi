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

Gemini, Grok y Qwen exponen un endpoint de chat completions compatible con OpenAI. El enrutamiento en `services/chat.py` mapea cada `conn.type` a su URL base:

| Tipo | URL base |
|---|---|
| `openai` | `https://api.openai.com/v1` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `grok` | `https://api.x.ai/v1` |
| `qwen` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |

---

## Añadir un nuevo proveedor

1. Crear `app/connections/{nombre}.py`.
2. Extender `BaseProvider` de `app/connections/base.py`.
3. Definir `type_id`, `label`, `icon` y `fields`.
4. Implementar el método de clase `test(config)` devolviendo un `TestResult`.
5. Decorar la clase con `@register`.
6. Importar el módulo en `app/connections/__init__.py`.

```python
from .base import BaseProvider, FieldDef, TestResult, register

@register
class MiProveedor(BaseProvider):
    type_id = "miproveedor"
    label = "Mi Proveedor"
    icon = "🔌"
    fields = [
        FieldDef("api_key", "API Key", "password", required=True),
        FieldDef("model", "Modelo", "text", "mi-modelo-v1"),
    ]

    @classmethod
    def test(cls, config):
        # validar credenciales y devolver TestResult
        ...
```
