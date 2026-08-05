#!/bin/sh
# Arranca como root, cede /data al usuario sin privilegios y baja privilegios.
#
# Por qué existe este fichero en vez de un `USER gaia` en el Dockerfile: el
# volumen /data de cualquier instalación anterior pertenece a root, porque hasta
# ahora el contenedor corría como root. Un `USER` a secas dejaría al proceso sin
# poder escribir su propia base de datos, y no en una instalación nueva —donde
# se vería enseguida— sino al ACTUALIZAR, que es cuando ya hay datos dentro.
set -eu

DATA_DIR="${GAIA_DATA_DIR:-/data}"
USUARIO="${GAIA_USER:-gaia}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"

    # El chown -R solo la primera vez. Recorrer un /data con miles de ficheros
    # en cada arranque cuesta segundos, y a partir del primero ya es del usuario
    # correcto: lo que escribe el proceso nace con su dueño.
    if [ "$(stat -c %U "$DATA_DIR")" != "$USUARIO" ]; then
        echo "[entrypoint] $DATA_DIR era de $(stat -c %U "$DATA_DIR"); cediéndolo a $USUARIO..."
        chown -R "$USUARIO:$USUARIO" "$DATA_DIR"
    fi

    # setpriv en vez de gosu o su-exec: ya viene en la imagen (util-linux) y
    # hace exec directo, sin proceso intermedio, así que las señales y el código
    # de salida llegan al proceso real. `su` bifurca y se los come.
    exec setpriv --reuid="$USUARIO" --regid="$USUARIO" --init-groups "$@"
fi

# Alguien arrancó ya sin privilegios (docker run --user, o Kubernetes con
# runAsUser). No hay nada que ceder y tampoco se puede: se sigue tal cual.
exec "$@"
