# 002 · El contenedor arranca como root y baja privilegios en el entrypoint

- **Fecha**: 2026-08-16 (la decisión es anterior; esto solo la saca del código)
- **Estado**: aceptada
- **Afecta a**: `Dockerfile`, `docker-entrypoint.sh`, cualquier instalación con
  un volumen `/data` creado por una versión anterior

## Contexto

Lo habitual en una imagen endurecida es terminar el Dockerfile con
`USER <no-root>`. Aquí no se puede: el volumen `/data` de una instalación **ya
existente** pertenece a root, porque las versiones anteriores de la imagen
corrían como root. Si el contenedor arrancara directamente como `gaia`, el
proceso no podría escribir en su propio volumen y la actualización rompería la
instalación de quien ya la tenía.

Un problema distinto y del mismo bloque: el repo se clona también en Windows.
Con `core.autocrlf`, `docker-entrypoint.sh` queda con finales de línea CRLF —
el shebang pasa a ser `#!/bin/sh\r` y el contenedor muere con
`no such file or directory` señalando un intérprete que sí existe. Windows
tampoco conserva el bit de ejecución.

## Decisión

El contenedor **arranca como root a propósito**. `docker-entrypoint.sh` cede
`/data` al usuario `gaia` (uid 1000, creado en el Dockerfile) y solo entonces
baja privilegios. El Dockerfile no lleva `USER`.

En el mismo `RUN` se normaliza el entrypoint antes de usarlo:

```dockerfile
RUN useradd --system --create-home --uid 1000 gaia \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R gaia:gaia /app
```

El `sed` y el `chmod` no sobran: son la defensa contra el clon hecho en Windows.

## Alternativas descartadas

- **`USER gaia` al final del Dockerfile** — rompe toda instalación existente
  cuyo `/data` sea de root. El coste cae sobre el usuario que actualiza, que es
  justo quien no debería pagarlo.
- **Pedir al usuario que ejecute `chown` sobre su volumen antes de actualizar** —
  un paso manual en una actualización es un paso que no se da.
- **Confiar en `.gitattributes` para el CRLF** — ayuda, pero solo si el clon lo
  respeta; la normalización en el build es incondicional y cuesta un `sed`.

## Consecuencias

- El proceso que sirve tráfico no es root, pero el arranque sí. Un escaneo de
  imágenes marcará la ausencia de `USER` como hallazgo: es esperado, y la
  respuesta es este documento.
- La cesión de `/data` ocurre en cada arranque, también cuando no hace falta.
- Ver `docker-entrypoint.sh` para el detalle de cómo se bajan los privilegios.
