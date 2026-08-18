# Decisiones de arquitectura (ADR)

Por qué el código es como es. Un fichero por decisión, con fecha y estado.

Están **solo en español** y fuera de `docs/es` + `docs/en` a propósito: aquello
es documentación de usuario y se traduce; esto es documentación interna de
ingeniería y no.

## Qué va aquí y qué no

Un bloque de comentario se convierte en ADR cuando **el porqué es transversal**:
afecta a más de un fichero, a la operación o al despliegue, y alguien puede
necesitarlo sin abrir ese fichero en concreto.

Se queda **en el código** cuando el porqué es local a la línea siguiente —
explica una constante concreta y solo tiene sentido junto a ella. Ejemplos que
se quedaron donde estaban: `_EXCLUIDAS` en `app/auth/user_lookup.py`,
`BCRYPT_ROUNDS` en `app/config/session.py`, `_COMPLETADAS` en
`app/storage/migration.py`, el allowlist de invitados en
`app/api/routes/auth/dependencies.py`.

Donde se movió un bloque, queda en el código una línea `# Ver docs/adr/NNN-….md`.

## Índice

| # | Decisión |
|---|---|
| [001](001-estado-en-memoria-con-multiples-workers.md) | Estado en memoria con múltiples workers — sesiones de invitado y reparto del rate limit |
| [002](002-el-contenedor-arranca-como-root.md) | El contenedor arranca como root y baja privilegios en el entrypoint |
| [003](003-gaia-data-dir-y-healthcheck.md) | `GAIA_DATA_DIR` fijado en la imagen y HEALTHCHECK con arranque largo |
| [004](004-explorar-esconde-lo-que-ya-tienes.md) | Explorar enseña lo que no tienes, y el estado vive en el servidor |
| [005](005-carga-diferida-en-flutter-web.md) | La web no descarga admin, workflows ni el checkout hasta que se entra |
| [006](006-csrf-en-dos-capas.md) | Anti-CSRF en dos capas: `Origin` verificado y token derivado del JWT |
| [007](007-sql-en-ficheros.md) | El SQL estático vive en `app/sql/` y se pide por identificador |
| [008](008-sesiones-revocables.md) | Sesiones revocables — access corto, refresh rotatorio y una tabla que manda |
| [009](009-cuota-compartida-y-por-principal.md) | La cuota de rate limit se comparte entre workers y se cuenta por principal |
| [010](010-el-grafo-se-arma-en-el-cliente.md) | El backend entrega relaciones; el grafo lo arma el cliente |
| [011](011-un-solo-limite-de-tamano-y-lo-pone-el-admin.md) | Un solo límite de tamaño de petición, y lo pone el administrador |

## Plantilla

```markdown
# NNN · <Título en una línea>

- **Fecha**: YYYY-MM-DD
- **Estado**: aceptada | sustituida por NNN | revertida
- **Afecta a**: <ficheros / repos>

## Contexto
<el fallo real que motivó la decisión>

## Decisión
## Alternativas descartadas
## Consecuencias
```
