# 003 · `GAIA_DATA_DIR` fijado en la imagen y HEALTHCHECK con arranque largo

- **Fecha**: 2026-08-16 (la decisión es anterior; esto solo la saca del código)
- **Estado**: aceptada
- **Afecta a**: `Dockerfile`, `app/config/data.py`, los tres ficheros compose,
  `/api/health`

## Contexto

**El directorio de datos.** `DATA_DIR` tiene un valor por defecto en
`app/config/data.py`: hermano del repo, que dentro del contenedor es `/iAgents`.
Sin `GAIA_DATA_DIR` explícito, la imagen escribe **fuera del volumen** y los
datos se pierden al recrear el contenedor, sin un solo error. Dos cosas lo
mantuvieron oculto: los composes ya fijaban la variable, así que solo se notaba
al arrancar la imagen a mano; y mientras el proceso era root no se notaba nada,
porque podía crear `/iAgents` sin quejarse.

**El healthcheck.** `/api/health` devuelve 503 cuando la BD no responde, pero
sin `HEALTHCHECK` en la imagen el endpoint existía y nadie lo consultaba desde
la infraestructura.

## Decisión

`ENV GAIA_DATA_DIR=/data` en el Dockerfile. La imagen no depende de que quien la
arranque acierte con la variable.

`HEALTHCHECK` con `--start-period=60s`, deliberadamente generoso: en un arranque
limpio el backend migra el esquema entero antes de responder, y con un
start-period corto Docker lo reiniciaría en bucle justo durante esa migración —
convirtiendo un arranque lento en un arranque imposible.

## Alternativas descartadas

- **Dejar `GAIA_DATA_DIR` solo en los composes** — es el estado que produjo el
  fallo. Funciona hasta que alguien hace `docker run` a mano.
- **Cambiar el default de `DATA_DIR` en `config/data.py`** — ese default es
  correcto para el desarrollo local, donde el repo sí tiene un hermano `iAgents/`.
  El que estaba mal era el del contenedor, y ahí es donde se corrige.
- **`--start-period` corto con más `--retries`** — los reintentos no ayudan si
  cada fallo reinicia el contenedor y con él la migración.

## Consecuencias

- Un despliegue que quiera otro directorio debe sobrescribir la variable
  explícitamente; el valor de la imagen ya no es "lo que caiga".
- Los primeros 60 s tras arrancar, el contenedor no se marca unhealthy aunque
  no responda. Es intencionado, y significa que el healthcheck no sirve para
  detectar un arranque colgado en ese primer minuto.
