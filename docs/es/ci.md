<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/ci.md">🇬🇧 Read in English</a>
</div>

<br>

# Calidad de código

El repositorio tiene dos capas de verificación automática que garantizan que el código es correcto antes de que llegue a la rama principal.

---

## Antes del commit

Un hook local revisa el código en el momento de hacer commit. Si alguna verificación falla, el commit se cancela hasta que se corrija.

Verifica dos cosas:

- **Estilo y errores comunes** — analiza el código en busca de problemas de formato y patrones problemáticos.
- **Tests** — ejecuta la suite completa de tests del proyecto.

Para activarlo, ejecuta una vez tras clonar el repositorio:

```bash
pip install pre-commit
pre-commit install
```

A partir de ese momento se ejecuta automáticamente en cada `git commit`.

---

## En GitHub (push y pull requests)

Cada vez que se sube código a la rama principal o se abre una pull request, GitHub ejecuta las mismas verificaciones en un entorno limpio. Esto actúa como red de seguridad para cambios que lleguen sin el hook local instalado.

Un pull request no puede fusionarse si las verificaciones fallan.
