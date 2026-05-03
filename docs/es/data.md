<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/data.md">🇬🇧 Read in English</a>
</div>

<br>

# Datos

El backend guarda toda la información en un directorio de datos externo montado en el servicio. No se utiliza ninguna base de datos.

---

## Qué contiene

| Ruta | Contenido |
|---|---|
| `settings.json` | Configuración del sistema (secreto JWT de emergencia) |
| `users.json` | Cuentas de usuario registradas |
| `agents/` | Configuraciones de los agentes (instrucciones, modelo, skills asignadas) |
| `connections/` | Credenciales de los proveedores de IA, incluyendo el consumo acumulado de tokens por conexión |
| `memory/` | Memoria acumulada por cada agente entre conversaciones |
| `skills/public/` | Skills sincronizadas desde el repositorio de skills |
| `skills/private/` | Skills privadas de la instalación |

---

## Qué se versiona

Solo `settings.json` se incluye en el repositorio como valor por defecto. El resto de los datos no se versiona: contiene información específica de cada instalación.

---

## Skills

Las skills son ficheros de texto con una cabecera de metadatos (nombre, descripción, icono, categoría) seguida del contenido de la skill. El contenido se inyecta en el system prompt del agente cuando la skill está activada.
